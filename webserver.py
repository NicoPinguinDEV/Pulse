# =============================================================
# BOCHUM RP – TEAM DASHBOARD  (v3.0)
# =============================================================
import asyncio
import base64
import contextlib
import contextvars
import copy
import csv
import hashlib
import hmac
import html
import io
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import sys
from contextlib import closing
import threading
import time
import weakref
import uuid
import zipfile
from datetime import date as date_cls, datetime, timedelta, timezone
from collections import OrderedDict
from types import SimpleNamespace
from typing import List
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse

from fastapi import FastAPI, Form, Request, Cookie, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
import httpx
import pulse_public_site
import pulse_summaries
import pulse_ticket_access
import discord
from pulse_discord_style import send_styled
from pulse_embed_design import color as _pulse_color
import pulse_db as pulse_db
from pulse_tasks import spawn as spawn_task
import pulse_paths
import pulse_sqlite
import pulse_logging
from pulse_safe_path import contained_file
pulse_logging.safe_console()  # status prints with emoji must not crash on a cp1252 console (no file access)
from pulse_version import PULSE_VERSION
from pulse_env_validation import safe_float, safe_int
from pulse_time import display, parse_timestamp
from pulse_employment import build_employment_event, employment_hired_at, store_employment_event

from pulse_shift_utils import (AUTO_SHIFT_END_HOURS, AUTO_SHIFT_END_SECONDS, OFFBOARDING_CLOSE_REASON, credited_shift_seconds,
                               needs_duration_confirmation, parse_max_shift_hours, shift_age_seconds, shift_event_id)
from career_applications import (
    APPLICATION_CAREERS, APPLICATION_PRACTICAL_TASKS, grade_career_test,
    grade_practical_tasks, render_career_test, render_practical_tasks,
)
from pulse_melonly import can_manage_melonly_type, rank_roblox_candidates, roblox_similarity_score
import pulse_ids as _pulse_ids  # A-09: fixed Discord IDs live in one place

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

BASE_DIR = Path(__file__).resolve().parent  # code (static/, cogs/) – read-only in Docker
# Phase 13 (D-06): everything Pulse writes lives below DATA_DIR (PULSE_DATA_DIR, default: working directory).
DATA_DIR = pulse_paths.DATA_DIR
# Phase 9: no os.chdir and no .env loading on import – main.prepare_process() does both for the bot process
# before webserver is imported (P8-06; tests importing webserver got the checkout's real .env).

# =============================================================
# KONFIGURATION & UMGEBUNGSVARIABELN
# =============================================================
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "").strip()
APPLICATION_REDIRECT_URI = os.getenv("DISCORD_APPLICATION_REDIRECT_URI", "").strip()

def request_origin(request: Request) -> str:
    """Ermittelt die öffentliche Dashboard-URL hinter einem Reverse Proxy."""
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip("/")
    # SEC-02: the configured Discord callback names the public origin as well. Only when nothing is
    # configured does the request decide – and then never X-Forwarded-Host/-Proto straight from the client
    # (uvicorn's proxy_headers already applies X-Forwarded-Proto from trusted proxies to request.url.scheme),
    # and only a plain host[:port], so a forged header cannot put a foreign link into a bot message.
    configured = urlparse(REDIRECT_URI)
    if configured.scheme in ("http", "https") and configured.netloc:
        return f"{configured.scheme}://{configured.netloc}"
    host = (request.headers.get("host") or "").strip()
    scheme = request.url.scheme if request.url.scheme in ("http", "https") else "http"
    if host and _PLAIN_HOST.fullmatch(host):
        return f"{scheme}://{host}"
    return str(request.base_url).rstrip("/")


_PLAIN_HOST = re.compile(r"(?:[A-Za-z0-9-]{1,63}\.)*[A-Za-z0-9-]{1,63}(?::\d{1,5})?|\[[0-9A-Fa-f:.]+\](?::\d{1,5})?")

OAUTH_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def _oauth_problem(response) -> str | None:
    if response.status_code == 429:
        return "Discord begrenzt gerade die Anmeldungen. Bitte in einer Minute erneut versuchen."
    if response.status_code >= 500:
        return "Discord ist gerade nicht erreichbar. Bitte gleich erneut versuchen."
    if response.status_code != 200:
        return "Discord hat den Login abgelehnt (Anmeldecode abgelaufen?). Bitte erneut anmelden."
    return None


async def discord_oauth_identify(code: str, redirect_uri: str) -> tuple[dict | None, str]:
    """P-12: exchange the OAuth code and read the user. Returns (user, "") or (None, user-facing message).
    Status codes, invalid JSON and timeouts are handled – never an unhandled 500."""
    try:
        async with httpx.AsyncClient(timeout=OAUTH_TIMEOUT) as client:
            token_res = await client.post(
                "https://discord.com/api/v10/oauth2/token",
                data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "grant_type": "authorization_code",
                      "code": code, "redirect_uri": redirect_uri},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            problem = _oauth_problem(token_res)
            if problem:
                return None, problem
            access_token = (token_res.json() or {}).get("access_token")
            if not access_token:
                return None, "Discord hat den Login abgelehnt. Bitte erneut versuchen."
            user_res = await client.get("https://discord.com/api/v10/users/@me",
                                        headers={"Authorization": f"Bearer {access_token}"})
            problem = _oauth_problem(user_res)
            if problem:
                return None, problem
            user = user_res.json()
    except httpx.TimeoutException:
        return None, "Discord hat nicht rechtzeitig geantwortet. Bitte erneut versuchen."
    except (httpx.HTTPError, ValueError):
        return None, "Die Verbindung zu Discord ist fehlgeschlagen. Bitte erneut versuchen."
    if not isinstance(user, dict) or not str(user.get("id") or "").isdigit():
        return None, "Discord hat keine gültigen Kontodaten geliefert. Bitte erneut versuchen."
    return user, ""


def oauth_redirect_uri(request: Request, application: bool = False) -> str:
    """Gibt eine explizit konfigurierte Callback-URL zurück.

    Ohne separate Bewerbungs-Callback-URL verwendet der Bewerbungslogin dieselbe
    registrierte Discord-Callback-URL wie das Dashboard. So wird nicht automatisch
    /apply/callback angefordert, obwohl in Discord nur /callback registriert ist.
    """
    # Für Bewerbungen hat die bereits vorhandene Dashboard-Callback-URL Vorrang.
    # Eine getrennte Bewerbungs-URI wird nur als Fallback genutzt, damit eine neu
    # hinzugefügte Route nicht plötzlich eine neue Discord-Developer-Portal-URI verlangt.
    configured = (REDIRECT_URI or APPLICATION_REDIRECT_URI) if application else REDIRECT_URI
    if configured:
        return configured.rstrip("/")
    return f"{request_origin(request)}/callback"

def oauth_cookie_secure(request: Request) -> bool:
    return oauth_redirect_uri(request).startswith("https://") or oauth_redirect_uri(request, application=True).startswith("https://")
GUILD_ID = safe_int("DISCORD_GUILD_ID", _pulse_ids.GUILD_ID, 1, 2**63 - 1)
APPLICATION_REQUIRED_ROLE_ID = _pulse_ids.APPLICATION_REQUIRED_ROLE_ID
APPLICATION_ALERT_CHANNEL_ID = _pulse_ids.APPLICATION_ALERT_CHANNEL_ID
APPLICATION_ALERT_ROLE_ID = _pulse_ids.APPLICATION_ALERT_ROLE_ID
APPLICATION_ROLE_ID = _pulse_ids.APPLICATION_ROLE_ID  # Bewerberolle bis zum abgeschlossenen Bewerbungsgespräch
TEAM_UPDATE_CHANNEL_NAME = os.getenv("TEAM_UPDATE_CHANNEL_NAME", "╚『⚡』𝐓𝐞𝐚𝐦-𝐔𝐩𝐝𝐚𝐭𝐞𝐬")
TEAM_UPDATE_CHANNEL_ID = _pulse_ids.TEAM_UPDATE_CHANNEL_ID  # zentraler Team-Updates-Kanal
# Nach einem manuellen Team-Kick bleiben ausschließlich diese beiden Rollen erhalten.
TEAM_KICK_KEEP_ROLE_IDS = _pulse_ids.TEAM_KICK_KEEP_ROLE_IDS

WARN_ROLE_IDS = _pulse_ids.WARN_ROLE_IDS
WARN_UPRANK_LOCK_ROLE_ID = _pulse_ids.WARN_UPRANK_LOCK_ROLE_ID
WARN_IC_LOCK_ROLE_ID = _pulse_ids.WARN_IC_LOCK_ROLE_ID
WARN_TEMP_ROLE_DAYS = 7
DEFAULT_AUTO_WARN_DURATION_DAYS = 7
TEST_SUPPORTER_ROLE_ID = _pulse_ids.TEST_SUPPORTER_ROLE_ID
VALID_PROBATION_DAYS = (3, 7)
SYNC_WARN_ROLES = os.getenv("SYNC_WARN_ROLES", "1") == "1"      # Warn-Rollen automatisch vergeben
SESSION_DAYS = safe_int("SESSION_DAYS", 7, 1, 365)                # Login-Dauer
# Vergessene Schichten werden nach genau 24 Stunden automatisch beendet und verwarnt.
# MAX_SHIFT_HOURS bleibt als kompatible Konstante für alte Integrationen bestehen.
MAX_SHIFT_HOURS = AUTO_SHIFT_END_HOURS
# E-2 / O-06: .env MAX_SHIFT_HOURS (0.5–24, Standard 12) begrenzt die von einer Führungskraft bestätigte Dauer
# einer automatisch beendeten Schicht. Unbestätigt zählt sie 0 h; der Auto-Abschluss bleibt bei 24 h.
MAX_CONFIRMED_SHIFT_HOURS = parse_max_shift_hours(os.getenv("MAX_SHIFT_HOURS"))
AUTO_BACKUP_HOURS = safe_float("AUTO_BACKUP_HOURS", 24.0, 0.0, 8760.0) # 0 = aus
MAX_BACKUPS = safe_int("MAX_BACKUPS", 30, 1, 1000)
VOICE_SUPPORT_AUDIO_DIR = DATA_DIR / "uploads" / "voice_support_audio"
MAX_VOICE_SUPPORT_AUDIO_BYTES = 10 * 1024 * 1024
VOICE_SUPPORT_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
COOKIE_SECURE = bool(PUBLIC_BASE_URL and PUBLIC_BASE_URL.startswith("https://"))

DATA_FILE = str(DATA_DIR / "team_data.json")
CONFIG_FILE = str(DATA_DIR / "config.json")
APPS_FILE = str(DATA_DIR / "applications.json")
SHIFTS_FILE = str(DATA_DIR / "shifts.json")
LOGS_FILE = str(DATA_DIR / "logs.json")
AUDIT_FILE = str(DATA_DIR / "audit_logs.json")
MEETINGS_FILE = str(DATA_DIR / "meetings.json")
DB_ABMELDUNGEN = str(DATA_DIR / "abmeldungen.db")
ACTIVITY_DB = str(DATA_DIR / "activity_check.db")
BACKUP_DIR = str(DATA_DIR / "backups")
SECRET_FILE = str(DATA_DIR / ".session_secret")

VALID_LOG_TYPES = ("Warn", "Kick", "Ban", "Notiz", "Ban BOLO")
PERM_KEYS = (
    "can_view_dashboard", "can_warn", "can_promote", "can_add_notes",
    "can_manage_tickets", "can_manage_applications", "can_manage_tasks",
    "can_manage_training", "can_manage_wiki", "can_view_analytics",
)
DASHBOARD_PERMISSION_LABELS = (
    ("can_view_dashboard", "Dashboard sehen"),
    ("can_warn", "Verwarnungen verwalten"),
    ("can_promote", "Befördern, degradieren und Team-Kick"),
    ("can_add_notes", "Teamakten-Notizen erstellen"),
    ("can_manage_tickets", "Tickets verwalten"),
    ("can_manage_applications", "Bewerbungen verwalten"),
    ("can_manage_tasks", "Aufgaben verwalten"),
    ("can_manage_training", "Schulungen verwalten"),
    ("can_manage_wiki", "Team-Wiki verwalten"),
    ("can_view_analytics", "Statistiken und Auswertungen sehen"),
)


def _load_panel_timezone():
    """K-13: Europe/Berlin (or PANEL_TIMEZONE). Without IANA data (tzdata) we fall back to the
    container time zone – loudly, because shift and absence times would otherwise be off."""
    name = os.getenv("PANEL_TIMEZONE", "Europe/Berlin")
    try:
        return ZoneInfo(name) if ZoneInfo else None
    except Exception as exc:
        print(f"⚠️ Zeitzone {name!r} nicht verfügbar ({type(exc).__name__}) – Uhrzeiten nutzen die "
              "Container-Zeitzone. Paket 'tzdata' installieren (steht in requirements.txt).")
        return None


TZ = _load_panel_timezone()

# K-17: no public API documentation (/docs, /redoc, /openapi.json listed every endpoint without login).
@contextlib.asynccontextmanager
async def _lifespan(_app):
    """A-04: FastAPI lifespan instead of the deprecated @app.on_event handlers."""
    init_runtime()
    try:
        # Phase 9: inside try – a start that fails half-way still closes what it opened (HTTP pool, backup task).
        await _start_background_tasks()
        yield
    finally:
        await _stop_background_tasks()


app = FastAPI(title="Pulse TeamOS", version=PULSE_VERSION, docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=_lifespan)


# Small inline favicon avoids the browser's recurring default /favicon.ico 404.
_FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="15" fill="#1f2225"/>
<path d="M18 13h9v29h20v9H18z" fill="#fff"/>
<circle cx="46" cy="17" r="5" fill="#ff6f4f"/>
</svg>"""


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(
        content=_FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _loa_rank_problem(ctx, guild, team_role_ids, target_id) -> bool:
    """AC-03: leaders manage absences of lower-ranked members only (admins excepted), like promote/demote/team_kick."""
    if ctx.perms.get("is_admin"):
        return False
    target = guild.get_member(int(target_id))
    actor_rank = team_rank(ctx.member, team_role_ids, guild) if ctx.member else -1
    return target is not None and actor_rank <= team_rank(target, team_role_ids, guild)


# Night audit F-1: bodies are parsed (Form/UploadFile) BEFORE the handler authenticates, so an anonymous client could
# make Pulse buffer a huge urlencoded/JSON body (768 MiB container). Declared sizes are capped here; multipart
# uploads (spooled to disk by Starlette) keep the larger limit that the upload routes enforce themselves.
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_UPLOAD_BODY_BYTES = 60 * 1024 * 1024
_BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def request_body_problem(method: str, headers) -> tuple[int, str] | None:
    """(status, message) when the declared request body is not acceptable, else None."""
    if str(method).upper() not in _BODY_METHODS:
        return None
    raw = (headers.get("content-length") or "").strip()
    if not raw:
        if "chunked" in (headers.get("transfer-encoding") or "").lower():
            return 411, "Content-Length erforderlich."
        return None
    if not raw.isdigit() or len(raw) > 12:
        return 400, "Ungültige Content-Length."
    limit = MAX_UPLOAD_BODY_BYTES if "multipart/form-data" in (headers.get("content-type") or "").lower() else MAX_BODY_BYTES
    if int(raw) > limit:
        return 413, "Anfrage zu groß."
    return None


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """U-13: the single security middleware – same-origin check for state-changing requests
    (Origin, else Referer) and one consistent set of headers incl. a basic CSP (pulse_security)."""
    import pulse_security
    if not pulse_security.same_origin_ok(request.method, request.headers):
        return JSONResponse({"detail": "Ungültige Anfragequelle."}, status_code=403)
    too_big = request_body_problem(request.method, request.headers)
    if too_big:
        return JSONResponse({"detail": too_big[1]}, status_code=too_big[0])
    started = time.perf_counter()
    probe = None
    if request.method == "POST" and request.url.path.startswith(AUDIT_PROBE_PREFIXES):
        probe = {"n": 0, "user": None}
        _AUDIT_PROBE.set(probe)
    response = await call_next(request)
    if probe is not None:
        try:  # Audit #165: never affects the response
            route = getattr(request.scope.get("route"), "path", "")
            audit_probe_finish(probe, request.method, request.url.path, route, response.status_code)
        except Exception:  # noqa: BLE001
            pass
    try:  # Audit #157: latency per route template (bounded in-memory, never affects the response)
        if not request.url.path.startswith("/static"):
            import pulse_ops
            route = request.scope.get("route")
            pulse_ops.LATENCY.record(f"{request.method} {getattr(route, 'path', '(unbekannt)')}", (time.perf_counter() - started) * 1000, response.status_code)
    except Exception:  # noqa: BLE001
        pass
    https = oauth_redirect_uri(request).startswith("https://") or oauth_redirect_uri(request, application=True).startswith("https://")
    pulse_security.apply_headers(request.url.path, request.method, response, https)
    return response


# =============================================================
# ZEIT-HELFER (alles in deutscher Zeit, unabhängig vom Server)
# =============================================================
def now_de() -> datetime:
    return datetime.now(TZ) if TZ else datetime.now().astimezone()


def utc_now_naive_iso() -> str:
    """UTC timestamp without offset – same stored format as the former (deprecated) utcnow-based stamps."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def parse_dt(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:  # alte Einträge ohne Zeitzone
        dt = dt.replace(tzinfo=TZ) if TZ else dt.astimezone()
    return dt


def fmt_date(value: str) -> str:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        return str(value)


def fmt_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def week_start() -> datetime:
    n = now_de().replace(tzinfo=None)
    return (n - timedelta(days=n.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)

# =============================================================
# DATENBANK-INITIALISIERUNG
# =============================================================
def init_db():
    conn = sqlite3.connect(DB_ABMELDUNGEN)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS abmeldungen (
            user_id INTEGER PRIMARY KEY,
            user_name TEXT,
            grund TEXT,
            von TEXT,
            bis TEXT,
            original_nick TEXT,
            guild_id INTEGER
        )
    """)
    conn.commit()
    conn.close()


_RUNTIME_READY = False


def init_runtime() -> None:
    """A-04: create runtime files explicitly at start (main.py / app lifespan) instead of on import.
    Idempotent. Neither webserver nor the modules registered at its end (pulse_pro, pulse_features,
    pulse_ultimate, pulse_next, …) create files, folders or tables on import (Phase 9, tests/test_lifecycle.py);
    pulse_db.connect also creates its tables on first use of a database path."""
    global _RUNTIME_READY
    if _RUNTIME_READY:
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    import pulse_next
    import pulse_ultimate
    pulse_next.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    pulse_ultimate.ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    pulse_db.init_db()
    pulse_next.setup_next()  # Phase 9: tables of pulse_ultimate/pulse_next, before registration created them on import
    import pulse_migrations  # A-05: versioned migrations (backup before the first pending one)
    pulse_migrations.run(os.path.abspath(pulse_db.DB_PATH), os.path.abspath(BACKUP_DIR))
    import pulse_shift_history  # Phase 14.7: switches only when PULSE_SHIFT_HISTORY_BACKEND is set explicitly
    pulse_shift_history.recover(sys.modules[__name__])  # Phase 14.7b: finish a save a crash interrupted
    pulse_shift_history.apply_configured_backend(sys.modules[__name__])
    secure_application_storage()  # applications.json contains private applicant answers and reviews
    melonly_repository = get_melonly_repository()  # validate backend before bot/dashboard writes start
    print(f"✅ Melonly-Speicher: {melonly_repository.backend_name.upper()}")
    _session_key()  # computes SESSION_SECRET_PROBLEM for the system status
    _RUNTIME_READY = True

# =============================================================
# DATEI-HELFER (thread-safe + atomares Schreiben)
# =============================================================
_io_lock = threading.RLock()
_APPLICATION_REPOSITORY = None
_APPLICATION_REPOSITORY_PATH = ""
# Serialize decision flows that hold Discord side effects across awaits.
APPLICATION_DECISION_LOCK = asyncio.Lock()


# K-05: files that could not be read/parsed. Writing them is blocked so defaults never overwrite real data.
_CORRUPT_JSON: dict[str, str] = {}


class JsonWriteBlocked(RuntimeError):
    """Raised by save_json for a file that is marked corrupt (degraded mode)."""


def corrupt_json_files() -> dict[str, str]:
    with _io_lock:
        return dict(_CORRUPT_JSON)


def _mark_corrupt(filepath, exc) -> None:
    key = os.path.abspath(filepath)
    if key in _CORRUPT_JSON:
        return
    backup = ""
    if isinstance(exc, ValueError):  # JSONDecodeError / UnicodeDecodeError – keep an untouched copy
        backup = f"{filepath}.corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        try:
            shutil.copy2(filepath, backup)
        except OSError:
            backup = ""
    import pulse_shift_history  # Phase 14.7b: say why the shift history is blocked (no data in the message)
    reason = f"{type(exc).__name__}: {exc}" if isinstance(exc, pulse_shift_history.ShiftHistoryUnavailable) else type(exc).__name__
    _CORRUPT_JSON[key] = (f"{os.path.basename(filepath)} ist beschädigt ({reason})"
                          + (f" – Sicherung: {os.path.basename(backup)}" if backup else ""))
    print(f"🔴 DATEN: {_CORRUPT_JSON[key]}. Pulse schreibt diese Datei nicht mehr, bis sie repariert "
          "oder aus einem Backup wiederhergestellt ist.")


def _load_json_file(filepath, default):
    """Read JSON using the established corruption marker and legacy fallback behavior."""
    with _io_lock:
        if os.path.exists(filepath):
            try:
                shifts = os.path.abspath(filepath) == os.path.abspath(SHIFTS_FILE)
                stat_key = _shift_stat_key(filepath) if shifts else None
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if shifts:
                    data = _attach_shift_history(filepath, data, stat_key)
            except Exception as exc:
                _mark_corrupt(filepath, exc)
                return default
            _CORRUPT_JSON.pop(os.path.abspath(filepath), None)  # repaired by hand → writable again
            return data
    return default


def load_json(filepath, default):
    if os.path.abspath(filepath) == os.path.abspath(APPS_FILE):
        return get_applications_repository().load(default)
    return _load_json_file(filepath, default)


def _shift_stat_key(filepath):
    import pulse_shift_history
    return pulse_shift_history._stat_key(filepath)


def _attach_shift_history(filepath, data, stat_key=None):
    """Phase 14.7: in SQLite mode the history of shifts.json lives in pulse.db – callers see the old shape.
    If pulse.db cannot deliver it, the error marks shifts.json as unreadable, so nothing writes over it."""
    import pulse_shift_history
    pulse_shift_history.remember_mode(filepath, data, stat_key)
    if pulse_shift_history.is_sqlite(data):
        data = pulse_shift_history.attach(data, sys.modules[__name__], filepath)  # 14.7b: may finish a crashed save
    return data


def abspath_corrupt_free(filepath) -> bool:
    return os.path.abspath(filepath) not in _CORRUPT_JSON


def get_applications_repository():
    """Return the JSON adapter for the current APPS_FILE path (lazy, test-path aware)."""
    global _APPLICATION_REPOSITORY, _APPLICATION_REPOSITORY_PATH
    path = os.path.abspath(APPS_FILE)
    if _APPLICATION_REPOSITORY is None or _APPLICATION_REPOSITORY_PATH != path:
        from pulse_applications_repository import JsonApplicationRepository

        _APPLICATION_REPOSITORY = JsonApplicationRepository(
            path,
            read_json=_load_json_file,
            write_json=_write_json_file,
            lock=_io_lock,
        )
        _APPLICATION_REPOSITORY_PATH = path
    return _APPLICATION_REPOSITORY


def secure_application_storage() -> None:
    """Restrict persisted applicant records to the Pulse service account on POSIX servers."""
    if os.name != "posix":
        return
    with _io_lock:
        if os.path.isfile(APPS_FILE):
            os.chmod(APPS_FILE, 0o600)


def save_json(filepath, data):
    if os.path.abspath(filepath) == os.path.abspath(APPS_FILE):
        get_applications_repository().save(data)
        return
    with _io_lock:
        if os.path.abspath(filepath) == os.path.abspath(SHIFTS_FILE) and isinstance(data, dict):
            import pulse_shift_history  # Phase 14.7: the one write point for shifts – JSON or SQLite history
            if pulse_shift_history.file_is_sqlite(filepath):
                problem = _CORRUPT_JSON.get(os.path.abspath(filepath))
                if problem:
                    raise JsonWriteBlocked(problem)
                import pulse_backup
                if pulse_backup.maintenance_active():
                    raise JsonWriteBlocked("Wartungsmodus – eine Wiederherstellung läuft gerade")
                pulse_shift_history.save(sys.modules[__name__], filepath, data)
                return
        _write_json_file(filepath, data)
        if os.path.abspath(filepath) == os.path.abspath(SHIFTS_FILE) and isinstance(data, dict):
            import pulse_shift_history
            pulse_shift_history.remember_mode(filepath, data)


def _write_json_file(filepath, data):
    import pulse_settings_history
    with _io_lock:
        config_before = None
        if os.path.abspath(filepath) == os.path.abspath(CONFIG_FILE):
            _CONFIG_CACHE.update(key=None, data=None)  # P-04: invalidate on every write
            if pulse_settings_history.wanted():  # F-03: previous config only when a dashboard change is recorded
                try:
                    with open(filepath, encoding="utf-8") as f:
                        config_before = json.load(f)
                except (OSError, ValueError):
                    config_before = {}
        problem = _CORRUPT_JSON.get(os.path.abspath(filepath))
        if problem:
            raise JsonWriteBlocked(problem)
        import pulse_backup
        if pulse_backup.maintenance_active():  # D-02: a restore is replacing the data files right now
            raise JsonWriteBlocked("Wartungsmodus – eine Wiederherstellung läuft gerade")
        tmp = f"{filepath}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            if os.path.abspath(filepath) == os.path.abspath(APPS_FILE) and os.name == "posix":
                os.chmod(tmp, 0o600)  # applicant answers and reviews are readable only by the service account
            os.replace(tmp, filepath)  # verhindert kaputte Dateien bei Absturz
        except BaseException:
            try:  # M-D2: no half-written .tmp file left behind; the original stays untouched
                os.remove(tmp)
            except OSError:
                pass
            raise
        if config_before is not None:
            pulse_settings_history.record_config(config_before, data)
        try:  # persist the rename itself (not supported on Windows)
            dir_fd = os.open(os.path.dirname(os.path.abspath(filepath)), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass


_CONFIG_CACHE: dict = {"key": None, "data": None}


def _config_cache_key():
    try:
        st = os.stat(CONFIG_FILE)
    except OSError:
        return None
    return (os.path.abspath(CONFIG_FILE), st.st_mtime_ns, st.st_size)


def load_config() -> dict:
    # P-04: config.json is read on every authenticated request – the file is only read from disk when it
    # changed (mtime/size). The raw bytes are cached and parsed per call (json.loads is several times
    # cheaper than deepcopy), so every caller gets its own object and changes never leak into the cache.
    key = _config_cache_key()
    cached = _CONFIG_CACHE["data"] if key is not None and key == _CONFIG_CACHE["key"] else None
    cfg = None
    if cached is not None and abspath_corrupt_free(CONFIG_FILE):
        try:
            cfg = json.loads(cached)
        except ValueError:
            cfg = None
    if cfg is None:
        cfg = load_json(CONFIG_FILE, {})  # marks a corrupt file (K-05) exactly as before
        if key is not None and isinstance(cfg, dict) and abspath_corrupt_free(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "rb") as fh:
                    raw = fh.read()
                if _config_cache_key() == key:  # unchanged while reading
                    _CONFIG_CACHE.update(key=key, data=raw)
            except OSError:
                pass
    cfg.setdefault("team_role_ids", [])
    cfg.setdefault("weekly_goal_hours", 3.0)
    cfg.setdefault("permissions", {})
    # Dashboard-only Rollen werden intern gespeichert und ändern keine Discord-Rollen.
    cfg.setdefault("dashboard_roles", {})
    cfg.setdefault("dashboard_user_roles", {})
    return cfg


def application_module_states(config: dict | None = None) -> dict[str, bool]:
    """Liefert den Aktivstatus der Bewerbungs-Laufbahnen; Alt-Konfigurationen bleiben aktiv."""
    cfg = config if isinstance(config, dict) else load_config()
    stored = cfg.get("application_modules", {})
    if not isinstance(stored, dict):
        stored = {}
    return {key: bool(stored.get(key, True)) for key in APPLICATION_CAREERS}


APPLICATION_REVIEW_CRITERIA = (
    ("rules", "Regelverständnis"),
    ("communication", "Kommunikation & Deeskalation"),
    ("judgement", "Urteilsvermögen"),
    ("teamwork", "Zusammenarbeit"),
    ("responsibility", "Verantwortungsbewusstsein"),
)


def load_meetings() -> dict:
    """Normalisiert die Meeting-Datei (vorher: KeyError, wenn 'rsvps' fehlte)."""
    d = load_json(MEETINGS_FILE, {})
    d.setdefault("title", "Nächste Teambesprechung")
    d.setdefault("date_time", "Noch nicht angesetzt")
    d.setdefault("description", "Hier können wichtige Punkte für die kommende Besprechung gesammelt werden.")
    d.setdefault("rsvps", {})
    d.setdefault("topics", [])
    changed = False
    for t in d["topics"]:
        if not t.get("id"):
            t["id"] = f"topic_{uuid.uuid4().hex[:6]}"
            changed = True
    if changed:
        save_json(MEETINGS_FILE, d)
    return d


# A-01a: explicit shell hooks instead of replacing get_head_html/get_sidebar_html/log_audit at runtime.
# One implementation per slot; webserver's own functions stay in place and call the registered hook.
#   head_extra(title) -> str        appended to get_head_html
#   sidebar(guild_name, current_page, current_user, perms) -> str   replaces the built-in sidebar markup
#   audit_listener(actor_name, actor_id, action, details)            called after the audit entry was saved
#   body_extra() -> str             prepended inside the shared dashboard body, before route content
SHELL_HOOK_SLOTS = ("head_extra", "sidebar", "audit_listener", "body_extra")
_shell_hooks: dict = {}


def register_shell_hook(slot: str, hook) -> None:
    """Register one hook per slot. The same function again is a no-op; a different one is refused."""
    if slot not in SHELL_HOOK_SLOTS:
        raise ValueError(f"Unbekannter Shell-Hook {slot!r} (erlaubt: {', '.join(SHELL_HOOK_SLOTS)})")
    if not callable(hook):
        raise TypeError(f"Shell-Hook {slot!r} muss aufrufbar sein")
    current = _shell_hooks.get(slot)
    if current is hook:
        return
    if current is not None:
        raise RuntimeError(f"Shell-Hook {slot!r} ist bereits durch "
                           f"{current.__module__}.{current.__qualname__} registriert")
    _shell_hooks[slot] = hook


def shell_hook(slot: str):
    """The registered hook of a slot or None (built-in behaviour)."""
    if slot not in SHELL_HOOK_SLOTS:
        raise ValueError(f"Unbekannter Shell-Hook {slot!r}")
    return _shell_hooks.get(slot)


# Audit #165: a state-changing admin request always leaves a trace. The probe counts audit writes during one request;
# if an admin POST succeeded without writing any, the middleware records a generic entry (who, which route).
_AUDIT_PROBE: contextvars.ContextVar = contextvars.ContextVar("pulse_audit_probe", default=None)
AUDIT_PROBE_PREFIXES = ("/admin/",)


def audit_probe_finish(probe, method: str, path: str, route: str, status: int) -> bool:
    """After a request: write the fallback entry if an admin POST succeeded, the actor is known and nothing was audited."""
    if not isinstance(probe, dict) or method != "POST" or status >= 400 or probe.get("n") or not probe.get("user"):
        return False
    if not any(path.startswith(prefix) for prefix in AUDIT_PROBE_PREFIXES):
        return False
    user = probe["user"]
    log_audit(user.get("global_name") or user.get("username") or "Admin", str(user.get("id")), "Admin-Aktion", f"POST {route or path}"[:200])
    return True


AUDIT_MAX_ENTRIES = 5000

# DATA-3: per-member lock for dashboard rank actions + short replay window (bounded: locks are weakly referenced,
# the replay table drops entries after the window).
_RANK_REPLAY_SECONDS = 8.0
_rank_locks: "weakref.WeakValueDictionary[tuple, asyncio.Lock]" = weakref.WeakValueDictionary()
_rank_recent: dict[tuple, float] = {}


def _rank_target_lock(guild_id, member_id) -> asyncio.Lock:
    key = (int(guild_id), int(member_id))
    lock = _rank_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _rank_locks[key] = lock
    return lock


def _rank_action_replayed(guild_id, member_id, action: str, now: float | None = None) -> bool:
    """True when the same rank action for this member was started within the replay window; otherwise records it."""
    current = time.monotonic() if now is None else now
    for key in [k for k, ts in _rank_recent.items() if current - ts > _RANK_REPLAY_SECONDS]:
        _rank_recent.pop(key, None)
    key = (int(guild_id), int(member_id), str(action))
    if key in _rank_recent:
        return True
    _rank_recent[key] = current
    return False


def _archive_audit_entries(entries: list) -> None:
    """DATA-2: audit entries that rotate out of the live file are appended (JSON lines, never rewritten) to
    audit_logs_archive.jsonl next to it, so the history is not truncated. Best effort: a problem here must never
    break the audited action itself."""
    try:
        archive = Path(AUDIT_FILE).with_name("audit_logs_archive.jsonl")
        with open(archive, "a", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ Audit-Archiv nicht beschreibbar: {type(exc).__name__}")


def log_audit(actor_name: str, actor_id: str, action: str, details: str) -> str:
    probe = _AUDIT_PROBE.get()
    if isinstance(probe, dict):
        probe["n"] = probe.get("n", 0) + 1
    audit_id = f"audit_{uuid.uuid4().hex[:6]}"
    audit_data = load_json(AUDIT_FILE, [])
    audit_data.append({
        "id": audit_id,
        "actor": actor_name,
        "actor_id": str(actor_id),
        "action": action,
        "details": details,
        "timestamp": now_de().strftime("%d.%m.%Y %H:%M:%S")
    })
    dropped_entries = audit_data[:-AUDIT_MAX_ENTRIES]
    save_json(AUDIT_FILE, audit_data[-AUDIT_MAX_ENTRIES:])  # the live file does not grow forever ...
    if dropped_entries:
        _archive_audit_entries(dropped_entries)  # ... but nothing is lost: append-only archive (DATA-2)
    listener = _shell_hooks.get("audit_listener")
    if listener is not None:
        try:
            listener(actor_name, actor_id, action, details)
        except Exception:
            pass  # the saved audit entry stays authoritative; a mirror must not fail the action
    return audit_id


def team_safety_admin_exempt(guild, member) -> bool:
    """Only the real Discord server owner/administrators bypass automatic team safety."""
    if not guild or not member:
        return False
    return member.id == getattr(guild, "owner_id", None) or bool(
        getattr(getattr(member, "guild_permissions", None), "administrator", False)
    )


async def _cleanup_absence_after_team_exit(guild, member) -> None:
    """After a successful team exit, end the member's absence (DB, role, nickname, lists). Never raises."""
    try:
        bot = getattr(app.state, "bot", None)
        cog = bot.get_cog("AbmeldungCog") if bot is not None and hasattr(bot, "get_cog") else None
        if cog is not None:
            await cog.cleanup_member_absence(guild, member.id, member_present=True, source="Team-Kick")
    except Exception as exc:
        print(f"⚠️ Abmeldungs-Bereinigung nach Team-Kick übersprungen ({type(exc).__name__}).")


async def apply_team_kick_role_reset(guild, member, config: dict | None = None, reason: str = "Team-Kick",
                                     actor=None, actor_is_admin: bool = True) -> dict:
    """Keep only the configured citizen/ex-team roles; never remove the member from Discord.

    AUTHZ-6: when a human ``actor`` who is not an administrator triggers the reset, a target holding an
    administrator role or any role at/above the actor's own top role is refused (nothing is changed).
    Automated callers pass no actor and keep the previous behaviour."""
    if not guild or not member:
        return {"ok": False, "changed": False, "message": "Guild oder Zielmitglied nicht verfügbar."}

    cfg = config if isinstance(config, dict) else load_config()
    bot_member = getattr(guild, "me", None)
    if not bot_member:
        return {"ok": False, "changed": False, "message": "Der Bot ist auf diesem Server noch nicht verfügbar."}
    if not bot_member.guild_permissions.manage_roles:
        return {"ok": False, "changed": False, "message": "Dem Bot fehlt die Berechtigung „Rollen verwalten“."}

    keep_ids = tuple(resolve_backup_role_id(role_id, cfg) for role_id in TEAM_KICK_KEEP_ROLE_IDS)
    keep_roles = [guild.get_role(int(role_id)) for role_id in keep_ids]
    if len(set(keep_ids)) != len(keep_ids):
        return {"ok": False, "changed": False, "message": "Die beiden Rollen für den Team-Kick verweisen auf dieselbe Discord-Rolle."}
    if any(role is None for role in keep_roles):
        return {"ok": False, "changed": False, "message": "Team-Kick abgebrochen: Bürgerrolle oder Ex-Teamler-Rolle wurde auf dem Server nicht gefunden."}

    blocked_keep = [
        role for role in keep_roles
        if role.is_default() or role.managed or role.position >= bot_member.top_role.position
    ]
    if blocked_keep:
        return {
            "ok": False, "changed": False,
            "message": "Team-Kick abgebrochen: Der Bot kann die zu behaltende Rolle nicht verwalten: "
                       + ", ".join(role.name for role in blocked_keep)
                       + ". Rollen-Hierarchie prüfen.",
        }

    keep_role_ids = {role.id for role in keep_roles}
    roles_to_remove = [
        role for role in member.roles
        if not role.is_default() and role.id not in keep_role_ids
    ]
    if actor is not None and not actor_is_admin:
        actor_top = getattr(getattr(actor, "top_role", None), "position", None)
        protected = [
            role for role in roles_to_remove
            if getattr(getattr(role, "permissions", None), "administrator", False)
            or actor_top is None or role.position >= actor_top
        ]
        if protected or getattr(getattr(member, "guild_permissions", None), "administrator", False):
            return {
                "ok": False, "changed": False,
                "message": "Team-Kick abgebrochen: Das Zielmitglied besitzt Administrator- oder höhere Rollen, "
                           "die du nicht verwalten darfst. Das kann nur ein Administrator durchführen.",
            }
    blocked_roles = [
        role for role in roles_to_remove
        if role.managed or role.position >= bot_member.top_role.position
    ]
    if blocked_roles:
        return {
            "ok": False, "changed": False,
            "message": "Team-Kick abgebrochen: Der Bot kann folgende Rollen nicht entfernen: "
                       + ", ".join(role.name for role in blocked_roles)
                       + ". Rollen-Hierarchie bzw. verwaltete Rollen prüfen.",
        }

    current_role_ids = {role.id for role in member.roles if not role.is_default()}
    if current_role_ids == keep_role_ids:
        await _cleanup_absence_after_team_exit(guild, member)
        return {
            "ok": True, "changed": False,
            "message": "Es sind bereits ausschließlich Bürgerrolle und Ex-Teamler-Rolle vorhanden.",
            "removed_roles": [], "removed_role_ids": [], "keep_roles": keep_roles,
        }

    try:
        await member.edit(roles=keep_roles, reason=f"Pulse Team-Kick: {reason}"[:512])
    except discord.Forbidden:
        return {"ok": False, "changed": False, "message": "Discord verweigert die Rollenänderung. Bot-Rolle und Berechtigungen prüfen."}
    except discord.HTTPException as exc:
        return {"ok": False, "changed": False, "message": f"Discord konnte die Rollen nicht zurücksetzen ({type(exc).__name__})."}
    except Exception as exc:
        return {"ok": False, "changed": False, "message": f"Team-Kick fehlgeschlagen ({type(exc).__name__})."}

    await _cleanup_absence_after_team_exit(guild, member)
    return {
        "ok": True, "changed": True,
        "message": "Alle Rollen außer Bürgerrolle und Ex-Teamler-Rolle wurden entfernt.",
        "removed_roles": [role.name for role in roles_to_remove],
        "removed_role_ids": [role.id for role in roles_to_remove],
        "keep_roles": keep_roles,
    }


async def _apply_team_safety_team_kick(guild, member, config: dict, reason: str) -> dict:
    """Remove all roles except citizen/ex-team after an automated team-safety kick."""
    if not guild or not member:
        return {"ok": False, "changed": False, "message": "Guild oder Zielmitglied nicht verfügbar."}

    configured_ids = set()
    for raw_id in config.get("team_role_ids", []) or []:
        try:
            configured_ids.add(int(raw_id))
        except (TypeError, ValueError):
            continue
    configured_ids.add(resolve_backup_role_id(TEST_SUPPORTER_ROLE_ID, config))
    current_team_roles = [
        role for role in member.roles
        if (role.id in configured_ids or "test supporter" in role.name.casefold())
        and not role.is_default()
    ]
    if not current_team_roles:
        return {"ok": True, "changed": False, "message": "Keine konfigurierten Teamrollen mehr vorhanden."}

    result = await apply_team_kick_role_reset(
        guild, member, config, f"automatischer Team-Kick wegen Warn-Missbrauch: {reason}"
    )
    if not result.get("ok"):
        return result

    pulse_db.mark_team_safety_kicked(guild.id, member.id)
    removed_names = ", ".join(result.get("removed_roles", [])) or "Keine zusätzlichen Rollen"
    keep_roles = result.get("keep_roles", [])
    keep_names = ", ".join(role.name for role in keep_roles)
    log_audit(
        "Pulse – Teamler-Schutz", "system", "Automatischer Team-Kick wegen Warn-Missbrauch",
        f"{member.display_name} ({member.id}) · entfernte Rollen: {removed_names} · behalten: {keep_names} · Grund: {reason}",
    )
    try:
        pulse_db.record_event(
            "team_warn_abuse_team_kick", "team_member", member.id, "system",
            "Pulse – Teamler-Schutz",
            {"reason": reason, "removed_roles": result.get("removed_role_ids", []), "kept_roles": [role.id for role in keep_roles]},
        )
    except Exception as exc:
        print(f"⚠️ Teamler-Schutz: Event-Audit konnte nicht geschrieben werden: {exc}")
    try:
        await send_dm_notification(
            member,
            f"🛡️ **Pulse-Teamler-Schutz:** Deine Team- und übrigen Rollen auf **{guild.name}** wurden automatisch entfernt. "
            f"Behalten wurden ausschließlich {', '.join(role.mention for role in keep_roles)}. "
            f"Grund: {reason}. Du wurdest nicht vom Discord-Server gekickt. Bitte kläre den Vorfall mit der Führungsebene.",
        )
    except Exception as exc:
        print(f"⚠️ Teamler-Schutz: DM an Moderator {member.id} fehlgeschlagen: {exc}")

    result["message"] = f"Team-Kick ausgeführt; entfernte Rollen: {removed_names}; behalten: {keep_names}."
    return result

async def _notify_team_safety_incident(guild, actor, target, config: dict, decision: dict, source: str):
    """Audit and notify leadership about a warning-abuse lockout or automatic team-kick."""
    actor_name = getattr(actor, "display_name", str(getattr(actor, "id", "Unbekannt")))
    actor_id = str(getattr(actor, "id", ""))
    target_name = getattr(target, "display_name", str(getattr(target, "id", "unbekannt")))
    action = str(decision.get("action") or "blocked")
    reason = str(decision.get("reason") or "Verdächtige Verwarnungsaktivität")
    kicked = None
    if action == "team_kick":
        kicked = await _apply_team_safety_team_kick(guild, actor, config, reason)

    locked_until = float(decision.get("locked_until") or 0)
    try:
        if locked_until:
            until = datetime.fromtimestamp(locked_until, TZ).strftime("%d.%m.%Y %H:%M") if TZ else datetime.fromtimestamp(locked_until).strftime("%d.%m.%Y %H:%M")
        else:
            until = "bis zur Prüfung durch die Führungsebene"
    except (ValueError, OverflowError, OSError):
        until = "bis zur Prüfung durch die Führungsebene"

    if action == "team_kick":
        status_text = (
            "Automatischer Team-Kick durchgeführt. " + kicked["message"]
            if kicked and kicked.get("ok")
            else "Automatischer Team-Kick fehlgeschlagen; sofort manuell prüfen. "
                 + str((kicked or {}).get("message", "Unbekannter Fehler"))
        )
        audit_action = "Teamler-Schutz: automatischer Team-Kick" if kicked and kicked.get("ok") else "Teamler-Schutz: Team-Kick fehlgeschlagen"
        title = "🚨 Teamler-Schutz: Automatischer Team-Kick"
        color = _pulse_color("danger")
    elif action == "duplicate":
        status_text = "Doppelte Anfrage wurde blockiert; keine Verwarnung erstellt."
        audit_action = "Teamler-Schutz: doppelte Warnanfrage blockiert"
        title = "⚠️ Teamler-Schutz: Doppelte Warnanfrage"
        color = _pulse_color("warning")
    else:
        status_text = f"Warnrechte für diesen Moderator bis {until} gesperrt. Der aktuelle Warnversuch wurde blockiert."
        audit_action = "Teamler-Schutz: Verwarnungssperre aktiviert"
        title = "🛡️ Teamler-Schutz: Verdächtige Warnaktivität"
        color = _pulse_color("warning")

    try:
        log_audit(
            "Pulse – Teamler-Schutz", "system", audit_action,
            f"Moderator {actor_name} ({actor_id}) · Ziel {target_name} "
            f"({getattr(target, 'id', 'unbekannt')}) · Quelle: {source} · Muster: {reason} · {status_text}",
        )
    except Exception as exc:
        print(f"⚠️ Teamler-Schutz: Audit-Log fehlgeschlagen: {exc}")
    try:
        pulse_db.record_event(
            "team_warn_abuse_detected", "team_member", actor_id, "system",
            "Pulse – Teamler-Schutz",
            {
                "target_id": str(getattr(target, "id", "")),
                "source": source,
                "action": action,
                "reason": reason,
                "status": status_text,
                "strike_count": int(decision.get("strike_count") or 0),
                "code": decision.get("code", ""),
            },
        )
    except Exception as exc:
        print(f"⚠️ Teamler-Schutz: Sicherheitsevent konnte nicht gespeichert werden: {exc}")

    try:
        description = (
            f"**Moderator:** {getattr(actor, 'mention', actor_name)} ({actor_id})\n"
            f"**Ziel der versuchten Verwarnung:** {getattr(target, 'mention', target_name)}\n"
            f"**Quelle:** {source}\n**Erkennung:** {reason}\n**Maßnahme:** {status_text}"
        )
        await send_team_update_embed(
            guild,
            title,
            description,
            color,
            target=getattr(actor, "mention", actor_name),
            action=audit_action,
            actor="Pulse – Teamler-Schutz",
            fields=[
                ("Erkennung", reason, False),
                ("Maßnahme", status_text, False),
                ("Wiederholungen", str(decision.get("strike_count", 0)), True),
            ],
            thumbnail=getattr(getattr(actor, "display_avatar", None), "url", None),
        )
    except Exception as exc:
        print(f"⚠️ Teamler-Schutz: Discord-Alarm konnte nicht gesendet werden: {exc}")

    # Notify leadership in the Pulse inbox, independently of team-update channel delivery.
    notified = 0
    for manager in list(getattr(guild, "members", []) or []):
        if getattr(manager, "bot", False) or str(manager.id) == actor_id:
            continue
        try:
            perms, _ = compute_perms(guild, manager.id, config)
            if not (perms.get("can_promote") or perms.get("is_admin")):
                continue
            pulse_db.notify(
                manager.id,
                title,
                f"{actor_name} ({actor_id}) · {reason} · {status_text}",
                "urgent" if action == "team_kick" else "warning",
                "/warns",
                f"team-safety:{guild.id}:{actor_id}:{decision.get('incident_id') or int(locked_until)}",
                86400,
            )
            notified += 1
        except Exception as exc:
            print(f"⚠️ Teamler-Schutz: Inbox-Alarm an {manager.id} fehlgeschlagen: {exc}")

    if action != "duplicate":
        try:
            await send_dm_notification(
                actor,
                f"🛡️ **Pulse-Teamler-Schutz:** Dein manueller Warnversuch wurde blockiert. "
                f"Erkennung: {reason}. {status_text}",
            )
        except Exception as exc:
            print(f"⚠️ Teamler-Schutz: DM an {actor_id} fehlgeschlagen: {exc}")
    print(
        f"🛡️ Teamler-Schutz ausgelöst: Moderator={actor_id}, Aktion={action}, "
        f"Quelle={source}, Führungskräfte benachrichtigt={notified}, Status={status_text}"
    )


async def enforce_team_warn_protection(guild, actor, target, config: dict | None = None, source: str = "Dashboard"):
    """Gate for all human-issued warnings; returns (allowed, explanation)."""
    if team_safety_admin_exempt(guild, actor):
        return True, ""
    if not guild or not actor or not target:
        return False, "Teamler-Schutz: Moderator oder Zielmitglied ist nicht verfügbar."
    cfg = config or load_config()
    decision = pulse_db.check_team_warn_safety(guild.id, actor.id, target.id)
    if decision.get("allowed"):
        return True, ""
    await _notify_team_safety_incident(guild, actor, target, cfg, decision, source)
    if decision.get("action") == "duplicate":
        return False, "Doppelte Warnanfrage erkannt; es wurde keine zusätzliche Verwarnung erstellt."
    if decision.get("action") == "team_kick":
        return False, "🚨 Teamler-Schutz hat den Warnversuch blockiert und einen automatischen Team-Kick ausgelöst. Die Führungsebene wurde benachrichtigt."
    return False, "🛡️ Teamler-Schutz: Warnrechte wurden wegen auffälliger Aktivität für 30 Minuten gesperrt. Die Führungsebene wurde benachrichtigt."


def high_impact_warning_reason(target, entry: dict, current_count: int) -> str:
    """Describe why the next warning would cause a consequential automatic sanction."""
    consequences = []
    if current_count >= 4:
        consequences.append("Die nächste Warnung erreicht 5/5 und löst den automatischen Team-Kick aus.")
    elif current_count >= 3:
        consequences.append("Die nächste Warnung erreicht 4/5 und löst den automatischen Downrank aus.")
    if any(
        role.id == TEST_SUPPORTER_ROLE_ID or "test supporter" in role.name.casefold()
        for role in getattr(target, "roles", [])
    ):
        consequences.append("Test-Supporter: bereits eine Warnung beendet die Probezeit mit Team-Kick.")
    probation = entry.get("probation") if isinstance(entry, dict) else None
    if isinstance(probation, dict) and probation.get("active"):
        consequences.append("Aktive Probezeit: eine Warnung löst eine automatische Rückstufung aus.")
    return " ".join(consequences)


async def enforce_high_impact_warning_approval(
    guild, actor, target, entry: dict, current_count: int,
    config: dict | None = None, source: str = "Dashboard",
) -> tuple[bool, str]:
    """Require a leader to issue any warning that automatically downranks or team-kicks."""
    cfg = config or load_config()
    if team_safety_admin_exempt(guild, actor):
        return True, ""
    actor_perms, _ = compute_perms(guild, actor.id, cfg)
    if actor_perms.get("can_promote") or actor_perms.get("is_admin"):
        return True, ""

    impact_reason = high_impact_warning_reason(target, entry, current_count)
    if not impact_reason:
        return True, ""

    actor_name = getattr(actor, "display_name", str(actor.id))
    target_name = getattr(target, "display_name", str(target.id))
    message = (
        f"Manueller Warnversuch durch {actor_name} ({actor.id}) gegen {target_name} ({target.id}) "
        f"wurde blockiert. Quelle: {source}. {impact_reason}"
    )
    try:
        log_audit(
            actor_name, str(actor.id), "Hochrisiko-Verwarnung blockiert",
            message,
        )
    except Exception as exc:
        print(f"⚠️ Teamler-Schutz: Audit für Hochrisiko-Verwarnung fehlgeschlagen: {exc}")
    try:
        pulse_db.record_event(
            "team_high_impact_warning_blocked", "team_member", target.id,
            actor.id, actor_name,
            {"source": source, "reason": impact_reason, "active_warns": current_count},
        )
    except Exception as exc:
        print(f"⚠️ Teamler-Schutz: Event für Hochrisiko-Verwarnung fehlgeschlagen: {exc}")

    try:
        await send_team_update_embed(
            guild,
            "🛡️ Teamler-Schutz: Hochrisiko-Verwarnung blockiert",
            f"Die Warnung durch {getattr(actor, 'mention', actor_name)} gegen "
            f"{getattr(target, 'mention', target_name)} wurde nicht eingetragen, weil sie eine automatische "
            "Rang-/Team-Sanktion auslösen würde.",
            _pulse_color("warning"),
            actor="Pulse – Teamler-Schutz",
            target=getattr(target, "mention", target_name),
            action="Hochrisiko-Verwarnung blockiert",
            fields=[
                ("Moderator", f"{actor_name} ({actor.id})", False),
                ("Zielmitglied", f"{target_name} ({target.id})", False),
                ("Grund", impact_reason, False),
                ("Quelle", source, True),
            ],
            thumbnail=getattr(getattr(target, "display_avatar", None), "url", None),
        )
    except Exception as exc:
        print(f"⚠️ Teamler-Schutz: Leadership-Alarm konnte nicht gesendet werden: {exc}")

    notified = 0
    for manager in list(getattr(guild, "members", []) or []):
        if getattr(manager, "bot", False) or manager.id == actor.id:
            continue
        try:
            manager_perms, _ = compute_perms(guild, manager.id, cfg)
            if not (manager_perms.get("can_promote") or manager_perms.get("is_admin")):
                continue
            pulse_db.notify(
                manager.id,
                "🛡️ Hochrisiko-Verwarnung blockiert",
                message,
                "warning",
                "/warns",
                f"team-high-impact-warn:{guild.id}:{actor.id}:{target.id}:{now_de().strftime('%Y%m%d%H%M')}",
                86400,
            )
            notified += 1
        except Exception as exc:
            print(f"⚠️ Teamler-Schutz: Warnprüfung konnte nicht an {manager.id} gemeldet werden: {exc}")
    print(
        f"🛡️ Hochrisiko-Verwarnung blockiert: Moderator={actor.id}, Ziel={target.id}, "
        f"Führungskräfte benachrichtigt={notified}, Grund={impact_reason}"
    )
    return False, (
        "🛡️ Diese Verwarnung würde automatisch einen Downrank, Team-Kick oder eine Probezeit-Sanktion auslösen. "
        "Das darf nur die Führungsebene mit Teamaktionsrechten durchführen; die Warnung wurde nicht gespeichert."
    )


async def enforce_team_safety_action_lockout(guild, actor, target, action_name: str, config: dict | None = None, source: str = "Dashboard") -> tuple[bool, str]:
    """Prevent a moderator in warning lockout from bypassing it with another power action."""
    if team_safety_admin_exempt(guild, actor):
        return True, ""
    if not guild or not actor:
        return False, "Teamler-Schutz: Moderator ist nicht verfügbar."
    decision = pulse_db.check_team_safety_action(guild.id, actor.id, action_name)
    if decision.get("allowed"):
        return True, ""
    cfg = config or load_config()
    await _notify_team_safety_incident(guild, actor, target or actor, cfg, decision, source)
    if decision.get("action") == "team_kick":
        return False, "🚨 Teamler-Schutz: Die Aktion wurde blockiert und wegen wiederholter Verstöße ein automatischer Team-Kick ausgelöst."
    return False, "🛡️ Teamler-Schutz: Während der aktiven Warnsperre sind Teamaktionen gesperrt. Die Führungsebene wurde benachrichtigt."


def get_loas() -> dict:
    """Alle Abmeldungen als {user_id_str: {...,'active': bool}} – abgelaufene bleiben sichtbar, zählen aber nicht mehr."""
    result = {}
    if not os.path.exists(DB_ABMELDUNGEN):
        return result
    try:
        with pulse_sqlite.connect_readonly(DB_ABMELDUNGEN, timeout=5.0) as conn:
            rows = conn.execute("SELECT user_id, user_name, grund, von, bis FROM abmeldungen").fetchall()
    except sqlite3.OperationalError as exc:
        # Optional legacy storage can be missing or contain an empty, uninitialized
        # file. Read access must never create it; both states mean no known LOAs.
        message = str(exc).casefold()
        if "no such table: abmeldungen" in message or "unable to open database file" in message:
            return result
        raise
    today = now_de().date()
    for uid, name, grund, von, bis in rows:
        active = True
        try:
            active = datetime.strptime(str(bis), "%Y-%m-%d").date() >= today
        except Exception:
            pass
        result[str(uid)] = {"user_id": uid, "name": name, "grund": grund, "von": von, "bis": bis, "active": active}
    return result

# =============================================================
# SICHERHEIT: ESCAPING, SIGNIERTE SESSIONS, RECHTE
# =============================================================
def esc(value) -> str:
    """HTML-Escaping für ALLE nutzergenerierten Inhalte (verhindert XSS)."""
    return html.escape("" if value is None else str(value), quote=True)


def discord_text(value) -> str:
    """User text for Discord messages/embeds: no markdown links/formatting, no mentions."""
    return discord.utils.escape_mentions(discord.utils.escape_markdown("" if value is None else str(value)))


def safe_url(url: str) -> str:
    url = (url or "").strip()
    return url if urlparse(url).scheme in ("http", "https") else ""


SESSION_SECRET_PLACEHOLDERS = {"change-me-to-a-long-random-secret", "change-me", "changeme", "secret"}
SESSION_SECRET_MIN_LENGTH = 32
# Shown in the system status and /pulse-diagnose; empty when everything is fine.
SESSION_SECRET_PROBLEM = ""


def _load_secret() -> bytes:
    """K-03: never sign sessions with the .env.example placeholder or a short secret.

    Decision E-10: fall back to the generated .session_secret and warn loudly (everybody is
    logged out once) instead of refusing to start.
    """
    global SESSION_SECRET_PROBLEM
    SESSION_SECRET_PROBLEM = ""
    env = (os.getenv("SESSION_SECRET") or "").strip()
    if env:
        # Only fixed texts are logged; nothing derived from the configured value reaches the log.
        min_length = int(SESSION_SECRET_MIN_LENGTH)
        if env.lower() in SESSION_SECRET_PLACEHOLDERS:
            notice = ("SESSION_SECRET ist noch der Platzhalter aus .env.example – "
                      "Pulse nutzt stattdessen die erzeugte .session_secret. Bitte ein eigenes langes Secret setzen.")
        elif len(env) < min_length:
            notice = (f"SESSION_SECRET ist zu kurz (< {min_length} Zeichen) – "
                      "Pulse nutzt stattdessen die erzeugte .session_secret.")
        else:
            return env.encode()
        print(f"🔴 SICHERHEIT: {notice}")
        SESSION_SECRET_PROBLEM = notice
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "rb") as f:
            key = f.read().strip()
            if key:
                return key
    key = secrets.token_hex(32).encode()
    # AUTH-7: create the file exclusively and with owner-only permissions from the start (no window with the
    # default umask, no overwriting a file another process created in the meantime).
    try:
        descriptor = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with open(SECRET_FILE, "rb") as f:
            existing = f.read().strip()
        if existing:
            return existing
        raise
    with os.fdopen(descriptor, "wb") as f:
        f.write(key)
    try:
        os.chmod(SECRET_FILE, 0o600)  # keeps the mode on platforms where the creation mode is ignored
    except Exception:
        pass
    return key


SESSION_KEY: bytes | None = None


def _session_key() -> bytes:
    """A-04: the signing key is loaded (and .session_secret created) on first use, not on import."""
    global SESSION_KEY
    if SESSION_KEY is None:
        SESSION_KEY = _load_secret()
    return SESSION_KEY


TOKEN_DASHBOARD = "dashboard"
TOKEN_APPLY = "apply"
_TOKEN_EXPIRY_KEY = {TOKEN_DASHBOARD: "exp", TOKEN_APPLY: "apply_exp"}


def sign_payload(data: dict, typ: str | None = None) -> str:
    if typ:
        # K-03: typed tokens with issue time; a token of one kind is never accepted as the other.
        data = {**data, "typ": typ, "iat": int(time.time())}
    raw = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(_session_key(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def verify_payload(token: str):
    try:
        raw, sig = token.rsplit(".", 1)
        expected = hmac.new(_session_key(), raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode())
    except Exception:
        return None


def verify_session_token(token: str | None, typ: str) -> dict | None:
    """Valid, unexpired token of exactly this kind. Untyped (pre-K-03) tokens are accepted by
    their former expiry field until they run out, so nobody is logged out by the update."""
    data = verify_payload(token) if token else None
    if not isinstance(data, dict) or not data.get("id"):
        return None
    expiry_key = _TOKEN_EXPIRY_KEY[typ]
    token_type = data.get("typ")
    if token_type is not None and token_type != typ:
        return None
    if token_type is None:
        other_key = next(k for t, k in _TOKEN_EXPIRY_KEY.items() if t != typ)
        if expiry_key not in data or other_key in data:
            return None
    if float(data.get(expiry_key, 0) or 0) < time.time():
        return None
    return data


# K-01 / E-12: application sessions last 3 hours and are renewed while the applicant is active.
APPLY_SESSION_SECONDS = 3 * 3600
APPLY_SESSION_MAX_SECONDS = 12 * 3600  # absolute limit after the Discord login, renewals never go beyond it
APPLY_RETURN_COOKIE = "apply_return_career"


def set_apply_session_cookie(response, request: Request, applicant: dict) -> None:
    now = int(time.time())
    session = {key: applicant.get(key) for key in ("id", "username", "global_name")}
    session["login_at"] = int(applicant.get("login_at") or now)
    session["apply_exp"] = min(now + APPLY_SESSION_SECONDS, session["login_at"] + APPLY_SESSION_MAX_SECONDS)
    response.set_cookie("apply_session", sign_payload(session, typ=TOKEN_APPLY), httponly=True, samesite="lax",
                        secure=oauth_cookie_secure(request), max_age=max(0, session["apply_exp"] - now))


def known_career(value) -> str:
    """The configured career key equal to *value*, or "" – never the request value itself."""
    return next((key for key in APPLICATION_CAREERS if key == value), "")


def apply_return_path(request: Request) -> str:
    """Where to land after the Discord login: back on the chosen career form if it was remembered."""
    career = known_career(str(request.cookies.get(APPLY_RETURN_COOKIE) or ""))
    return f"/apply?career={quote(career)}" if career else "/apply"


def get_current_user(user_session: str = Cookie(None)) -> dict:
    """Vorher konnte JEDER den Cookie selbst schreiben (= Login als beliebiger User). Jetzt signiert."""
    data = dashboard_session(user_session)
    if not data:
        raise HTTPException(status_code=303, headers={"Location": "/"})
    return data


def dashboard_session(user_session) -> dict | None:
    """Signed, unexpired dashboard token whose server-side session is still alive (U-14).
    "/" and every protected page must use the same check, otherwise they redirect to each other forever."""
    data = verify_session_token(user_session, TOKEN_DASHBOARD) if user_session else None
    if not data:
        return None
    try:
        import pulse_sessions  # revoked, idle or signed out everywhere → log in again
        valid = pulse_sessions.is_valid(data)
    except Exception as exc:  # AUTH-3: fail closed – a session that cannot be checked is not accepted (like the login)
        print(f"⚠️ Sitzungsprüfung nicht möglich: {type(exc).__name__}")
        valid = False
    return data if valid else None


def compute_perms(guild, user_id, config):
    """A-03: one permission model (pulse_permissions) – team roles only, inheritance in one place,
    extra Pulse permissions included. Contract unchanged: (perms dict incl. is_admin, member)."""
    import pulse_permissions
    perms, member = pulse_permissions.compute(guild, user_id, config)
    perms["can_manage_team_warnings"] = can_manage_team_warnings(guild, member, config.get("team_role_ids", []))
    return perms, member


def _set_request_actor(user: dict) -> None:
    try:
        import pulse_settings_history
        pulse_settings_history.set_actor(user.get("id"), user.get("global_name") or user.get("username") or "")
    except Exception:
        pass


def auth(request: Request, user_session, perm="can_view_dashboard", admin=False):
    """Zentrale Prüfung: Login gültig? Noch auf dem Server? Hat die Rolle das Recht?"""
    user = get_current_user(user_session)
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    if not guild:
        raise HTTPException(status_code=503, detail="Der Bot ist noch nicht bereit. Bitte in ein paar Sekunden neu laden.")
    import pulse_testmode  # Audit #168: im Testmodus arbeitet das Dashboard nur mit dem Test-Guild
    if not pulse_testmode.guild_allowed(guild.id):
        raise HTTPException(status_code=503, detail="Testmodus: Dieser Server ist nicht der konfigurierte Test-Guild.")
    config = load_config()
    perms, member = compute_perms(guild, user["id"], config)
    if not (perms.get("can_view_dashboard") or perms.get("is_admin")):
        # U-14: left the team (team kick, roles removed, left the server) → sessions end. Only revoke when
        # the member list is reliable, so an incomplete cache after a restart does not log everyone out.
        if member is not None or getattr(guild, "chunked", False):
            try:
                import pulse_sessions
                pulse_sessions.revoke_all(user["id"], reason="nicht mehr im Team")
            except Exception as exc:  # P-10: a session that stays valid after leaving the team must be visible
                pulse_logging.report_error("sessions", f"Sitzungen von {user['id']} nach Teamaustritt nicht widerrufen: {type(exc).__name__}")
        raise HTTPException(status_code=403, detail="Du bist nicht (mehr) im Team oder deine Rolle darf das Dashboard nicht sehen.")
    if admin and not perms["is_admin"]:
        raise HTTPException(status_code=403, detail="Dieser Bereich ist nur für Administratoren.")
    if perm and not perms.get(perm):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
    import pulse_flags
    if pulse_flags.blocks_write(request.method, request.url.path, perms.get("is_admin")):  # F-04
        raise HTTPException(status_code=503, detail="Wartungsmodus: Änderungen sind gerade gesperrt. Ansehen geht weiterhin.")
    _set_request_actor(user)  # F-03: who changes settings during this request
    probe = _AUDIT_PROBE.get()
    if isinstance(probe, dict) and perms.get("is_admin"):
        probe["user"] = user  # #165
    return SimpleNamespace(
        user=user, guild=guild, guild_name=guild.name, perms=perms, config=config, member=member,
        msg=request.query_params.get("msg", ""), msg_ok=request.query_params.get("t", "ok") != "err",
    )


def local_path(url: str, fallback: str = "/") -> str:
    """*url* if it is a path on this site, otherwise *fallback* (no //host, no scheme, no backslash tricks)."""
    url = str(url or "")
    parsed = urlparse(url.replace("\\", "/"))
    if parsed.scheme or parsed.netloc or not url.startswith("/") or url.startswith(("//", "/\\")):
        return fallback
    # Rebuilt behind a fixed "/" so the request value never forms the start of the target (CodeQL #113).
    return "/" + url[1:]


def back(url: str, msg: str = None, ok: bool = True):
    url = local_path(url)
    if msg:
        # The message must come before an anchor (#logs), otherwise the browser treats it as part of the anchor.
        base, sep, anchor = url.partition("#")
        # Joined with "+" (not an f-string) so CodeQL sees the message only ever follows the local path (#113).
        url = base + ("&" if "?" in base else "?") + "msg=" + quote(msg) + "&t=" + ("ok" if ok else "err") + sep + anchor
    return RedirectResponse(url=url, status_code=303)


_rate_hits = {}
# Fixed-size overflow counters used only when the per-source table is full.
_rate_overflow_hits: dict[str, list[float]] = {}
_rate_lock = threading.Lock()
_RATE_LIMIT_MAX_KEYS = 5000
_RATE_LIMIT_SWEEP_INTERVAL = 30
_rate_last_full_sweep = 0.0


def rate_limited(key: str, limit: int = 3, window: int = 3600) -> bool:
    """Bounded rate limiter that never evicts an active client's bucket.

    Unknown sources share one small overflow bucket per action when the source table is full.
    Source rotation therefore cannot reset another client's active login/callback limit.
    """
    now = time.time()
    window = max(1, int(window))
    limit = max(1, int(limit))
    global _rate_last_full_sweep
    with _rate_lock:
        if key not in _rate_hits and len(_rate_hits) >= _RATE_LIMIT_MAX_KEYS:
            if now - _rate_last_full_sweep >= _RATE_LIMIT_SWEEP_INTERVAL:
                cutoff = now - max(window, 3600)
                stale = [k for k, values in _rate_hits.items() if not any(ts >= cutoff for ts in values)]
                for stale_key in stale:
                    _rate_hits.pop(stale_key, None)
                _rate_last_full_sweep = now
        if key not in _rate_hits and len(_rate_hits) >= _RATE_LIMIT_MAX_KEYS:
            action = str(key).partition(":")[0] or "__global__"
            hits = [t for t in _rate_overflow_hits.get(action, []) if now - t < window]
            if hits and len(hits) >= limit:
                _rate_overflow_hits[action] = hits
                return True
            hits.append(now)
            _rate_overflow_hits[action] = hits
            return False
        hits = [t for t in _rate_hits.get(key, []) if now - t < window]
        if hits and len(hits) >= limit:
            _rate_hits[key] = hits
            return True
        hits.append(now)
        _rate_hits[key] = hits
        return False

def request_rate_key(request: Request, action: str) -> str:
    """Use the connection peer, not an untrusted forwarding header, for abuse limits."""
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) or "unknown"
    return f"{action}:{_rate_limit_source(host)}"


def _rate_limit_source(host: str) -> str:
    """AUTH-4: one limit bucket per IPv4 address but per /64 for IPv6 (a single customer owns a whole /64 and could
    otherwise rotate through billions of addresses); IPv4-mapped IPv6 addresses count as the IPv4 address."""
    try:
        address = ipaddress.ip_address(str(host))
    except ValueError:
        return str(host)
    if address.version == 6:
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def rate_limit_response(message: str) -> HTMLResponse:
    return HTMLResponse(
        simple_page("⏳", "Bitte kurz warten", message),
        status_code=429,
        headers={"Retry-After": "60"},
    )

# =============================================================
# SCHICHT-HELFER (Pausen werden jetzt wirklich nicht mitgezählt)
# =============================================================
def shift_elapsed(shift: dict) -> int:
    total = int(shift.get("accumulated_seconds", 0))
    if shift.get("status") == "online":
        seg = shift.get("segment_start_iso") or shift.get("started_at_iso")
        try:
            total += max(0, int((now_de() - parse_dt(seg)).total_seconds()))
        except Exception:
            pass
    return total


def load_shifts_raw() -> dict:
    """shifts.json exactly as stored (no auto-close)."""
    db = load_json(SHIFTS_FILE, {})
    if not isinstance(db, dict):
        db = {}
    db.setdefault("active_shifts", {})
    db.setdefault("history", [])
    return db


def load_shifts() -> dict:
    """Lädt Schichten; vergessene Schichten (> 24 h) erscheinen bereits als beendet.

    P-05: this is a pure read – the auto-close is only *shown* here and persisted by
    process_overdue_shifts (housekeeping) or by the next regular save of shifts.json.
    """
    db = load_json(SHIFTS_FILE, {})
    db.setdefault("active_shifts", {})
    db.setdefault("history", [])
    changed = False
    now = now_de()
    limit = AUTO_SHIFT_END_SECONDS
    for uid, s in list(db["active_shifts"].items()):
        age = shift_age_seconds(s.get("started_at_iso"), now)
        if age is None or age < limit:
            continue
        started_at = str(s.get("started_at_iso") or "")
        event_id = shift_event_id(uid, started_at)
        db["history"].append({
            "mod_id": str(uid),
            "date": s.get("date"),
            "started_at_iso": started_at,
            "ended_at_iso": now.isoformat(timespec="seconds"),
            # E-2: 0 h until a leader confirms the real duration; the measured time stays visible for that check.
            "duration_seconds": 0,
            "duration_confirmed": False,
            "measured_seconds": min(shift_elapsed(s), limit),
            "auto_closed": True,
            "shift_event_id": event_id,
            "auto_warn_status": "pending",
            "auto_warn_reason": "Schicht nicht innerhalb von 24 Stunden beendet.",
        })
        del db["active_shifts"][uid]
        changed = True
    return db


def load_member_shifts(mod_id) -> dict:
    """Optimized personal view; preserve load_json's blocked-file fallback on read errors."""
    import sys
    import pulse_shift_history
    try:
        return pulse_shift_history.load_member_view(sys.modules[__name__], str(mod_id))
    except Exception as exc:
        _mark_corrupt(SHIFTS_FILE, exc)
        return {"active_shifts": {}, "history": []}


def load_shift_state() -> dict:
    """Optimized active-shift state; preserve load_json's blocked-file fallback on read errors."""
    import sys
    import pulse_shift_history
    try:
        return pulse_shift_history.load_shift_state(sys.modules[__name__])
    except Exception as exc:
        _mark_corrupt(SHIFTS_FILE, exc)
        return {"active_shifts": {}, "history": []}


async def process_overdue_shifts(guild, config: dict | None = None) -> dict:
    """Schließt Schichten nach 24h, vergibt genau eine Team-Warn und protokolliert sie."""
    report = {"checked": 0, "closed": 0, "warned": 0, "failed": 0, "max_warns": 0}
    if not guild:
        report["failed"] = 1
        return report

    cfg = config or load_config()
    shifts_db = load_shifts_raw()  # P-05: the stored state – this function persists the auto-close itself
    active = shifts_db.setdefault("active_shifts", {})
    history = shifts_db.setdefault("history", [])
    now = now_de()
    changed = False

    # The specified 24-hour threshold must not be delayed by an older env setting.
    for uid, shift in list(active.items()):
        report["checked"] += 1
        age = shift_age_seconds(shift.get("started_at_iso"), now)
        if age is None or age < AUTO_SHIFT_END_SECONDS:
            continue
        started_at = str(shift.get("started_at_iso") or "")
        history.append({
            "mod_id": str(uid),
            "date": shift.get("date"),
            "started_at_iso": started_at,
            "ended_at_iso": now.isoformat(timespec="seconds"),
            "duration_seconds": 0,  # E-2: credited only after a leader confirms the real duration
            "duration_confirmed": False,
            "measured_seconds": min(shift_elapsed(shift), AUTO_SHIFT_END_SECONDS),
            "auto_closed": True,
            "shift_event_id": shift_event_id(uid, started_at),
            "auto_warn_status": "pending",
            "auto_warn_reason": "Schicht nicht innerhalb von 24 Stunden beendet.",
        })
        del active[uid]
        report["closed"] += 1
        changed = True

    if changed:
        save_json(SHIFTS_FILE, shifts_db)

    team_role_ids = {int(value) for value in cfg.get("team_role_ids", []) if str(value).isdigit()}
    system_actor = "Pulse – automatische Schichtkontrolle"
    reason = "Automatische Team-Verwarnung: Die Schicht wurde nicht innerhalb von 24 Stunden beendet."

    # Process pending events oldest first. source_shift_id makes retries idempotent.
    for shift_event in history[-1000:]:
        if not shift_event.get("auto_closed") or shift_event.get("auto_warn_status") != "pending":
            continue
        event_id = str(shift_event.get("shift_event_id") or "")
        uid = str(shift_event.get("mod_id") or "")
        if not event_id or not uid.isdigit():
            shift_event["auto_warn_status"] = "invalid_shift_record"
            commit_shift_event(shift_event)
            continue

        member = guild.get_member(int(uid))
        if member is None:
            try:
                member = await guild.fetch_member(int(uid))
            except discord.NotFound:
                shift_event["auto_warn_status"] = "member_left_server"
                commit_shift_event(shift_event)
                continue
            except Exception as exc:
                shift_event["auto_warn_error"] = str(exc)[:400]
                report["failed"] += 1
                continue
        if member.bot:
            shift_event["auto_warn_status"] = "not_team_member"
            commit_shift_event(shift_event)
            continue

        # K-04: fresh entry per member (earlier awaits may have taken long) and merge-on-save.
        team_db = load_json(DATA_FILE, {})
        entry = user_entry(team_db if isinstance(team_db, dict) else {}, uid)
        tracker = track_member_entry(uid, entry)
        warnings = entry.setdefault("warns_list", [])
        existing_warn = next(
            (item for item in warnings if isinstance(item, dict) and item.get("source_shift_id") == event_id),
            None,
        )
        if existing_warn:
            count = len(active_warns(entry))
            await sync_warn_roles(guild, member, count, cfg)
            await apply_warn_consequences(guild, member, existing_warn, count, cfg, entry=entry)
            tracker.commit()
            shift_event["auto_warn_status"] = "issued"
            shift_event["auto_warn_issued_at"] = shift_event.get("auto_warn_issued_at") or now.isoformat(timespec="seconds")
            shift_event.pop("auto_warn_error", None)
            commit_shift_event(shift_event)
            continue

        if team_role_ids and not any(role.id in team_role_ids for role in member.roles):
            shift_event["auto_warn_status"] = "not_team_member"
            commit_shift_event(shift_event)
            continue

        count_before = len(active_warns(entry))
        if count_before >= 5:
            shift_event["auto_warn_status"] = "max_warns"
            shift_event["auto_warn_processed_at"] = now.isoformat(timespec="seconds")
            commit_shift_event(shift_event)
            report["max_warns"] += 1
            log_audit(system_actor, "system", "Automatische Schicht-Warnung nicht möglich",
                      f"{member.display_name} ({member.id}) hat bereits 5 aktive Warnungen; Schicht {event_id}.")
            for manager in guild.members:
                if manager.bot or manager.id == member.id:
                    continue
                manager_perms, _ = compute_perms(guild, manager.id, cfg)
                if manager_perms.get("can_promote") or manager_perms.get("is_admin"):
                    try:
                        pulse_db.notify(
                            manager.id, "🚨 Schichtkontrolle: bereits 5 Warnungen",
                            f"{member.display_name} hat eine Schicht über 24 Stunden offen gelassen, aber bereits 5 aktive Warnungen.",
                            "warning", "/warns", f"shift-warn-max:{event_id}", 86400,
                        )
                    except Exception:
                        pass
            continue

        warning_id = "warn_" + uuid.uuid5(uuid.NAMESPACE_URL, f"pulse-shift-warning:{event_id}").hex[:10]
        warn = {
            "id": warning_id,
            "reason": reason,
            "proof": "",
            "by": system_actor,
            "date": now.strftime("%d.%m.%Y %H:%M"),
            "active": True,
            "revoked_at": None,
            "revoked_by": None,
            "revoked_reason": "",
            "duration_days": DEFAULT_AUTO_WARN_DURATION_DAYS,
            "expires_at": (now + timedelta(days=DEFAULT_AUTO_WARN_DURATION_DAYS)).isoformat(timespec="seconds"),
            "source": "auto_shift_timeout",
            "source_shift_id": event_id,
            "shift_started_at_iso": shift_event.get("started_at_iso"),
        }
        warnings.append(warn)
        count = len(active_warns(entry))
        tracker.commit()

        try:
            role_report = await sync_warn_roles(guild, member, count, cfg)
            consequence_report = await apply_warn_consequences(guild, member, warn, count, cfg, entry=entry)
            tracker.commit()
            try:
                await send_team_update_embed(
                    guild,
                    "⚠️ Team-Warn: Schicht nicht beendet",
                    f"{member.mention} hat die Schicht nach 24 Stunden nicht beendet.",
                    _pulse_color("warning"),
                    target=f"{member.mention}\nWarn-Stufe: {count}/5",
                    action=f"Automatische Schicht-Verwarnung {count}/5",
                    actor=system_actor,
                    fields=[
                        ("Grund", reason, False),
                        ("Schicht gestartet", str(shift_event.get("started_at_iso") or "Unbekannt"), False),
                        ("Automatische Beendigung", str(shift_event.get("ended_at_iso") or "Unbekannt"), False),
                        ("Warnrolle", role_report.get("message", "Nicht verfügbar"), False),
                    ],
                    thumbnail=member.display_avatar.url,
                )
            except Exception as exc:
                print(f"Team-Update für automatische Schicht-Warnung fehlgeschlagen: {exc}")
            try:
                consequence_text = "; ".join(str(value) for value in consequence_report.get("actions", []) if value)
                suffix = f"\n**Automatische Folge:** {consequence_text}" if consequence_text else ""
                await send_dm_notification(
                    member,
                    f"⚠️ Du hast eine Team-Warnung erhalten ({count}/5).\n**Grund:** {reason}\n**Von:** {system_actor}{suffix}",
                )
            except Exception as exc:
                print(f"DM für automatische Schicht-Warnung fehlgeschlagen: {exc}")
            log_audit(system_actor, "system", "Automatische Schicht-Verwarnung",
                      f"{member.display_name} ({member.id}) ({count}/5): {reason}")
            shift_event["auto_warn_status"] = "issued"
            shift_event["auto_warn_issued_at"] = now.isoformat(timespec="seconds")
            shift_event.pop("auto_warn_error", None)
            report["warned"] += 1
            commit_shift_event(shift_event)
        except Exception as exc:
            shift_event["auto_warn_error"] = str(exc)[:400]
            report["failed"] += 1
            commit_shift_event(shift_event)
            print(f"Automatische Schicht-Warnung fehlgeschlagen für {uid}: {exc}")

    return report


_ISO_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WEEKLY_MEMO: dict = {"key": None, "history": None, "totals": {}}
_WEEKLY_MEMO_SECONDS = 5


def _in_week(date_value, start: datetime, start_text: str) -> bool:
    if isinstance(date_value, str) and _ISO_DAY_RE.match(date_value):
        # fast C parser; rejects invalid days like strptime does (e.g. 2026-13-01)
        return date_cls.fromisoformat(date_value) >= start.date()
    return datetime.strptime(date_value, "%Y-%m-%d") >= start  # legacy values (e.g. 2026-1-5); errors propagate


def weekly_history_totals(history) -> dict:
    """P-03: seconds of finished shifts in the current week, per member – one pass over the history.
    Kept for a few seconds for the same list object, so pages looping over all members pay once."""
    start = week_start()
    key = (id(history), len(history), start, int(time.time() // _WEEKLY_MEMO_SECONDS))
    if _WEEKLY_MEMO["history"] is history and _WEEKLY_MEMO["key"] == key:
        return _WEEKLY_MEMO["totals"]
    start_text = start.strftime("%Y-%m-%d")
    totals: dict = {}
    for entry in history:
        try:
            if _in_week(entry.get("date"), start, start_text):
                uid = str(entry.get("mod_id"))
                totals[uid] = totals.get(uid, 0) + credited_shift_seconds(entry)  # E-2
        except Exception:
            pass
    _WEEKLY_MEMO.update(key=key, history=history, totals=totals)
    return totals


def calculate_weekly_seconds(mod_id, history, active_shifts=None) -> int:
    ws = week_start()
    total = weekly_history_totals(history).get(str(mod_id), 0)
    s = (active_shifts or {}).get(str(mod_id))
    if s:
        try:
            if datetime.strptime(s.get("date"), "%Y-%m-%d") >= ws:
                total += shift_elapsed(s)
        except Exception:
            pass
    return total


def calculate_weekly_hours(mod_id_str, shifts_history):  # Kompatibilität zum alten Code
    return f"{calculate_weekly_seconds(mod_id_str, shifts_history) / 3600:.1f}h"

def shift_weekly_totals(mod_ids=None) -> dict:
    """Historical current-week totals; report indexed-store errors through the existing data status."""
    import sys
    import pulse_shift_history
    try:
        return pulse_shift_history.weekly_totals(sys.modules[__name__], mod_ids=mod_ids)
    except Exception as exc:
        _mark_corrupt(SHIFTS_FILE, exc)
        return {}


def shift_total_credited_seconds(mod_id=None) -> int:
    """All-time credited seconds, optionally for one member, without materializing SQLite history."""
    import sys
    import pulse_shift_history
    try:
        return pulse_shift_history.total_credited_seconds(sys.modules[__name__], mod_id=mod_id)
    except Exception as exc:
        _mark_corrupt(SHIFTS_FILE, exc)
        return 0


def shift_total_credited_seconds_by_member(mod_ids=None) -> dict[str, int]:
    """All-time credited seconds grouped by member without materializing SQLite history."""
    import sys
    import pulse_shift_history
    try:
        return pulse_shift_history.total_credited_seconds_by_member(sys.modules[__name__], mod_ids=mod_ids)
    except Exception as exc:
        _mark_corrupt(SHIFTS_FILE, exc)
        return {}


def shift_range_totals(mod_ids, date_from: str, date_to: str) -> dict:
    """Credited seconds per member for a calendar-date range."""
    import sys
    import pulse_shift_history
    try:
        return pulse_shift_history.range_totals(sys.modules[__name__], mod_ids, date_from, date_to)
    except Exception as exc:
        _mark_corrupt(SHIFTS_FILE, exc)
        return {}


def shift_latest_dates(mod_ids) -> dict:
    """Latest recorded shift date per member."""
    import sys
    import pulse_shift_history
    try:
        return pulse_shift_history.latest_shift_dates(sys.modules[__name__], mod_ids)
    except Exception as exc:
        _mark_corrupt(SHIFTS_FILE, exc)
        return {}


def calculate_weekly_seconds_from_totals(mod_id, totals: dict, active_shifts=None) -> int:
    """Apply the existing active-shift rule to already aggregated historical totals."""
    start, total = week_start(), totals.get(str(mod_id), 0)
    active = (active_shifts or {}).get(str(mod_id))
    if active:
        try:
            if datetime.strptime(active.get("date"), "%Y-%m-%d") >= start:
                total += shift_elapsed(active)
        except Exception:
            pass
    return total


def calculate_weekly_seconds_for_member(mod_id, active_shifts=None) -> int:
    """Store-backed equivalent of calculate_weekly_seconds() for one member."""
    active = active_shifts if active_shifts is not None else load_shift_state().get("active_shifts", {})
    totals = shift_weekly_totals([str(mod_id)])
    return calculate_weekly_seconds_from_totals(mod_id, totals, active)


# =============================================================
# DISCORD-HELFER
# =============================================================
async def send_dm_notification(user_or_member, message: str, embed: discord.Embed = None):
    if not user_or_member:
        return False
    try:
        if embed:
            await send_styled(user_or_member, content=message, embed=embed)
        else:
            await user_or_member.send(content=message)
        return True
    except Exception as e:
        print(f"DM konnte nicht gesendet werden an {user_or_member}: {e}")
        return False


async def send_team_update_embed(guild, title, description, color=None, *, fields=None,
                                 actor=None, target=None, action=None, thumbnail=None):
    """Einheitliches professionelles Embed für alle Team-Updates."""
    if not guild:
        return None
    color = color or _pulse_color("info")
    channel = guild.get_channel(TEAM_UPDATE_CHANNEL_ID)
    if not channel:
        print(f"Team-Updates-Kanal nicht gefunden oder Bot hat keinen Zugriff: {TEAM_UPDATE_CHANNEL_ID}")
        return None
    try:
        embed = discord.Embed(
            title=title[:256],
            description=description[:4096],
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        if target:
            embed.add_field(name="👤 Betroffen", value=str(target)[:1024], inline=True)
        if action:
            embed.add_field(name="📌 Vorgang", value=str(action)[:1024], inline=True)
        if actor:
            embed.add_field(name="🛡️ Bearbeitet von", value=str(actor)[:1024], inline=True)
        for field in (fields or []):
            if isinstance(field, (tuple, list)) and len(field) >= 2:
                embed.add_field(
                    name=str(field[0])[:256],
                    value=str(field[1])[:1024] or "—",
                    inline=bool(field[2]) if len(field) > 2 else False,
                )
        if thumbnail:
            try:
                embed.set_thumbnail(url=thumbnail)
            except Exception:
                pass
        if guild.icon:
            try:
                embed.set_author(name=guild.name, icon_url=guild.icon.url)
            except Exception:
                pass
        embed.set_footer(text="Pulse TeamOS • Team-Updates")
        return await send_styled(channel, embed=embed)
    except Exception as e:
        print(f"Fehler beim Senden des Team-Updates in Discord: {e}")
        return None


def resolve_backup_role_id(role_id: int, config: dict | None = None) -> int:
    """Resolve a built-in role ID after Discord recreated that role with a new snowflake."""
    cfg = config if isinstance(config, dict) else load_config()
    aliases = cfg.get("backup_role_id_aliases", {}) if isinstance(cfg, dict) else {}
    current = int(role_id)
    seen = set()
    for _ in range(12):
        if current in seen:
            break
        seen.add(current)
        replacement = aliases.get(str(current), aliases.get(current))
        if replacement is None:
            break
        try:
            next_id = int(replacement)
        except (TypeError, ValueError):
            break
        if next_id == current:
            break
        current = next_id
    return current


def get_warn_role_ids(config: dict | None = None) -> dict:
    """Return the canonical five warning levels, remapped after a backup restore."""
    return {
        level: resolve_backup_role_id(role_id, config)
        for level, role_id in WARN_ROLE_IDS.items()
    }

def _legacy_warning_expiry(warn: dict, current: datetime | None = None) -> tuple[datetime, int, str]:
    """Calculate a stable expiry for old active warnings that have no expires_at.

    Older warnings without a configured duration use seven calendar days (the issue
    date counts as day one), so a legacy warning dated 16.10 expires on 22.10.
    Explicit duration_days values retain their full elapsed duration.
    """
    raw_duration = warn.get("duration_days")
    duration_is_explicit = raw_duration is not None and str(raw_duration).strip() != ""
    try:
        duration_days = int(raw_duration) if duration_is_explicit else 7
        if not 1 <= duration_days <= 365:
            raise ValueError("invalid warning duration")
    except (TypeError, ValueError, OverflowError):
        duration_days = 7
        duration_is_explicit = False

    issued_at = None
    for field in ("issued_at", "issued_at_iso", "created_at", "created_at_iso", "date", "timestamp"):
        issued_at = parse_timestamp(warn.get(field))
        if issued_at is not None:
            break

    now = current or now_de()
    if issued_at is not None:
        # New/explicitly timed warnings run for the full number of elapsed days.
        # Unconfigured legacy warnings count the issue date as day 1 to preserve
        # the requested seven-calendar-day rule for already-existing records.
        days = duration_days if duration_is_explicit else max(0, duration_days - 1)
        expires_at = issued_at + timedelta(days=days)
        source = "legacy_explicit_duration" if duration_is_explicit else "legacy_default_7_calendar_days"
    else:
        # Never guess the age of an undated historical warning. Give it one
        # seven-day grace period from migration; normalize_warns persists the
        # computed timestamp so subsequent reads cannot extend that deadline.
        expires_at = now + timedelta(days=duration_days)
        source = "legacy_missing_date_grace_period"

    return expires_at, duration_days, source


def normalize_warns(entry: dict) -> bool:
    """Migriert Warns zu einem stabilen Format mit Status statt hartem Löschen."""
    raw = entry.get("warns_list", [])
    if not isinstance(raw, list):
        raw = []
    normalized = []
    changed = not isinstance(entry.get("warns_list"), list)

    for warn in raw:
        if isinstance(warn, dict):
            item = dict(warn)
        else:
            item = {"reason": str(warn), "proof": "", "by": "Altsystem", "date": "N/A"}
            changed = True
        if not item.get("id"):
            item["id"] = f"warn_{uuid.uuid4().hex[:10]}"
            changed = True
        if "active" not in item:
            item["active"] = True
            changed = True
        item.setdefault("reason", "Kein Grund")
        item.setdefault("proof", "")
        item.setdefault("by", "System")
        item.setdefault("date", "N/A")
        item.setdefault("revoked_at", None)
        item.setdefault("revoked_by", None)
        item.setdefault("revoked_reason", "")
        item.setdefault("expires_at", None)
        item.setdefault("duration_days", None)
        item.setdefault("expired_at", None)
        item.setdefault("expired_reason", "")
        if item.get("active", True) and not item.get("revoked_at") and not item.get("expires_at"):
            expiry, duration_days, migration_source = _legacy_warning_expiry(item)
            item["duration_days"] = duration_days
            item["expires_at"] = expiry.isoformat(timespec="seconds")
            item.setdefault("expiry_migrated_at", now_de().isoformat(timespec="seconds"))
            item["expiry_migration_source"] = migration_source
            changed = True
        normalized.append(item)

    if normalized != entry.get("warns_list"):
        entry["warns_list"] = normalized
        changed = True
    return changed


def _restriction_expired(restriction: dict, current: datetime | None = None) -> bool:
    try:
        return parse_dt(str(restriction.get("expires_at") or "")) <= (current or now_de())
    except (TypeError, ValueError, OverflowError):
        return False


def warning_is_expired(warn: dict, current: datetime | None = None) -> bool:
    """Whether an optional warning expiry timestamp has passed."""
    raw_expiry = warn.get("expires_at") if isinstance(warn, dict) else None
    if not raw_expiry:
        return False
    try:
        return parse_dt(str(raw_expiry)) <= (current or now_de())
    except (TypeError, ValueError, OverflowError):
        # A malformed historical value must not silently erase a warning.
        return False


def active_warns(entry: dict) -> list[dict]:
    """Count active, unexpired warnings while retaining expired/revoked history."""
    normalize_warns(entry)
    current = now_de()
    return [
        w for w in entry.get("warns_list", [])
        if isinstance(w, dict)
        and w.get("active", True)
        and not w.get("revoked_at")
        and not warning_is_expired(w, current)
    ]


def warning_role_health(guild, config: dict | None = None) -> list[dict]:
    """Prüft Existenz und Bot-Hierarchie aller fünf Warn-Rollen."""
    ids = get_warn_role_ids(config)
    me = getattr(guild, "me", None) if guild else None
    rows = []
    seen = {}
    for level in sorted(ids):
        rid = ids[level]
        role = guild.get_role(rid) if guild else None
        if rid in seen and rid:
            rows.append({"level": level, "id": rid, "role": role, "ok": False, "detail": f"Diese Rolle ist bereits als Warn {seen[rid]} konfiguriert"})
            continue
        if rid:
            seen[rid] = level
        if not role:
            rows.append({"level": level, "id": rid, "role": None, "ok": False, "detail": "Rolle nicht gefunden"})
        elif role.is_default() or role.managed:
            rows.append({"level": level, "id": rid, "role": role, "ok": False, "detail": "Rolle ist nicht verwaltbar"})
        elif me and not me.guild_permissions.manage_roles:
            rows.append({"level": level, "id": rid, "role": role, "ok": False, "detail": "Bot hat keine 'Rollen verwalten'-Berechtigung"})
        elif me and me.top_role.position <= role.position:
            rows.append({"level": level, "id": rid, "role": role, "ok": False, "detail": "Bot-Rolle steht nicht darüber"})
        else:
            rows.append({"level": level, "id": rid, "role": role, "ok": True, "detail": "Bereit"})
    return rows


async def _add_temporary_warn_role(guild, member, role_id: int, warn: dict, label: str):
    """Vergibt eine Warn-Folgerolle bis zum Ablauf der zugehörigen Warnung."""
    result = {"ok": True, "role_id": role_id, "expires_at": None, "message": ""}
    role = guild.get_role(role_id) if guild else None
    if not role:
        result.update(ok=False, message=f"{label}-Rolle {role_id} wurde nicht gefunden.")
        return result
    if role.is_default() or role.managed:
        result.update(ok=False, message=f"{label}-Rolle ist nicht verwaltbar.")
        return result
    if guild.me and (not guild.me.guild_permissions.manage_roles or role.position >= guild.me.top_role.position):
        result.update(ok=False, message=f"Bot kann die {label}-Rolle nicht verwalten.")
        return result

    # The punishment follows the warning lifetime selected in the dashboard.
    # Legacy warnings without an expiry retain the safe seven-day default.
    raw_warn_expiry = warn.get("expires_at")
    try:
        expires_at = parse_dt(str(raw_warn_expiry)).isoformat(timespec="seconds") if raw_warn_expiry else (now_de() + timedelta(days=WARN_TEMP_ROLE_DAYS)).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError):
        expires_at = (now_de() + timedelta(days=WARN_TEMP_ROLE_DAYS)).isoformat(timespec="seconds")
    try:
        if role not in member.roles:
            await member.add_roles(role, reason=f"Warn-Folge {label} · Warn {warn.get('id')}")
        consequences = warn.setdefault("consequences", {})
        temporary_roles = consequences.setdefault("temporary_roles", [])
        temporary_roles = [
            x for x in temporary_roles
            if str(x.get("role_id")) != str(role_id)
        ]
        temporary_roles.append({
            "role_id": role_id,
            "label": label,
            "expires_at": expires_at,
            "active": True,
        })
        consequences["temporary_roles"] = temporary_roles
        result["expires_at"] = expires_at
        try:
            expiry_text = datetime.fromisoformat(expires_at).strftime("%d.%m.%Y %H:%M")
        except Exception:
            expiry_text = expires_at
        result["message"] = f"{label} bis {expiry_text}"
        return result
    except discord.Forbidden:
        result.update(ok=False, message=f"Discord verweigert die {label}-Rolle.")
        return result
    except Exception as exc:
        result.update(ok=False, message=f"{label}-Rolle fehlgeschlagen: {exc}")
        return result


async def _apply_warn4_downrank(guild, member, warn: dict, team_role_ids: list[int]):
    """Downrankt bei Warn 4 genau eine Team-Rangstufe."""
    if warn.get("consequences", {}).get("downrank_applied"):
        return {"ok": True, "message": "Downrank bereits ausgeführt.", "changed": False}

    if not team_role_ids:
        return {"ok": False, "message": "Keine Team-Rollen konfiguriert.", "changed": False}

    ranked_team_role_ids = ordered_team_role_ids(guild, team_role_ids)
    target_idx = team_rank(member, ranked_team_role_ids, guild)
    if target_idx <= 0:
        return {"ok": True, "message": "Bereits auf niedrigster Team-Rangstufe.", "changed": False}

    new_role = guild.get_role(ranked_team_role_ids[target_idx - 1])
    old_roles = [r for r in member.roles if r.id in team_role_ids]
    if not new_role:
        return {"ok": False, "message": "Zielrolle für den automatischen Downrank nicht gefunden.", "changed": False}
    if guild.me and (not guild.me.guild_permissions.manage_roles or new_role.position >= guild.me.top_role.position):
        return {"ok": False, "message": "Bot kann die Zielrolle für den automatischen Downrank nicht verwalten.", "changed": False}

    actor = warn.get("by") or "Warn-System"
    reason = warn.get("reason") or "Warn 4"
    try:
        await member.add_roles(new_role, reason=f"Automatischer Downrank durch {actor}: {reason}")
        remove = [r for r in old_roles if r.id != new_role.id]
        if remove:
            await member.remove_roles(*remove, reason=f"Automatischer Downrank durch {actor}: {reason}")
        consequences = warn.setdefault("consequences", {})
        consequences["downrank_applied"] = True
        consequences["downrank_from_role_ids"] = [role.id for role in old_roles]
        consequences["downrank_to_role_id"] = new_role.id
        return {"ok": True, "message": f"Automatisch auf {new_role.name} degradiert.", "changed": True}
    except discord.Forbidden:
        return {"ok": False, "message": "Discord verweigert den automatischen Downrank.", "changed": False}
    except Exception as exc:
        return {"ok": False, "message": f"Automatischer Downrank fehlgeschlagen: {exc}", "changed": False}


async def _apply_warn5_team_kick(
    guild, member, warn: dict, team_role_ids: list[int], config: dict | None = None
):
    """Permanently remove all roles except citizen/ex-team when a Warn-5 Team-Kick fires."""
    consequences = warn.setdefault("consequences", {})
    if consequences.get("team_kick_applied"):
        return {"ok": True, "message": "Team-Kick bereits ausgeführt.", "changed": False}

    current_team_roles = [role for role in member.roles if role.id in team_role_ids]
    consequences["team_kick_applied"] = True
    if not current_team_roles:
        consequences["team_kick_applied"] = False
        return {"ok": True, "message": "Mitglied hatte bereits keine Team-Rolle mehr.", "changed": False}

    actor = warn.get("by") or "Warn-System"
    reason = warn.get("reason") or "Warn 5"
    result = await apply_team_kick_role_reset(
        guild, member, config if isinstance(config, dict) else load_config(),
        f"automatischer Team-Kick durch {actor}: {reason}",
    )
    if not result.get("ok"):
        consequences["team_kick_applied"] = False
        return {"ok": False, "message": result.get("message", "Automatischer Team-Kick fehlgeschlagen."), "changed": False}

    # Team-Kick is permanent: warning expiry must never silently re-hire the member.
    consequences["team_kick_removed_role_ids"] = [role.id for role in current_team_roles]
    consequences["team_kick_permanent"] = True
    consequences["team_kick_restore_skipped_at"] = now_de().isoformat(timespec="seconds")
    kept = ", ".join(role.name for role in result.get("keep_roles", []))
    return {
        "ok": True, "message": f"Automatischer Team-Kick ausgeführt. Alle Rollen außer {kept} wurden entfernt.",
        "changed": bool(result.get("changed")), "removed_role_ids": result.get("removed_role_ids", []),
    }


async def process_probation_warning(
    guild,
    member,
    warn: dict,
    team_role_ids: list[int],
    entry: dict | None = None,
    config: dict | None = None,
):
    """Apply Test-Supporter kick or restore the previous rank for probation warnings."""
    result = {"actions": [], "ok": True, "team_kicked": False, "probation_reverted": False}
    if not member or member.bot or not guild:
        return result

    if entry is None:
        entry = user_entry(load_json(DATA_FILE, {}), str(member.id))
        # K-04: Discord calls are awaited before saving – commit() merges only our changes into the current file,
        # so a warning or note saved by another handler in the meantime is not overwritten with this stale copy.
        tracker = track_member_entry(member.id, entry)
        owns_db = True
    else:
        tracker = None
        owns_db = False

    def persist_probation() -> None:
        if tracker is not None:
            tracker.commit()

    warn_reason = str(warn.get("reason") or "Team-Warn")
    current_config = load_config()
    test_supporter_role_id = resolve_backup_role_id(TEST_SUPPORTER_ROLE_ID, current_config)
    test_supporter_roles = [
        role for role in member.roles
        if role.id == test_supporter_role_id or "test supporter" in role.name.casefold()
    ]
    is_test_supporter = bool(test_supporter_roles)

    # The Test-Supporter role is a trial rank. Any team warning ends the trial immediately.
    if is_test_supporter:
        kick_ids = sorted(
            {int(role_id) for role_id in team_role_ids}
            | {role.id for role in test_supporter_roles}
            | {test_supporter_role_id}
        )
        kick = await _apply_warn5_team_kick(guild, member, warn, kick_ids, config=config)
        result["actions"].append(
            kick["message"] if kick.get("ok")
            else f"Test-Supporter-Team-Kick fehlgeschlagen: {kick['message']}"
        )
        result["ok"] = bool(kick.get("ok"))
        result["team_kicked"] = bool(kick.get("ok") and kick.get("changed"))
        if result["team_kicked"]:
            probation = entry.get("probation")
            if isinstance(probation, dict) and probation.get("active"):
                probation["active"] = False
                probation["status"] = "ended"
                probation["ended_at"] = now_de().isoformat(timespec="seconds")
                probation["completion_reason"] = "Team-Kick nach Verwarnung während Test-Supporter-Phase"
            consequences = warn.setdefault("consequences", {})
            consequences["team_kick_applied"] = True
            consequences["test_supporter_team_kick"] = True
            log_audit(
                "Pulse – Test-Supporter-Schutz",
                "system",
                "Automatischer Team-Kick",
                f"{member.display_name} ({member.id}) · Grund: {warn_reason}",
            )
            try:
                await send_dm_notification(
                    member,
                    f"❌ Du wurdest nach einer Team-Verwarnung während deiner Test-Supporter-Phase "
                    f"aus dem Team von **{guild.name}** entfernt. Grund: {warn_reason}",
                )
            except Exception:
                pass
            try:
                await send_team_update_embed(
                    guild,
                    "🚨 Team-Update: Test-Supporter-Team-Kick",
                    f"{member.mention} wurde nach einer Team-Verwarnung automatisch aus dem Team entfernt.",
                    _pulse_color("danger"),
                    target=member.mention,
                    action="Test-Supporter-Team-Kick",
                    actor="Pulse – Test-Supporter-Schutz",
                    fields=[("Grund", warn_reason, False), ("Auslöser", "Team-Warn während Test-Supporter-Phase", False)],
                    thumbnail=member.display_avatar.url,
                )
            except Exception as exc:
                print(f"⚠️ Team-Update zum Test-Supporter-Team-Kick fehlgeschlagen: {exc}")
        if owns_db:
            persist_probation()
        return result

    probation = entry.get("probation")
    if not isinstance(probation, dict) or not probation.get("active"):
        if owns_db:
            persist_probation()
        return result

    try:
        expires_at = parse_dt(str(probation.get("expires_at") or ""))
    except (TypeError, ValueError, OverflowError):
        result["actions"].append("Aktive Probezeit hat ein ungültiges Ablaufdatum; Rolle wurde nicht geändert")
        result["ok"] = False
        return result

    current = now_de()
    if current >= expires_at:
        probation["active"] = False
        probation["status"] = "completed"
        probation["completed_at"] = current.isoformat(timespec="seconds")
        probation["completion_reason"] = "Probezeit bereits abgelaufen"
        if owns_db:
            persist_probation()
        return result

    previous_role_id = int(probation.get("previous_role_id") or 0)
    previous_role = guild.get_role(previous_role_id) if previous_role_id else None
    team_ids = {int(role_id) for role_id in team_role_ids}
    current_team_roles = [role for role in member.roles if role.id in team_ids]
    if previous_role is None or previous_role_id not in team_ids:
        result["actions"].append("Probezeit aktiv, aber die vorherige Teamrolle fehlt – automatische Rückstufung nicht möglich")
        result["ok"] = False
        return result
    if guild.me and (
        not guild.me.guild_permissions.manage_roles
        or previous_role.managed
        or previous_role.position >= guild.me.top_role.position
        or any(
            role.position >= guild.me.top_role.position or role.managed
            for role in current_team_roles if role.id != previous_role_id
        )
    ):
        result["actions"].append("Probezeit-Rückstufung fehlgeschlagen: Bot-Rollenrechte oder Rollen-Hierarchie prüfen")
        result["ok"] = False
        return result

    try:
        if previous_role not in member.roles:
            await member.add_roles(previous_role, reason=f"Probezeit-Rückstufung nach Team-Warn: {warn_reason}")
        to_remove = [role for role in current_team_roles if role.id != previous_role.id]
        if to_remove:
            await member.remove_roles(*to_remove, reason=f"Probezeit-Rückstufung nach Team-Warn: {warn_reason}")
    except (discord.Forbidden, discord.HTTPException) as exc:
        result["actions"].append(f"Probezeit-Rückstufung fehlgeschlagen: {type(exc).__name__}")
        result["ok"] = False
        return result

    probation["active"] = False
    probation["status"] = "reverted"
    probation["ended_at"] = current.isoformat(timespec="seconds")
    probation["completion_reason"] = "Vorherige Teamrolle nach Team-Warn wiederhergestellt"
    probation["reverted_by_warn_id"] = warn.get("id")
    consequences = warn.setdefault("consequences", {})
    consequences["probation_reverted"] = True
    consequences["probation_previous_role_id"] = previous_role.id
    result["actions"].append(f"Probezeit-Rückstufung: vorherige Rolle {previous_role.name} wiederhergestellt")
    result["probation_reverted"] = True
    log_audit(
        "Pulse – Probezeit-Automatik",
        "system",
        "Probezeit-Rückstufung nach Team-Warn",
        f"{member.display_name} ({member.id}) · Rolle zurück auf {previous_role.name} · Grund: {warn_reason}",
    )
    try:
        await send_dm_notification(
            member,
            f"⚠️ Du hast während deiner Probezeit eine Team-Verwarnung erhalten. "
            f"Deine Teamrolle wurde deshalb auf **{previous_role.name}** zurückgesetzt. Grund: {warn_reason}",
        )
    except Exception:
        pass
    try:
        await send_team_update_embed(
            guild,
            "⚠️ Team-Update: Probezeit-Rückstufung",
            f"{member.mention} erhielt während der Probezeit eine Team-Warnung; die vorherige Rolle wurde wiederhergestellt.",
            _pulse_color("warning"),
            target=member.mention,
            action="Probezeit-Rückstufung",
            actor="Pulse – Probezeit-Automatik",
            fields=[("Wiederhergestellte Rolle", previous_role.mention, True), ("Grund", warn_reason, False)],
            thumbnail=member.display_avatar.url,
        )
    except Exception as exc:
        print(f"⚠️ Team-Update zur Probezeit-Rückstufung fehlgeschlagen: {exc}")
    if owns_db:
        persist_probation()
    return result


async def apply_warn_consequences(
    guild,
    member,
    warn: dict,
    count: int,
    config: dict | None = None,
    entry: dict | None = None,
):
    """Apply warning escalation and probation rules for every new warning."""
    result = {"level": int(count or 0), "actions": [], "ok": True}
    team_role_ids = [int(x) for x in (config or load_config()).get("team_role_ids", [])]

    probation_result = await process_probation_warning(guild, member, warn, team_role_ids, entry, config=config)
    result["actions"].extend(probation_result.get("actions", []))
    result["ok"] = result["ok"] and probation_result.get("ok", True)
    if probation_result.get("team_kicked"):
        return result

    if count == 2:
        role = await _add_temporary_warn_role(
            guild, member, resolve_backup_role_id(WARN_UPRANK_LOCK_ROLE_ID, config), warn, "Uprank-Sperre"
        )
        result["actions"].append(role["message"] if role["ok"] else role["message"])
        result["ok"] = result["ok"] and role["ok"]

    elif count == 3:
        role = await _add_temporary_warn_role(
            guild, member, resolve_backup_role_id(WARN_IC_LOCK_ROLE_ID, config), warn, "IC-Rechte-Sperre"
        )
        result["actions"].append(role["message"] if role["ok"] else role["message"])
        result["ok"] = result["ok"] and role["ok"]

    elif count == 4 and not probation_result.get("probation_reverted"):
        downrank = await _apply_warn4_downrank(guild, member, warn, team_role_ids)
        result["actions"].append(downrank["message"])
        result["ok"] = result["ok"] and downrank["ok"]

    elif count == 5:
        team_kick = await _apply_warn5_team_kick(guild, member, warn, team_role_ids, config=config)
        result["actions"].append(team_kick["message"])
        result["ok"] = result["ok"] and team_kick["ok"]

    return result


async def restore_expired_warn_sanctions(guild, member, entry: dict, config: dict | None = None) -> dict:
    """Reverse temporary warning sanctions once no active team warnings remain.

    A Warn-5 Team-Kick is permanent; only temporary role restrictions/downranks
    can be reversed automatically. Re-hiring always requires an explicit manager action.
    """
    result = {"restored": [], "failed": [], "changed": False}
    if not guild or not member or active_warns(entry):
        return result

    cfg = config or load_config()
    team_role_ids = {
        int(role_id) for role_id in (cfg.get("team_role_ids") or [])
        if str(role_id).isdigit()
    }
    current_team_ids = {role.id for role in member.roles if role.id in team_role_ids}
    warnings = entry.get("warns_list", []) or []
    permanent_test_kick = any(
        isinstance(item, dict)
        and bool((item.get("consequences") or {}).get("test_supporter_team_kick"))
        for item in warnings
    )
    # Treat older Warn-5 kicks as permanent too; pre-7.14.3 records could otherwise
    # automatically restore a team rank when the last warning expired.
    permanent_team_kick = any(
        isinstance(item, dict)
        and (item.get("consequences") or {}).get("team_kick_applied")
        and not (item.get("consequences") or {}).get("team_kick_reverted_at")
        for item in warnings
    )
    if permanent_team_kick:
        stamp = now_de().isoformat(timespec="seconds")
        for item in warnings:
            if not isinstance(item, dict):
                continue
            item_consequences = item.get("consequences") or {}
            if item_consequences.get("team_kick_applied") and not item_consequences.get("team_kick_reverted_at"):
                needs_marker = (
                    not item_consequences.get("team_kick_permanent")
                    or not item_consequences.get("team_kick_restore_skipped_at")
                )
                item_consequences["team_kick_permanent"] = True
                item_consequences.setdefault("team_kick_restore_skipped_at", stamp)
                if needs_marker:
                    result["changed"] = True

    manual_team_override = bool(current_team_ids) and any(
        isinstance(item, dict)
        and (item.get("consequences") or {}).get("team_kick_applied")
        and not (item.get("consequences") or {}).get("team_kick_reverted_at")
        and not (item.get("consequences") or {}).get("test_supporter_team_kick")
        for item in warnings
    )
    if permanent_test_kick or permanent_team_kick or manual_team_override:
        for item in warnings:
            if not isinstance(item, dict):
                continue
            item_consequences = item.get("consequences") or {}
            if (
                item_consequences.get("downrank_applied")
                and not item_consequences.get("downrank_reverted_at")
                and not item_consequences.get("downrank_restore_skipped_at")
            ):
                item_consequences["downrank_restore_skipped_at"] = now_de().isoformat(timespec="seconds")
                result["changed"] = True

    # Reverse only temporary warning sanctions. Team-Kicks remain permanent; a manager must re-hire explicitly.
    for warn in reversed(warnings):
        if not isinstance(warn, dict):
            continue
        consequences = warn.get("consequences") or {}

        if (
            consequences.get("team_kick_applied")
            and not consequences.get("team_kick_reverted_at")
            and not consequences.get("team_kick_restore_skipped_at")
            and not consequences.get("test_supporter_team_kick")
        ):
            raw_previous_ids = consequences.get("team_kick_removed_role_ids") or []
            previous_ids = []
            for value in raw_previous_ids:
                try:
                    role_id = int(value)
                except (TypeError, ValueError):
                    continue
                if role_id in team_role_ids and role_id not in previous_ids:
                    previous_ids.append(role_id)

            # If a manager already re-hired the member, do not overwrite their edit.
            if current_team_ids:
                consequences["team_kick_restore_skipped_at"] = now_de().isoformat(timespec="seconds")
                result["changed"] = True
            elif not previous_ids:
                # Older records did not keep a snapshot of the old rank.
                consequences["team_kick_restore_skipped_at"] = now_de().isoformat(timespec="seconds")
                result["changed"] = True
            else:
                previous_roles = [guild.get_role(role_id) for role_id in previous_ids]
                if any(role is None or role.managed for role in previous_roles):
                    result["failed"].append("Die ursprüngliche Teamrolle des Team-Kicks fehlt oder ist verwaltet.")
                elif (
                    not guild.me
                    or not guild.me.guild_permissions.manage_roles
                    or any(role.position >= guild.me.top_role.position for role in previous_roles)
                ):
                    result["failed"].append("Team-Kick-Rücknahme fehlgeschlagen: Bot-Rollenrechte/Hiearchie prüfen.")
                else:
                    try:
                        await member.add_roles(
                            *previous_roles,
                            reason=f"Warn-Folge aufgehoben: keine aktiven Team-Warnungen mehr (Warn {warn.get('id')})",
                        )
                        current_team_ids.update(previous_ids)
                        consequences["team_kick_reverted_at"] = now_de().isoformat(timespec="seconds")
                        result["restored"].extend(role.name for role in previous_roles)
                        result["changed"] = True
                        log_audit(
                            "Pulse – Warn-Ablaufkontrolle",
                            "system",
                            "Team-Kick-Folge aufgehoben",
                            f"{member.display_name} ({member.id}) · Teamrollen wiederhergestellt: "
                            f"{', '.join(role.name for role in previous_roles)} · keine aktiven Warnungen mehr.",
                        )
                    except (discord.Forbidden, discord.HTTPException) as exc:
                        result["failed"].append(f"Team-Kick-Rücknahme: {type(exc).__name__}")

        if (
            not permanent_test_kick
            and not manual_team_override
            and consequences.get("downrank_applied")
            and not consequences.get("downrank_reverted_at")
            and not consequences.get("downrank_restore_skipped_at")
        ):
            raw_previous_ids = consequences.get("downrank_from_role_ids") or []
            previous_ids = []
            for value in raw_previous_ids:
                try:
                    role_id = int(value)
                except (TypeError, ValueError):
                    continue
                if role_id in team_role_ids and role_id not in previous_ids:
                    previous_ids.append(role_id)
            try:
                downrank_to_id = int(consequences.get("downrank_to_role_id") or 0)
            except (TypeError, ValueError):
                downrank_to_id = 0

            # Only undo the exact rank that Pulse applied; respect later manual changes.
            if downrank_to_id and current_team_ids != {downrank_to_id}:
                if current_team_ids:
                    consequences["downrank_restore_skipped_at"] = now_de().isoformat(timespec="seconds")
                    result["changed"] = True
                continue
            if not previous_ids:
                # Safe migration behavior: do not guess a rank for pre-7.8.8 records.
                consequences["downrank_restore_skipped_at"] = now_de().isoformat(timespec="seconds")
                result["changed"] = True
                continue

            previous_roles = [guild.get_role(role_id) for role_id in previous_ids]
            current_roles = [guild.get_role(role_id) for role_id in current_team_ids]
            all_roles = previous_roles + current_roles
            if any(role is None or role.managed for role in all_roles):
                result["failed"].append("Für die Downrank-Rücknahme fehlt eine Teamrolle oder sie ist verwaltet.")
                continue
            if (
                not guild.me
                or not guild.me.guild_permissions.manage_roles
                or any(role.position >= guild.me.top_role.position for role in all_roles)
            ):
                result["failed"].append("Downrank-Rücknahme fehlgeschlagen: Bot-Rollenrechte/Hiearchie prüfen.")
                continue
            try:
                roles_to_add = [role for role in previous_roles if role.id not in current_team_ids]
                roles_to_remove = [role for role in current_roles if role.id not in set(previous_ids)]
                if roles_to_add:
                    await member.add_roles(
                        *roles_to_add,
                        reason=f"Warn-Downrank aufgehoben: keine aktiven Team-Warnungen mehr (Warn {warn.get('id')})",
                    )
                if roles_to_remove:
                    await member.remove_roles(
                        *roles_to_remove,
                        reason=f"Warn-Downrank aufgehoben: keine aktiven Team-Warnungen mehr (Warn {warn.get('id')})",
                    )
                current_team_ids.difference_update(role.id for role in roles_to_remove)
                current_team_ids.update(role.id for role in previous_roles)
                consequences["downrank_reverted_at"] = now_de().isoformat(timespec="seconds")
                result["restored"].extend(role.name for role in previous_roles)
                result["changed"] = True
                log_audit(
                    "Pulse – Warn-Ablaufkontrolle",
                    "system",
                    "Downrank-Folge aufgehoben",
                    f"{member.display_name} ({member.id}) · Teamrolle(n) wiederhergestellt: "
                    f"{', '.join(role.name for role in previous_roles)} · keine aktiven Warnungen mehr.",
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                result["failed"].append(f"Downrank-Rücknahme: {type(exc).__name__}")

    if result["restored"]:
        unique_names = list(dict.fromkeys(result["restored"]))
        try:
            await send_dm_notification(
                member,
                f"✅ Deine Team-Warnungen sind abgelaufen oder wurden zurückgezogen. "
                f"Folgende zeitlich gebundene Team-Bestrafung(en) wurden aufgehoben: {', '.join(unique_names)}.",
            )
        except Exception as exc:
            print(f"⚠️ DM zu aufgehobenen Warn-Folgen für {member.id} fehlgeschlagen: {exc}")
        try:
            await send_team_update_embed(
                guild,
                "✅ Team-Update: Warn-Folgen aufgehoben",
                f"Für {member.mention} sind keine aktiven Team-Warnungen mehr vorhanden. "
                "Automatisch gespeicherte Downrank-/Team-Kick-Folgen wurden geprüft und gegebenenfalls zurückgenommen.",
                _pulse_color("success"),
                target=member.mention,
                action="Warn-Folgen aufgehoben",
                actor="Pulse – automatische Warn-Ablaufkontrolle",
                fields=[("Wiederhergestellt", ", ".join(unique_names), False)],
                thumbnail=member.display_avatar.url,
            )
        except Exception as exc:
            print(f"⚠️ Team-Update zu aufgehobenen Warn-Folgen fehlgeschlagen: {exc}")

    return result


async def cleanup_expired_team_warnings(guild, config: dict | None = None):
    """Expire warnings, reliably synchronize roles, and restore temporary sanctions."""
    result = {
        "checked": 0, "expired": 0, "roles_synced": 0, "failed": 0,
        "sanctions_restored": 0, "sanctions_failed": 0,
    }
    if not guild:
        result["failed"] = 1
        return result

    team_db = load_json(DATA_FILE, {})
    if not isinstance(team_db, dict):
        return result

    current = now_de()
    current_iso = current.isoformat(timespec="seconds")
    changed = False
    users_to_process = []

    for user_id, entry in team_db.items():
        if not isinstance(entry, dict):
            continue
        changed = normalize_warns(entry) or changed
        newly_expired = []
        for warn in entry.get("warns_list", []) or []:
            if not isinstance(warn, dict):
                continue
            result["checked"] += 1
            if warn.get("active", True) and not warn.get("revoked_at") and warning_is_expired(warn, current):
                warn["active"] = False
                warn["expired_at"] = current_iso
                warn["expired_reason"] = "Gültigkeitsdauer abgelaufen"
                newly_expired.append(warn)
                result["expired"] += 1
                changed = True

        # Retry role sync for warnings that were already marked expired by an
        # earlier run, but whose Discord role synchronization did not complete.
        pending_expired = [
            warn for warn in (entry.get("warns_list", []) or [])
            if isinstance(warn, dict)
            and warn.get("expired_reason") == "Gültigkeitsdauer abgelaufen"
            and not warn.get("expiry_roles_synced_at")
            and not warn.get("expiry_roles_sync_skipped_at")
        ]

        no_active_warnings = not active_warns(entry)
        sanctions_pending = no_active_warnings and any(
            isinstance(warn, dict)
            and (
                (
                    (warn.get("consequences") or {}).get("downrank_applied")
                    and not (warn.get("consequences") or {}).get("downrank_reverted_at")
                    and not (warn.get("consequences") or {}).get("downrank_restore_skipped_at")
                )
                or (
                    (warn.get("consequences") or {}).get("team_kick_applied")
                    and not (warn.get("consequences") or {}).get("team_kick_reverted_at")
                    and not (warn.get("consequences") or {}).get("team_kick_restore_skipped_at")
                    and not (warn.get("consequences") or {}).get("test_supporter_team_kick")
                )
            )
            for warn in (entry.get("warns_list", []) or [])
        )
        if newly_expired or pending_expired or sanctions_pending:
            users_to_process.append((str(user_id), entry, newly_expired, pending_expired))

    # Persist expiry timestamps before making Discord API calls. A temporary API
    # error must not reactivate a warning, and pending role sync is retried below.
    if changed:
        save_json(DATA_FILE, team_db)

    cfg = config or load_config()
    sanctions_changed = False
    state_changed = False

    for user_id, entry, newly_expired, pending_expired in users_to_process:
        numeric_user_id = str(user_id).isdigit()
        member = guild.get_member(int(user_id)) if numeric_user_id else None
        member_not_found = False

        # The member cache can be incomplete. Fetch from Discord before deciding
        # a role sync is impossible; a confirmed 404 means the user left the guild.
        if member is None and numeric_user_id and (newly_expired or pending_expired):
            try:
                member = await guild.fetch_member(int(user_id))
            except discord.NotFound:
                member_not_found = True
            except Exception as exc:
                result["failed"] += 1
                print(f"⚠️ Discord-Mitglied {user_id} konnte für Warnungsablauf nicht geladen werden: {exc}")
        elif member is None and not numeric_user_id:
            result["failed"] += 1
            print(f"⚠️ Ungültige Discord-User-ID bei Warnungsablauf: {user_id!r}")

        if member:
            if pending_expired:
                report = await sync_warn_roles(guild, member, len(active_warns(entry)), cfg)
                if report.get("ok"):
                    for warn in pending_expired:
                        warn["expiry_roles_synced_at"] = current_iso
                        warn.pop("expiry_roles_sync_skipped_at", None)
                        warn.pop("expiry_roles_sync_skip_reason", None)
                    state_changed = True
                    result["roles_synced"] += 1
                else:
                    result["failed"] += 1
                    message = report.get("message") or "Unbekannter Fehler"
                    print(f"⚠️ Warnrollen-Sync für {user_id} fehlgeschlagen: {message}")

            if not active_warns(entry):
                restoration = await restore_expired_warn_sanctions(guild, member, entry, cfg)
                if restoration.get("changed"):
                    sanctions_changed = True
                result["sanctions_restored"] += len(restoration.get("restored", []))
                result["sanctions_failed"] += len(restoration.get("failed", []))
                if restoration.get("failed"):
                    result["failed"] += len(restoration["failed"])
                    for issue in restoration["failed"]:
                        print(f"⚠️ Warn-Folge konnte für {member.id} nicht aufgehoben werden: {issue}")

        elif member_not_found:
            # A user who left the guild has no server roles to synchronize.
            # Record the skip so the five-minute task does not repeatedly fetch them.
            for warn in pending_expired:
                warn["expiry_roles_sync_skipped_at"] = current_iso
                warn["expiry_roles_sync_skip_reason"] = "Mitglied nicht mehr auf dem Server"
                state_changed = True
            if pending_expired:
                print(f"ℹ️ Warnrollen-Sync für {user_id} übersprungen: Mitglied ist nicht mehr auf dem Server.")

        # If lookup failed temporarily, leave pending markers untouched. The
        # next scheduled cleanup will retry; expiry itself remains persisted.
        if newly_expired:
            reasons = "; ".join(str(w.get("reason") or "Kein Grund") for w in newly_expired[:3])
            log_audit(
                "Pulse – automatische Warn-Ablaufkontrolle",
                "system",
                "Team-Warnung abgelaufen",
                f"User-ID {user_id}: {len(newly_expired)} Warnung(en) abgelaufen. Gründe: {reasons}",
            )
            if member:
                try:
                    await send_dm_notification(
                        member,
                        f"✅ Deine Team-Verwarnung(en) bei **{guild.name}** sind abgelaufen. "
                        f"Verbleibende aktive Warnungen: {len(active_warns(entry))}/5.",
                    )
                except Exception as exc:
                    print(f"⚠️ DM über abgelaufene Warnung für {user_id} fehlgeschlagen: {exc}")

    if sanctions_changed or state_changed:
        save_json(DATA_FILE, team_db)
    return result

async def process_expired_probations(guild) -> dict:
    """End active promotion probation periods after their selected 3 or 7 days."""
    result = {"checked": 0, "completed": 0, "failed": 0}
    if not guild:
        result["failed"] = 1
        return result

    team_db = load_json(DATA_FILE, {})
    if not isinstance(team_db, dict):
        return result
    current = now_de()
    changed = False
    completed = []
    for user_id, entry in team_db.items():
        if not isinstance(entry, dict):
            continue
        probation = entry.get("probation")
        if not isinstance(probation, dict) or not probation.get("active"):
            continue
        result["checked"] += 1
        try:
            expires_at = parse_dt(str(probation.get("expires_at") or ""))
        except (TypeError, ValueError, OverflowError):
            result["failed"] += 1
            continue
        if current < expires_at:
            continue
        probation["active"] = False
        probation["status"] = "completed"
        probation["completed_at"] = current.isoformat(timespec="seconds")
        probation["completion_reason"] = "Probezeit erfolgreich abgelaufen"
        completed.append((str(user_id), probation))
        changed = True
        result["completed"] += 1

    if changed:
        save_json(DATA_FILE, team_db)
    for user_id, probation in completed:
        member = guild.get_member(int(user_id)) if str(user_id).isdigit() else None
        display_name = member.display_name if member else f"User {user_id}"
        log_audit(
            "Pulse – Probezeit-Automatik",
            "system",
            "Probezeit abgeschlossen",
            f"{display_name} ({user_id}) · neue Rolle-ID {probation.get('new_role_id')} · "
            f"Probezeit bis {probation.get('expires_at')}",
        )
    return result


async def cleanup_expired_warn_restrictions(guild, config: dict | None = None):
    """Entfernt abgelaufene Warn-Folgerollen. Läuft auch nach Neustarts weiter."""
    team_db = load_json(DATA_FILE, {})
    if not isinstance(team_db, dict):
        return {"checked": 0, "removed": 0, "failed": 0}

    result = {"checked": 0, "removed": 0, "failed": 0}
    changed = False
    current = now_de()
    # (user_id, index, warn id from the file, reason, removed restrictions) – applied to a fresh copy at the end
    removals = []

    for user_id, entry in team_db.items():
        if not isinstance(entry, dict):
            continue
        for index, warn in enumerate(entry.get("warns_list", []) or []):
            if not isinstance(warn, dict):
                continue
            temp_roles = (warn.get("consequences") or {}).get("temporary_roles", [])
            if not isinstance(temp_roles, list):
                continue

            keep = []
            for restriction in temp_roles:
                if not isinstance(restriction, dict):
                    keep.append(restriction)
                    result["failed"] += 1
                    continue
                try:
                    expires = parse_dt(str(restriction.get("expires_at") or ""))
                except Exception:
                    keep.append(restriction)
                    continue

                warn_is_active = (
                    bool(warn.get("active", True))
                    and not warn.get("revoked_at")
                    and not warning_is_expired(warn, current)
                )
                if warn_is_active and current < expires:
                    keep.append(restriction)
                    continue

                # Keep all warning-related punishment roles while any other
                # team warning remains active. Keep this expiry record as well so
                # the role is removed automatically once the final warning ends.
                result["checked"] += 1
                another_active_source = any(
                    other is not warn
                    and isinstance(other, dict)
                    and other.get("active", True)
                    and not other.get("revoked_at")
                    and not warning_is_expired(other, current)
                    for other in (entry.get("warns_list", []) or [])
                )
                if another_active_source:
                    keep.append(restriction)
                    continue

                try:
                    role_id = int(restriction.get("role_id") or 0)
                except (TypeError, ValueError):
                    keep.append(restriction)
                    result["failed"] += 1
                    continue
                if role_id <= 0:
                    keep.append(restriction)
                    result["failed"] += 1
                    continue

                member = guild.get_member(int(user_id)) if guild else None
                role = guild.get_role(role_id) if guild else None

                if not member or not role:
                    result["removed"] += 1
                    continue

                if guild.me and (not guild.me.guild_permissions.manage_roles or role.position >= guild.me.top_role.position):
                    keep.append(restriction)
                    result["failed"] += 1
                    continue

                try:
                    if role in member.roles:
                        await member.remove_roles(
                            role,
                            reason=f"Warn-Folge abgelaufen · {restriction.get('label') or role.name}"
                        )
                    result["removed"] += 1
                except discord.Forbidden:
                    keep.append(restriction)
                    result["failed"] += 1
                except Exception:
                    keep.append(restriction)
                    result["failed"] += 1

            removed = [r for r in temp_roles if not any(r is k for k in keep)]
            if removed:
                removals.append((user_id, index, warn.get("id"), warn.get("reason"), removed))
                changed = True

    if changed:
        # remove_roles() was awaited above, possibly for many members: apply only the removed expiry records to a
        # fresh copy, so warnings, notes or new consequences saved meanwhile by other handlers are not overwritten.
        with _io_lock:
            _apply_warn_restriction_removals(removals)
    return result


def _apply_warn_restriction_removals(removals) -> None:
    """K-04 for cleanup_expired_warn_restrictions: remove only the expired records from the current file.
    Warnings are matched by their stored id, or (no id yet) by position and reason; a warning that changed
    meanwhile is skipped and decided again on the next run."""
    fresh = load_json(DATA_FILE, {})
    if not isinstance(fresh, dict):
        return
    touched = set()
    for user_id, index, warn_id, reason, removed in removals:
        fresh_entry = fresh.get(user_id)
        warns = (fresh_entry.get("warns_list") or []) if isinstance(fresh_entry, dict) else []
        target = next((w for w in warns if isinstance(w, dict) and warn_id and w.get("id") == warn_id), None)
        if target is None and not warn_id and index < len(warns):
            candidate = warns[index]
            if isinstance(candidate, dict) and not candidate.get("id") and candidate.get("reason") == reason:
                target = candidate
        if target is None:
            continue  # warning removed or changed meanwhile – the next run decides again
        consequences = target.setdefault("consequences", {})
        roles = consequences.get("temporary_roles")
        if isinstance(roles, list):
            consequences["temporary_roles"] = [r for r in roles if r not in removed]
            touched.add(user_id)
    for user_id in touched:
        normalize_warns(fresh[user_id])
    if touched:
        save_json(DATA_FILE, fresh)

async def sync_warn_roles(guild, member, count: int, config: dict | None = None):
    """Synchronisiert exakt eine Warn-Rolle 1-5 und gibt einen Diagnosebericht zurück."""
    report = {"ok": True, "count": max(0, min(int(count or 0), 5)), "role": None, "message": "Warn-Rollen synchronisiert."}
    if not (SYNC_WARN_ROLES and guild and member):
        report["message"] = "Warn-Rollen-Synchronisierung deaktiviert."
        return report

    ids = get_warn_role_ids(config)
    target_level = report["count"] if report["count"] > 0 else None
    target_role = guild.get_role(ids[target_level]) if target_level else None
    if target_level and not target_role:
        report.update(ok=False, message=f"Warn-Rolle {target_level} ({ids[target_level]}) wurde nicht gefunden.")
        return report

    health = {row["level"]: row for row in warning_role_health(guild, config)}

    # Erst alle betroffenen Rollen prüfen, dann erst Discord verändern.
    # So entsteht bei einer falsch positionierten alten Warnrolle kein Zwischenzustand
    # mit zwei Warnstufen gleichzeitig.
    if target_level:
        target_health = health.get(target_level)
        if not target_health or not target_health["ok"]:
            detail = target_health["detail"] if target_health else "Warnrolle nicht konfiguriert"
            report.update(ok=False, message=f"Warn-Rolle {target_level}: {detail}.")
            return report

    for level in sorted(ids):
        role = guild.get_role(ids[level])
        if role and role in member.roles and level != target_level:
            current_health = health.get(level)
            if not current_health or not current_health["ok"]:
                detail = current_health["detail"] if current_health else "Warnrolle nicht verwaltbar"
                report.update(ok=False, message=f"Warn-Rolle {level}: {detail}.")
                return report

    try:
        for level in sorted(ids):
            role = guild.get_role(ids[level])
            if not role:
                continue
            if level == target_level:
                if role not in member.roles:
                    await member.add_roles(role, reason=f"Warn-System (Dashboard) · Stufe {level}")
                report["role"] = role
            elif role in member.roles:
                await member.remove_roles(role, reason="Warn-System (Dashboard) · alte Stufe entfernen")
        return report
    except discord.Forbidden:
        report.update(ok=False, message="Discord verweigert die Rollenänderung. Prüfe Bot-Rolle und Manage-Roles-Recht.")
        return report
    except Exception as exc:
        print(f"Warn-Rolle konnte nicht angepasst werden: {exc}")
        report.update(ok=False, message=f"Technischer Fehler bei der Warn-Rolle: {exc}")
        return report

async def reconcile_warning_roles(guild, config: dict | None = None) -> dict:
    """Bringt alle Teammitglieder auf den korrekten Warnrollen-Stand."""
    result = {"checked": 0, "updated": 0, "failed": 0, "errors": []}
    if not SYNC_WARN_ROLES:
        result["message"] = "Warn-Rollen-Synchronisierung ist deaktiviert."
        return result
    if not guild:
        result["errors"].append("Guild nicht verfügbar")
        return result
    team_role_ids = {int(x) for x in (config or load_config()).get("team_role_ids", [])}
    warn_role_ids = set(get_warn_role_ids(config).values())
    team_db = load_json(DATA_FILE, {})
    for member in guild.members:
        if member.bot:
            continue
        is_team = any(r.id in team_role_ids for r in member.roles)
        has_warn_role = any(r.id in warn_role_ids for r in member.roles)
        if not is_team and not has_warn_role:
            continue

        entry = team_db.get(str(member.id), {})
        # Warnrollen richten sich nach aktiven Warnungen, auch wenn Warn 5 bereits
        # die Teamrollen entfernt hat. Dadurch bleibt die Warnstufe nachvollziehbar.
        count = len(active_warns(entry)) if isinstance(entry, dict) else 0
        report = await sync_warn_roles(guild, member, count, config)
        result["checked"] += 1
        if report.get("ok"):
            result["updated"] += 1
        else:
            result["failed"] += 1
            if len(result["errors"]) < 10:
                result["errors"].append(f"{member.display_name}: {report.get('message')}")
    return result


def ordered_team_role_ids(guild, team_role_ids) -> list[int]:
    """Return configured team roles from lowest to highest Discord role position."""
    role_ids = []
    seen = set()
    for value in team_role_ids or []:
        try:
            role_id = int(value)
        except (TypeError, ValueError):
            continue
        if role_id > 0 and role_id not in seen:
            role_ids.append(role_id)
            seen.add(role_id)

    def sort_key(role_id: int):
        get_role = getattr(guild, "get_role", None) if guild is not None else None
        role = get_role(role_id) if callable(get_role) else None
        # Missing configured roles sort last but remain available for diagnostics.
        return (role is None, role.position if role else 0, role_id)

    return sorted(role_ids, key=sort_key)


def team_rank(member, team_role_ids, guild=None) -> int:
    """Return the member's highest configured team rank (lowest -> highest).

    Prefer the guild explicitly supplied by the caller. Discord Member objects expose
    member.guild, but detached/test/proxy member objects do not always do so. The
    authorization decision must use the same guild that resolved the target and config.
    """
    if member is None:
        return -1
    rank_guild = guild if guild is not None else getattr(member, "guild", None)
    ordered_ids = ordered_team_role_ids(rank_guild, team_role_ids)
    member_role_ids = {getattr(role, "id", None) for role in getattr(member, "roles", ()) or ()}
    indexes = [index for index, role_id in enumerate(ordered_ids) if role_id in member_role_ids]
    return max(indexes) if indexes else -1


def team_supervisor_rank(guild, team_role_ids) -> int:
    """Lowest configured Teamleitung rank; Stv.Teamleitung and lower ranks are excluded."""
    from pulse_team_roles import canonical_team_role_name

    candidates = []
    get_role = getattr(guild, "get_role", None) if guild is not None else None
    for index, role_id in enumerate(ordered_team_role_ids(guild, team_role_ids)):
        role = get_role(role_id) if callable(get_role) else None
        if role is not None and canonical_team_role_name(getattr(role, "name", "")) == "Teamleitung":
            candidates.append(index)
    return min(candidates) if candidates else -1


def can_manage_team_warnings(guild, member, team_role_ids) -> bool:
    """Teamleitung+ authority; the server owner and Discord administrators are the highest rank."""
    if member is None:
        return False
    if getattr(member, "id", None) == getattr(guild, "owner_id", None):
        return True
    if getattr(getattr(member, "guild_permissions", None), "administrator", False):
        return True
    minimum_rank = team_supervisor_rank(guild, team_role_ids)
    member_rank = team_rank(member, team_role_ids, guild)
    return minimum_rank >= 0 and member_rank >= minimum_rank

# =============================================================

# =============================================================
# WEB-DOMAIN-MODULES
# =============================================================
# Registration order is intentional: it preserves FastAPI route precedence and
# remains aligned with the previous single-file implementation.
from pulse_web._compat import load_domain_modules as _load_domain_modules

_PULSE_WEB_MODULES = _load_domain_modules(
    globals(),
    (
        "pulse_web.ui",
        "pulse_web.dashboard",
        "pulse_web.tickets",
        "pulse_web.team",
        "pulse_web.applications",
        "pulse_web.backups",
        "pulse_web.administration",
    ),
)
for _web_module in _PULSE_WEB_MODULES:
    _web_module.__dict__.pop("_load_domain_modules", None)
del _web_module
del _load_domain_modules
