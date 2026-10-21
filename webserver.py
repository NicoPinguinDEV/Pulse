# =============================================================
# BOCHUM RP – TEAM DASHBOARD  (v3.0)
# =============================================================
import asyncio
import base64
import contextlib
import copy
import csv
import hashlib
import hmac
import html
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
from contextlib import closing
import threading
import time
import uuid
import zipfile
from datetime import date as date_cls, datetime, timedelta
from collections import OrderedDict
from types import SimpleNamespace
from typing import List
from pathlib import Path
from urllib.parse import quote, urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, Cookie, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
import httpx
import discord
from pulse_discord_style import send_styled
from pulse_embed_design import color as _pulse_color
import pulse_db as pulse_db
from pulse_version import PULSE_VERSION
from pulse_time import display, parse_timestamp

from pulse_shift_utils import AUTO_SHIFT_END_HOURS, AUTO_SHIFT_END_SECONDS, shift_age_seconds, shift_event_id
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

BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)
load_dotenv(BASE_DIR / ".env")

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
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_host:
        scheme = (forwarded_proto.split(",")[0].strip() if forwarded_proto else request.url.scheme)
        return f"{scheme}://{forwarded_host}".rstrip("/")
    return str(request.base_url).rstrip("/")

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
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", str(_pulse_ids.GUILD_ID)))
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
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "7"))              # Login-Dauer
# Vergessene Schichten werden nach genau 24 Stunden automatisch beendet und verwarnt.
# MAX_SHIFT_HOURS bleibt als kompatible Konstante für alte Integrationen bestehen.
MAX_SHIFT_HOURS = AUTO_SHIFT_END_HOURS
AUTO_BACKUP_HOURS = float(os.getenv("AUTO_BACKUP_HOURS", "24")) # 0 = aus
MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", "30"))
VOICE_SUPPORT_AUDIO_DIR = BASE_DIR / "uploads" / "voice_support_audio"
MAX_VOICE_SUPPORT_AUDIO_BYTES = 10 * 1024 * 1024
VOICE_SUPPORT_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
COOKIE_SECURE = bool(PUBLIC_BASE_URL and PUBLIC_BASE_URL.startswith("https://"))

DATA_FILE = "team_data.json"
CONFIG_FILE = "config.json"
APPS_FILE = "applications.json"
SHIFTS_FILE = "shifts.json"
LOGS_FILE = "logs.json"
AUDIT_FILE = "audit_logs.json"
MEETINGS_FILE = "meetings.json"
DB_ABMELDUNGEN = str(BASE_DIR / "abmeldungen.db")
ACTIVITY_DB = str(BASE_DIR / "activity_check.db")
BACKUP_DIR = "backups"
SECRET_FILE = ".session_secret"

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
    await _start_background_tasks()
    try:
        yield
    finally:
        await _stop_background_tasks()


app = FastAPI(title="Pulse TeamOS", version=PULSE_VERSION, docs_url=None, redoc_url=None, openapi_url=None,
              lifespan=_lifespan)


# Small inline favicon avoids the browser's recurring default /favicon.ico 404.
_FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="15" fill="#4f46e5"/>
<path d="M18 13h9v29h20v9H18z" fill="#fff"/>
<circle cx="46" cy="17" r="5" fill="#a5f3fc"/>
</svg>"""


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(
        content=_FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """U-13: the single security middleware – same-origin check for state-changing requests
    (Origin, else Referer) and one consistent set of headers incl. a basic CSP (pulse_security)."""
    import pulse_security
    if not pulse_security.same_origin_ok(request.method, request.headers):
        return JSONResponse({"detail": "Ungültige Anfragequelle."}, status_code=403)
    response = await call_next(request)
    https = oauth_redirect_uri(request).startswith("https://") or oauth_redirect_uri(request, application=True).startswith("https://")
    pulse_security.apply_headers(request.url.path, request.method, response, https)
    return response


# =============================================================
# ZEIT-HELFER (alles in deutscher Zeit, unabhängig vom Server)
# =============================================================
def now_de() -> datetime:
    return datetime.now(TZ) if TZ else datetime.now().astimezone()


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
    Idempotent. webserver itself no longer creates abmeldungen.db, backups/ or .session_secret on import;
    the modules registered at its end (pulse_pro, pulse_features, pulse_next, …) still create pulse.db
    tables while registering – to be moved here step by step (A-02/A-04)."""
    global _RUNTIME_READY
    if _RUNTIME_READY:
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    init_db()
    pulse_db.init_db()
    import pulse_migrations  # A-05: versioned migrations (backup before the first pending one)
    pulse_migrations.run(os.path.abspath(pulse_db.DB_PATH), os.path.abspath(BACKUP_DIR))
    _session_key()  # computes SESSION_SECRET_PROBLEM for the system status
    _RUNTIME_READY = True

# =============================================================
# DATEI-HELFER (thread-safe + atomares Schreiben)
# =============================================================
_io_lock = threading.RLock()


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
    _CORRUPT_JSON[key] = (f"{os.path.basename(filepath)} ist beschädigt ({type(exc).__name__})"
                          + (f" – Sicherung: {os.path.basename(backup)}" if backup else ""))
    print(f"🔴 DATEN: {_CORRUPT_JSON[key]}. Pulse schreibt diese Datei nicht mehr, bis sie repariert "
          "oder aus einem Backup wiederhergestellt ist.")


def load_json(filepath, default):
    with _io_lock:
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as exc:
                _mark_corrupt(filepath, exc)
                return default
            _CORRUPT_JSON.pop(os.path.abspath(filepath), None)  # repaired by hand → writable again
            return data
    return default


def abspath_corrupt_free(filepath) -> bool:
    return os.path.abspath(filepath) not in _CORRUPT_JSON


def save_json(filepath, data):
    with _io_lock:
        if os.path.abspath(filepath) == os.path.abspath(CONFIG_FILE):
            _CONFIG_CACHE.update(key=None, data=None)  # P-04: invalidate on every write
        problem = _CORRUPT_JSON.get(os.path.abspath(filepath))
        if problem:
            raise JsonWriteBlocked(problem)
        import pulse_backup
        if pulse_backup.maintenance_active():  # D-02: a restore is replacing the data files right now
            raise JsonWriteBlocked("Wartungsmodus – eine Wiederherstellung läuft gerade")
        tmp = f"{filepath}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, filepath)  # verhindert kaputte Dateien bei Absturz
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


def log_audit(actor_name: str, actor_id: str, action: str, details: str):
    audit_data = load_json(AUDIT_FILE, [])
    audit_data.append({
        "id": f"audit_{uuid.uuid4().hex[:6]}",
        "actor": actor_name,
        "actor_id": str(actor_id),
        "action": action,
        "details": details,
        "timestamp": now_de().strftime("%d.%m.%Y %H:%M:%S")
    })
    save_json(AUDIT_FILE, audit_data[-5000:])  # Datei wächst nicht unendlich


def team_safety_admin_exempt(guild, member) -> bool:
    """Only the real Discord server owner/administrators bypass automatic team safety."""
    if not guild or not member:
        return False
    return member.id == getattr(guild, "owner_id", None) or bool(
        getattr(getattr(member, "guild_permissions", None), "administrator", False)
    )


async def _apply_team_safety_team_kick(guild, member, config: dict, reason: str) -> dict:
    """Remove Pulse team rank roles only; never ban or kick the member from Discord."""
    if not guild or not member:
        return {"ok": False, "changed": False, "message": "Guild oder Moderator nicht verfügbar."}
    configured_ids = set()
    for raw_id in config.get("team_role_ids", []) or []:
        try:
            configured_ids.add(int(raw_id))
        except (TypeError, ValueError):
            continue
    configured_ids.add(resolve_backup_role_id(TEST_SUPPORTER_ROLE_ID, config))
    roles_to_remove = [
        role for role in member.roles
        if (
            role.id in configured_ids
            or "test supporter" in role.name.casefold()
        ) and not role.is_default()
    ]
    if not roles_to_remove:
        return {"ok": True, "changed": False, "message": "Keine konfigurierten Teamrollen mehr vorhanden."}

    bot_member = getattr(guild, "me", None)
    if not bot_member or not bot_member.guild_permissions.manage_roles:
        return {"ok": False, "changed": False, "message": "Bot hat keine Berechtigung „Rollen verwalten“."}
    blocked = [
        role for role in roles_to_remove
        if role.managed or role.position >= bot_member.top_role.position
    ]
    if blocked:
        return {
            "ok": False, "changed": False,
            "message": "Bot kann folgende Teamrollen nicht entfernen: "
                       + ", ".join(role.name for role in blocked)
                       + ". Rollen-Hierarchie oder verwaltete Rollen prüfen.",
        }

    try:
        await member.remove_roles(
            *roles_to_remove,
            reason=f"Pulse-Teamler-Schutz: automatischer Team-Kick: {reason}"[:500],
        )
        pulse_db.mark_team_safety_kicked(guild.id, member.id)
        removed_names = ", ".join(role.name for role in roles_to_remove)
        log_audit(
            "Pulse – Teamler-Schutz", "system", "Automatischer Team-Kick wegen Warn-Missbrauch",
            f"{member.display_name} ({member.id}) · entfernte Teamrollen: {removed_names} · Grund: {reason}",
        )
        try:
            pulse_db.record_event(
                "team_warn_abuse_team_kick", "team_member", member.id, "system",
                "Pulse – Teamler-Schutz", {"reason": reason, "removed_roles": [r.id for r in roles_to_remove]},
            )
        except Exception as exc:
            print(f"⚠️ Teamler-Schutz: Event-Audit konnte nicht geschrieben werden: {exc}")
        try:
            await send_dm_notification(
                member,
                f"🛡️ **Pulse-Teamler-Schutz:** Deine Teamrollen auf **{guild.name}** wurden automatisch entfernt. "
                f"Grund: {reason}. Du wurdest nicht vom Discord-Server gekickt. "
                "Bitte kläre den Vorfall mit der Führungsebene.",
            )
        except Exception as exc:
            print(f"⚠️ Teamler-Schutz: DM an Moderator {member.id} fehlgeschlagen: {exc}")
        return {"ok": True, "changed": True, "message": f"Teamrollen entfernt: {removed_names}"}
    except (discord.Forbidden, discord.HTTPException) as exc:
        return {
            "ok": False, "changed": False,
            "message": f"Discord konnte die Teamrollen nicht entfernen ({type(exc).__name__}).",
        }


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
    conn = sqlite3.connect(DB_ABMELDUNGEN)
    try:
        rows = conn.execute("SELECT user_id, user_name, grund, von, bis FROM abmeldungen").fetchall()
    finally:
        conn.close()
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
        if env.lower() in SESSION_SECRET_PLACEHOLDERS:
            SESSION_SECRET_PROBLEM = ("SESSION_SECRET ist noch der Platzhalter aus .env.example – "
                                      "Pulse nutzt stattdessen die erzeugte .session_secret. Bitte ein eigenes langes Secret setzen.")
        elif len(env) < SESSION_SECRET_MIN_LENGTH:
            SESSION_SECRET_PROBLEM = (f"SESSION_SECRET ist zu kurz (< {SESSION_SECRET_MIN_LENGTH} Zeichen) – "
                                      "Pulse nutzt stattdessen die erzeugte .session_secret.")
        else:
            return env.encode()
        print(f"🔴 SICHERHEIT: {SESSION_SECRET_PROBLEM}")
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "rb") as f:
            key = f.read().strip()
            if key:
                return key
    key = secrets.token_hex(32).encode()
    with open(SECRET_FILE, "wb") as f:
        f.write(key)
    try:
        os.chmod(SECRET_FILE, 0o600)
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
    session = {key: applicant.get(key) for key in ("id", "username", "global_name", "avatar")}
    session["login_at"] = int(applicant.get("login_at") or now)
    session["apply_exp"] = min(now + APPLY_SESSION_SECONDS, session["login_at"] + APPLY_SESSION_MAX_SECONDS)
    response.set_cookie("apply_session", sign_payload(session, typ=TOKEN_APPLY), httponly=True, samesite="lax",
                        secure=oauth_cookie_secure(request), max_age=max(0, session["apply_exp"] - now))


def apply_return_path(request: Request) -> str:
    """Where to land after the Discord login: back on the chosen career form if it was remembered."""
    career = str(request.cookies.get(APPLY_RETURN_COOKIE) or "")
    return f"/apply?career={quote(career)}" if career in APPLICATION_CAREERS else "/apply"


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
    except Exception as exc:  # the signed cookie stays the primary check; a DB hiccup must not lock everyone out
        print(f"⚠️ Sitzungsprüfung nicht möglich: {type(exc).__name__}")
        valid = True
    return data if valid else None


def compute_perms(guild, user_id, config):
    """A-03: one permission model (pulse_permissions) – team roles only, inheritance in one place,
    extra Pulse permissions included. Contract unchanged: (perms dict incl. is_admin, member)."""
    import pulse_permissions
    return pulse_permissions.compute(guild, user_id, config)


def auth(request: Request, user_session, perm="can_view_dashboard", admin=False):
    """Zentrale Prüfung: Login gültig? Noch auf dem Server? Hat die Rolle das Recht?"""
    user = get_current_user(user_session)
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    if not guild:
        raise HTTPException(status_code=503, detail="Der Bot ist noch nicht bereit. Bitte in ein paar Sekunden neu laden.")
    config = load_config()
    perms, member = compute_perms(guild, user["id"], config)
    if not (perms.get("can_view_dashboard") or perms.get("is_admin")):
        # U-14: left the team (team kick, roles removed, left the server) → sessions end. Only revoke when
        # the member list is reliable, so an incomplete cache after a restart does not log everyone out.
        if member is not None or getattr(guild, "chunked", False):
            try:
                import pulse_sessions
                pulse_sessions.revoke_all(user["id"], reason="nicht mehr im Team")
            except Exception:
                pass
        raise HTTPException(status_code=403, detail="Du bist nicht (mehr) im Team oder deine Rolle darf das Dashboard nicht sehen.")
    if admin and not perms["is_admin"]:
        raise HTTPException(status_code=403, detail="Dieser Bereich ist nur für Administratoren.")
    if perm and not perms.get(perm):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
    return SimpleNamespace(
        user=user, guild=guild, guild_name=guild.name, perms=perms, config=config, member=member,
        msg=request.query_params.get("msg", ""), msg_ok=request.query_params.get("t", "ok") != "err",
    )


def back(url: str, msg: str = None, ok: bool = True):
    if msg:
        # The message must come before an anchor (#logs), otherwise the browser treats it as part of the anchor.
        base, sep, anchor = url.partition("#")
        url = f"{base}{'&' if '?' in base else '?'}msg={quote(msg)}&t={'ok' if ok else 'err'}{sep}{anchor}"
    return RedirectResponse(url=url, status_code=303)


_rate_hits = {}


def rate_limited(key: str, limit: int = 3, window: int = 3600) -> bool:
    """Ein kleiner In-Memory-Rate-Limiter mit periodischer Bereinigung alter Keys."""
    now = time.time()
    window = max(1, int(window))
    hits = [t for t in _rate_hits.get(key, []) if now - t < window]
    if len(hits) >= limit:
        _rate_hits[key] = hits
        return True

    hits.append(now)
    _rate_hits[key] = hits

    # Verhindert unbegrenztes Wachstum bei vielen Clients/IPs.
    if len(_rate_hits) > 5000:
        cutoff = now - window
        stale = [k for k, values in _rate_hits.items() if not any(ts >= cutoff for ts in values)]
        for stale_key in stale[:2500]:
            _rate_hits.pop(stale_key, None)

    return False

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
            "duration_seconds": min(shift_elapsed(s), limit),
            "auto_closed": True,
            "shift_event_id": event_id,
            "auto_warn_status": "pending",
            "auto_warn_reason": "Schicht nicht innerhalb von 24 Stunden beendet.",
        })
        del db["active_shifts"][uid]
        changed = True
    return db


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
            "duration_seconds": min(shift_elapsed(shift), AUTO_SHIFT_END_SECONDS),
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
                totals[uid] = totals.get(uid, 0) + int(entry.get("duration_seconds", 0))
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
        # Legacy warnings without an expiry remain permanent.
        item.setdefault("expires_at", None)
        item.setdefault("duration_days", None)
        item.setdefault("expired_at", None)
        item.setdefault("expired_reason", "")
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
    target_idx = team_rank(member, ranked_team_role_ids)
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


async def _apply_warn5_team_kick(guild, member, warn: dict, team_role_ids: list[int]):
    """Entfernt bei Warn 5 sämtliche konfigurierten Team-Rollen."""
    if warn.get("consequences", {}).get("team_kick_applied"):
        return {"ok": True, "message": "Team-Kick bereits ausgeführt.", "changed": False}

    current_team_roles = [r for r in member.roles if r.id in team_role_ids]
    consequences = warn.setdefault("consequences", {})
    consequences["team_kick_applied"] = True

    if not current_team_roles:
        return {"ok": True, "message": "Mitglied hatte bereits keine Team-Rolle mehr.", "changed": False}

    if guild.me and not guild.me.guild_permissions.manage_roles:
        consequences["team_kick_applied"] = False
        return {"ok": False, "message": "Bot hat keine 'Rollen verwalten'-Berechtigung für den Team-Kick.", "changed": False}

    if guild.me and any(r.position >= guild.me.top_role.position for r in current_team_roles):
        consequences["team_kick_applied"] = False
        return {"ok": False, "message": "Mindestens eine Team-Rolle liegt auf oder über der Bot-Rolle.", "changed": False}

    actor = warn.get("by") or "Warn-System"
    reason = warn.get("reason") or "Warn 5"
    try:
        await member.remove_roles(*current_team_roles, reason=f"Automatischer Team-Kick durch {actor}: {reason}")
        # Keep the previous rank for an automatic return once every active warning expires.
        consequences["team_kick_removed_role_ids"] = [role.id for role in current_team_roles]
        return {"ok": True, "message": "Alle Team-Rollen wurden automatisch entfernt.", "changed": True}
    except discord.Forbidden:
        consequences["team_kick_applied"] = False
        return {"ok": False, "message": "Discord verweigert den automatischen Team-Kick.", "changed": False}
    except Exception as exc:
        consequences["team_kick_applied"] = False
        return {"ok": False, "message": f"Automatischer Team-Kick fehlgeschlagen: {exc}", "changed": False}


async def process_probation_warning(
    guild,
    member,
    warn: dict,
    team_role_ids: list[int],
    entry: dict | None = None,
):
    """Apply Test-Supporter kick or restore the previous rank for probation warnings."""
    result = {"actions": [], "ok": True, "team_kicked": False, "probation_reverted": False}
    if not member or member.bot or not guild:
        return result

    if entry is None:
        team_db = load_json(DATA_FILE, {})
        entry = user_entry(team_db, str(member.id))
        owns_db = True
    else:
        team_db = None
        owns_db = False

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
        kick = await _apply_warn5_team_kick(guild, member, warn, kick_ids)
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
            save_json(DATA_FILE, team_db)
        return result

    probation = entry.get("probation")
    if not isinstance(probation, dict) or not probation.get("active"):
        if owns_db:
            save_json(DATA_FILE, team_db)
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
            save_json(DATA_FILE, team_db)
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
        save_json(DATA_FILE, team_db)
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

    probation_result = await process_probation_warning(guild, member, warn, team_role_ids, entry)
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
        team_kick = await _apply_warn5_team_kick(guild, member, warn, team_role_ids)
        result["actions"].append(team_kick["message"])
        result["ok"] = result["ok"] and team_kick["ok"]

    return result


async def restore_expired_warn_sanctions(guild, member, entry: dict, config: dict | None = None) -> dict:
    """Reverse warning-derived rank sanctions once every team warning has expired or been revoked."""
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
    manual_team_override = bool(current_team_ids) and any(
        isinstance(item, dict)
        and (item.get("consequences") or {}).get("team_kick_applied")
        and not (item.get("consequences") or {}).get("team_kick_reverted_at")
        and not (item.get("consequences") or {}).get("team_kick_restore_skipped_at")
        and not (item.get("consequences") or {}).get("test_supporter_team_kick")
        for item in warnings
    )
    if permanent_test_kick or manual_team_override:
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

    # Reverse consequences newest-first: undo the Warn-5 team removal first,
    # then restore the rank that was present before a Warn-4 downrank.
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
    """Expire warnings, update warning roles, and release warning sanctions when no active warns remain."""
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
                warn["expired_at"] = current.isoformat(timespec="seconds")
                warn["expired_reason"] = "Gültigkeitsdauer abgelaufen"
                newly_expired.append(warn)
                result["expired"] += 1
                changed = True

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
        if newly_expired or sanctions_pending:
            users_to_process.append((str(user_id), entry, newly_expired))

    if changed:
        save_json(DATA_FILE, team_db)

    cfg = config or load_config()
    sanctions_changed = False
    for user_id, entry, newly_expired in users_to_process:
        member = guild.get_member(int(user_id)) if str(user_id).isdigit() else None
        if member:
            report = await sync_warn_roles(guild, member, len(active_warns(entry)), cfg)
            if report.get("ok"):
                result["roles_synced"] += 1
            else:
                result["failed"] += 1

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
        elif newly_expired:
            result["failed"] += 1

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

    if sanctions_changed:
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

    for user_id, entry in team_db.items():
        if not isinstance(entry, dict):
            continue
        warns_changed = False
        for warn in entry.get("warns_list", []) or []:
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
                    warns_changed = True
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
                    warns_changed = True
                    result["removed"] += 1
                except discord.Forbidden:
                    keep.append(restriction)
                    result["failed"] += 1
                except Exception:
                    keep.append(restriction)
                    result["failed"] += 1

            if warns_changed:
                warn.setdefault("consequences", {})["temporary_roles"] = keep
                changed = True

        if entry.get("warns_list") is not None and warns_changed:
            normalize_warns(entry)

    if changed:
        save_json(DATA_FILE, team_db)
    return result


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
        role = guild.get_role(role_id) if guild else None
        # Missing configured roles sort last but remain available for diagnostics.
        return (role is None, role.position if role else 0, role_id)

    return sorted(role_ids, key=sort_key)


def team_rank(member, team_role_ids) -> int:
    """Return hierarchy index based on actual Discord role positions, lowest -> highest."""
    guild = getattr(member, "guild", None)
    ordered_ids = ordered_team_role_ids(guild, team_role_ids)
    idx = [ordered_ids.index(r.id) for r in member.roles if r.id in ordered_ids]
    return max(idx) if idx else -1

# =============================================================
# UI-BAUSTEINE
# =============================================================
CARD = "bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl shadow-sm"
INPUT = ("w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl "
         "p-3 text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500")
BTN = "bg-indigo-600 hover:bg-indigo-500 text-white font-semibold rounded-xl transition shadow-md shadow-indigo-600/20"
BADGE_OK = "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30"
BADGE_BAD = "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30"
BADGE_WARN = "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30"

SCROLL_RESTORE_SCRIPT = "<script>\n(function () {\n    const key = \"pulse:scroll-restore:v1\";\n    function clearSavedPosition() {\n        try { sessionStorage.removeItem(key); } catch (_) {}\n    }\n\n    // Restore only when a form redirect returns to the same page, never on unrelated navigation.\n    try {\n        const raw = sessionStorage.getItem(key);\n        if (raw) {\n            const saved = JSON.parse(raw);\n            const now = Date.now();\n            const fresh = saved && typeof saved.at === \"number\" && now >= saved.at && now - saved.at < 120000;\n            if (fresh && saved.path === location.pathname && Number.isFinite(saved.y)) {\n                clearSavedPosition();\n                const restore = () => requestAnimationFrame(() => requestAnimationFrame(() => {\n                    window.scrollTo(Number(saved.x) || 0, Number(saved.y) || 0);\n                }));\n                if (document.readyState === \"complete\") restore();\n                else window.addEventListener(\"load\", restore, { once: true });\n            } else {\n                clearSavedPosition();\n            }\n        }\n    } catch (_) {\n        clearSavedPosition();\n    }\n\n    // Save the viewport before ordinary form navigation (save buttons, filters and actions).\n    document.addEventListener(\"submit\", function (event) {\n        const form = event.target;\n        if (!(form instanceof HTMLFormElement) || form.dataset.noScrollRestore === \"true\") return;\n        if (form.target && form.target.toLowerCase() !== \"_self\") return;\n        try {\n            sessionStorage.setItem(key, JSON.stringify({\n                path: location.pathname,\n                x: window.scrollX,\n                y: window.scrollY,\n                at: Date.now()\n            }));\n        } catch (_) {}\n    }, true);\n})();\n</script>"

# U-03: Tailwind is built once with the standalone CLI (tools/build_css.py) and served from
# /static/pulse.css. Only if that file is missing (E-10: fall back and warn) the old Play-CDN is used.
STATIC_CSS = BASE_DIR / "static" / "pulse.css"


def _static_css_version() -> str:
    import hashlib
    try:
        return hashlib.sha256(STATIC_CSS.read_bytes()).hexdigest()[:12]
    except OSError:
        return ""


STATIC_CSS_VERSION = _static_css_version()
if STATIC_CSS_VERSION:
    TAILWIND_TAG = f'<link rel="stylesheet" href="/static/pulse.css?v={STATIC_CSS_VERSION}">'
else:
    print("⚠️ static/pulse.css fehlt – Dashboard nutzt ersatzweise das Tailwind-Play-CDN (tools/build_css.py ausführen).")
    TAILWIND_TAG = ('<script src="https://cdn.tailwindcss.com"></script>\n'
                    "<script>tailwind.config = { darkMode: 'class' }</script>")

HEAD_SCRIPT = TAILWIND_TAG + """
<script>
    if (localStorage.theme === 'dark' || (!('theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
        document.documentElement.classList.add('dark');
    } else {
        document.documentElement.classList.remove('dark');
    }
    function toggleTheme() {
        if (document.documentElement.classList.contains('dark')) {
            document.documentElement.classList.remove('dark'); localStorage.theme = 'light';
        } else {
            document.documentElement.classList.add('dark'); localStorage.theme = 'dark';
        }
    }
    function toggleSidebar() {
        const s = document.getElementById('sidebar'), b = document.getElementById('sidebarBackdrop');
        s.classList.toggle('hidden'); s.classList.toggle('flex'); b.classList.toggle('hidden');
    }
    function escapeHtml(v) {
        return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
</script>
""" + SCROLL_RESTORE_SCRIPT


def get_head_html(title: str):
    return (
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>{esc(title)}</title>\n" + HEAD_SCRIPT
    )


def get_sidebar_html(guild_name, current_page="dashboard", current_user=None, perms=None):
    perms = perms or {}
    user_name = esc(current_user.get("global_name", "Team Mitglied")) if current_user else "Gast"
    avatar_id = current_user.get("avatar") if current_user else None
    user_id = current_user.get("id") if current_user else None
    avatar_url = (f"https://cdn.discordapp.com/avatars/{esc(user_id)}/{esc(avatar_id)}.png"
                  if avatar_id and user_id else "https://cdn.discordapp.com/embed/avatars/0.png")
    try:
        unread = pulse_db.unread_count(user_id) if user_id else 0
    except Exception:
        unread = 0

    inbox_label = "Pulse Inbox" + (f" <span class='ml-auto text-[9px] rounded-full bg-rose-500 text-white px-1.5 py-0.5'>{unread}</span>" if unread else "")
    main_items = [
        ("ultimate", "/ultimate", "⚡", "Command Center"),
        ("dashboard", "/dashboard", "🛡️", "Moderatoren-Panel"),
        ("melonly", "/melonly", "🎮", "Melonly"),
        ("team", "/team", "👥", "Teamliste"),
        ("inbox", "/pulse-inbox", "📥", inbox_label),
        ("tasks", "/tasks", "📋", "Aufgaben"),
        ("tickets", "/tickets", "🎫", "Tickets"),
        ("meetings", "/meetings", "🎙️", "Teambesprechung"),
        ("meeting_history", "/meetings-history", "🗂️", "Meeting-Historie"),
        ("calendar", "/calendar", "🗓️", "Team-Kalender"),
        ("loa", "/loa", "🌴", "Abmeldungen (LOA)"),
        ("apps", "/applications", "📝", "Bewerbungen"),
        ("wiki", "/wiki", "📚", "Team-Wiki"),
        ("training", "/training", "🎓", "Schulungen"),
        ("achievements", "/achievements", "🏅", "Achievements"),
        ("stats", "/stats", "📊", "Statistiken"),
    ]
    admin_items = [
        ("backups", "/backups", "💾", "Server Backups"),
        ("system", "/system", "🩺", "Systemstatus"),
        ("search", "/search", "🔎", "Globale Suche"),
        ("settings", "/settings", "⚙️", "Rechte & Audit-Log"),
    ] if perms.get("is_admin") else [("search", "/search", "🔎", "Globale Suche")]

    def link(key, href, icon, label):
        cls = ("bg-indigo-600/10 text-indigo-600 dark:text-indigo-400 font-semibold border border-indigo-500/20 shadow-sm"
               if current_page == key else
               "text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800/50 hover:text-slate-900 dark:hover:text-slate-200")
        return (f'<a href="{href}" class="flex items-center gap-2.5 px-3 py-2.5 rounded-xl {cls} transition">'
                f'{icon} <span>{label}</span></a>')

    nav = '<div class="text-[10px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider px-2 mb-2">Hauptmenü</div>'
    nav += "".join(link(*i) for i in main_items)
    if admin_items:
        nav += '<div class="text-[10px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider px-2 mt-5 mb-2">Verwaltung</div>'
        nav += "".join(link(*i) for i in admin_items)

    return f"""
    <button onclick="toggleSidebar()" aria-label="Menü" class="md:hidden fixed top-3 left-3 z-50 w-10 h-10 rounded-xl bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-700 shadow text-lg">☰</button>
    <div id="sidebarBackdrop" onclick="toggleSidebar()" class="hidden fixed inset-0 bg-black/50 z-30 md:hidden"></div>
    <aside id="sidebar" class="hidden md:flex fixed md:sticky top-0 left-0 z-40 h-screen w-64 overflow-y-auto bg-white dark:bg-[#141824] border-r border-slate-200 dark:border-slate-800/80 flex-col justify-between p-4 shrink-0 transition-colors duration-200">
        <div class="space-y-6">
            <div class="flex items-center gap-3 px-2">
                <div class="w-9 h-9 rounded-xl bg-indigo-600 flex items-center justify-center font-bold text-white shadow-md shadow-indigo-600/20">B</div>
                <div>
                    <h2 class="font-bold text-slate-900 dark:text-white leading-none">{esc(guild_name)}</h2>
                    <span class="text-[10px] text-slate-500 dark:text-slate-400 font-mono">Pulse v7 TeamOS</span>
                </div>
            </div>
            <div class="bg-slate-100 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-800 rounded-xl p-2.5 flex items-center gap-2 shadow-inner">
                <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                <span class="text-xs font-semibold text-slate-700 dark:text-slate-200 truncate">{esc(guild_name)}</span>
            </div>
            <nav class="space-y-1 text-xs">{nav}</nav>
        </div>
        <div class="border-t border-slate-200 dark:border-slate-800/80 pt-4 px-1 space-y-3 mt-6">
            <button onclick="toggleTheme()" class="w-full flex items-center justify-between px-3 py-2.5 rounded-xl bg-slate-100 dark:bg-slate-800/60 hover:bg-slate-200 dark:hover:bg-slate-800 text-slate-700 dark:text-slate-300 text-xs font-medium transition shadow-sm">
                <span class="flex items-center gap-2">
                    <span class="dark:hidden">🌙 Dark Mode</span>
                    <span class="hidden dark:inline">☀️ Light Mode</span>
                </span>
                <span class="text-[10px] px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-700 font-mono">Umschalten</span>
            </button>
            <div class="flex items-center justify-between pt-1">
                <div class="flex items-center gap-2.5 truncate">
                    <img src="{avatar_url}" alt="" class="w-7 h-7 rounded-full border border-slate-200 dark:border-slate-700 object-cover">
                    <span class="text-xs font-medium text-slate-700 dark:text-slate-300 truncate">{user_name}</span>
                </div>
                <a href="/logout" class="text-xs text-slate-400 hover:text-rose-500 dark:hover:text-rose-400 transition font-medium">↤ Abmelden</a>
            </div>
        </div>
    </aside>
    """


def render_page(title: str, ctx, page: str, body: str, extra_head: str = "") -> str:
    """Gemeinsamer Seitenrahmen (vorher in jeder Route kopiert) inkl. Toast-Meldung."""
    toast = ""
    if ctx.msg:
        color = BADGE_OK if ctx.msg_ok else BADGE_BAD
        icon = "✅" if ctx.msg_ok else "⚠️"
        # U-04: the message sticks to the top of the viewport (forms scroll back to their position after
        # submitting) and errors stay until closed instead of vanishing after 6 seconds.
        auto_close = 'setTimeout(()=>{const t=document.getElementById("toast"); if(t) t.remove();}, 6000);' if ctx.msg_ok else ""
        toast = (f'<div id="toast" role="{"status" if ctx.msg_ok else "alert"}" class="pulse-toast mb-5 rounded-xl shadow-lg">'
                 f'<div class="border {color} rounded-xl px-4 py-3 text-xs font-semibold flex items-center justify-between gap-3">'
                 f'<span>{icon} {esc(ctx.msg)}</span>'
                 f'<button onclick="document.getElementById(\'toast\').remove()" class="opacity-60 hover:opacity-100" aria-label="Meldung schließen">✕</button></div></div>'
                 f'<script>{auto_close}history.replaceState(null,"",location.pathname+location.hash);</script>')
    return f"""<!DOCTYPE html>
    <html lang="de">
    <head>{get_head_html(f"{title} - {ctx.guild_name}")}{extra_head}</head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-800 dark:text-slate-200 font-sans min-h-screen flex transition-colors duration-200">
        {get_sidebar_html(ctx.guild_name, page, ctx.user, ctx.perms)}
        <main class="flex-1 min-w-0 p-4 pt-16 md:p-8 md:pt-8">
            {toast}
            {body}
        </main>
    </body>
    </html>"""


def time_tag(value) -> str:
    """U-09: a time shown as "vor 5 Min." in the browser, with the exact Berlin time as tooltip/fallback."""
    parsed = parse_timestamp(value)
    if parsed is None:
        return esc(str(value or "–"))
    exact = display(parsed)
    return f'<time datetime="{esc(parsed.isoformat())}" data-relative title="{esc(exact)}">{esc(exact)}</time>'


def empty_state(text: str, icon: str = "📭") -> str:
    """U-09: one look for "nothing here yet" across pages."""
    return f'<div class="pulse-empty-state" role="status"><span class="icon" aria-hidden="true">{icon}</span><span>{esc(text)}</span></div>'


def simple_page(icon: str, title: str, message: str, links: str = "") -> str:
    return f"""<!DOCTYPE html>
    <html lang="de"><head>{get_head_html(title)}</head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-900 dark:text-white min-h-screen flex items-center justify-center p-4 font-sans">
        <div class="{CARD} p-8 max-w-md w-full text-center space-y-4">
            <div class="text-4xl">{icon}</div>
            <h1 class="text-lg font-bold">{esc(title)}</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400">{esc(message)}</p>
            <div class="flex justify-center gap-3 text-xs pt-2">{links}</div>
        </div>
    </body></html>"""


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code in (301, 302, 303, 307) and exc.headers and "Location" in exc.headers:
        return RedirectResponse(url=exc.headers["Location"], status_code=303)
    icons = {400: "⚠️", 403: "🔒", 404: "🔍", 503: "⏳"}
    if exc.status_code in icons:
        links = ('<a href="/dashboard" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Zum Dashboard</a>'
                 '<a href="/logout" class="px-4 py-2 rounded-xl bg-slate-100 dark:bg-slate-800">Abmelden</a>')
        return HTMLResponse(simple_page(icons[exc.status_code], f"Fehler {exc.status_code}", str(exc.detail), links),
                            status_code=exc.status_code)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    """P-11: friendly page with an error ID instead of "Internal Server Error"; the traceback goes to the log only."""
    import traceback as _traceback
    error_id = secrets.token_hex(3).upper()
    print(f"❌ Dashboard-Fehler [Fehler-ID {error_id}] {request.method} {request.url.path}: {type(exc).__name__}: {exc}")
    import pulse_logging
    pulse_logging.report_error(f"web.{request.method} {request.url.path}", f"{type(exc).__name__}: {exc}", error_id)
    _traceback.print_exception(type(exc), exc, exc.__traceback__)
    wants_json = request.url.path.startswith(("/api/", "/ultimate/api/", "/suite/api/")) or \
        "application/json" in request.headers.get("accept", "")
    if wants_json:
        return JSONResponse({"detail": "Interner Fehler", "error_id": error_id}, status_code=500)
    links = ('<a href="/dashboard" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Zum Dashboard</a>'
             '<a href="javascript:history.back()" class="px-4 py-2 rounded-xl bg-slate-100 dark:bg-slate-800">Zurück</a>')
    return HTMLResponse(simple_page("⚠️", "Da ist etwas schiefgelaufen",
                                    f"Ein unerwarteter Fehler ist aufgetreten. Fehler-ID {error_id} – das Team findet sie im Log. "
                                    "Deine letzte Aktion wurde eventuell nicht ausgeführt.", links), status_code=500)


@app.exception_handler(JsonWriteBlocked)
async def json_write_blocked_handler(request: Request, exc: JsonWriteBlocked):
    links = '<a href="/system" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Systemstatus</a>'
    return HTMLResponse(simple_page("🛑", "Änderung nicht gespeichert",
                                    f"{exc}. Die Änderung wurde nicht gespeichert, damit keine Daten verloren gehen. "
                                    "Bitte die Datei reparieren oder aus einem Backup wiederherstellen.", links),
                        status_code=503)


# =============================================================
# API-ROUTE: ROBLOX LIVE USER LOOKUP (nur eingeloggt, mit Cache)
# =============================================================
# Small bounded LRU cache: both expiry and size are enforced so a stream of
# unique Roblox usernames cannot grow process memory without limit.
_ROBLOX_CACHE_TTL_SECONDS = 300
_ROBLOX_CACHE_MAX_ENTRIES = 300
_roblox_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()


def _prune_roblox_cache(now: float | None = None) -> None:
    now = time.time() if now is None else now
    expired = [
        key for key, (created_at, _value) in _roblox_cache.items()
        if now - created_at >= _ROBLOX_CACHE_TTL_SECONDS
    ]
    for key in expired:
        _roblox_cache.pop(key, None)
    while len(_roblox_cache) > _ROBLOX_CACHE_MAX_ENTRIES:
        _roblox_cache.popitem(last=False)


def _roblox_http_client() -> httpx.AsyncClient:
    """Reuse a low-concurrency HTTP pool instead of opening a client per lookup."""
    client = getattr(app.state, "roblox_http_client", None)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=3.0),
            limits=httpx.Limits(
                max_connections=4,
                max_keepalive_connections=2,
                keepalive_expiry=15.0,
            ),
        )
        app.state.roblox_http_client = client
    return client


@app.get("/api/roblox-user")
async def roblox_user_lookup(request: Request, username: str, user_session: str = Cookie(None)):
    user = get_current_user(user_session)
    client_key = f"roblox-lookup:{user.get('id')}:{request.client.host if request.client else 'unknown'}"
    if rate_limited(client_key, limit=60, window=60):
        return JSONResponse({"success": False, "message": "Zu viele Roblox-Abfragen. Bitte kurz warten."}, status_code=429)
    name = (username or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", name):
        return JSONResponse({"success": False, "message": "Ungültiger Roblox-Name (3-20 Zeichen, A-Z, 0-9, _)"})

    cache_key = name.lower()
    cached = _roblox_cache.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _ROBLOX_CACHE_TTL_SECONDS:
        _roblox_cache.move_to_end(cache_key)
        info = dict(cached[1])
    else:
        if cached:
            _roblox_cache.pop(cache_key, None)
        client = _roblox_http_client()
        try:
            res = await client.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [name], "excludeBannedUsers": False}, timeout=5.0)
            res.raise_for_status()
            data = res.json()
            if not data.get("data"):
                return JSONResponse({"success": False, "message": "Nutzer auf Roblox nicht gefunden"})
            u = data["data"][0]
            avatar_url = "https://www.roblox.com/headshot-thumbnail/image"
            try:
                thumb = await client.get(
                    f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={u['id']}&size=150x150&format=Png&isCircular=true",
                    timeout=5.0)
                thumb.raise_for_status()
                td = thumb.json()
                if td.get("data"):
                    avatar_url = td["data"][0].get("imageUrl", avatar_url)
            except Exception:
                pass
            info = {"success": True, "id": str(u["id"]), "username": u["name"],
                    "displayName": u.get("displayName", u["name"]), "avatarUrl": avatar_url}
            _roblox_cache[cache_key] = (time.time(), info)
            _roblox_cache.move_to_end(cache_key)
            _prune_roblox_cache()
        except Exception:
            return JSONResponse({"success": False, "message": "Roblox ist gerade nicht erreichbar"})

    # Vorstrafen-Hinweis aus den eigenen Logs
    previous = [l for l in load_json(LOGS_FILE, [])
                if str(l.get("roblox_id")) == info["id"] or str(l.get("target_user", "")).lower() == info["username"].lower()]
    counts = {}
    for l in previous:
        counts[l.get("type", "Log")] = counts.get(l.get("type", "Log"), 0) + 1
    info["previous_total"] = len(previous)
    info["previous_summary"] = ", ".join(f"{v}x {k}" for k, v in counts.items())
    return JSONResponse(info)


# =============================================================
# API: ROBLOX SUCHVORSCHLÄGE FÜR MELOONLY
# =============================================================
@app.get("/api/roblox-search")
async def roblox_search(request: Request, query: str, user_session: str = Cookie(None)):
    user = get_current_user(user_session)
    client_key = f"roblox-search:{user.get('id')}:{request.client.host if request.client else 'unknown'}"
    if rate_limited(client_key, limit=120, window=60):
        return JSONResponse({"success": False, "users": [], "message": "Zu viele Roblox-Suchanfragen. Bitte kurz warten."}, status_code=429)
    clean = (query or "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{2,20}", clean):
        return JSONResponse({"success": True, "users": []})

    # Lokale Einträge fangen Roblox-Ausfälle ab; fuzzy Treffer helfen auch bei Tippfehlern.
    users_map = {}
    query_lower = clean.lower()
    for entry in reversed(load_json(LOGS_FILE, [])[-5000:]):
        saved_name = str(entry.get("target_user") or "").strip()
        saved_id = str(entry.get("roblox_id") or "")
        if not saved_name:
            continue
        if query_lower not in saved_name.lower() and roblox_similarity_score(clean, saved_name) < 0.42:
            continue
        users_map.setdefault(saved_name.lower(), {
            "id": saved_id,
            "name": saved_name,
            "displayName": saved_name,
        })

    try:
        client = _roblox_http_client()
        res = await client.get(
            "https://users.roblox.com/v1/users/search",
            params={"keyword": clean, "limit": 8},
            timeout=5.0,
        )
        res.raise_for_status()
        data = res.json()
        for u in data.get("data", [])[:8]:
            name = str(u.get("name") or "")
            if not name:
                continue
            users_map[name.lower()] = {
                "id": str(u.get("id", "")),
                "name": name,
                "displayName": str(u.get("displayName") or name),
            }
    except Exception:
        # Bereits protokollierte ähnliche Namen werden bei Roblox-Ausfällen weiterhin angezeigt.
        pass

    ranked_users = rank_roblox_candidates(clean, list(users_map.values()), limit=8)
    return JSONResponse({
        "success": True,
        "users": ranked_users,
        "best_match": ranked_users[0] if ranked_users else None,
    })

# =============================================================
# HEALTH CHECK
# =============================================================
@app.get("/healthz")
async def healthz(request: Request):
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    db_ok = True
    try:
        with pulse_db.connect() as db:
            db.execute("SELECT 1")
    except Exception:
        db_ok = False

    ready = bool(bot and bot.is_ready() and guild and db_ok)
    return JSONResponse(
        {
            "ok": ready,
            "version": PULSE_VERSION,
            "bot_ready": bool(bot and bot.is_ready()),
            "guild_ready": bool(guild),
            "database_ok": db_ok,
        },
        status_code=200 if ready else 503,
    )


# =============================================================
# ROUTEN: LOGIN, LOGOUT & OAUTH CALLBACK
# =============================================================
# U-02: every team member chooses their own start page (default: Mein Pulse).
START_PAGES = {"/me": "Mein Pulse", "/dashboard": "Übersicht", "/cockpit": "Leitungs-Cockpit",
               "/ultimate": "Command Center", "/suite": "Operations Suite", "/tickets": "Tickets"}
DEFAULT_START_PAGE = "/me"


def start_page_for(user_id) -> str:
    try:
        chosen = pulse_db.get_setting(f"start_page:{user_id}", DEFAULT_START_PAGE)
    except Exception:
        chosen = DEFAULT_START_PAGE
    return chosen if chosen in START_PAGES else DEFAULT_START_PAGE


@app.get("/static/pulse.css")
async def static_pulse_css():
    """U-03: the prebuilt Tailwind CSS; the ?v=<hash> in the link changes with every rebuild."""
    if not STATIC_CSS.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(STATIC_CSS, media_type="text/css", headers={"Cache-Control": "public, max-age=604800, immutable"})


@app.get("/", response_class=HTMLResponse)
async def home(user_session: str = Cookie(None)):
    session = dashboard_session(user_session)  # same check as every protected page (no redirect loop)
    if session:
        return RedirectResponse(url=start_page_for(session.get("id")), status_code=303)
    page = f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>{get_head_html("Bochum RP Panel Login")}</head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-900 dark:text-white min-h-screen flex items-center justify-center p-4 font-sans transition-colors duration-200">
        <div class="absolute top-6 right-6">
            <button onclick="toggleTheme()" class="px-3 py-2 rounded-xl bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 text-xs shadow-sm font-medium">
                <span class="dark:hidden">🌙 Dark</span><span class="hidden dark:inline">☀️ Light</span>
            </button>
        </div>
        <div class="{CARD} p-8 shadow-xl w-full max-w-md text-center">
            <div class="flex justify-center mb-4">
                <div class="w-12 h-12 rounded-2xl bg-indigo-600 flex items-center justify-center text-2xl shadow-lg shadow-indigo-600/30">🛡️</div>
            </div>
            <h1 class="text-2xl font-bold tracking-tight mb-2">Team Management Panel</h1>
            <p class="text-slate-500 dark:text-slate-400 text-xs mb-8">Melde dich mit deinem Discord-Account an, um Zugriff zu erhalten.</p>
            <a href="/login" class="inline-flex items-center justify-center gap-3 w-full bg-[#5865F2] hover:bg-[#4752C4] text-white font-semibold py-3 px-4 rounded-xl transition shadow-lg shadow-[#5865F2]/20 text-sm">
                Mit Discord anmelden
            </a>
            <div class="mt-6 pt-6 border-t border-slate-100 dark:border-slate-800">
                <a href="/apply" class="text-xs text-indigo-600 dark:text-indigo-400 hover:underline font-medium">Du möchtest dich ins Team bewerben? Hier klicken!</a>
            </div>
        </div>
    </body>
    </html>
    """
    response = HTMLResponse(page)
    if user_session:
        response.delete_cookie(key="user_session")  # ended or revoked session: clear it, show the login
    return response


@app.get("/login")
async def login(request: Request):
    state = secrets.token_urlsafe(24)  # CSRF-Schutz für den OAuth-Ablauf
    redirect_uri = oauth_redirect_uri(request)
    url = (f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}"
           f"&redirect_uri={quote(redirect_uri, safe='')}&response_type=code&scope=identify&state={state}")
    response = RedirectResponse(url=url, status_code=303)
    response.set_cookie("oauth_state", state, max_age=600, httponly=True, samesite="lax",
                        secure=oauth_cookie_secure(request))
    return response


@app.get("/logout", response_class=HTMLResponse)
async def logout_confirm():
    """U-13: GET no longer logs out (a foreign page could otherwise do it via an image link);
    existing /logout links show a one-click confirmation that posts the form."""
    form = ('<form method="post" action="/logout" class="inline"><button class="px-4 py-2 rounded-xl bg-indigo-600 '
            'text-white font-semibold" autofocus>Jetzt abmelden</button></form>'
            '<a href="/me" class="px-4 py-2 rounded-xl bg-slate-100 dark:bg-slate-800">Abbrechen</a>')
    return HTMLResponse(simple_page("↤", "Abmelden", "Möchtest du dich vom Pulse-Dashboard abmelden?", form))


@app.post("/logout")
async def logout(user_session: str = Cookie(None)):
    data = verify_session_token(user_session, TOKEN_DASHBOARD) if user_session else None
    if data and data.get("sid"):
        try:
            import pulse_sessions  # U-14: the server-side session ends too
            pulse_sessions.revoke(data["id"], data["sid"])
        except Exception:
            pass
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key="user_session")
    return response


@app.get("/callback")
async def callback(request: Request, code: str = None, state: str = None, error: str = None,
                   oauth_state: str = Cookie(None), apply_oauth_state: str = Cookie(None)):
    application_flow = bool(
        state and apply_oauth_state and hmac.compare_digest(state, apply_oauth_state)
    )
    retry = (
        '<a href="/apply" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Zur Bewerbung</a>'
        if application_flow else
        '<a href="/" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Erneut versuchen</a>'
    )
    if error or not code:
        title = "Bewerbungs-Login fehlgeschlagen" if application_flow else "Login abgebrochen"
        return HTMLResponse(simple_page("❌", title, "Du hast die Anmeldung nicht bestätigt. Bitte erneut versuchen.", retry))
    state_valid = application_flow or bool(
        state and oauth_state and hmac.compare_digest(state, oauth_state)
    )
    if not state_valid:
        title = "Bewerbungs-Login fehlgeschlagen" if apply_oauth_state else "Login fehlgeschlagen"
        return HTMLResponse(simple_page("❌", title, "Ungültiger Sicherheits-Token. Bitte erneut anmelden.", retry), status_code=400)

    redirect_uri = oauth_redirect_uri(request, application=application_flow)
    user_data, problem = await discord_oauth_identify(code, redirect_uri)  # P-12
    if user_data is None:
        title = "Bewerbungs-Login fehlgeschlagen" if application_flow else "Login fehlgeschlagen"
        return HTMLResponse(simple_page("❌", title, problem, retry), status_code=502)

    if application_flow:
        application_session = {
            "id": str(user_data.get("id")),
            "username": user_data.get("username"),
            "global_name": user_data.get("global_name") or user_data.get("username"),
            "avatar": user_data.get("avatar"),
        }
        response = RedirectResponse(url="/apply", status_code=303)
        response.headers["location"] = apply_return_path(request)
        set_apply_session_cookie(response, request, application_session)
        response.delete_cookie("apply_oauth_state")
        response.delete_cookie(APPLY_RETURN_COOKIE)
        return response

    # Nur Mitglieder mit Dashboard-Recht dürfen überhaupt eine Session bekommen
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    if not guild:
        return HTMLResponse(simple_page("⏳", "Bot startet noch", "Bitte in ein paar Sekunden erneut versuchen.", retry), status_code=503)
    perms, _ = compute_perms(guild, user_data.get("id"), load_config())
    if not perms["can_view_dashboard"]:
        return HTMLResponse(simple_page("🔒", "Kein Zugriff", "Du bist nicht im Team oder deine Rolle darf das Dashboard nicht sehen.",
                                        '<a href="/" class="px-4 py-2 rounded-xl bg-slate-100 dark:bg-slate-800">Zurück</a>'), status_code=403)

    session = {
        "id": str(user_data.get("id")),
        "username": user_data.get("username"),
        "global_name": user_data.get("global_name") or user_data.get("username"),
        "avatar": user_data.get("avatar"),
        "exp": int(time.time() + SESSION_DAYS * 86400),
    }
    try:
        import pulse_sessions  # U-14: server-side session for the session list
        session["sid"] = pulse_sessions.create(session["id"], request.headers.get("user-agent", ""))
    except Exception as exc:
        print(f"⚠️ Sitzung konnte nicht gespeichert werden: {type(exc).__name__}")
    response = RedirectResponse(url=start_page_for(session["id"]), status_code=303)  # U-02
    response.set_cookie(
        "user_session",
        sign_payload(session, typ=TOKEN_DASHBOARD),
        httponly=True,
        samesite="lax",
        secure=oauth_cookie_secure(request),
        max_age=SESSION_DAYS * 86400,
    )
    response.delete_cookie("oauth_state")
    return response

# =============================================================
# GEMEINSAME KLEINTEILE
# =============================================================
def display_of(guild, uid) -> str:
    try:
        m = guild.get_member(int(uid))
    except Exception:
        m = None
    return m.display_name if m else f"User {uid}"


def role_hex(role) -> str:
    return f"#{role.color.value:06x}" if role.color.value else "#6366f1"


def stat_card(icon, label, value, tint):
    return f"""
    <div class="{CARD} p-4 flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-{tint}-500/10 text-{tint}-500 flex items-center justify-center font-bold text-lg">{icon}</div>
        <div>
            <div class="text-xs text-slate-400 font-medium">{label}</div>
            <div class="text-xl font-bold text-slate-900 dark:text-white">{value}</div>
        </div>
    </div>"""


LOG_TYPE_STYLE = {
    "Ban": "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30",
    "Kick": "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30",
    "Warn": "bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 border-yellow-500/30",
    "Notiz": "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/30",
    "Ban BOLO": "bg-fuchsia-500/10 text-fuchsia-600 dark:text-fuchsia-400 border-fuchsia-500/30",
}

DASHBOARD_HEAD = """
<style>
    .type-chip { cursor:pointer; transition:.15s; }
    .type-chip.active { background:#4f46e5 !important; color:#fff !important; border-color:#4f46e5 !important; }
    .pulse-glass{background:linear-gradient(135deg,rgba(255,255,255,.82),rgba(248,250,252,.68));backdrop-filter:blur(18px);border:1px solid rgba(148,163,184,.20)}
    .dark .pulse-glass{background:linear-gradient(135deg,rgba(20,24,36,.88),rgba(11,14,20,.78));border-color:rgba(148,163,184,.12)}
    .pulse-hero{background:radial-gradient(700px 260px at 0% 0%,rgba(99,102,241,.22),transparent 60%),radial-gradient(500px 240px at 100% 100%,rgba(6,182,212,.14),transparent 60%),linear-gradient(135deg,rgba(99,102,241,.08),rgba(6,182,212,.05))}
    .dark .pulse-hero{background:radial-gradient(700px 260px at 0% 0%,rgba(99,102,241,.28),transparent 60%),radial-gradient(500px 240px at 100% 100%,rgba(6,182,212,.16),transparent 60%),linear-gradient(135deg,rgba(15,23,42,.92),rgba(8,15,27,.96))}
    .pulse-hover{transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease}
    .pulse-hover:hover{transform:translateY(-2px);box-shadow:0 16px 40px rgba(15,23,42,.10)}
    .dark .pulse-hover:hover{box-shadow:0 18px 44px rgba(0,0,0,.28)}
    .pulse-ring{box-shadow:0 0 0 1px rgba(99,102,241,.10),0 12px 32px rgba(99,102,241,.10)}
    .pulse-dot{box-shadow:0 0 0 4px rgba(16,185,129,.08)}
    @keyframes pulseFloat{from{transform:translateY(0)}to{transform:translateY(-3px)}}
    .pulse-float{animation:pulseFloat 2.8s ease-in-out infinite alternate}
    @media(max-width:800px){.pulse-hide-mobile{display:none}.pulse-grid-mobile{grid-template-columns:1fr}}
</style>
<script>
    document.addEventListener("DOMContentLoaded", function () {
        const el = document.getElementById("liveShiftTimer");
        if (!el) return;
        const base = parseInt(el.dataset.base || "0", 10);
        const running = el.dataset.running === "1";
        const t0 = Date.now();
        function tick() {
            const s = base + (running ? Math.floor((Date.now() - t0) / 1000) : 0);
            el.innerText = Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m " + (s % 60) + "s";
        }
        tick();
        if (running) setInterval(tick, 1000);
    });

    let activeType = "all";
    function setTypeFilter(t) {
        activeType = t;
        document.querySelectorAll(".type-chip").forEach(c => c.classList.toggle("active", c.dataset.type === t));
        filterLogs();
    }
    function filterLogs() {
        const q = (document.getElementById("logSearch").value || "").toLowerCase();
        document.querySelectorAll(".log-card").forEach(card => {
            const okType = activeType === "all" || card.dataset.type === activeType;
            const okText = (card.dataset.search || "").includes(q);
            card.style.display = (okType && okText) ? "" : "none";
        });
    }

    // Pulse Command Palette (Ctrl/Cmd+K)
    document.addEventListener("keydown", function(e){
        if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="k"){e.preventDefault();const q=prompt("Pulse Suche – Name, Ticket, Aufgabe, Bewerbung oder Wiki:");if(q) window.location.href="/search?q="+encodeURIComponent(q);}
    });
    function pulseClock(){
        const el=document.getElementById("pulseLiveClock");
        if(!el) return;
        const d=new Date();
        el.textContent=d.toLocaleTimeString("de-DE",{hour:"2-digit",minute:"2-digit",second:"2-digit"});
    }
    pulseClock(); setInterval(pulseClock,1000);
    // PWA
    if("serviceWorker" in navigator){ navigator.serviceWorker.register("/sw.js").catch(()=>{}); }

    let robloxSearchTimeout = null;
    let robloxSearchRequestId = 0;
    let lookupTimeout = null;
    let lookupRequestId = 0;
    let resolvedRobloxUsername = "";

    function normalizedRobloxInput(value) {
        return String(value || "").trim().replace(/^@+/, "").toLowerCase();
    }

    function searchRobloxUsers(val) {
        clearTimeout(robloxSearchTimeout);
        const input = String(val || "").trim().replace(/^@+/, "");
        const list = document.getElementById("robloxUserSuggestions");
        const panel = document.getElementById("robloxSuggestionPanel");
        const requestId = ++robloxSearchRequestId;
        if (!list || input.length < 2) {
            if (list) list.innerHTML = "";
            if (panel) { panel.replaceChildren(); panel.classList.add("hidden"); }
            return;
        }
        robloxSearchTimeout = setTimeout(() => {
            fetch("/api/roblox-search?query=" + encodeURIComponent(input), {cache: "no-store"})
                .then(r => r.json())
                .then(data => {
                    if (requestId !== robloxSearchRequestId) return;
                    const usernameInput = document.getElementById("robloxUsernameInput");
                    if (!usernameInput || normalizedRobloxInput(usernameInput.value) !== input.toLowerCase()) return;
                    if (!data.success) {
                        list.innerHTML = "";
                        if (panel) { panel.replaceChildren(); panel.classList.add("hidden"); }
                        return;
                    }
                    const users = Array.isArray(data.users) ? data.users : [];
                    list.innerHTML = users.map(u => "<option value=\"" + escapeHtml(u.name) + "\">" + escapeHtml(u.displayName || u.name) + " · ID " + escapeHtml(u.id) + "</option>").join("");
                    if (panel) {
                        panel.replaceChildren();
                        users.forEach((u, index) => {
                            const username = String(u.name || "").trim();
                            if (!username) return;
                            const button = document.createElement("button");
                            button.type = "button";
                            button.className = "flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left hover:bg-indigo-50 dark:hover:bg-indigo-950/40 border-b last:border-b-0 border-slate-100 dark:border-slate-800 focus:outline-none focus:bg-indigo-50 dark:focus:bg-indigo-950/40";
                            button.setAttribute("role", "option");
                            const left = document.createElement("span");
                            left.className = "min-w-0 flex flex-col";
                            const name = document.createElement("span");
                            name.className = "font-semibold text-xs text-slate-900 dark:text-white truncate";
                            name.textContent = (index === 0 ? "⭐ " : "") + username;
                            const display = document.createElement("span");
                            display.className = "text-[10px] text-slate-500 dark:text-slate-400 truncate";
                            display.textContent = String(u.displayName || username);
                            left.append(name, display);
                            const right = document.createElement("span");
                            right.className = "shrink-0 text-[10px] font-mono text-indigo-600 dark:text-indigo-300";
                            right.textContent = (index === 0 ? "Bester Treffer · " : "") + "ID " + String(u.id || "unbekannt");
                            button.append(left, right);
                            button.addEventListener("click", () => selectRobloxSuggestion(u));
                            panel.appendChild(button);
                        });
                        panel.classList.toggle("hidden", panel.childElementCount === 0);
                    }
                    const exact = users.find(u => String(u.name || "").toLowerCase() === input.toLowerCase());
                    const idInput = document.getElementById("robloxIdInput");
                    if (exact && idInput && /^\\d+$/.test(String(exact.id || ""))) {
                        idInput.value = String(exact.id);
                        resolvedRobloxUsername = String(exact.name).toLowerCase();
                    }
                })
                .catch(() => {
                    if (requestId !== robloxSearchRequestId) return;
                    list.innerHTML = "";
                    if (panel) { panel.replaceChildren(); panel.classList.add("hidden"); }
                });
        }, 220);
    }

    function selectRobloxSuggestion(user) {
        const username = String(user && user.name || "").trim();
        if (!username) return;
        const usernameInput = document.getElementById("robloxUsernameInput");
        const idInput = document.getElementById("robloxIdInput");
        const panel = document.getElementById("robloxSuggestionPanel");
        clearTimeout(robloxSearchTimeout);
        robloxSearchRequestId += 1;
        if (usernameInput) usernameInput.value = username;
        if (idInput) idInput.value = /^\\d+$/.test(String(user.id || "")) ? String(user.id) : "";
        resolvedRobloxUsername = username.toLowerCase();
        if (panel) { panel.replaceChildren(); panel.classList.add("hidden"); }
        lookupRobloxUser(username, true);
    }

    function renderRobloxPreview(data) {
        const infoDiv = document.getElementById("robloxUserPreview");
        if (!infoDiv) return;
        const prev = Number(data.previous_total || 0) > 0
            ? '<div class="text-[10px] text-amber-600 dark:text-amber-400 font-semibold mt-0.5">⚠️ ' + Number(data.previous_total) + ' frühere Logs (' + escapeHtml(data.previous_summary || "") + ')</div>'
            : '<div class="text-[10px] text-emerald-600 dark:text-emerald-400 mt-0.5">✔ Keine früheren Logs</div>';
        infoDiv.innerHTML =
            '<div class="flex items-center gap-3 p-2.5 bg-indigo-50/50 dark:bg-indigo-950/20 border border-indigo-200 dark:border-indigo-800/50 rounded-xl">' +
                (data.avatarUrl ? '<img src="' + escapeHtml(data.avatarUrl) + '" class="w-9 h-9 rounded-full border border-indigo-300 dark:border-indigo-700">' : '') +
                '<div class="truncate">' +
                    '<div class="font-bold text-slate-900 dark:text-white text-xs">' + escapeHtml(data.displayName || data.username || data.name || "") +
                    ' <span class="text-slate-400 text-[10px]">(@' + escapeHtml(data.username || data.name || "") + ')</span></div>' +
                    '<div class="text-[10px] text-indigo-600 dark:text-indigo-400 font-mono font-semibold">Roblox ID: ' + escapeHtml(data.id || "") + '</div>' +
                    (data.previous_total === undefined ? '' : prev) +
                '</div>' +
            '</div>';
        infoDiv.classList.remove("hidden");
    }

    function lookupRobloxUser(val, fromSuggestion) {
        val = String(val || "").trim().replace(/^@+/, "");
        const usernameInput = document.getElementById("robloxUsernameInput");
        const idInput = document.getElementById("robloxIdInput");
        const infoDiv = document.getElementById("robloxUserPreview");
        if (!fromSuggestion) {
            searchRobloxUsers(val);
            if (resolvedRobloxUsername && normalizedRobloxInput(val) !== resolvedRobloxUsername) {
                resolvedRobloxUsername = "";
                if (idInput) idInput.value = "";
            }
            if (infoDiv) infoDiv.classList.add("hidden");
        } else {
            clearTimeout(robloxSearchTimeout);
            robloxSearchRequestId += 1;
        }
        clearTimeout(lookupTimeout);
        const requestId = ++lookupRequestId;
        if (!val || val.length < 3) {
            if (infoDiv) infoDiv.classList.add("hidden");
            return;
        }
        lookupTimeout = setTimeout(() => {
            fetch("/api/roblox-user?username=" + encodeURIComponent(val), {cache: "no-store"})
                .then(r => r.json())
                .then(data => {
                    if (requestId !== lookupRequestId) return;
                    if (!usernameInput || normalizedRobloxInput(usernameInput.value) !== val.toLowerCase()) return;
                    if (data.success) {
                        if (idInput) idInput.value = String(data.id || "");
                        resolvedRobloxUsername = String(data.username || val).toLowerCase();
                        renderRobloxPreview(data);
                    } else if (fromSuggestion) {
                        renderRobloxPreview({name: val, username: val, id: idInput ? idInput.value : "", displayName: val});
                    } else {
                        const panel = document.getElementById("robloxSuggestionPanel");
                        if (panel && !panel.classList.contains("hidden")) return;
                        if (infoDiv) {
                            infoDiv.innerHTML = '<span class="text-rose-500 text-[11px] block px-1">⚠️ ' + escapeHtml(data.message || "Roblox-Nutzer nicht gefunden") + '</span>';
                            infoDiv.classList.remove("hidden");
                        }
                    }
                })
                .catch(() => {
                    if (requestId !== lookupRequestId) return;
                    if (fromSuggestion && idInput && idInput.value) {
                        renderRobloxPreview({name: val, username: val, id: idInput.value, displayName: val});
                    } else if (infoDiv) {
                        infoDiv.classList.add("hidden");
                    }
                });
        }, fromSuggestion ? 80 : 380);
    }
</script>
"""


# =============================================================
# ROUTE 1: HAUPT-DASHBOARD
# =============================================================
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_main(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    guild = ctx.guild
    mod_id = ctx.user["id"]

    shifts_db = load_shifts()
    logs_db = load_json(LOGS_FILE, [])
    apps_db = load_json(APPS_FILE, {})
    active_shifts = shifts_db["active_shifts"]
    loas = get_loas()
    team_role_ids = ctx.config.get("team_role_ids", [])

    active_staff_count = len([s for s in active_shifts.values() if s.get("status") in ("online", "break")])
    loa_count = len([l for l in loas.values() if l["active"]])
    pending_apps_count = len([a for a in apps_db.values() if a.get("status") in {"pending", "interview"}])

    current_shift = active_shifts.get(mod_id)
    shift_status = current_shift.get("status") if current_shift else "offline"
    elapsed = shift_elapsed(current_shift) if current_shift else 0
    status_label = {"online": "IM DIENST", "break": "PAUSE", "offline": "OFFLINE"}.get(shift_status, "OFFLINE")

    if shift_status == "offline":
        shift_buttons = '<button name="shift_action" value="start" class="col-span-2 bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-3 px-4 rounded-xl transition shadow-md shadow-emerald-900/10 text-xs">▶️ Schicht Starten</button>'
    else:
        middle = ('<button name="shift_action" value="break" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 font-semibold py-2.5 px-3 rounded-xl transition text-xs">⏸️ Pause</button>'
                  if shift_status == "online" else
                  '<button name="shift_action" value="resume" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 font-semibold py-2.5 px-3 rounded-xl transition text-xs">▶️ Fortsetzen</button>')
        shift_buttons = middle + '<button name="shift_action" value="end" class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 font-semibold py-2.5 px-3 rounded-xl transition text-xs">⏹️ Beenden</button>'

    # Wochen-Rangliste (inkl. laufender Schichten)
    board = []
    for member in guild.members:
        if any(r.id in team_role_ids for r in member.roles):
            secs = calculate_weekly_seconds(str(member.id), shifts_db["history"], active_shifts)
            if secs > 0:
                board.append((member.display_name, secs))
    board.sort(key=lambda x: x[1], reverse=True)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    leaderboard_html = "".join(f"""
        <div class="flex items-center justify-between bg-slate-50 dark:bg-[#0b0e14] px-3.5 py-2.5 rounded-xl border border-slate-200 dark:border-slate-800 text-xs">
            <div class="flex items-center gap-2"><span class="font-bold text-sm">{medals[i]}</span>
            <span class="text-slate-700 dark:text-slate-200 font-medium truncate max-w-[140px]">{esc(n)}</span></div>
            <span class="text-indigo-600 dark:text-indigo-400 font-mono font-bold">{s / 3600:.1f}h</span>
        </div>""" for i, (n, s) in enumerate(board[:3], 1))

    # Wer ist gerade im Dienst?
    duty_html = ""
    for uid, s in active_shifts.items():
        on_break = s.get("status") == "break"
        duty_html += f"""
        <div class="flex items-center justify-between text-xs bg-slate-50 dark:bg-[#0b0e14] px-3.5 py-2.5 rounded-xl border border-slate-200 dark:border-slate-800">
            <span class="flex items-center gap-2 font-medium truncate"><span class="w-2 h-2 rounded-full {'bg-amber-500' if on_break else 'bg-emerald-500 animate-pulse'}"></span>{esc(display_of(guild, uid))}</span>
            <span class="font-mono text-slate-500">{'☕ ' if on_break else ''}{fmt_duration(shift_elapsed(s))}</span>
        </div>"""

    # Logs / Melonly
    logs_html = ""
    edit_log_id = str(request.query_params.get("edit_log") or "")
    is_manager = ctx.perms["can_promote"] or ctx.perms["is_admin"]
    type_labels = {"Warn":"⚠️ Warn","Kick":"🚪 Kick","Ban":"🚫 Ban","Ban BOLO":"🚨 Ban BOLO","Notiz":"📝 Notiz"}

    # Search the complete archive for a player's history, not only the latest 50 records.
    player_history_query = str(request.query_params.get("player_history") or "").strip()[:80]
    normalized_query = player_history_query.lstrip("@").strip().casefold()

    def normalized_player_name(log):
        return str(log.get("target_user") or "").strip().lstrip("@").casefold()

    def normalized_player_id(log):
        value = str(log.get("roblox_id") or "").strip()
        return value if value.isdigit() else ""

    player_history_matches = []
    if normalized_query:
        if normalized_query.isdigit():
            seed_matches = [
                log for log in logs_db
                if normalized_player_id(log) == normalized_query
            ]
            if not seed_matches:
                exact_names = [
                    log for log in logs_db
                    if normalized_player_name(log) == normalized_query
                ]
                seed_matches = exact_names or [
                    log for log in logs_db
                    if normalized_query in normalized_player_name(log)
                ]
        else:
            exact_names = [
                log for log in logs_db
                if normalized_player_name(log) == normalized_query
            ]
            seed_matches = exact_names or [
                log for log in logs_db
                if normalized_query in normalized_player_name(log)
            ]

        # Link records under an older username if their saved Roblox ID matches.
        matched_names = {normalized_player_name(log) for log in seed_matches if normalized_player_name(log)}
        matched_ids = {normalized_player_id(log) for log in seed_matches if normalized_player_id(log)}
        player_history_matches = [
            log for log in logs_db
            if (normalized_player_name(log) and normalized_player_name(log) in matched_names)
            or (normalized_player_id(log) and normalized_player_id(log) in matched_ids)
        ]

    history_active = bool(normalized_query)
    logs_to_render = reversed(player_history_matches) if history_active else reversed(logs_db[-50:])
    history_query_param = f"player_history={quote(player_history_query, safe='')}&" if history_active else ""
    history_return_to = (
        f"/dashboard?player_history={quote(player_history_query, safe='')}#playerlog"
        if history_active else "/dashboard#playerlog"
    )

    history_players = sorted(
        {str(log.get("target_user") or "").strip().lstrip("@") for log in player_history_matches
         if str(log.get("target_user") or "").strip()},
        key=str.casefold,
    )
    if history_active:
        history_warns = sum(1 for log in player_history_matches if log.get("type") == "Warn")
        history_kicks = sum(1 for log in player_history_matches if log.get("type") == "Kick")
        history_bans = sum(1 for log in player_history_matches if log.get("type") in {"Ban", "Ban BOLO"})
        history_notes = sum(1 for log in player_history_matches if log.get("type") == "Notiz")
        matched_people = ", ".join("@" + name for name in history_players[:4]) or player_history_query
        if len(history_players) > 4:
            matched_people += f" (+{len(history_players) - 4} weitere)"
        player_history_summary = f"""
            <div class="{CARD} p-4 space-y-3">
                <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                        <div class="text-sm font-bold text-slate-900 dark:text-white">🔎 Spieler-Verlauf</div>
                        <div class="text-xs text-slate-500 dark:text-slate-400 mt-1 break-words">{esc(matched_people)}</div>
                    </div>
                    <span class="shrink-0 rounded-full px-2.5 py-1 text-[11px] font-bold bg-indigo-500/10 text-indigo-600 dark:text-indigo-300">{len(player_history_matches)} Vorgänge</span>
                </div>
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    <div class="rounded-lg bg-slate-50 dark:bg-[#0b0e14] p-2"><div class="text-[10px] text-slate-500">Warns</div><div class="font-bold text-sm">{history_warns}</div></div>
                    <div class="rounded-lg bg-slate-50 dark:bg-[#0b0e14] p-2"><div class="text-[10px] text-slate-500">Kicks</div><div class="font-bold text-sm">{history_kicks}</div></div>
                    <div class="rounded-lg bg-slate-50 dark:bg-[#0b0e14] p-2"><div class="text-[10px] text-slate-500">Bans / BOLO</div><div class="font-bold text-sm">{history_bans}</div></div>
                    <div class="rounded-lg bg-slate-50 dark:bg-[#0b0e14] p-2"><div class="text-[10px] text-slate-500">Notizen</div><div class="font-bold text-sm">{history_notes}</div></div>
                </div>
                <p class="text-[11px] text-slate-500 dark:text-slate-400">Alle gespeicherten Vorgänge dieses Suchtreffers werden angezeigt – auch ältere Einträge außerhalb der normalen letzten 50.</p>
            </div>"""
    else:
        player_history_summary = ""
    empty_logs_html = (
        f'<div class="text-xs text-slate-500 dark:text-slate-400 bg-white dark:bg-[#141824] p-6 rounded-2xl border border-slate-200 dark:border-slate-800 text-center shadow-sm">Keine Vorgänge für „{esc(player_history_query)}“ gefunden. Bitte prüfe den Roblox-@Namen oder die ID.</div>'
        if history_active else
        "<div class='text-xs text-slate-400 italic bg-white dark:bg-[#141824] p-6 rounded-2xl border border-slate-200 dark:border-slate-800 text-center shadow-sm'>Noch keine Logs eingetragen.</div>"
    )

    for log in logs_to_render:
        ltype = log.get("type", "Log")
        badge = LOG_TYPE_STYLE.get(ltype, "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300")
        search = f"{log.get('target_user','')} {log.get('roblox_id','')} {log.get('moderator','')} {ltype} {log.get('reason','')}".lower()
        can_edit = can_manage_melonly_entry(ctx, log)
        edit_button = (
            f'<a href="/dashboard?{history_query_param}edit_log={quote(str(log.get("id") or ""))}#playerlog" class="text-indigo-500 hover:underline font-semibold">✏️ Bearbeiten</a>'
            if can_edit else ""
        )
        delete_form = f"""
                <form action="/log/delete" method="post" onsubmit="return confirm('Diesen Log-Eintrag wirklich löschen?');">
                    <input type="hidden" name="log_id" value="{esc(log.get('id'))}">
                    <input type="hidden" name="return_to" value="{esc(history_return_to)}">
                    <button class="text-rose-500 hover:underline font-semibold">🗑️ Löschen</button>
                </form>""" if can_edit else ""
        edit_form = ""
        if edit_log_id == str(log.get("id")) and can_edit:
            edit_options = "".join(
                f'<option value="{esc(key)}" {"selected" if ltype == key else ""}>{esc(label)}</option>'
                for key, label in type_labels.items()
            )
            edit_form = f"""
            <form action="/log/edit" method="post" class="mt-3 pt-3 border-t border-slate-100 dark:border-slate-800/60 space-y-2.5">
                <input type="hidden" name="log_id" value="{esc(log.get('id'))}">
                <input type="hidden" name="return_to" value="{esc(history_return_to)}">
                <input class="{INPUT}" name="target_user" maxlength="50" value="{esc(log.get('target_user'))}" required>
                <input class="{INPUT}" name="roblox_id" maxlength="15" value="{esc(log.get('roblox_id','N/A'))}" placeholder="Roblox ID">
                <select class="{INPUT}" name="log_type">{edit_options}</select>
                <textarea class="{INPUT} h-24" name="reason" maxlength="1000" required>{esc(log.get('reason'))}</textarea>
                <div class="flex gap-2">
                    <button class="{BTN} px-4 py-2 text-xs">💾 Änderungen speichern</button>
                    <a href="/dashboard#playerlog" class="px-4 py-2 rounded-xl border border-slate-200 dark:border-slate-700 text-xs font-semibold">Abbrechen</a>
                </div>
            </form>"""
        logs_html += f"""
        <div class="log-card {CARD} p-4 space-y-2 hover:shadow transition-all" data-type="{esc(ltype)}" data-search="{esc(search)}">
            <div class="flex items-center justify-between border-b border-slate-100 dark:border-slate-800/60 pb-2">
                <div class="flex items-center gap-2 min-w-0">
                    <span class="px-2.5 py-0.5 rounded-full text-[10px] font-bold border {badge}">{esc(ltype)}</span>
                    <span class="text-xs font-semibold text-slate-900 dark:text-white truncate">{esc(log.get('target_user'))}</span>
                </div>
                <span class="text-[10px] text-slate-400 font-mono shrink-0">{esc(log.get('created_at'))}</span>
            </div>
            <div class="text-xs text-slate-600 dark:text-slate-300 space-y-1">
                <div><span class="text-slate-400">Roblox ID:</span> <span class="font-mono text-slate-800 dark:text-slate-200">{esc(log.get('roblox_id', 'N/A'))}</span></div>
                <div><span class="text-slate-400">Grund:</span> <span class="text-slate-700 dark:text-slate-200 break-words">{esc(log.get('reason'))}</span></div>
            </div>
            <div class="text-[10px] text-slate-400 pt-1 border-t border-slate-100 dark:border-slate-800/40 flex justify-between items-center gap-3">
                <span>Moderator: {esc(log.get('moderator'))}</span>
                <span class="flex items-center gap-3">{edit_button}{delete_form}</span>
            </div>
            {edit_form}
        </div>"""

    chips = "".join(
        f'<button type="button" data-type="{t}" onclick="setTypeFilter(\'{t}\')" class="type-chip {"active" if t == "all" else ""} px-2.5 py-1 rounded-lg text-[11px] font-semibold border border-slate-200 dark:border-slate-700 bg-white dark:bg-[#141824] text-slate-600 dark:text-slate-300">{label}</button>'
        for t, label in [("all", "Alle"), ("Warn", "⚠️ Warn"), ("Kick", "🚪 Kick"), ("Ban", "🚫 Ban"), ("Ban BOLO", "🚨 Ban BOLO"), ("Notiz", "📝 Notiz")])

    body = f"""
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        {stat_card("🛡️", "Team im Dienst", active_staff_count, "emerald")}
        {stat_card("📜", "Registrierte Logs", len(logs_db), "indigo")}
        {stat_card("🌴", "Aktive Abmeldungen", loa_count, "amber")}
        {stat_card("📋", "Offene Bewerbungen", pending_apps_count, "purple")}
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-12 gap-8">
        <div class="lg:col-span-4 space-y-6">
            <div class="{CARD} p-6 space-y-5">
                <div class="flex items-center justify-between">
                    <div>
                        <h2 class="text-lg font-bold text-slate-900 dark:text-white">Schicht-Steuerung</h2>
                        <p class="text-xs text-slate-500 dark:text-slate-400">Pausen zählen nicht zur Arbeitszeit</p>
                    </div>
                    <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border {BADGE_OK}">
                        <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>{active_staff_count} im Dienst
                    </span>
                </div>
                <div class="bg-slate-50 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-800 rounded-xl p-3 flex items-center justify-between text-xs">
                    <span class="flex items-center gap-2 font-medium">
                        <span class="w-2 h-2 rounded-full {'bg-emerald-500 animate-pulse' if shift_status == 'online' else ('bg-amber-500' if shift_status == 'break' else 'bg-slate-400')}"></span>{status_label}
                    </span>
                    <span id="liveShiftTimer" data-base="{elapsed}" data-running="{1 if shift_status == 'online' else 0}" class="font-mono font-bold text-indigo-600 dark:text-indigo-400">{fmt_duration(elapsed)}</span>
                </div>
                <form action="/shift/action" method="post" class="grid grid-cols-2 gap-3">{shift_buttons}</form>
            </div>

            <div class="{CARD} p-6 space-y-3">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white">🟢 Aktuell im Dienst</h3>
                <div class="space-y-2">{duty_html or "<div class='text-xs text-slate-400 italic'>Gerade ist niemand im Dienst.</div>"}</div>
            </div>

            <div class="{CARD} p-6 space-y-3">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white">🏆 Wochen-Aktivität (Top 3)</h3>
                <div class="space-y-2">{leaderboard_html or "<div class='text-xs text-slate-400 italic'>Noch keine Daten vorhanden.</div>"}</div>
            </div>
        </div>

        <div class="lg:col-span-4 space-y-6">
            <div id="playerlog" class="{CARD} p-6 space-y-4">
                <div>
                    <h2 class="text-lg font-bold text-slate-900 dark:text-white">🛡️ Melonly – Spielerakte</h2>
                    <p class="text-xs text-slate-500 dark:text-slate-400">Roblox-Spieler suchen, ID automatisch übernehmen und Vorgang protokollieren. <span class="font-semibold text-indigo-500">Nur im Panel – keine Team-Updates.</span></p>
                </div>
                <form action="/log/create" method="post" class="space-y-4 text-xs">
                    <div>
                        <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Roblox Username *</label>
                        <input type="text" id="robloxUsernameInput" name="target_user" maxlength="50" list="robloxUserSuggestions" autocomplete="off" oninput="lookupRobloxUser(this.value)" onfocus="searchRobloxUsers(this.value)" placeholder="z. B. Spieler123" required class="{INPUT}">
                        <div id="robloxSuggestionPanel" class="hidden mt-2 overflow-hidden rounded-xl border border-indigo-200 dark:border-indigo-800/70 bg-white dark:bg-[#10141f] shadow-sm" role="listbox" aria-label="Roblox-Spielervorschläge"></div>
                        <datalist id="robloxUserSuggestions"></datalist>
                    </div>
                    <div id="robloxUserPreview" class="hidden"></div>
                    <div>
                        <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Roblox Player ID (Auto-Ausfüllung)</label>
                        <input type="text" name="roblox_id" id="robloxIdInput" placeholder="z. B. 12345678" class="{INPUT}">
                    </div>
                    <div>
                        <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Typ der Strafe *</label>
                        <select name="log_type" required class="{INPUT}">
                            <option value="Warn">⚠️ Verwarnung (Warn)</option>
                            <option value="Kick">🚪 Kick</option>
                            <option value="Ban">🚫 Ban</option>
                            <option value="Ban BOLO">🚨 Ban BOLO</option>
                            <option value="Notiz">📝 Notiz / Hinweis</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Begründung *</label>
                        <textarea name="reason" maxlength="1000" placeholder="Grund hier eingeben..." required class="{INPUT} h-24"></textarea>
                    </div>
                    <button class="w-full {BTN} py-3">Log Speichern</button>
                </form>
            </div>
        </div>

        <div class="lg:col-span-4 space-y-4">
            <div class="flex items-center justify-between">
                <h2 class="text-lg font-bold text-slate-900 dark:text-white">Protokoll</h2>
                <div class="flex items-center gap-2">
                    <a href="/export/logs" class="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-700 text-xs px-2.5 py-1.5 rounded-lg text-slate-700 dark:text-slate-300 transition shadow-sm">📊 CSV</a>
                    <span class="text-xs text-slate-400 font-mono">{len(logs_db)} gesamt</span>
                </div>
            </div>
            <form action="/dashboard#playerlog" method="get" class="flex flex-col sm:flex-row gap-2">
                <input type="search" name="player_history" maxlength="80" value="{esc(player_history_query)}" placeholder="Roblox-@Name oder Spieler-ID suchen…" required class="{INPUT} flex-1">
                <button type="submit" class="{BTN} px-4 py-2.5 text-xs shrink-0">🔎 Spielerakte suchen</button>
                {('<a href="/dashboard#playerlog" class="px-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 text-xs font-semibold text-center">Suche zurücksetzen</a>' if history_active else '')}
            </form>
            {player_history_summary}
            <div class="flex flex-wrap gap-1.5">{chips}</div>
            <input type="text" id="logSearch" oninput="filterLogs()" placeholder="🔎 Name, ID, Moderator oder Grund..." class="w-full bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-xl px-3.5 py-2 text-xs text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500 shadow-sm">
            <div class="space-y-3 max-h-[calc(100vh-300px)] overflow-y-auto pr-1">
                {logs_html or empty_logs_html}
            </div>
            <p class="text-[10px] text-slate-400">{f"Alle {len(player_history_matches)} gespeicherten Vorgänge der Spielerakte werden angezeigt." if history_active else "Die Standardansicht zeigt die letzten 50 Einträge – der CSV-Export enthält alle."}</p>
        </div>
    </div>"""
    return render_page("Moderatoren-Panel", ctx, "dashboard", body, DASHBOARD_HEAD)


# =============================================================
# ROUTE: PULSE COMMAND CENTER / ULTIMATE
# =============================================================
@app.get("/ultimate", response_class=HTMLResponse)
async def ultimate_dashboard(request: Request, user_session: str = Cookie(None)):
    """Modernes Pulse Command Center: Team, Dienstzeit, Activity, Tickets und Handlungsbedarf auf einer Seite."""
    ctx = auth(request, user_session)
    guild = ctx.guild
    uid = str(ctx.user["id"])
    team_role_ids = ctx.config.get("team_role_ids", [])
    shifts_db = load_shifts()
    active_shifts = shifts_db.get("active_shifts", {})
    logs_db = load_json(LOGS_FILE, [])
    apps_db = load_json(APPS_FILE, {})
    loas = get_loas()

    members = [m for m in guild.members if not m.bot and any(r.id in team_role_ids for r in m.roles)]
    members.sort(key=lambda m: m.display_name.lower())

    current_shift = active_shifts.get(uid)
    shift_status = current_shift.get("status") if current_shift else "offline"
    current_elapsed = shift_elapsed(current_shift) if current_shift else 0
    weekly_goal = max(0.0, float(ctx.config.get("weekly_goal_hours", 3.0) or 0))
    weekly_seconds = calculate_weekly_seconds(uid, shifts_db.get("history", []), active_shifts)
    weekly_hours = weekly_seconds / 3600
    weekly_pct = min(100, int((weekly_hours / weekly_goal) * 100)) if weekly_goal > 0 else 100

    activity = get_activity_today(guild.id)
    eligible = activity.get("eligible")
    confirmed = activity.get("confirmed", set())
    activity_confirmed = sum(1 for m in members if activity.get("check_id") and (eligible is None or m.id in eligible) and m.id in confirmed)
    activity_open = sum(1 for m in members if activity.get("check_id") and (eligible is None or m.id in eligible) and m.id not in confirmed)

    try: tickets = pulse_db.list_tickets(limit=3000)
    except Exception: tickets = []
    open_tickets = [t for t in tickets if t.get("status") != "closed"]
    urgent_tickets = [t for t in open_tickets if t.get("priority") == "urgent"]

    try: tasks = pulse_db.list_tasks(limit=3000)
    except Exception: tasks = []
    open_tasks = [t for t in tasks if t.get("status") not in {"done", "archived"}]

    def is_overdue(t):
        raw = t.get("due_at")
        if not raw: return False
        try:
            due = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if due.tzinfo is None: due = due.replace(tzinfo=now_de().tzinfo)
            return due <= now_de()
        except Exception: return False

    overdue_tasks = [t for t in open_tasks if is_overdue(t)]
    pending_apps = sum(1 for a in apps_db.values() if str(a.get("status", "")).lower() in {"pending", "new", "in_review", "in prüfung", "review"})
    active_loas = sum(1 for x in loas.values() if x.get("active"))

    team_db = load_json(DATA_FILE, {})
    warning_5 = 0
    for m in members:
        try:
            if len(active_warns(user_entry(team_db, str(m.id)))) >= 5: warning_5 += 1
        except Exception: pass

    presence_counts = {
        "online": sum(str(m.status) == "online" for m in members),
        "idle": sum(str(m.status) == "idle" for m in members),
        "dnd": sum(str(m.status) == "dnd" for m in members),
        "offline": sum(str(m.status) == "offline" for m in members),
    }
    duty_count = sum(1 for x in active_shifts.values() if x.get("status") in {"online", "break"})
    try: unread = pulse_db.unread_count(ctx.user["id"])
    except Exception: unread = 0

    health_rows = warning_role_health(guild, ctx.config) if (ctx.perms.get("is_admin") or ctx.perms.get("can_warn")) else []
    health_ok = not health_rows or all(r.get("ok") for r in health_rows)
    critical = bool(warning_5 or urgent_tickets or overdue_tasks or not health_ok)
    attention = bool(pending_apps or open_tickets or active_loas or activity_open)
    state_text, state_cls, state_icon = (
        ("Sofort handeln", "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20", "🔴") if critical else
        ("Aufmerksamkeit", "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20", "🟡") if attention else
        ("Alles normal", "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20", "🟢")
    )

    def person_card(m):
        duty = active_shifts.get(str(m.id))
        duty_label = "🟢 Im Dienst" if duty and duty.get("status") == "online" else "☕ Pause" if duty and duty.get("status") == "break" else ""
        presence = {"online": ("Online", "bg-emerald-500"), "idle": ("Abwesend", "bg-amber-500"), "dnd": ("Bitte nicht stören", "bg-rose-500"), "offline": ("Offline", "bg-slate-400")}.get(str(m.status), ("Offline", "bg-slate-400"))
        roles = [r for r in m.roles if r.id in team_role_ids]
        top = max(roles, key=lambda r: r.position) if roles else m.top_role
        warns_count = 0
        try: warns_count = len(active_warns(user_entry(team_db, str(m.id))))
        except Exception: pass
        return f'''
        <a href="/member/{m.id}" class="pulse-glass pulse-hover rounded-2xl p-4 flex items-center gap-3 group">
            <div class="relative shrink-0">
                <img src="{esc(m.display_avatar.url)}" alt="" class="w-11 h-11 rounded-full border border-slate-200/60 dark:border-slate-700 shadow-sm object-cover">
                <span class="absolute -right-0.5 -bottom-0.5 w-3.5 h-3.5 rounded-full {presence[1]} border-2 border-white dark:border-[#0b0e14] pulse-dot"></span>
            </div>
            <div class="min-w-0 flex-1">
                <div class="font-semibold text-sm text-slate-900 dark:text-white truncate">{esc(m.display_name)}</div>
                <div class="text-[10px] text-slate-500 dark:text-slate-400 truncate">{esc(top.name)} · {presence[0]}{(" · " + duty_label) if duty_label else ""}</div>
            </div>
            <div class="flex items-center gap-1.5">
                {f'<span class="text-[10px] px-2 py-1 rounded-full border bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20">⚠ {warns_count}</span>' if warns_count else ''}
                <span class="text-slate-400 group-hover:text-indigo-500 transition">›</span>
            </div>
        </a>'''

    team_preview = "".join(person_card(m) for m in members[:10]) or '<div class="text-xs text-slate-400 py-8 text-center">Noch keine Teammitglieder konfiguriert.</div>'

    if shift_status == "offline":
        shift_action_html = '<button name="shift_action" value="start" class="w-full rounded-2xl bg-emerald-600 hover:bg-emerald-500 text-white py-3.5 font-bold shadow-lg shadow-emerald-900/10 transition">▶️ Schicht starten</button>'
    else:
        shift_action_html = (
            '<button name="shift_action" value="break" class="flex-1 rounded-2xl border border-amber-500/20 bg-amber-500/10 hover:bg-amber-500/15 text-amber-600 dark:text-amber-400 py-3 font-bold transition">⏸ Pause</button>'
            if shift_status == "online" else
            '<button name="shift_action" value="resume" class="flex-1 rounded-2xl border border-emerald-500/20 bg-emerald-500/10 hover:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 py-3 font-bold transition">▶️ Fortsetzen</button>'
        )
        shift_action_html += '<button name="shift_action" value="end" class="flex-1 rounded-2xl border border-rose-500/20 bg-rose-500/10 hover:bg-rose-500/15 text-rose-600 dark:text-rose-400 py-3 font-bold transition">⏹ Beenden</button>'

    manager_link = '<a href="/suite" class="inline-flex items-center justify-center rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2.5 text-xs font-bold transition">Führungs-Suite öffnen</a>' if (ctx.perms.get("can_promote") or ctx.perms.get("is_admin")) else ""
    action_links = [('/team', '👥', 'Teamliste'),('/tickets', '🎫', 'Tickets'),('/applications', '📝', 'Bewerbungen'),('/meetings', '🎙️', 'Meeting'),('/wiki', '📚', 'Wiki')]
    action_html = "".join(f'<a href="{href}" class="pulse-glass pulse-hover rounded-2xl p-4 text-center"><div class="text-2xl mb-1">{icon}</div><div class="text-[11px] font-bold text-slate-700 dark:text-slate-200">{label}</div></a>' for href,icon,label in action_links)

    attention_items = []
    if warning_5: attention_items.append(f'<a href="/team" class="flex items-center justify-between gap-3 p-3 rounded-xl bg-rose-500/5 border border-rose-500/15"><span class="text-xs text-rose-700 dark:text-rose-300">🚨 {warning_5} Teammitglied(er) bei 5/5 Warnungen</span><span class="text-[10px] font-bold">Prüfen →</span></a>')
    if urgent_tickets: attention_items.append(f'<a href="/tickets" class="flex items-center justify-between gap-3 p-3 rounded-xl bg-rose-500/5 border border-rose-500/15"><span class="text-xs text-rose-700 dark:text-rose-300">🚨 {len(urgent_tickets)} dringende Tickets offen</span><span class="text-[10px] font-bold">Öffnen →</span></a>')
    if overdue_tasks: attention_items.append(f'<a href="/tasks" class="flex items-center justify-between gap-3 p-3 rounded-xl bg-amber-500/5 border border-amber-500/15"><span class="text-xs text-amber-700 dark:text-amber-300">⏰ {len(overdue_tasks)} Aufgaben überfällig</span><span class="text-[10px] font-bold">Prüfen →</span></a>')
    if activity_open: attention_items.append(f'<a href="/team#activity" class="flex items-center justify-between gap-3 p-3 rounded-xl bg-indigo-500/5 border border-indigo-500/15"><span class="text-xs text-indigo-700 dark:text-indigo-300">✅ {activity_open} Activity-Check-Antwort(en) fehlen</span><span class="text-[10px] font-bold">Ansehen →</span></a>')
    if not health_ok: attention_items.append('<a href="/settings" class="flex items-center justify-between gap-3 p-3 rounded-xl bg-amber-500/5 border border-amber-500/15"><span class="text-xs text-amber-700 dark:text-amber-300">🛡️ Warnrollen-Konfiguration prüfen</span><span class="text-[10px] font-bold">Settings →</span></a>')
    attention_html = "".join(attention_items) or '<div class="text-xs text-slate-400 py-5 text-center">Keine offenen Handlungsfelder. Gute Arbeit.</div>'

    recent_events = []
    try:
        for ev in pulse_db.events(limit=6):
            label, actor, when = esc(ev.get("event_type") or "Ereignis"), esc(ev.get("actor_name") or "System"), esc(ev.get("created_at") or "")
            recent_events.append(f'<div class="flex items-start gap-3 py-2.5 border-b border-slate-200/60 dark:border-slate-800 last:border-0"><span class="w-8 h-8 rounded-xl bg-indigo-500/10 text-indigo-500 grid place-items-center shrink-0">•</span><div class="min-w-0"><div class="text-xs font-semibold text-slate-800 dark:text-slate-200 truncate">{label}</div><div class="text-[10px] text-slate-400 truncate">{actor} · {when}</div></div></div>')
    except Exception: recent_events=[]
    events_html="".join(recent_events) or '<div class="text-xs text-slate-400 py-5">Noch keine zentralen Events vorhanden.</div>'

    hero = f'''
    <section class="pulse-glass pulse-hero pulse-ring rounded-3xl p-6 md:p-8 mb-6 overflow-hidden relative">
        <div class="absolute -right-14 -top-16 w-44 h-44 rounded-full bg-indigo-500/10 blur-2xl"></div>
        <div class="relative flex flex-col xl:flex-row xl:items-center xl:justify-between gap-6">
            <div class="min-w-0">
                <div class="text-[11px] uppercase tracking-[.18em] font-black text-indigo-500 dark:text-indigo-400 mb-2">Pulse Command Center</div>
                <div class="flex flex-wrap items-center gap-3">
                    <h1 class="text-2xl md:text-3xl font-black text-slate-900 dark:text-white">Hallo, {esc(ctx.user.get("global_name") or ctx.user.get("username") or "Team")} 👋</h1>
                    <span class="inline-flex items-center gap-2 text-[11px] font-bold px-3 py-1.5 rounded-full border {state_cls}">{state_icon} {state_text}</span>
                </div>
                <p class="text-sm text-slate-500 dark:text-slate-400 mt-2 max-w-3xl">Die wichtigsten Team-, Dienst- und Moderationsdaten an einem Ort. Änderungen kommen direkt aus Discord und Pulse.</p>
                <div class="flex flex-wrap gap-2 mt-5">{action_html}{manager_link}</div>
            </div>
            <div class="shrink-0 rounded-2xl border border-white/30 dark:border-slate-700/50 bg-white/40 dark:bg-slate-900/30 p-4 min-w-[190px]">
                <div class="text-[10px] uppercase tracking-widest font-black text-slate-400 mb-1">Lokale Zeit</div>
                <div id="pulseLiveClock" class="text-3xl font-black font-mono text-slate-900 dark:text-white">{now_de().strftime("%H:%M:%S")}</div>
                <div class="text-[10px] text-slate-500 dark:text-slate-400 mt-1">{now_de().strftime("%d.%m.%Y")} · Europe/Berlin</div>
                <div class="mt-3 text-[10px] text-slate-400">📥 {unread} ungelesene Pulse-Nachrichten</div>
            </div>
        </div>
    </section>'''

    stats_html=f'''
    <div class="grid grid-cols-2 xl:grid-cols-4 gap-4 mb-6">
        <div class="pulse-glass pulse-hover rounded-2xl p-5"><div class="text-[10px] uppercase tracking-widest font-black text-slate-400">Team online</div><div class="text-3xl font-black mt-1">{presence_counts["online"]}</div><div class="text-[10px] text-slate-500 mt-1">{presence_counts["idle"]} abwesend · {presence_counts["dnd"]} DND</div></div>
        <div class="pulse-glass pulse-hover rounded-2xl p-5"><div class="text-[10px] uppercase tracking-widest font-black text-slate-400">Im Dienst</div><div class="text-3xl font-black mt-1">{duty_count}</div><div class="text-[10px] text-slate-500 mt-1">Schichtsystem</div></div>
        <div class="pulse-glass pulse-hover rounded-2xl p-5"><div class="text-[10px] uppercase tracking-widest font-black text-slate-400">Offene Tickets</div><div class="text-3xl font-black mt-1">{len(open_tickets)}</div><div class="text-[10px] text-slate-500 mt-1">{len(urgent_tickets)} dringend</div></div>
        <div class="pulse-glass pulse-hover rounded-2xl p-5"><div class="text-[10px] uppercase tracking-widest font-black text-slate-400">Bewerbungen</div><div class="text-3xl font-black mt-1">{pending_apps}</div><div class="text-[10px] text-slate-500 mt-1">wartend auf Prüfung</div></div>
    </div>'''

    body=f'''
    {hero}{stats_html}
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <div class="xl:col-span-8 space-y-6">
            <section class="pulse-glass rounded-3xl p-5 md:p-6">
                <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5"><div><div class="text-[10px] uppercase tracking-widest font-black text-indigo-500">Dein Dienst</div><h2 class="text-lg font-black text-slate-900 dark:text-white">Persönlicher Überblick</h2></div><span class="text-[11px] font-bold px-3 py-1.5 rounded-full border {("bg-emerald-500/10 text-emerald-600 border-emerald-500/20" if shift_status=="online" else "bg-amber-500/10 text-amber-600 border-amber-500/20" if shift_status=="break" else "bg-slate-500/10 text-slate-500 border-slate-500/20")}">{"🟢 IM DIENST" if shift_status=="online" else "☕ PAUSE" if shift_status=="break" else "⚪ OFFLINE"}</span></div>
                <div class="grid md:grid-cols-3 gap-4">
                    <div class="rounded-2xl bg-slate-50/80 dark:bg-slate-950/30 border border-slate-200/70 dark:border-slate-800 p-4"><div class="text-[10px] text-slate-400 uppercase tracking-widest font-black">Aktuelle Schicht</div><div class="text-2xl font-black mt-1" id="ultimateShiftTimer">{fmt_duration(current_elapsed)}</div><div class="text-[10px] text-slate-500 mt-1">Pausen werden nicht gutgeschrieben</div></div>
                    <div class="md:col-span-2 rounded-2xl bg-slate-50/80 dark:bg-slate-950/30 border border-slate-200/70 dark:border-slate-800 p-4"><div class="flex justify-between text-[10px] uppercase tracking-widest font-black text-slate-400"><span>Wochenziel</span><span>{weekly_hours:.1f}h / {weekly_goal:g}h</span></div><div class="mt-3 h-3 rounded-full bg-slate-200 dark:bg-slate-800 overflow-hidden"><div class="h-full rounded-full bg-gradient-to-r from-indigo-500 to-cyan-400 transition-all" style="width:{weekly_pct}%"></div></div><div class="flex justify-between mt-2 text-[10px] text-slate-500"><span>{weekly_pct}% erreicht</span><span>⏱ {fmt_duration(weekly_seconds)}</span></div></div>
                </div>
                <form action="/shift/action" method="post" class="flex flex-col sm:flex-row gap-2 mt-4">{shift_action_html}</form>
            </section>
            <section class="pulse-glass rounded-3xl p-5 md:p-6">
                <div class="flex items-center justify-between gap-3 mb-5"><div><div class="text-[10px] uppercase tracking-widest font-black text-indigo-500">Live Team</div><h2 class="text-lg font-black text-slate-900 dark:text-white">Wer ist gerade da?</h2></div><a href="/team" class="text-xs font-bold text-indigo-500 hover:underline">Gesamte Teamliste →</a></div>
                <div class="grid md:grid-cols-2 gap-3">{team_preview}</div>
                {f'<div class="text-[10px] text-slate-400 mt-4 text-center">+ {len(members)-10} weitere Teammitglieder</div>' if len(members)>10 else ''}
            </section>
        </div>
        <div class="xl:col-span-4 space-y-6">
            <section class="pulse-glass rounded-3xl p-5"><div class="flex items-center justify-between"><div><div class="text-[10px] uppercase tracking-widest font-black text-rose-500">Priorität</div><h2 class="text-lg font-black text-slate-900 dark:text-white">Handlungsbedarf</h2></div><span class="text-xl">{state_icon}</span></div><div class="mt-4 space-y-2">{attention_html}</div></section>
            <section class="pulse-glass rounded-3xl p-5"><div class="flex items-center justify-between mb-4"><div><div class="text-[10px] uppercase tracking-widest font-black text-indigo-500">Activity Check</div><h2 class="text-lg font-black text-slate-900 dark:text-white">Team-Rückmeldungen</h2></div><a href="/team#activity" class="text-xs font-bold text-indigo-500 hover:underline">Details →</a></div><div class="grid grid-cols-3 gap-2 text-center"><div class="rounded-2xl bg-emerald-500/5 border border-emerald-500/15 p-3"><div class="text-2xl font-black text-emerald-600">{activity_confirmed}</div><div class="text-[10px] text-slate-500">Bestätigt</div></div><div class="rounded-2xl bg-rose-500/5 border border-rose-500/15 p-3"><div class="text-2xl font-black text-rose-600">{activity_open}</div><div class="text-[10px] text-slate-500">Offen</div></div><div class="rounded-2xl bg-slate-500/5 border border-slate-500/15 p-3"><div class="text-2xl font-black">{len(members)}</div><div class="text-[10px] text-slate-500">Team</div></div></div><div class="mt-3 text-[10px] text-slate-500">{("Check vom " + esc(activity.get("check_date") or "")) if activity.get("check_id") else "Heute wurde noch kein Activity Check gestartet."}</div></section>
            <section class="pulse-glass rounded-3xl p-5"><div class="flex items-center justify-between mb-4"><div><div class="text-[10px] uppercase tracking-widest font-black text-indigo-500">System</div><h2 class="text-lg font-black text-slate-900 dark:text-white">Letzte Ereignisse</h2></div><a href="/search" class="text-xs font-bold text-indigo-500 hover:underline">Suche →</a></div>{events_html}</section>
        </div>
    </div>'''

    extra_js=f'''
    <script>
    (function(){{
        const base={int(current_elapsed)}; const running={"true" if shift_status=="online" else "false"}; const started=Date.now();
        const el=document.getElementById("ultimateShiftTimer");
        function tick(){{ if(!el)return; const s=base+(running?Math.floor((Date.now()-started)/1000):0); el.textContent=Math.floor(s/3600)+"h "+Math.floor((s%3600)/60)+"m "+(s%60)+"s"; }}
        tick(); if(running)setInterval(tick,1000);
    }})();
    </script>'''
    return render_page("Pulse Command Center", ctx, "ultimate", body, DASHBOARD_HEAD + extra_js)


# =============================================================
# MELOONLY – eigenständige Spielerakte mit vollständiger Verlaufssuche
# =============================================================
@app.get("/melonly", response_class=HTMLResponse)
async def melonly_alias(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    if not (ctx.perms.get("can_warn") or ctx.perms.get("can_add_notes") or ctx.perms.get("is_admin")):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")

    logs_db = load_json(LOGS_FILE, [])
    query = str(request.query_params.get("player_history") or "").strip()[:80]
    needle = query.lstrip("@").strip().casefold()
    edit_log_id = str(request.query_params.get("edit_log") or "")

    def player_name(log):
        return str(log.get("target_user") or "").strip().lstrip("@").casefold()

    def player_id(log):
        rid = str(log.get("roblox_id") or "").strip()
        return rid if rid.isdigit() else ""

    matches = []
    if needle:
        if needle.isdigit():
            candidates = [log for log in logs_db if player_id(log) == needle]
        else:
            candidates = []
        if not candidates:
            exact = [log for log in logs_db if player_name(log) == needle]
            candidates = exact or [log for log in logs_db if needle in player_name(log)]
        names = {player_name(log) for log in candidates if player_name(log)}
        ids = {player_id(log) for log in candidates if player_id(log)}
        matches = [
            log for log in logs_db
            if (player_name(log) and player_name(log) in names)
            or (player_id(log) and player_id(log) in ids)
        ]

    searched = bool(needle)
    selected_logs = list(reversed(matches if searched else logs_db[-50:]))
    return_to = f"/melonly?player_history={quote(query, safe='')}" if searched else "/melonly"
    search_param = f"player_history={quote(query, safe='')}&" if searched else ""

    ban_count = sum(1 for log in logs_db if log.get("type") in {"Ban", "Ban BOLO"})
    kick_count = sum(1 for log in logs_db if log.get("type") == "Kick")
    note_count = sum(1 for log in logs_db if log.get("type") == "Notiz")

    summary = ""
    if searched:
        names = sorted({str(log.get("target_user") or "").strip().lstrip("@") for log in matches if str(log.get("target_user") or "").strip()}, key=str.casefold)
        display_names = ", ".join("@" + name for name in names[:4]) or query
        if len(names) > 4:
            display_names += f" (+{len(names) - 4} weitere)"
        count_warn = sum(1 for log in matches if log.get("type") == "Warn")
        count_kick = sum(1 for log in matches if log.get("type") == "Kick")
        count_ban = sum(1 for log in matches if log.get("type") in {"Ban", "Ban BOLO"})
        count_note = sum(1 for log in matches if log.get("type") == "Notiz")
        summary = f"""
        <div class="{CARD} p-4 space-y-3">
          <div class="flex items-start justify-between gap-3">
            <div><h2 class="font-bold text-sm">🔎 Spieler-Verlauf</h2><p class="text-xs text-slate-500 mt-1">{esc(display_names)}</p></div>
            <span class="rounded-full px-3 py-1 bg-indigo-500/10 text-indigo-600 dark:text-indigo-300 text-xs font-bold">{len(matches)} Vorgänge</span>
          </div>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <div class="rounded-xl bg-slate-50 dark:bg-[#0b0e14] p-3"><div class="text-[10px] text-slate-500">Warns</div><div class="text-lg font-bold">{count_warn}</div></div>
            <div class="rounded-xl bg-slate-50 dark:bg-[#0b0e14] p-3"><div class="text-[10px] text-slate-500">Kicks</div><div class="text-lg font-bold">{count_kick}</div></div>
            <div class="rounded-xl bg-slate-50 dark:bg-[#0b0e14] p-3"><div class="text-[10px] text-slate-500">Bans / BOLOs</div><div class="text-lg font-bold">{count_ban}</div></div>
            <div class="rounded-xl bg-slate-50 dark:bg-[#0b0e14] p-3"><div class="text-[10px] text-slate-500">Notizen</div><div class="text-lg font-bold">{count_note}</div></div>
          </div>
          <p class="text-[11px] text-slate-500">Alle gespeicherten Treffer werden gezeigt – nicht nur die letzten 50 Einträge.</p>
        </div>"""

    logs_html = ""
    for log in selected_logs:
        log_type = str(log.get("type") or "Log")
        badge = LOG_TYPE_STYLE.get(log_type, "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 border-slate-200")
        target = str(log.get("target_user") or "Unbekannt")
        rid = str(log.get("roblox_id") or "N/A")
        log_id = str(log.get("id") or "")
        reason = str(log.get("reason") or "")
        moderator = str(log.get("moderator") or "Unbekannt")
        can_edit = can_manage_melonly_entry(ctx, log)
        edit_link = (
            f'<a href="/dashboard?{search_param}edit_log={quote(log_id, safe="")}#playerlog" class="text-indigo-500 hover:underline font-semibold">✏️ Bearbeiten</a>'
            if can_edit else ""
        )
        delete_form = f"""
          <form action="/log/delete" method="post" onsubmit="return confirm('Diesen Eintrag wirklich löschen?');">
            <input type="hidden" name="log_id" value="{esc(log_id)}">
            <input type="hidden" name="return_to" value="{esc(return_to)}">
            <button class="text-rose-500 hover:underline font-semibold">🗑️ Löschen</button>
          </form>""" if can_edit else ""
        logs_html += f"""
        <article class="{CARD} p-4 space-y-2">
          <div class="flex items-start justify-between gap-3 border-b border-slate-100 dark:border-slate-800/60 pb-2">
            <div class="flex items-center gap-2 min-w-0"><span class="px-2.5 py-0.5 rounded-full text-[10px] font-bold border {badge}">{esc(log_type)}</span><span class="text-xs font-semibold break-all">{esc(target)}</span></div>
            <span class="text-[10px] text-slate-400 font-mono shrink-0">{esc(log.get("created_at") or "Zeit unbekannt")}</span>
          </div>
          <div class="text-xs space-y-1"><div><span class="text-slate-400">Roblox ID:</span> <span class="font-mono">{esc(rid)}</span></div><div><span class="text-slate-400">Grund:</span> <span class="break-words">{esc(reason)}</span></div></div>
          <div class="text-[10px] text-slate-400 pt-1 border-t border-slate-100 dark:border-slate-800/40 flex justify-between items-center gap-3"><span>Moderator: {esc(moderator)}</span><span class="flex items-center gap-3">{edit_link}{delete_form}</span></div>
        </article>"""

    if not logs_html:
        if searched:
            logs_html = f'<div class="{CARD} p-8 text-center"><div class="text-3xl mb-2">🔎</div><div class="font-bold text-sm">Keine Einträge gefunden</div><p class="text-xs text-slate-500 mt-2">Zu „{esc(query)}“ wurden keine gespeicherten Vorgänge gefunden. Prüfe den @-Namen oder die Roblox-ID.</p></div>'
        else:
            logs_html = f'<div class="{CARD} p-8 text-center text-xs text-slate-500">Noch keine Spieler-Vorgänge gespeichert.</div>'

    body = f"""
    <div class="max-w-7xl mx-auto space-y-6">
      <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
        <div><div class="text-[11px] font-semibold tracking-wide uppercase text-indigo-500 mb-1">Pulse TeamOS · Moderation</div><h1 class="text-2xl font-bold text-slate-900 dark:text-white">🛡️ Melonly</h1><p class="text-sm text-slate-500 dark:text-slate-400 mt-1">Roblox-Spieler direkt suchen, ID übernehmen und Moderationsvorgänge sauber dokumentieren.</p></div>
        <a href="/ultimate" class="inline-flex items-center justify-center px-3 py-2 rounded-xl border border-slate-200 dark:border-slate-700 text-xs font-semibold">← Übersicht</a>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        {stat_card("🚫", "Bans / BOLOs", ban_count, "rose")}{stat_card("🚪", "Kicks", kick_count, "amber")}{stat_card("📝", "Notizen", note_count, "indigo")}{stat_card("📚", "Alle Einträge", len(logs_db), "purple")}
      </div>
      <div class="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <section class="xl:col-span-4">
          <div class="{CARD} p-5 space-y-4" id="player-create">
            <div><h2 class="text-base font-bold">🛡️ Spieler eintragen</h2><p class="text-xs text-slate-500 mt-1">Der Eintrag wird im Pulse-Moderationsprotokoll gespeichert.</p></div>
            <form action="/log/create" method="post" class="space-y-3 text-xs">
              <input type="hidden" name="return_to" value="{esc(return_to)}">
              <div><label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Roblox Username *</label><input type="text" id="robloxUsernameInput" name="target_user" maxlength="50" list="robloxUserSuggestions" autocomplete="off" oninput="lookupRobloxUser(this.value)" onfocus="searchRobloxUsers(this.value)" placeholder="@Username" required class="{INPUT}"><div id="robloxSuggestionPanel" class="hidden mt-2 overflow-hidden rounded-xl border border-indigo-200 dark:border-indigo-800/70 bg-white dark:bg-[#10141f] shadow-sm" role="listbox" aria-label="Roblox-Spielervorschläge"></div><datalist id="robloxUserSuggestions"></datalist></div>
              <div id="robloxUserPreview" class="hidden"></div>
              <div><label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Roblox Player ID</label><input type="text" name="roblox_id" id="robloxIdInput" placeholder="z. B. 12345678" class="{INPUT}"></div>
              <div><label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Aktion</label><select name="log_type" required class="{INPUT}"><option value="Warn">⚠️ Warn</option><option value="Kick">🚪 Kick</option><option value="Ban">🚫 Ban</option><option value="Ban BOLO">🚨 Ban BOLO</option><option value="Notiz">📝 Notiz</option></select></div>
              <div><label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Grund</label><textarea name="reason" maxlength="1000" placeholder="Begründung / Notiz…" required class="{INPUT} h-24"></textarea></div>
              <button class="w-full {BTN} py-3">✓ Spieler-Vorgang eintragen</button>
              <p class="text-[10px] leading-relaxed text-slate-500">Ban/Kick sind hier Protokoll-Aktionen und keine automatische Roblox-API-Sperre.</p>
            </form>
          </div>
        </section>
        <section class="xl:col-span-8 space-y-4" id="playerlog">
          <div class="flex items-center justify-between gap-3"><div><h2 class="text-lg font-bold">{"Spieler-Verlauf" if searched else "Letzte Spieler-Vorgänge"}</h2><p class="text-xs text-slate-500 mt-1">{"Alle passenden Einträge" if searched else "Die Übersicht zeigt die letzten 50 Vorgänge."}</p></div><a href="/export/logs" class="px-3 py-2 rounded-xl border border-slate-200 dark:border-slate-700 text-xs font-semibold">📊 CSV</a></div>
          <form action="/melonly" method="get" class="flex flex-col sm:flex-row gap-2"><input type="search" name="player_history" maxlength="80" value="{esc(query)}" placeholder="Roblox-@Name oder Spieler-ID eingeben…" required class="{INPUT} flex-1"><button type="submit" class="{BTN} px-4 py-2.5 text-xs shrink-0">🔎 Spieler suchen</button>{('<a href="/melonly#playerlog" class="px-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 text-xs font-semibold text-center">Suche zurücksetzen</a>' if searched else '')}</form>
          {summary}
          <div class="space-y-3 max-h-[calc(100vh-240px)] overflow-y-auto pr-1">{logs_html}</div>
          <p class="text-[10px] text-slate-400">{"Alle " + str(len(matches)) + " gespeicherten Vorgänge des Suchtreffers werden gezeigt." if searched else "Nutze die Suche, um den vollständigen Verlauf eines einzelnen Spielers aufzurufen."}</p>
        </section>
      </div>
    </div>
    """
    return render_page("Melonly", ctx, "melonly", body, DASHBOARD_HEAD)

# =============================================================
# ACTION: SCHICHT-SYSTEM (Start / Pause / Fortsetzen / Ende)
# =============================================================
def shift_end_open_items_text(mod_id: str, now: datetime) -> str:
    """Short summary of open work at shift end: claimed open tickets and overdue tasks."""
    tickets = [t for t in pulse_db.list_tickets(claimed_by_id=mod_id, limit=50) if t.get("status") != "closed"]
    overdue = 0
    for task in pulse_db.list_tasks(assignee_id=mod_id, limit=100):
        if task.get("status") in ("done", "archived") or not task.get("due_at"):
            continue
        try:
            if parse_dt(str(task["due_at"]).replace("Z", "+00:00")) < now:
                overdue += 1
        except (TypeError, ValueError):
            continue
    parts = []
    if tickets:
        parts.append(f"{len(tickets)} übernommene Ticket{'s' if len(tickets) != 1 else ''} noch offen – bei Bedarf freigeben oder übergeben")
    if overdue:
        parts.append(f"{overdue} überfällige Aufgabe{'n' if overdue != 1 else ''}")
    return (" Noch offen: " + "; ".join(parts) + ".") if parts else " Keine offenen Tickets oder überfälligen Aufgaben – sauberer Abschluss! ✅"


SHIFT_ACTIONS = ("start", "break", "resume", "end")


def apply_shift_action(mod_id, shift_action: str) -> tuple[bool, str]:
    """BOT-03: start/pause/resume/end one person's shift – the single logic for the dashboard, the Discord
    panel and /dienst. Synchronous on purpose: load and save happen without an await in between."""
    mod_id = str(mod_id)
    shifts_db = load_shifts()
    team_db = load_json(DATA_FILE, {})
    active = shifts_db["active_shifts"]
    shift = active.get(mod_id)
    now = now_de()

    if shift_action == "start":
        if shift:
            return False, "Du hast bereits eine laufende Schicht."
        if len(active_warns(user_entry(team_db, mod_id))) >= 5:
            return False, "Schicht-Start gesperrt: Du hast bereits 5 aktive Verwarnungen!"
        # An active leave is informational, not a lock: team members may
        # voluntarily work during an LOA without deleting or shortening it.
        loa = get_loas().get(mod_id)
        leave_active = bool(loa and loa.get("active"))
        active[mod_id] = {
            "status": "online",
            "started_at_iso": now.isoformat(),
            "segment_start_iso": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "accumulated_seconds": 0,
        }
        msg = (
            f"Schicht gestartet. Deine Abmeldung bleibt bis {fmt_date(loa['bis'])} hinterlegt "
            "und wird nicht automatisch beendet."
            if leave_active
            else "Schicht gestartet – viel Erfolg!"
        )

    elif shift_action == "break" and shift and shift.get("status") == "online":
        shift["accumulated_seconds"] = shift_elapsed(shift)
        shift["status"] = "break"
        shift["segment_start_iso"] = None
        msg = "Pause gestartet."

    elif shift_action == "resume" and shift and shift.get("status") == "break":
        shift["status"] = "online"
        shift["segment_start_iso"] = now.isoformat()
        msg = "Schicht fortgesetzt."

    elif shift_action == "end" and shift:
        duration = shift_elapsed(shift)
        shifts_db["history"].append({
            "mod_id": mod_id, "date": shift.get("date"), "duration_seconds": duration,
            "started_at_iso": shift.get("started_at_iso"), "ended_at_iso": now.isoformat(),
        })
        del active[mod_id]
        msg = f"Schicht beendet – {fmt_duration(duration)} gutgeschrieben."
        # Handover hint (never blocks ending the shift): what is still open for this person.
        try:
            msg += shift_end_open_items_text(mod_id, now)
        except Exception as exc:
            print(f"⚠️ Schichtende-Übersicht nicht verfügbar: {type(exc).__name__}")
    else:
        return False, "Diese Aktion ist gerade nicht möglich."

    save_json(SHIFTS_FILE, shifts_db)
    return True, msg


def shift_status_text(mod_id) -> str:
    """One line for /dienst status and the panel answer."""
    shifts = load_shifts()
    shift = shifts.get("active_shifts", {}).get(str(mod_id))
    week = fmt_duration(calculate_weekly_seconds(str(mod_id), shifts.get("history", []), shifts.get("active_shifts", {})))
    if not shift:
        return f"Du bist nicht im Dienst. Diese Woche: {week}."
    state = "im Dienst" if shift.get("status") == "online" else "in der Pause"
    return f"Du bist {state} seit {fmt_duration(shift_elapsed(shift))}. Diese Woche: {week}."


async def sync_duty_after_shift(bot, guild, mod_id) -> None:
    """Voice-Support-Bereitschaft is a view of this same shift state, not a second independent check-in.
    Keep its Discord role and status post in sync (dashboard and Discord use this)."""
    try:
        voice_support = bot.get_cog("VoiceSupport") if bot else None
        if voice_support and guild:
            await voice_support.sync_member_duty_role(guild, str(mod_id))
            await voice_support._refresh_duty_status(guild)
    except Exception as exc:
        print(f"⚠️ Voice-Support: Bereitschaft konnte nach Schichtaktion nicht aktualisiert werden: {exc}")


@app.post("/shift/action")
async def handle_shift_action(request: Request, shift_action: str = Form(...), return_to: str = Form("/dashboard"),
                              user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    # Only known local pages; never redirect to user-supplied external URLs.
    target_page = return_to if return_to in {"/dashboard", "/me"} else "/dashboard"
    ok, msg = apply_shift_action(ctx.user["id"], shift_action)  # BOT-03: same logic as the Discord panel
    if not ok:
        return back(target_page, msg, False)
    await sync_duty_after_shift(getattr(request.app.state, "bot", None), ctx.guild, ctx.user["id"])
    return back(target_page, msg)


# =============================================================
# ACTION: LOGS ERSTELLEN & LÖSCHEN, CSV-EXPORT
# =============================================================
def safe_log_return_to(value: str) -> str:
    """Allow only local dashboard/Melonly return paths; reject external redirects."""
    value = str(value or "/dashboard").strip()
    if value in {"/dashboard", "/melonly"}:
        return value
    for base in ("/dashboard", "/melonly"):
        prefix = base + "?player_history="
        if value.startswith(prefix):
            raw_query = value.split("player_history=", 1)[1].split("#", 1)[0].split("&", 1)[0]
            if re.fullmatch(r"[A-Za-z0-9_%@._+-]{1,240}", raw_query):
                return f"{prefix}{raw_query}" + ("#playerlog" if base == "/dashboard" else "")
    return "/dashboard"


@app.post("/log/create")
async def create_log(request: Request, target_user: str = Form(...), roblox_id: str = Form("N/A"),
                     log_type: str = Form(...), reason: str = Form(...),
                     return_to: str = Form("/dashboard"), user_session: str = Cookie(None)):
    return_to = safe_log_return_to(return_to)
    ctx = auth(request, user_session)
    target_user = target_user.strip()[:50]
    reason = reason.strip()[:1000]
    roblox_id = roblox_id.strip()
    if log_type not in VALID_LOG_TYPES or not target_user or not reason:
        return back(return_to, "Ungültige Eingabe.", False)
    if not can_manage_melonly_type(
        log_type,
        can_warn=bool(ctx.perms.get("can_warn")),
        can_add_notes=bool(ctx.perms.get("can_add_notes")),
        can_moderate_kicks=bool(ctx.perms.get("can_moderate_kicks")),
        can_moderate_bans=bool(ctx.perms.get("can_moderate_bans")),
        can_moderate_bolo=bool(ctx.perms.get("can_moderate_bolo")),
        manager=bool(ctx.perms.get("is_admin")),
    ):
        raise HTTPException(status_code=403, detail="Für diesen Melonly-Aktionstyp fehlt dir die gesonderte Berechtigung.")
    if not re.fullmatch(r"\d{1,15}", roblox_id):
        roblox_id = "N/A"

    logs_db = load_json(LOGS_FILE, [])
    logs_db.append({
        "id": f"log_{uuid.uuid4().hex[:6]}",
        "target_user": target_user,
        "roblox_id": roblox_id,
        "type": log_type,
        "reason": reason,
        "moderator": ctx.user.get("global_name", "Dashboard Admin"),
        "moderator_id": ctx.user["id"],
        "created_at": now_de().strftime("%d.%m.%Y %H:%M"),
    })
    save_json(LOGS_FILE, logs_db)
    # Melonly bleibt ausschließlich im Panel. Es gibt dafür bewusst kein Discord-Team-Update.
    log_audit(
        ctx.user.get("global_name"),
        ctx.user["id"],
        "Melonly-Eintrag erstellt",
        f"Spieler: {target_user} ({log_type})",
    )
    return back(return_to, f"{log_type}-Log für {target_user} gespeichert.")


@app.post("/log/edit")
async def edit_log(
    request: Request,
    log_id: str = Form(...),
    target_user: str = Form(...),
    roblox_id: str = Form("N/A"),
    log_type: str = Form(...),
    reason: str = Form(...),
    return_to: str = Form("/dashboard"),
    user_session: str = Cookie(None),
):
    return_to = safe_log_return_to(return_to)
    ctx = auth(request, user_session)
    logs_db = load_json(LOGS_FILE, [])
    entry = next((l for l in logs_db if l.get("id") == log_id), None)
    if not entry:
        return back(return_to, "Log nicht gefunden.", False)

    if not can_manage_melonly_entry(ctx, entry, target_type=log_type):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung, diesen Melonly-Eintrag zu bearbeiten.")

    target_user = target_user.strip()[:50]
    reason = reason.strip()[:1000]
    roblox_id = roblox_id.strip()
    if log_type not in VALID_LOG_TYPES or not target_user or not reason:
        return back(return_to, "Ungültige Eingabe.", False)
    if not re.fullmatch(r"\d{1,15}", roblox_id):
        roblox_id = "N/A"

    old_target = str(entry.get("target_user") or "")
    old_type = str(entry.get("type") or "Log")
    entry.update({
        "target_user": target_user,
        "roblox_id": roblox_id,
        "type": log_type,
        "reason": reason,
        "edited_at": now_de().strftime("%d.%m.%Y %H:%M"),
        "edited_by": ctx.user.get("global_name") or ctx.user.get("username") or "Team",
    })
    save_json(LOGS_FILE, logs_db)
    log_audit(
        ctx.user.get("global_name"),
        ctx.user["id"],
        "Melonly-Eintrag bearbeitet",
        f"{log_id}: {old_target} ({old_type}) -> {target_user} ({log_type})",
    )
    return back(return_to, "Melonly-Eintrag wurde bearbeitet.")


@app.post("/log/delete")
async def delete_log(
    request: Request,
    log_id: str = Form(...),
    return_to: str = Form("/dashboard"),
    user_session: str = Cookie(None),
):
    return_to = safe_log_return_to(return_to)
    ctx = auth(request, user_session)
    logs_db = load_json(LOGS_FILE, [])
    entry = next((l for l in logs_db if l.get("id") == log_id), None)
    if not entry:
        return back(return_to, "Log nicht gefunden.", False)
    if not can_manage_melonly_entry(ctx, entry):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung, diesen Melonly-Eintrag zu löschen.")
    save_json(LOGS_FILE, [l for l in logs_db if l.get("id") != log_id])
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Log Gelöscht",
              f"{entry.get('target_user')} ({entry.get('type')}) – ID {log_id}")
    return back(return_to, "Melonly-Eintrag gelöscht.")


def can_manage_melonly_entry(ctx, entry: dict, target_type: str | None = None) -> bool:
    """Enforce per-action Melonly permissions in UI and every write endpoint."""
    perms = ctx.perms
    manager = bool(perms.get("is_admin"))
    options = {
        "can_warn": bool(perms.get("can_warn")),
        "can_add_notes": bool(perms.get("can_add_notes")),
        "can_moderate_kicks": bool(perms.get("can_moderate_kicks")),
        "can_moderate_bans": bool(perms.get("can_moderate_bans")),
        "can_moderate_bolo": bool(perms.get("can_moderate_bolo")),
        "manager": manager,
    }
    existing_type = str(entry.get("type") or "Log")
    if not can_manage_melonly_type(existing_type, **options):
        return False
    if target_type is not None and not can_manage_melonly_type(target_type, **options):
        return False
    return True


def csv_safe(value) -> str:
    """Schützt vor Excel-Formel-Injection (=, +, -, @)."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@app.get("/export/logs")
async def export_logs(request: Request, user_session: str = Cookie(None)):
    auth(request, user_session, perm="can_view_analytics")
    output = io.StringIO()
    output.write("\ufeff")  # BOM: Excel zeigt Umlaute korrekt an
    writer = csv.writer(output, delimiter=';')
    writer.writerow(["ID", "Datum", "Typ", "Ziel-Nutzer", "Roblox ID", "Moderator", "Grund"])
    for log in load_json(LOGS_FILE, []):
        writer.writerow([csv_safe(log.get(k)) for k in
                         ("id", "created_at", "type", "target_user", "roblox_id", "moderator", "reason")])
    filename = f"punishment_logs_{now_de().strftime('%Y-%m-%d')}.csv"
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f"attachment; filename={filename}"})


# =============================================================
# ACTIVITY CHECK: STATUS DIREKT IN DER TEAMLISTE
# =============================================================
def get_activity_today(guild_id: int):
    """Lädt den heutigen Activity Check inklusive des eingefrorenen Teilnehmer-Snapshots."""
    today = now_de().date().isoformat()
    empty = {"check_id": None, "check_date": today, "confirmed": set(), "eligible": None}
    if not os.path.exists(ACTIVITY_DB):
        return empty
    try:
        with closing(sqlite3.connect(ACTIVITY_DB)) as conn, conn:
            row = conn.execute(
                "SELECT id FROM checks WHERE guild_id=? AND check_date=?",
                (guild_id, today),
            ).fetchone()
            if not row:
                return empty

            check_id = int(row[0])
            confirmed = {
                int(x[0])
                for x in conn.execute(
                    "SELECT user_id FROM responses WHERE check_id=?",
                    (check_id,),
                ).fetchall()
            }
            snapshot_rows = conn.execute(
                "SELECT user_id FROM check_members WHERE check_id=?",
                (check_id,),
            ).fetchall()
            columns = {str(x[1]) for x in conn.execute("PRAGMA table_info(checks)").fetchall()}
            snapshot_version = 0
            if "snapshot_version" in columns:
                version_row = conn.execute(
                    "SELECT snapshot_version FROM checks WHERE id=?", (check_id,)
                ).fetchone()
                snapshot_version = int(version_row[0] or 0) if version_row else 0
            # Empty set means a new check intentionally recorded an empty snapshot;
            # None is reserved for legacy checks that predate immutable snapshots.
            eligible = (
                {int(x[0]) for x in snapshot_rows}
                if snapshot_rows
                else (set() if snapshot_version >= 1 else None)
            )
            return {
                "check_id": check_id,
                "check_date": today,
                "confirmed": confirmed,
                "eligible": eligible,
                "snapshot_version": snapshot_version,
            }
    except sqlite3.Error as exc:
        print(f"Activity-Check-Status konnte Datenbank nicht lesen: {type(exc).__name__}: {exc}")
        return empty


@app.get("/api/team/activity")
async def team_activity_api(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    activity = get_activity_today(ctx.guild.id)
    role_ids = ctx.config.get("team_role_ids", [])
    members = [
        m for m in ctx.guild.members
        if not m.bot and any(r.id in role_ids for r in m.roles)
    ]
    current_ids = {m.id for m in members}
    eligible = activity.get("eligible")
    statuses = {}
    for m in members:
        if not activity["check_id"]:
            statuses[str(m.id)] = "none"
        elif eligible is not None and m.id not in eligible:
            statuses[str(m.id)] = "not_in_snapshot"
        else:
            statuses[str(m.id)] = "confirmed" if m.id in activity["confirmed"] else "open"

    confirmed = sum(1 for m in members if statuses.get(str(m.id)) == "confirmed")
    eligible_ids = eligible if eligible is not None else current_ids
    open_count = (
        sum(1 for uid in eligible_ids if uid in current_ids and uid not in activity["confirmed"])
        if activity["check_id"] else 0
    )
    return JSONResponse({
        "ok": True,
        "check_id": activity["check_id"],
        "date": activity["check_date"],
        "confirmed": confirmed,
        "open": open_count,
        "team_total": len(members),
        "snapshot_total": len(eligible) if eligible is not None else len(members),
        "statuses": statuses,
    })

@app.get("/api/team/warn-roles")
async def warn_roles_api(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm="can_warn")
    rows = warning_role_health(ctx.guild, ctx.config)
    return JSONResponse({
        "ok": all(x["ok"] for x in rows),
        "sync_enabled": SYNC_WARN_ROLES,
        "roles": [
            {"level": x["level"], "id": x["id"], "name": x["role"].name if x["role"] else None,
             "ok": x["ok"], "detail": x["detail"]}
            for x in rows
        ],
    })


# =============================================================
# PULSE-EIGENES TICKETSYSTEM (Dashboard)
# =============================================================
# =============================================================
# PULSE-EIGENES TICKETSYSTEM (Dashboard)
# =============================================================
PULSE_TICKET_DEFAULT_PANEL_CHANNEL_IDS = _pulse_ids.TICKET_PANEL_CHANNEL_IDS
PULSE_TICKET_OPEN_LOG_CHANNEL_ID = _pulse_ids.TICKET_OPEN_LOG_CHANNEL_ID
PULSE_TICKET_CLAIM_LOG_CHANNEL_ID = _pulse_ids.TICKET_CLAIM_LOG_CHANNEL_ID
PULSE_TICKET_META = {
    "support": ("Allgemeiner Support", "🎫"),
    "bug": ("Bug melden", "⚙️"),
    "unban": ("Entbannungsantrag", "📩"),
    "highteam": ("Highteam Ticket", "💢"),
    "leadership": ("Führungsebene Ticket", "🐬"),
}
PULSE_TICKET_PRIORITY_LABELS = {
    "low": "⚪ Niedrig",
    "normal": "🟢 Normal",
    "high": "🟠 Hoch",
    "urgent": "🚨 Dringend",
}
PULSE_TICKET_PRIORITY_ORDER = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


def pulse_ticket_config() -> dict:
    config = pulse_db.get_setting("ticket_system_config", {}) or {}
    return config if isinstance(config, dict) else {}


def pulse_ticket_category_registry() -> dict:
    """Return built-in plus configurable custom Pulse ticket categories."""
    registry = {
        "support": {"id": "support", "name": "Allgemeiner Support", "emoji": "🎫", "description": "Fragen, Hilfe und allgemeine Anliegen", "prefix": "support", "max_open": 10, "group": "team", "role_ids": [], "parent_category_id": 0, "opening_message": "", "active": True, "builtin": True},
        "bug": {"id": "bug", "name": "Bug melden", "emoji": "⚙️", "description": "Technische Fehler oder Server-Bugs melden", "prefix": "bug", "max_open": 5, "group": "team", "role_ids": [], "parent_category_id": 0, "opening_message": "", "active": True, "builtin": True},
        "unban": {"id": "unban", "name": "Entbannungsantrag", "emoji": "📩", "description": "Antrag auf Überprüfung einer Sanktion", "prefix": "unban", "max_open": 5, "group": "team", "role_ids": [], "parent_category_id": 0, "opening_message": "", "active": True, "builtin": True},
        "highteam": {"id": "highteam", "name": "Highteam Ticket", "emoji": "💢", "description": "Vertrauliche Angelegenheiten für das Highteam", "prefix": "highteam", "max_open": 3, "group": "highteam", "role_ids": [], "parent_category_id": 0, "opening_message": "", "active": True, "builtin": True},
        "leadership": {"id": "leadership", "name": "Führungsebene Ticket", "emoji": "🐬", "description": "Vertrauliche Angelegenheiten für die Führungsebene", "prefix": "leadership", "max_open": 3, "group": "leadership", "role_ids": [], "parent_category_id": 0, "opening_message": "", "active": True, "builtin": True},
    }
    saved = pulse_ticket_config().get("ticket_categories", [])
    for item in saved if isinstance(saved, list) else []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "").strip()
        if not key or not re.fullmatch(r"[a-z0-9_-]{1,80}", key):
            continue
        if key not in registry and not key.startswith("custom_"):
            continue
        base = registry.get(key, {"id": key, "name": "", "emoji": "🎫", "description": "", "prefix": key, "max_open": 0, "group": "team", "role_ids": [], "parent_category_id": 0, "opening_message": "", "active": True, "builtin": False})
        base.update(item)
        base["id"] = key
        base["builtin"] = key in {"support", "bug", "unban", "highteam", "leadership"}
        registry[key] = base
    return registry


def pulse_ticket_panel_channel_ids() -> tuple[int, ...]:
    raw = pulse_ticket_config().get("panel_channel_ids", list(PULSE_TICKET_DEFAULT_PANEL_CHANNEL_IDS))
    if not isinstance(raw, list):
        raw = list(PULSE_TICKET_DEFAULT_PANEL_CHANNEL_IDS)
    ids = []
    for value in raw[:25]:
        try:
            channel_id = int(value)
        except (TypeError, ValueError):
            continue
        if channel_id > 0 and channel_id not in ids:
            ids.append(channel_id)
    return tuple(ids) or PULSE_TICKET_DEFAULT_PANEL_CHANNEL_IDS


def pulse_ticket_log_channel_id(kind: str) -> int:
    config = pulse_ticket_config()
    key = "ticket_open_log_channel_id" if kind == "created" else "ticket_claim_log_channel_id"
    fallback = PULSE_TICKET_OPEN_LOG_CHANNEL_ID if kind == "created" else PULSE_TICKET_CLAIM_LOG_CHANNEL_ID
    try:
        return int(config.get(key) or fallback)
    except (TypeError, ValueError):
        return fallback


def _ticket_dashboard_embed(ticket: dict, actor, kind: str, guild) -> discord.Embed:
    label, emoji = PULSE_TICKET_META.get(str(ticket.get("category")), ("Ticket", "🎫"))
    titles = {
        "created": "🎫 Neues Ticket geöffnet",
        "claimed": "📥 Ticket übernommen",
        "unclaimed": "↩️ Ticket freigegeben",
        "closed": "🔒 Ticket geschlossen",
        "reopened": "🔓 Ticket erneut geöffnet",
        "priority": "🚦 Ticket-Priorität geändert",
    }
    descriptions = {
        "created": "Ein neues Ticket wurde über Pulse erstellt.",
        "claimed": "Ein Teammitglied hat ein Pulse-Ticket beansprucht.",
        "unclaimed": "Ein beanspruchtes Ticket wurde freigegeben.",
        "closed": "Ein Pulse-Ticket wurde geschlossen.",
        "reopened": "Ein zuvor geschlossenes Ticket wurde erneut geöffnet.",
        "priority": "Die Priorität eines Pulse-Tickets wurde geändert.",
    }
    channel_id = str(ticket.get("channel_id") or "")
    is_claimed = bool(ticket.get("claimed_by_id"))
    embed = discord.Embed(
        title=titles.get(kind, "🎫 Pulse-Ticket aktualisiert"),
        description=descriptions.get(kind, "Ein Pulse-Ticket wurde aktualisiert."),
        color=_pulse_color("success") if kind in {"created", "reopened"} else (
            _pulse_color("danger") if kind == "closed" else (
                _pulse_color("warning") if kind == "priority" else _pulse_color("info")
            )
        ),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Ticket-ID", value=str(ticket.get("id") or "unbekannt")[:100], inline=True)
    embed.add_field(name="Kategorie", value=f"{emoji} {label}", inline=True)
    embed.add_field(name="Status", value="🔒 Geschlossen" if ticket.get("status") == "closed" else ("🔵 In Bearbeitung" if is_claimed else "🟢 Offen"), inline=True)
    embed.add_field(name="Priorität", value=PULSE_TICKET_PRIORITY_LABELS.get(str(ticket.get("priority") or "normal"), "🟢 Normal"), inline=True)
    creator = str(ticket.get("user_name") or "Unbekannt")[:200]
    creator_id = str(ticket.get("user_id") or "")
    embed.add_field(name="Ersteller", value=creator + (f" ({creator_id})" if creator_id else ""), inline=True)
    if ticket.get("claimed_by_name"):
        embed.add_field(name="Aktueller Bearbeiter", value=str(ticket.get("claimed_by_name"))[:200], inline=True)
    if actor:
        embed.add_field(name="Aktion von", value=f"{actor.display_name} ({actor.id})", inline=True)
    if ticket.get("rating"):
        embed.add_field(name="Bewertung", value=f"{ticket['rating']}/5", inline=True)
    if ticket.get("close_reason") and ticket.get("status") == "closed":
        embed.add_field(name="Schließgrund", value=str(ticket.get("close_reason"))[:900], inline=False)
    if channel_id.isdigit():
        embed.add_field(name="Ticket-Kanal", value=f"<#{channel_id}>", inline=True)
        embed.url = f"https://discord.com/channels/{guild.id}/{channel_id}"
    embed.set_footer(text="Pulse Ticket-System • Ereignisprotokoll")
    return embed


async def _send_pulse_ticket_log(bot, guild, kind: str, ticket: dict, actor=None):
    if kind not in {"created", "reopened", "claimed"}:
        return False
    destination_id = pulse_ticket_log_channel_id("created" if kind in {"created", "reopened"} else "claimed")
    destination = guild.get_channel(destination_id)
    if destination is None:
        try:
            destination = await bot.fetch_channel(destination_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            destination = None
    if not isinstance(destination, discord.TextChannel) or destination.guild.id != guild.id:
        print(f"⚠️ Pulse-Tickets: Protokollkanal {destination_id} nicht erreichbar.")
        return False
    try:
        await destination.send(
            embed=_ticket_dashboard_embed(ticket, actor, kind, guild),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"⚠️ Pulse-Tickets: Protokoll in {destination_id} fehlgeschlagen ({type(exc).__name__}).")
        return False


async def _refresh_pulse_ticket_discord_state(channel, ticket: dict):
    """Keep the ticket's Discord embed/button state aligned with dashboard actions."""
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        from cogs.tickets import move_ticket_channel, refresh_ticket_embed
        await refresh_ticket_embed(channel, ticket)
        if ticket.get("status") != "closed":
            await move_ticket_channel(channel, ticket, claimed=bool(ticket.get("claimed_by_id")))
    except Exception as exc:
        print(f"⚠️ Pulse-Tickets: Discord-Anzeige konnte nicht mit dem Dashboard synchronisiert werden ({type(exc).__name__}).")


def _ticket_channel_options(guild, selected_ids) -> str:
    selected = {int(item) for item in selected_ids if str(item).isdigit()}
    return "".join(
        f'<option value="{channel.id}" {"selected" if channel.id in selected else ""}>#{html.escape(channel.name)} · {channel.id}</option>'
        for channel in sorted(guild.text_channels, key=lambda item: item.position)
    )


def _ticket_role_options(guild, field_name: str, selected_ids) -> str:
    selected = {int(item) for item in selected_ids if str(item).isdigit()}
    roles = [role for role in guild.roles if not role.is_default() and not role.managed]
    return "".join(
        f'<option value="{role.id}" {"selected" if role.id in selected else ""}>{html.escape(role.name)} · {role.id}</option>'
        for role in sorted(roles, key=lambda item: item.position, reverse=True)
    )


@app.get("/tickets/panels", response_class=HTMLResponse)
@app.get("/tickets", response_class=HTMLResponse)
async def pulse_tickets_dashboard(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm="can_manage_tickets")
    if request.url.path == "/tickets/panels" and not ctx.perms.get("is_admin"):
        return HTMLResponse(
            content="<!doctype html><html lang='de'><meta charset='utf-8'><title>Keine Berechtigung</title><body style='font-family:system-ui;background:#0b1020;color:#e8eefb;padding:40px'><h1>Kein Zugriff auf den Panel-Builder</h1><p>Nur Dashboard-Administratoren dürfen Ticket-Panels und Kategorien verwalten.</p><p><a style='color:#b9c7ff' href='/tickets'>Zur Ticket-Zentrale</a></p></body></html>",
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    if request.url.path == "/tickets/panels":
        # Visual builder (live Discord preview, images, welcome texts, forms, automation).
        from pulse_ticket_builder import render_ticket_builder
        return render_ticket_builder(request, ctx)
    guild = ctx.guild
    settings = pulse_ticket_config()
    category_registry = pulse_ticket_category_registry()
    items = [
        item for item in pulse_db.list_tickets(limit=2000)
        if str(item.get("guild_id")) == str(guild.id)
    ]
    items.sort(key=lambda item: (
        PULSE_TICKET_PRIORITY_ORDER.get(str(item.get("priority") or "normal"), 2),
        str(item.get("opened_at") or ""),
    ), reverse=False)
    open_items = [item for item in items if item.get("status") != "closed" and not item.get("claimed_by_id")]
    claimed_items = [item for item in items if item.get("status") != "closed" and item.get("claimed_by_id")]
    closed_items = [item for item in items if item.get("status") == "closed"]
    urgent_count = sum(1 for item in items if item.get("status") != "closed" and item.get("priority") == "urgent")
    rated_count = sum(1 for item in items if item.get("rating"))

    filter_name = request.query_params.get("status", "all")
    if filter_name not in {"open", "claimed", "closed", "all", "rated"}:
        filter_name = "open"
    query = str(request.query_params.get("q", "")).strip()[:100].casefold()
    category_filter = str(request.query_params.get("category", "")).strip()
    groups = {
        "open": open_items,
        "claimed": claimed_items,
        "closed": closed_items,
        "all": items,
        "rated": [item for item in items if item.get("rating")],
    }
    selected_items = groups[filter_name]
    if category_filter in category_registry:
        selected_items = [item for item in selected_items if str(item.get("category")) == category_filter]
    if query:
        selected_items = [
            item for item in selected_items
            if query in str(item.get("id") or "").casefold()
            or query in str(item.get("user_name") or "").casefold()
            or query in str(item.get("claimed_by_name") or "").casefold()
            or query in str(item.get("category") or "").casefold()
            or query in str(item.get("close_reason") or "").casefold()
        ]
    selected_items = selected_items[:200]

    nav = "".join(
        f'<a class="tab {"active" if key == filter_name else ""}" href="/tickets?status={key}">{label}</a>'
        for key, label in (
            ("open", f"Offen ({len(open_items)})"),
            ("claimed", f"Übernommen ({len(claimed_items)})"),
            ("closed", f"Geschlossen ({len(closed_items)})"),
            ("rated", f"Bewertet ({rated_count})"),
            ("all", f"Alle ({len(items)})"),
        )
    )
    category_options = '<option value="">Alle Kategorien</option>' + "".join(
        f'<option value="{html.escape(key)}" {"selected" if category_filter == key else ""}>{html.escape(str(meta.get("emoji") or "🎫"))} {html.escape(str(meta.get("name") or "Ticket"))}</option>'
        for key, meta in category_registry.items()
    )
    cards = []
    for ticket in selected_items:
        ticket_id = str(ticket.get("id") or "")
        safe_ticket_id = html.escape(ticket_id)
        category_definition = category_registry.get(str(ticket.get("category")), {})
        category = str(category_definition.get("name") or "Ticket")
        emoji = str(category_definition.get("emoji") or "🎫")
        creator = html.escape(str(ticket.get("user_name") or "Unbekannt"))
        opened = html.escape(str(ticket.get("opened_at") or ""))
        channel_id = str(ticket.get("channel_id") or "")
        channel_link = (
            f'<a class="btn" href="https://discord.com/channels/{guild.id}/{channel_id}" target="_blank" rel="noopener">Discord öffnen ↗</a>'
            if channel_id.isdigit() else ""
        )
        closed = ticket.get("status") == "closed"
        is_claimed = bool(ticket.get("claimed_by_id"))
        priority = str(ticket.get("priority") or "normal")
        priority_label = PULSE_TICKET_PRIORITY_LABELS.get(priority, "🟢 Normal")
        status_html = (
            '<span class="state closed">Geschlossen</span>' if closed else
            ('<span class="state claimed">In Bearbeitung</span>' if is_claimed else '<span class="state open">Offen</span>')
        )
        actions = channel_link
        if closed:
            reason_html = html.escape(str(ticket.get("close_reason") or "Kein Schließgrund hinterlegt"))
            actions += (
                f'<a class="btn" href="/tickets/transcript/{safe_ticket_id}">Transcript ansehen</a>'
                if ticket.get("transcript_path") else ""
            )
            actions += (
                f'<form method="post" action="/tickets/action"><input type="hidden" name="ticket_id" value="{safe_ticket_id}">'
                '<input type="hidden" name="action" value="reopen"><button class="btn primary" type="submit">Wieder öffnen</button></form>'
            )
            detail = f'<div class="detail">Schließgrund: {reason_html}</div>'
            if ticket.get("closed_by_name"):
                detail += f'<div class="muted">Geschlossen von: {html.escape(str(ticket.get("closed_by_name")))}</div>'
        else:
            actions += (
                f'<form method="post" action="/tickets/action"><input type="hidden" name="ticket_id" value="{safe_ticket_id}">'
                '<input type="hidden" name="action" value="priority">'
                '<select name="priority" aria-label="Ticketpriorität">'
                + "".join(
                    f'<option value="{value}" {"selected" if priority == value else ""}>{label}</option>'
                    for value, label in PULSE_TICKET_PRIORITY_LABELS.items()
                )
                + '</select><button class="btn" type="submit">Priorität speichern</button></form>'
            )
            if is_claimed:
                if str(ticket.get("claimed_by_id")) == str(ctx.member.id) or ctx.perms.get("is_admin"):
                    actions += (
                        f'<form method="post" action="/tickets/action"><input type="hidden" name="ticket_id" value="{safe_ticket_id}">'
                        '<input type="hidden" name="action" value="unclaim"><button class="btn" type="submit">Freigeben</button></form>'
                    )
            else:
                actions += (
                    f'<form method="post" action="/tickets/action"><input type="hidden" name="ticket_id" value="{safe_ticket_id}">'
                    '<input type="hidden" name="action" value="claim"><button class="btn primary" type="submit">Übernehmen</button></form>'
                )
            actions += (
                f'<form method="post" action="/tickets/action" class="close-form"><input type="hidden" name="ticket_id" value="{safe_ticket_id}">'
                '<input type="hidden" name="action" value="close"><input name="reason" required maxlength="500" placeholder="Schließgrund">'
                '<button class="btn danger" type="submit">Schließen</button></form>'
            )
            detail = (
                f'<div class="detail">Bearbeiter: {html.escape(str(ticket.get("claimed_by_name") or "Niemand"))} · Priorität: {html.escape(priority_label)}</div>'
            )
        rating_html = (
            f'<div class="muted">Bewertung: {"⭐" * int(ticket.get("rating") or 0)} ({int(ticket.get("rating") or 0)}/5)'
            + (f' · {html.escape(str(ticket.get("rating_comment") or ""))}' if ticket.get("rating_comment") else "")
            + '</div>'
            if ticket.get("rating") else ""
        )
        cards.append(
            '<article class="ticket"><div class="ticket-main"><div class="kicker">'
            + emoji + " " + html.escape(category) + " · <code>" + safe_ticket_id + "</code> · "
            + html.escape(priority_label) + "</div><h3>" + creator + "</h3>"
            + '<div class="muted">Eröffnet: ' + opened + "</div>"
            + detail + rating_html + '</div><div class="ticket-side">' + status_html
            + '<div class="actions">' + actions + "</div></div></article>"
        )
    listing = "".join(cards) or '<div class="empty">Keine Tickets entsprechen dieser Auswahl.</div>'
    message = html.escape(str(ctx.msg or ""))
    notice = f'<div class="notice {"ok" if ctx.msg_ok else "err"}">{message}</div>' if message else ""

    panel_ids = pulse_ticket_panel_channel_ids()
    panel_input = ", ".join(str(item) for item in panel_ids)
    log_open_id = pulse_ticket_log_channel_id("created")
    log_claim_id = pulse_ticket_log_channel_id("claimed")
    settings_html = ""
    panel_builder_html = (
        '<section class="panel-builder settings" id="panel-builder">'
        '<div class="builder-heading"><div><h2>🎛️ Panel-Builder</h2>'
        '<p>Der Panel-Builder ist nur für Dashboard-Administratoren verfügbar.</p></div></div></section>'
    )
    if ctx.perms.get("is_admin"):
        role_cfg = settings
        team_selected = role_cfg.get("team_role_ids", ctx.config.get("team_role_ids", []))
        highteam_selected = role_cfg.get("highteam_role_ids", [])
        leadership_selected = role_cfg.get("leadership_role_ids", [])
        panel_options = "".join(
            f'<option value="{channel.id}" {"selected" if channel.id in panel_ids else ""}>#{html.escape(channel.name)} · {channel.id}</option>'
            for channel in sorted(guild.text_channels, key=lambda item: item.position)
        )
        channel_options_open = _ticket_channel_options(guild, [log_open_id])
        channel_options_claim = _ticket_channel_options(guild, [log_claim_id])
        team_options = _ticket_role_options(guild, "team_role_ids", team_selected)
        highteam_options = _ticket_role_options(guild, "highteam_role_ids", highteam_selected)
        leadership_options = _ticket_role_options(guild, "leadership_role_ids", leadership_selected)

        # Multi-panel designer: every panel can have its own Discord channel, embed and categories.
        panel_configs = settings.get("ticket_panels", [])
        panel_configs = [p for p in panel_configs if isinstance(p, dict)] if isinstance(panel_configs, list) else []
        text_channels = sorted(guild.text_channels, key=lambda item: item.position)
        role_list = [role for role in guild.roles if not role.is_default() and not role.managed]
        category_channels = sorted(guild.categories, key=lambda item: item.position)
        panel_cards = []
        active_categories = [(key, meta) for key, meta in category_registry.items() if meta.get("active", True)]

        for panel in panel_configs:
            pid = str(panel.get("id") or "")
            safe_pid = html.escape(pid, quote=True)
            selected_keys = {str(value) for value in panel.get("category_keys", []) if str(value) in category_registry}
            channel_id = str(panel.get("channel_id") or "")
            channel_options_for_panel = '<option value="">Kanal auswählen …</option>' + "".join(
                f'<option value="{ch.id}" {"selected" if str(ch.id) == channel_id else ""}>#{html.escape(ch.name)} · {ch.id}</option>'
                for ch in text_channels
            )
            category_checks = "".join(
                f'<label class="check"><input type="checkbox" name="category_keys" value="{html.escape(key, quote=True)}" {"checked" if key in selected_keys else ""}><span>{html.escape(str(meta.get("emoji") or "🎫"))} <strong>{html.escape(str(meta.get("name") or key))}</strong><small>{html.escape(str(meta.get("description") or ""))}</small></span></label>'
                for key, meta in active_categories
            )
            color_value = str(panel.get("color") or "#5865F2")
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", color_value):
                color_value = "#5865F2"
            state_label = "✅ Veröffentlicht" if panel.get("message_id") else "📝 Noch nicht veröffentlicht"
            panel_cards.append(f"""<details class="builder-item"><summary><span>{html.escape(str(panel.get("name") or "Panel"))}</span><small>{state_label}</small></summary>
<form method="post" action="/tickets/panels/save" class="builder-form">
<input type="hidden" name="panel_id" value="{safe_pid}">
<label>Interner Panelname<input name="name" value="{html.escape(str(panel.get("name") or ""), quote=True)}" maxlength="80" required></label>
<label>Panel-Kanal<select name="channel_id" required>{channel_options_for_panel}</select></label>
<label>Embed-Titel<input name="title" value="{html.escape(str(panel.get("title") or panel.get("name") or "Ticket-Support"), quote=True)}" maxlength="256" required></label>
<label>Embed-Farbe<input type="color" name="color" value="{color_value}" required></label>
<label class="wide">Embed-Beschreibung<textarea name="description" maxlength="4000" rows="3" required>{html.escape(str(panel.get("description") or ""))}</textarea></label>
<label class="wide">Fußzeile<input name="footer" value="{html.escape(str(panel.get("footer") or "Pulse Ticket-System"), quote=True)}" maxlength="200"></label>
<fieldset class="wide"><legend>Kategorien in diesem Panel (bis zu 25)</legend><div class="check-grid">{category_checks}</div></fieldset>
<div class="wide actions"><button class="btn primary" type="submit">Änderungen speichern</button></div>
</form>
<div class="actions panel-actions">
<form method="post" action="/tickets/panels/publish"><input type="hidden" name="panel_id" value="{safe_pid}"><button class="btn success" type="submit">🚀 Panel veröffentlichen / aktualisieren</button></form>
<form method="post" action="/tickets/panels/delete" onsubmit="return confirm('Dieses Panel wirklich löschen? Die registrierte Panel-Nachricht wird ebenfalls entfernt.');"><input type="hidden" name="panel_id" value="{safe_pid}"><button class="btn danger" type="submit">Panel löschen</button></form>
</div></details>""")
        panel_cards_html = "".join(panel_cards) or '<div class="empty small-empty">Du hast noch keine eigenen Panels erstellt. Erstelle dein erstes Panel rechts.</div>'

        category_editor_cards = []
        for key, meta in category_registry.items():
            category_id_html = html.escape(key, quote=True)
            selected_roles_raw = meta.get("role_ids") or settings.get(str(meta.get("group") or "team") + "_role_ids", ctx.config.get("team_role_ids", []))
            if not isinstance(selected_roles_raw, (list, tuple, set)):
                selected_roles_raw = []
            selected_role_ids = {str(value) for value in selected_roles_raw if str(value).isdigit()}
            parent_id = str(meta.get("parent_category_id") or "")
            parent_options = '<option value="">Standard-Ticketkategorie verwenden</option>' + "".join(
                f'<option value="{cat.id}" {"selected" if str(cat.id) == parent_id else ""}>📁 {html.escape(cat.name)} · {cat.id}</option>'
                for cat in category_channels
            )
            role_options_for_category = "".join(
                f'<option value="{role.id}" {"selected" if str(role.id) in selected_role_ids else ""}>{html.escape(role.name)} · {role.id}</option>'
                for role in sorted(role_list, key=lambda item: item.position, reverse=True)
            )
            group_options = "".join(
                f'<option value="{value}" {"selected" if str(meta.get("group") or "team") == value else ""}>{label}</option>'
                for value, label in (("team", "Support-Team"), ("highteam", "Highteam"), ("leadership", "Führungsebene"))
            )
            delete_form_html = ""
            if not meta.get("builtin"):
                delete_form_html = f"""<form method="post" action="/tickets/categories/delete" class="category-delete" onsubmit="return confirm('Diese eigene Kategorie wirklich löschen?');"><input type="hidden" name="category_id" value="{category_id_html}"><button class="btn danger" type="submit">Eigene Kategorie löschen</button></form>"""
            category_editor_cards.append(f"""<details class="builder-item category-item"><summary><span>{html.escape(str(meta.get("emoji") or "🎫"))} {html.escape(str(meta.get("name") or key))}</span><small>{"Standardkategorie" if meta.get("builtin") else "Eigene Kategorie"}</small></summary>
<form method="post" action="/tickets/categories/save" class="builder-form">
<input type="hidden" name="category_id" value="{category_id_html}">
<label>Name<input name="name" value="{html.escape(str(meta.get("name") or ""), quote=True)}" maxlength="80" required></label>
<label>Präfix für Ticketkanäle<input name="prefix" value="{html.escape(str(meta.get("prefix") or key), quote=True)}" pattern="[a-zA-Z0-9_-]{{1,24}}" maxlength="24" required></label>
<label>Emoji<input name="emoji" value="{html.escape(str(meta.get("emoji") or "🎫"), quote=True)}" maxlength="100" required></label>
<label>Maximal offene Tickets (0 = unbegrenzt)<input type="number" name="max_open" min="0" max="1000" value="{int(meta.get("max_open") or 0)}"></label>
<label class="wide">Beschreibung im Auswahlmenü<input name="description" value="{html.escape(str(meta.get("description") or ""), quote=True)}" maxlength="100" required></label>
<label>Discord-Zielkategorie<select name="parent_category_id">{parent_options}</select></label>
<label>Zuständige Rollen (Mehrfachauswahl)<select name="role_ids" multiple size="5">{role_options_for_category}</select></label>
<label class="wide">Begrüßung im neuen Ticket<textarea name="opening_message" maxlength="1800" rows="3">{html.escape(str(meta.get("opening_message") or ""))}</textarea></label>
<label>Bearbeitungsbereich<select name="group">{group_options}</select></label>
<div class="wide actions"><button class="btn primary" type="submit">Kategorie speichern</button></div>
</form>
{delete_form_html}
</details>""")
        category_editor_html = "".join(category_editor_cards)
        new_category_options_roles = "".join(
            f'<option value="{role.id}">{html.escape(role.name)} · {role.id}</option>'
            for role in sorted(role_list, key=lambda item: item.position, reverse=True)
        )
        new_parent_options = '<option value="">Standard-Ticketkategorie verwenden</option>' + "".join(
            f'<option value="{cat.id}">📁 {html.escape(cat.name)} · {cat.id}</option>'
            for cat in category_channels
        )
        default_category_keys = [key for key, meta in active_categories][:25]
        new_panel_checks = "".join(
            f'<label class="check"><input type="checkbox" name="category_keys" value="{html.escape(key, quote=True)}" {"checked" if key in default_category_keys else ""}><span>{html.escape(str(meta.get("emoji") or "🎫"))} <strong>{html.escape(str(meta.get("name") or key))}</strong><small>{html.escape(str(meta.get("description") or ""))}</small></span></label>'
            for key, meta in active_categories
        )
        panel_builder_html = f"""<section class="panel-builder settings" id="panel-builder">
<div class="builder-heading"><div><div class="muted">PULSE TICKET SYSTEM</div><h2>🎛️ Panel-Builder</h2><p>Erstelle beliebig viele separate Panels. Jedes Panel hat einen eigenen Kanal, eigenes Embed-Design und eine eigene Kategorienauswahl. Speichern und anschließend veröffentlichen.</p></div><span class="builder-count">{len(panel_configs)} Panels</span></div>
<div class="builder-columns"><div><h3>Deine Panels</h3><div class="builder-list">{panel_cards_html}</div></div>
<div><h3>➕ Neues Panel erstellen</h3><form method="post" action="/tickets/panels/save" class="builder-form">
<label>Interner Panelname<input name="name" placeholder="z. B. Support-Zentrale" maxlength="80" required></label>
<label>Panel-Kanal<select name="channel_id" required><option value="">Kanal auswählen …</option>{''.join(f'<option value="{ch.id}">#{html.escape(ch.name)} · {ch.id}</option>' for ch in text_channels)}</select></label>
<label>Embed-Titel<input name="title" value="Ticket-Support" maxlength="256" required></label>
<label>Embed-Farbe<input type="color" name="color" value="#5865F2" required></label>
<label class="wide">Embed-Beschreibung<textarea name="description" maxlength="4000" rows="4" required>Wähle unten die passende Kategorie aus. Pulse erstellt anschließend einen privaten Ticket-Kanal.</textarea></label>
<label class="wide">Fußzeile<input name="footer" value="Pulse Ticket-System" maxlength="200"></label>
<fieldset class="wide"><legend>Kategorien für dieses Panel (bis zu 25)</legend><div class="check-grid">{new_panel_checks}</div></fieldset>
<button class="btn primary wide" type="submit">Panel erstellen und speichern</button>
</form></div></div>
<hr>
<div class="builder-heading"><div><h3>📂 Kategorien verwalten</h3><p>Jede Kategorie kann ein eigenes Präfix, Emoji, Beschreibung, zuständige Rollen, Discord-Zielkategorie und einen Begrüßungstext besitzen. Füge Kategorien anschließend über die Checkboxen zu beliebigen Panels hinzu.</p></div></div>
<div class="category-editor-grid">{category_editor_html}
<details class="builder-item"><summary><span>➕ Neue Ticket-Kategorie anlegen</span><small>Benutzerdefiniert</small></summary>
<form method="post" action="/tickets/categories/save" class="builder-form">
<input type="hidden" name="category_id" value="">
<label>Name<input name="name" placeholder="z. B. Fraktionsverwaltung" maxlength="80" required></label>
<label>Präfix für Ticketkanäle<input name="prefix" placeholder="fraktion" pattern="[a-zA-Z0-9_-]{{1,24}}" maxlength="24" required></label>
<label>Emoji<input name="emoji" value="🎫" maxlength="100" required></label>
<label>Maximal offene Tickets (0 = unbegrenzt)<input type="number" name="max_open" min="0" max="1000" value="0"></label>
<label class="wide">Beschreibung im Auswahlmenü<input name="description" placeholder="Worum geht es in dieser Kategorie?" maxlength="100" required></label>
<label>Discord-Zielkategorie<select name="parent_category_id">{new_parent_options}</select></label>
<label>Zuständige Rollen (Mehrfachauswahl)<select name="role_ids" multiple size="5">{new_category_options_roles}</select></label>
<label class="wide">Begrüßung im neuen Ticket<textarea name="opening_message" maxlength="1800" rows="3" placeholder="Dieser Text wird im neuen Ticket angezeigt."></textarea></label>
<label>Bearbeitungsbereich<select name="group"><option value="team">Support-Team</option><option value="highteam">Highteam</option><option value="leadership">Führungsebene</option></select></label>
<button class="btn primary wide" type="submit">Kategorie erstellen</button>
</form></details>
</div></section>"""

        settings_html = f"""<section class="settings">
<details><summary>⚙ Ticket-System konfigurieren</summary>
<form method="post" action="/tickets/settings" class="settings-form">
<label>Panel-Kanäle (mehrere auswählen, für Mehrfachauswahl STRG halten)<select name="panel_channel_ids" multiple size="6" required>{panel_options}</select></label>
<label>Protokoll: neue Tickets<select name="open_log_channel_id" required>{channel_options_open}</select></label>
<label>Protokoll: beanspruchte Tickets<select name="claim_log_channel_id" required>{channel_options_claim}</select></label>
<label>Zuständige Rollen: Allgemeiner Support, Bug und Entbannungsantrag<select name="team_role_ids" multiple size="6">{team_options}</select></label>
<label>Zuständige Rollen: Highteam<select name="highteam_role_ids" multiple size="5">{highteam_options}</select></label>
<label>Zuständige Rollen: Führungsebene<select name="leadership_role_ids" multiple size="5">{leadership_options}</select></label>
<p class="muted">Die Rollenauswahl steuert Sichtbarkeit, Ping und Bearbeitungsrechte der jeweiligen Ticketkategorie. Wird für einen Bereich keine Rolle ausgewählt, greift bei allgemeinen Tickets die bestehende Pulse-Teamrollen-Konfiguration.</p>
<button class="btn primary" type="submit">Einstellungen speichern</button>
</form>
<form method="post" action="/tickets/setup-panels"><button class="btn" type="submit">Panels aktualisieren/einrichten</button></form>
</details></section>"""

    page = f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pulse · Ticket-Zentrale</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 12% 0%,#1a2850 0,transparent 35%),#0b1020;color:#e8eefb;font-family:system-ui,Segoe UI,sans-serif}}a{{color:inherit;text-decoration:none}}
.wrap{{max-width:1280px;margin:auto;padding:28px 18px 56px}}header,.hero{{display:flex;justify-content:space-between;gap:15px;flex-wrap:wrap;align-items:center}}.brand{{font-size:20px;font-weight:900}}.nav,.tabs,.actions{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
h1{{font-size:clamp(28px,4vw,40px);margin:22px 0 8px}}.muted{{color:#9cabc5;font-size:12px}}.btn,.tab{{border:1px solid #2a3952;border-radius:10px;padding:9px 12px;font-size:12px;font-weight:800;background:#172238;color:#e8eefb;cursor:pointer;display:inline-flex;align-items:center}}.btn.primary,.tab.active{{background:#647cf5;border-color:#647cf5;color:white}}.btn.danger{{background:#502034;border-color:#81394d}}
.stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:22px 0}}.stat,.ticket,.settings{{background:#121a2c;border:1px solid #27354d;border-radius:17px;padding:16px}}.stat span{{color:#9cabc5;font-size:12px}}.stat strong{{display:block;font-size:28px;margin-top:4px}}
.ticket-list{{display:grid;gap:10px;margin-top:14px}}.ticket{{display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}}.ticket-main{{flex:1;min-width:220px}}.kicker{{color:#b9c7ff;font-size:12px;font-weight:800;margin-bottom:8px}}h3{{margin:0 0 5px;font-size:17px}}.detail{{font-size:12px;color:#c4cde0;margin-top:10px;overflow-wrap:anywhere}}.ticket-side{{display:grid;justify-items:end;gap:10px;min-width:240px}}.actions form{{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:0}}.actions{{justify-content:flex-end;max-width:680px}}input,select{{max-width:100%;background:#0b1221;color:#e8eefb;border:1px solid #27354d;border-radius:9px;padding:9px;font-size:11px}}select[name="priority"]{{width:130px}}.close-form input[name="reason"]{{width:150px}}
.state{{font-size:10px;font-weight:900;border-radius:999px;padding:6px 9px}}.state.open{{background:#153b31;color:#9cf0d0}}.state.claimed{{background:#1c2c55;color:#bfcaff}}.state.closed{{background:#333b4c;color:#d9e0ed}}.empty{{text-align:center;padding:45px;color:#9cabc5;border:1px dashed #27354d;border-radius:17px}}.notice{{padding:12px;border-radius:12px;margin-top:14px;font-size:12px}}.notice.ok{{background:#123b30;color:#adf5d8}}.notice.err{{background:#4a1e2c;color:#ffd4dc}}footer{{margin-top:28px;color:#9cabc5;font-size:11px}}.filters{{display:flex;gap:9px;flex-wrap:wrap;margin:12px 0}}.filters input{{min-width:230px}}.filters select{{min-width:180px}}.settings{{margin-top:24px}}summary{{font-weight:900;cursor:pointer}}.settings-form{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:18px}}.panel-builder{{padding:22px}}.builder-heading{{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}}.builder-heading h2{{margin:6px 0}}.panel-builder h3{{margin:16px 0 10px}}.builder-heading p{{max-width:900px;color:#9cabc5;font-size:13px}}.builder-count{{background:#202d4a;border:1px solid #34486f;padding:9px 13px;border-radius:999px;font-weight:800}}.builder-columns{{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:14px}}.builder-list,.category-editor-grid{{display:grid;gap:10px}}.category-editor-grid{{grid-template-columns:repeat(2,minmax(0,1fr));margin-top:12px}}.builder-item{{background:#0d1527;border:1px solid #2a3952;border-radius:12px;padding:13px;margin-bottom:10px}}.builder-item summary{{display:flex;justify-content:space-between;gap:10px;align-items:center}}.builder-item summary small{{color:#9cabc5;font-size:10px}}.builder-form{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:16px}}.builder-form label{{display:grid;gap:6px;font-size:12px;font-weight:750}}.builder-form input,.builder-form textarea,.builder-form select{{width:100%;font:inherit}}.builder-form textarea{{resize:vertical;background:#0b1221;color:#e8eefb;border:1px solid #27354d;border-radius:9px;padding:10px}}.builder-form .wide{{grid-column:1/-1}}.builder-form fieldset{{border:1px solid #2a3952;border-radius:10px;padding:12px;min-width:0}}.builder-form legend{{font-size:12px;font-weight:800;padding:0 6px}}.check-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}.check{{display:flex!important;align-items:flex-start;gap:8px!important;padding:9px;background:#121a2c;border-radius:8px;font-weight:500!important}}.check input{{width:auto!important;margin-top:3px}}.check span{{display:grid;gap:3px}}.check small{{color:#9cabc5;font-weight:400}}.panel-actions{{justify-content:flex-start;margin-top:12px}}.category-delete{{margin-top:10px}}.btn.success{{background:#124534;border-color:#246c4e}}.small-empty{{padding:20px}}.settings-form label{{display:grid;gap:6px;font-size:12px;font-weight:800}}.settings-form select[multiple]{{width:100%;min-height:130px}}.settings-form p,.settings-form button{{grid-column:1/-1}}
@media(max-width:820px){{.stats{{grid-template-columns:repeat(2,minmax(0,1fr))}}.settings-form{{grid-template-columns:1fr}}.builder-columns,.category-editor-grid{{grid-template-columns:1fr}}.ticket-side{{width:100%;justify-items:start}}.actions{{justify-content:flex-start}}}}
@media(max-width:480px){{.stats{{grid-template-columns:1fr}}.wrap{{padding:18px 12px 40px}}}}
</style>{SCROLL_RESTORE_SCRIPT}</head><body><main class="wrap">
<header><a class="brand" href="/dashboard">⚡ Pulse TeamOS</a><nav class="nav"><a class="btn" href="/dashboard">Dashboard</a><a class="btn" href="/team">Teamliste</a><a class="btn" href="/tickets">Ticket-Zentrale</a><a class="btn primary" href="/tickets/panels#panel-builder">Panel-Builder</a></nav></header>
<section class="hero"><div><div class="muted">BOCHUM RP · TEAM OPERATIONS · <strong>Pulse Release v{PULSE_VERSION}</strong></div><h1>Ticket-Zentrale</h1><div class="muted">Tickets priorisieren, übernehmen, wieder öffnen, schließen und nachhalten – direkt in Pulse.</div></div><a class="btn" href="https://discord.com/channels/{guild.id}/{log_open_id}" target="_blank" rel="noopener">Öffnungsprotokoll ↗</a></section>
{notice}
{panel_builder_html}
<section class="stats"><div class="stat"><span>Offen</span><strong>{len(open_items)}</strong></div><div class="stat"><span>🚨 Dringend</span><strong>{urgent_count}</strong></div><div class="stat"><span>◉ Übernommen</span><strong>{len(claimed_items)}</strong></div><div class="stat"><span>★ Bewertet</span><strong>{rated_count}</strong></div></section>
<nav class="tabs">{nav}</nav>
<form class="filters" method="get" action="/tickets"><input name="q" value="{html.escape(str(request.query_params.get('q', '')), quote=True)}" placeholder="Ticket-ID, Nutzer, Bearbeiter oder Grund suchen"><select name="category">{category_options}</select><input type="hidden" name="status" value="{html.escape(filter_name)}"><button class="btn primary" type="submit">Suchen / filtern</button><a class="btn" href="/tickets">Zurücksetzen</a></form>
<section class="ticket-list">{listing}</section>
{settings_html}
<footer>Pulse TeamOS · Native Pulse-Tickets · Ticketdaten, Aktionen und Protokolle bleiben im eigenen Pulse-System.</footer>
</main></body></html>"""
    if request.url.path == "/tickets/panels":
        # Render a dedicated builder page instead of merely scrolling within the ticket list.
        # Reuse the established stylesheet and the same admin form handlers.
        document_head = page.split("<body>", 1)[0] + "<body>"
        page = document_head + f"""
<main class="wrap">
<header>
  <a class="brand" href="/dashboard">⚡ Pulse TeamOS</a>
  <nav class="nav">
    <a class="btn" href="/dashboard">Dashboard</a>
    <a class="btn" href="/team">Teamliste</a>
    <a class="btn" href="/tickets">Ticket-Zentrale</a>
    <a class="btn primary" href="/tickets/panels">Panel-Builder</a>
  </nav>
</header>
<section class="hero">
  <div><div class="muted">BOCHUM RP · TEAM OPERATIONS · <strong>Pulse Release v{PULSE_VERSION}</strong></div>
  <h1>Ticket-Panel-Builder</h1>
  <div class="muted">Erstelle mehrere unabhängige Ticket-Panels und verwalte deren Kategorien direkt in Pulse.</div></div>
  <a class="btn" href="/tickets">← Zur Ticket-Zentrale</a>
</section>
{notice}
{panel_builder_html}
<footer>Pulse TeamOS · Ticket-Panel-Builder · Einstellungen werden im eigenen Pulse-System gespeichert.</footer>
</main></body></html>"""
    return HTMLResponse(content=page, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "Expires": "0"})


@app.post("/tickets/settings")
async def pulse_tickets_dashboard_settings(
    request: Request,
    panel_channel_ids: list[str] = Form(default=[]),
    open_log_channel_id: str = Form(...),
    claim_log_channel_id: str = Form(...),
    team_role_ids: list[str] = Form(default=[]),
    highteam_role_ids: list[str] = Form(default=[]),
    leadership_role_ids: list[str] = Form(default=[]),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, admin=True)
    if not panel_channel_ids:
        return back("/tickets", "Bitte wähle mindestens einen Ticketpanel-Kanal aus.", False)
    if len(panel_channel_ids) > 25:
        return back("/tickets", "Es können höchstens 25 Panel-Kanäle ausgewählt werden.", False)

    bot = getattr(request.app.state, "bot", None)
    if not bot or not bot.is_ready():
        return back("/tickets", "Pulse-Bot ist nicht bereit.", False)

    valid_panels = []
    for raw in panel_channel_ids:
        try:
            channel_id = int(raw)
        except (TypeError, ValueError):
            return back("/tickets", "Ungültige Panel-Kanal-ID.", False)
        channel = ctx.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != ctx.guild.id:
            return back("/tickets", f"Panel-Kanal {channel_id} ist kein Textkanal dieses Servers.", False)
        if channel_id not in valid_panels:
            valid_panels.append(channel_id)

    log_ids = {}
    for key, raw in (("ticket_open_log_channel_id", open_log_channel_id), ("ticket_claim_log_channel_id", claim_log_channel_id)):
        try:
            channel_id = int(raw)
        except (TypeError, ValueError):
            return back("/tickets", "Ungültige Protokoll-Kanal-ID.", False)
        channel = ctx.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != ctx.guild.id:
            return back("/tickets", f"Protokoll-Kanal {channel_id} ist kein Textkanal dieses Servers.", False)
        log_ids[key] = channel_id

    role_values = {}
    for key, raw_values in (
        ("team_role_ids", team_role_ids),
        ("highteam_role_ids", highteam_role_ids),
        ("leadership_role_ids", leadership_role_ids),
    ):
        values = []
        for raw in raw_values:
            try:
                role_id = int(raw)
            except (TypeError, ValueError):
                return back("/tickets", "Ungültige Rollen-ID.", False)
            role = ctx.guild.get_role(role_id)
            if role is None or role.is_default() or role.managed:
                return back("/tickets", "Bitte wähle nur gültige, normale Serverrollen.", False)
            if role_id not in values:
                values.append(role_id)
        role_values[key] = values

    settings = pulse_ticket_config()
    settings.update(log_ids)
    settings.update(role_values)
    settings["panel_channel_ids"] = valid_panels
    pulse_db.set_setting("ticket_system_config", settings)
    return back("/tickets", f"Ticket-Einstellungen gespeichert: {len(valid_panels)} Panelkanal/-kanäle konfiguriert.", True)


@app.post("/tickets/setup-panels")
async def pulse_ticket_panels_setup(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, admin=True)
    bot = getattr(request.app.state, "bot", None)
    if not bot or not bot.is_ready():
        return back("/tickets", "Pulse-Bot ist nicht bereit.", False)
    cog = bot.get_cog("Tickets")
    if not cog or not hasattr(cog, "setup_panels_for_guild"):
        return back("/tickets", "Das Pulse-Ticketmodul ist nicht geladen.", False)
    try:
        result = await cog.setup_panels_for_guild(ctx.guild)
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"⚠️ Pulse-Ticketpanels über Dashboard konnten nicht eingerichtet werden ({type(exc).__name__}).")
        return back("/tickets", "Discord hat die Einrichtung abgelehnt. Prüfe die Rechte des Bots.", False)
    successes = len(result.get("successes", []))
    failures = result.get("failures", [])
    message = f"Pulse-Ticketpanels: {successes}/{result.get('expected', 6)} eingerichtet."
    if failures:
        message += " Nicht eingerichtet: " + "; ".join(failures)
    return back("/tickets", message, successes > 0 and not failures)



@app.post("/tickets/panels/save")
async def pulse_ticket_panel_save(
    request: Request,
    name: str = Form(...),
    channel_id: str = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    color: str = Form("#5865F2"),
    footer: str = Form("Pulse Ticket-System"),
    panel_id: str = Form(""),
    category_keys: list[str] = Form(default=[]),
    thumbnail_url: str = Form(""),
    banner_url: str = Form(""),
    use_server_icon: bool = Form(False),
    show_load: bool = Form(False),
    list_categories: bool = Form(False),
    component_style: str = Form("select"),
    placeholder: str = Form(""),
    welcome_title: str = Form(""),
    welcome_message: str = Form(""),
    ping_roles: bool = Form(False),
    remove_thumbnail: bool = Form(False),
    remove_banner: bool = Form(False),
    builder_v2: bool = Form(False),
    thumbnail_upload: UploadFile | None = File(None),
    banner_upload: UploadFile | None = File(None),
    welcome_thumbnail_url: str = Form(""),
    welcome_banner_url: str = Form(""),
    welcome_use_avatar: bool = Form(False),
    remove_welcome_thumbnail: bool = Form(False),
    remove_welcome_banner: bool = Form(False),
    welcome_thumbnail_upload: UploadFile | None = File(None),
    welcome_banner_upload: UploadFile | None = File(None),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, admin=True)
    bot = getattr(request.app.state, "bot", None)
    if not bot or not bot.is_ready():
        return back("/tickets/panels", "Pulse-Bot ist nicht bereit. Panel wurde nicht gespeichert.", False)
    try:
        target_channel_id = int(channel_id)
    except (TypeError, ValueError):
        return back("/tickets/panels", "Bitte wähle einen gültigen Panel-Kanal aus.", False)
    target_channel = ctx.guild.get_channel(target_channel_id)
    if not isinstance(target_channel, discord.TextChannel) or target_channel.guild.id != ctx.guild.id:
        return back("/tickets/panels", "Der ausgewählte Panel-Kanal gehört nicht zu diesem Server.", False)

    cleaned_name = str(name).strip()[:80]
    cleaned_title = str(title).strip()[:256]
    cleaned_description = str(description).strip()[:4000]
    cleaned_footer = str(footer).strip()[:200]
    cleaned_color = str(color).strip()
    if not cleaned_name or not cleaned_title or not cleaned_description:
        return back("/tickets/panels", "Panelname, Embed-Titel und Beschreibung sind Pflichtfelder.", False)
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", cleaned_color):
        return back("/tickets/panels", "Die Embed-Farbe ist ungültig.", False)

    registry = pulse_ticket_category_registry()
    if len(category_keys) > 25:
        return back("/tickets/panels", "Pro Panel sind höchstens 25 Kategorien möglich.", False)
    valid_keys = []
    for key in category_keys:
        key = str(key).strip()
        if key not in registry or not registry[key].get("active", True):
            return back("/tickets/panels", "Eine ausgewählte Ticket-Kategorie ist ungültig oder deaktiviert.", False)
        if key not in valid_keys:
            valid_keys.append(key)
    if not valid_keys:
        return back("/tickets/panels", "Wähle mindestens eine Kategorie für dieses Panel aus.", False)

    settings = pulse_ticket_config()
    panels = settings.get("ticket_panels", [])
    panels = [p for p in panels if isinstance(p, dict)] if isinstance(panels, list) else []
    cleaned_panel_id = str(panel_id or "").strip()
    existing = next((p for p in panels if str(p.get("id") or "") == cleaned_panel_id), None) if cleaned_panel_id else None
    if cleaned_panel_id and existing is None:
        return back("/tickets/panels", "Das zu bearbeitende Panel wurde nicht gefunden. Bitte lade die Seite neu.", False)
    if existing is None:
        cleaned_panel_id = "panel_" + secrets.token_hex(6)
        existing = {"id": cleaned_panel_id, "created_at": datetime.utcnow().isoformat(timespec="seconds")}
        panels.append(existing)

    existing.update({
        "id": cleaned_panel_id,
        "name": cleaned_name,
        "channel_id": target_channel_id,
        "title": cleaned_title,
        "description": cleaned_description,
        "color": cleaned_color.upper(),
        "footer": cleaned_footer or "Pulse Ticket-System",
        "category_keys": valid_keys,
    })
    if builder_v2:
        # Fields of the visual builder; older forms without them keep the stored values.
        from pulse_ticket_design import normalize_panel
        candidate = normalize_panel({
            **existing,
            "thumbnail_url": thumbnail_url, "banner_url": banner_url,
            "use_server_icon": use_server_icon, "show_load": show_load,
            "component_style": component_style, "placeholder": placeholder,
            "welcome_title": str(welcome_title).strip()[:256],
            "welcome_message": str(welcome_message).strip()[:3500],
            "ping_roles": ping_roles,
            "welcome_thumbnail_url": welcome_thumbnail_url, "welcome_banner_url": welcome_banner_url,
            "welcome_use_avatar": welcome_use_avatar,
        })
        for key, raw_url in (("thumbnail_url", thumbnail_url), ("banner_url", banner_url),
                             ("welcome_thumbnail_url", welcome_thumbnail_url), ("welcome_banner_url", welcome_banner_url)):
            if raw_url.strip() and not candidate[key]:
                return back(f"/tickets/panels?panel={cleaned_panel_id}", "Bild-URLs müssen mit https:// beginnen.", False)
        for key in ("thumbnail_url", "banner_url", "use_server_icon", "show_load", "component_style",
                    "placeholder", "welcome_title", "welcome_message", "ping_roles",
                    "welcome_thumbnail_url", "welcome_banner_url", "welcome_use_avatar"):
            existing[key] = candidate[key]
        existing["list_categories"] = bool(list_categories)
        for kind, upload, remove in (
            ("thumbnail", thumbnail_upload, remove_thumbnail),
            ("banner", banner_upload, remove_banner),
            ("welcome_thumbnail", welcome_thumbnail_upload, remove_welcome_thumbnail),
            ("welcome_banner", welcome_banner_upload, remove_welcome_banner),
        ):
            file_key = f"{kind}_file"
            if remove and existing.get(file_key):
                _remove_ticket_panel_image(existing.get(file_key), cleaned_panel_id)
                existing[file_key] = ""
            if upload is not None and upload.filename:
                stored, error = await _store_ticket_panel_image(upload, cleaned_panel_id, kind)
                if error:
                    return back(f"/tickets/panels?panel={cleaned_panel_id}", error, False)
                if existing.get(file_key):
                    _remove_ticket_panel_image(existing.get(file_key), cleaned_panel_id)
                existing[file_key] = stored
    settings["ticket_panels"] = panels
    pulse_db.set_setting("ticket_system_config", settings)
    target = f"/tickets/panels?panel={cleaned_panel_id}" if builder_v2 else "/tickets/panels"
    return back(target, f"Panel „{cleaned_name}“ wurde gespeichert. Klicke auf „Panel veröffentlichen / aktualisieren“, um es in Discord anzuzeigen.", True)


TICKET_PANEL_IMAGE_DIR = BASE_DIR / "uploads" / "ticket_panels"


async def _store_ticket_panel_image(upload: UploadFile, panel_id: str, kind: str):
    """Validate (signature + size) and store an uploaded panel image; returns (filename, error)."""
    from pulse_ticket_design import MAX_PANEL_IMAGE_BYTES, detect_image_type
    from pulse_uploads import save_image_upload
    safe_pid = re.sub(r"[^A-Za-z0-9_-]", "", panel_id)[:40] or "panel"
    # Streamed to disk in chunks: a full 8 MB read per image pushed the container RAM up.
    return await save_image_upload(
        upload, TICKET_PANEL_IMAGE_DIR, lambda ext: f"{safe_pid}_{kind}_{secrets.token_hex(4)}.{ext}",
        MAX_PANEL_IMAGE_BYTES, detect=detect_image_type,
        too_large=f"Das Bild ist zu groß (maximal {MAX_PANEL_IMAGE_BYTES // (1024 * 1024)} MB).",
        wrong_type="Nur PNG-, JPG-, GIF- oder WEBP-Bilder sind erlaubt.",
    )


def _remove_ticket_panel_image(filename: str, panel_id: str):
    """Delete only images this panel owns inside the panel image folder."""
    safe_pid = re.sub(r"[^A-Za-z0-9_-]", "", str(panel_id))[:40] or "panel"
    name = os.path.basename(str(filename or ""))
    if not name.startswith(safe_pid + "_"):
        return
    path = (TICKET_PANEL_IMAGE_DIR / name).resolve()
    if path.parent == TICKET_PANEL_IMAGE_DIR.resolve() and path.is_file():
        try:
            path.unlink()
        except OSError:
            pass


@app.get("/tickets/panels/media/{filename}")
async def pulse_ticket_panel_media(request: Request, filename: str, user_session: str = Cookie(None)):
    auth(request, user_session, admin=True)
    name = os.path.basename(filename)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}\.(png|jpg|gif|webp)", name):
        raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
    path = (TICKET_PANEL_IMAGE_DIR / name).resolve()
    if path.parent != TICKET_PANEL_IMAGE_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
    return FileResponse(path=str(path), headers={"Cache-Control": "private, max-age=300"})


@app.post("/tickets/panels/duplicate")
async def pulse_ticket_panel_duplicate(request: Request, panel_id: str = Form(...), user_session: str = Cookie(None)):
    auth(request, user_session, admin=True)
    settings = pulse_ticket_config()
    panels = [p for p in settings.get("ticket_panels", []) if isinstance(p, dict)] if isinstance(settings.get("ticket_panels"), list) else []
    source = next((p for p in panels if str(p.get("id") or "") == str(panel_id)), None)
    if source is None:
        return back("/tickets/panels", "Panel wurde nicht gefunden.", False)
    copy = {k: v for k, v in source.items() if k not in {"message_id", "published_channel_id", "thumbnail_file", "banner_file", "welcome_thumbnail_file", "welcome_banner_file"}}
    copy["id"] = "panel_" + secrets.token_hex(6)
    copy["name"] = (str(source.get("name") or "Panel") + " (Kopie)")[:80]
    copy["created_at"] = datetime.utcnow().isoformat(timespec="seconds")
    panels.append(copy)
    settings["ticket_panels"] = panels
    pulse_db.set_setting("ticket_system_config", settings)
    return back(f"/tickets/panels?panel={copy['id']}", "Panel dupliziert. Hochgeladene Bilder bitte neu wählen.", True)


@app.post("/tickets/automation")
async def pulse_ticket_automation_save(
    request: Request,
    unclaimed_ping_minutes: int = Form(0),
    staff_reply_hours: int = Form(0),
    user_reminder_hours: int = Form(0),
    auto_close_hours: int = Form(0),
    user_session: str = Cookie(None),
):
    auth(request, user_session, admin=True)
    from pulse_ticket_design import automation_settings
    settings = pulse_ticket_config()
    settings["automation"] = automation_settings({
        "unclaimed_ping_minutes": unclaimed_ping_minutes, "staff_reply_hours": staff_reply_hours,
        "user_reminder_hours": user_reminder_hours, "auto_close_hours": auto_close_hours,
    })
    pulse_db.set_setting("ticket_system_config", settings)
    return back("/tickets/panels?tab=automation", "Ticket-Automatik gespeichert.", True)


@app.post("/tickets/panels/publish")
async def pulse_ticket_panel_publish(
    request: Request,
    panel_id: str = Form(...),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, admin=True)
    bot = getattr(request.app.state, "bot", None)
    if not bot or not bot.is_ready():
        return back("/tickets/panels", "Pulse-Bot ist nicht bereit. Das Panel wurde nicht veröffentlicht.", False)
    cog = bot.get_cog("Tickets")
    if not cog or not hasattr(cog, "publish_panel_for_guild"):
        return back("/tickets/panels", "Der neue Pulse-Panel-Builder ist im laufenden Bot noch nicht geladen. Bitte den Host-Prozess neu starten.", False)
    try:
        result = await cog.publish_panel_for_guild(ctx.guild, panel_id)
    except ValueError as exc:
        return back("/tickets/panels", str(exc), False)
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"⚠️ Pulse-Panel {panel_id} konnte nicht veröffentlicht werden ({type(exc).__name__}).")
        return back("/tickets/panels", "Discord hat das Veröffentlichen abgelehnt. Prüfe Kanalzugriff sowie Rechte zum Senden von Nachrichten und Einbettungen.", False)
    return back("/tickets/panels", f"Panel „{result.get('panel_name', 'Panel')}“ wurde in {result.get('channel', 'Discord')} veröffentlicht bzw. aktualisiert.", True)


@app.post("/tickets/panels/delete")
async def pulse_ticket_panel_delete(
    request: Request,
    panel_id: str = Form(...),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, admin=True)
    settings = pulse_ticket_config()
    panels = settings.get("ticket_panels", [])
    panels = [p for p in panels if isinstance(p, dict)] if isinstance(panels, list) else []
    panel = next((p for p in panels if str(p.get("id") or "") == str(panel_id)), None)
    if panel is None:
        return back("/tickets/panels", "Panel wurde nicht gefunden.", False)
    bot = getattr(request.app.state, "bot", None)
    message_id = panel.get("message_id")
    destination_id = panel.get("published_channel_id") or panel.get("channel_id")
    if message_id and bot and bot.is_ready():
        try:
            destination = ctx.guild.get_channel(int(destination_id or 0))
            if isinstance(destination, discord.TextChannel):
                message = await destination.fetch_message(int(message_id))
                await message.delete(reason="Pulse-Panel über die Website gelöscht")
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException, TypeError, ValueError) as exc:
            print(f"⚠️ Pulse-Panel {panel_id} konnte nicht entfernt werden ({type(exc).__name__}).")
            return back("/tickets/panels", "Die Panel-Nachricht konnte nicht entfernt werden. Prüfe die Rechte des Bots; die Konfiguration bleibt erhalten.", False)
    settings["ticket_panels"] = [p for p in panels if str(p.get("id") or "") != str(panel_id)]
    pulse_db.set_setting("ticket_system_config", settings)
    return back("/tickets/panels", "Panel und seine Konfiguration wurden gelöscht.", True)


@app.post("/tickets/categories/save")
async def pulse_ticket_category_save(
    request: Request,
    name: str = Form(...),
    prefix: str = Form(...),
    emoji: str = Form("🎫"),
    description: str = Form(...),
    max_open: int = Form(0),
    parent_category_id: str = Form(""),
    claimed_category_id: str = Form(""),
    opening_message: str = Form(""),
    group: str = Form("team"),
    category_id: str = Form(""),
    role_ids: list[str] = Form(default=[]),
    ping_role_ids: list[str] = Form(default=[]),
    channel_label: str = Form(""),
    ping_roles_field: str = Form(""),
    welcome_title: str = Form(""),
    button_style: str = Form("primary"),
    builder_v2: bool = Form(False),
    return_to: str = Form(""),
    welcome_banner_url: str = Form(""),
    remove_welcome_banner: str = Form(""),
    welcome_banner_upload: UploadFile | None = File(None),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, admin=True)
    cleaned_banner_url = str(welcome_banner_url or "").strip()[:500]
    if cleaned_banner_url and not re.fullmatch(r"https://[^\s<>\"']{4,500}", cleaned_banner_url):
        return back("/tickets/panels?tab=categories", "Die Banner-URL muss mit https:// beginnen.", False)
    if builder_v2:
        # Questions arrive as q_label_1..5 (+placeholder/required/long) from the visual builder.
        form = await request.form()
        from pulse_ticket_design import BUTTON_STYLES, clean_questions
        questions = clean_questions([
            {
                "label": form.get(f"q_label_{i}", ""),
                "placeholder": form.get(f"q_placeholder_{i}", ""),
                "required": bool(form.get(f"q_required_{i}")),
                "long": bool(form.get(f"q_long_{i}")),
            }
            for i in range(1, 6)
        ])
        if button_style not in BUTTON_STYLES:
            button_style = "primary"
    cleaned_name = str(name).strip()[:80]
    cleaned_prefix = str(prefix).strip().lower()
    cleaned_emoji = str(emoji).strip()[:100] or "🎫"
    cleaned_description = str(description).strip()[:100]
    cleaned_opening = str(opening_message).strip()[:1800]
    if not cleaned_name or not cleaned_description:
        return back("/tickets/panels", "Kategorien benötigen einen Namen und eine Beschreibung.", False)
    if not re.fullmatch(r"[a-z0-9_-]{1,24}", cleaned_prefix):
        return back("/tickets/panels", "Das Präfix darf nur Buchstaben, Zahlen, Bindestriche und Unterstriche enthalten (max. 24 Zeichen).", False)
    if group not in {"team", "highteam", "leadership"}:
        return back("/tickets/panels", "Ungültiger Bearbeitungsbereich.", False)
    if not 0 <= int(max_open) <= 1000:
        return back("/tickets/panels", "Die Auslastungsgrenze muss zwischen 0 und 1000 liegen.", False)

    parent_id = 0
    if str(parent_category_id or "").strip():
        try:
            parent_id = int(parent_category_id)
        except (TypeError, ValueError):
            return back("/tickets/panels", "Ungültige Discord-Kategorie.", False)
        parent = ctx.guild.get_channel(parent_id)
        if not isinstance(parent, discord.CategoryChannel) or parent.guild.id != ctx.guild.id:
            return back("/tickets/panels", "Die ausgewählte Ticket-Zielkategorie gehört nicht zu diesem Server.", False)

    claimed_id = 0
    if str(claimed_category_id or "").strip():
        try:
            claimed_id = int(claimed_category_id)
        except (TypeError, ValueError):
            return back("/tickets/panels", "Ungültige Discord-Kategorie für übernommene Tickets.", False)
        claimed_parent = ctx.guild.get_channel(claimed_id)
        if not isinstance(claimed_parent, discord.CategoryChannel) or claimed_parent.guild.id != ctx.guild.id:
            return back("/tickets/panels", "Die Kategorie für übernommene Tickets gehört nicht zu diesem Server.", False)

    role_values = []
    for raw in role_ids:
        try:
            role_id = int(raw)
        except (TypeError, ValueError):
            return back("/tickets/panels", "Ungültige Rollen-ID.", False)
        role = ctx.guild.get_role(role_id)
        if role is None or role.is_default() or role.managed:
            return back("/tickets/panels", "Bitte wähle nur gültige, normale Serverrollen.", False)
        if role_id not in role_values:
            role_values.append(role_id)

    ping_values = []
    for raw in ping_role_ids:
        try:
            ping_id = int(raw)
        except (TypeError, ValueError):
            return back("/tickets/panels?tab=categories", "Ungültige Ping-Rolle.", False)
        ping_role = ctx.guild.get_role(ping_id)
        if ping_role is None or ping_role.is_default() or ping_role.managed:
            return back("/tickets/panels?tab=categories", "Bitte wähle als Ping nur normale Serverrollen (kein @everyone, keine Bot-Rollen).", False)
        if ping_id not in ping_values:
            ping_values.append(ping_id)
    if len(ping_values) > 10:
        return back("/tickets/panels?tab=categories", "Höchstens 10 Ping-Rollen pro Kategorie.", False)

    registry = pulse_ticket_category_registry()
    key = str(category_id or "").strip()
    if key:
        if key not in registry:
            return back("/tickets/panels", "Die zu bearbeitende Kategorie wurde nicht gefunden.", False)
    else:
        key = "custom_" + secrets.token_hex(5)
        while key in registry:
            key = "custom_" + secrets.token_hex(5)
    settings = pulse_ticket_config()
    definitions = settings.get("ticket_categories", [])
    definitions = [item for item in definitions if isinstance(item, dict)] if isinstance(definitions, list) else []
    category_value = {
        "id": key,
        "name": cleaned_name,
        "prefix": cleaned_prefix,
        "emoji": cleaned_emoji,
        "description": cleaned_description,
        "max_open": int(max_open),
        "group": group,
        "role_ids": role_values,
        "parent_category_id": parent_id,
        "claimed_category_id": claimed_id,
        "opening_message": cleaned_opening,
        "active": True,
    }
    previous = next((item for item in definitions if str(item.get("id") or "") == key), {})
    if ping_roles_field:
        # The builder sends this marker, so an empty selection really means "nobody is pinged".
        category_value["ping_role_ids"] = ping_values
    elif "ping_role_ids" in previous:
        category_value["ping_role_ids"] = previous["ping_role_ids"]
    if builder_v2:
        category_value.update({
            "welcome_title": str(welcome_title).strip()[:256],
            "questions": questions,
            "button_style": button_style,
            "welcome_banner_url": cleaned_banner_url,
            "welcome_banner_file": str(previous.get("welcome_banner_file") or ""),
            "channel_label": re.sub(r"\s+", " ", str(channel_label or "")).strip()[:40],
        })
        # Own welcome banner per category (uploaded file > URL > panel banner).
        owner = f"cat-{key}"
        if remove_welcome_banner and category_value["welcome_banner_file"]:
            _remove_ticket_panel_image(category_value["welcome_banner_file"], owner)
            category_value["welcome_banner_file"] = ""
        if welcome_banner_upload is not None and welcome_banner_upload.filename:
            stored, error = await _store_ticket_panel_image(welcome_banner_upload, owner, "welcome_banner")
            if error:
                return back("/tickets/panels?tab=categories", error, False)
            if category_value["welcome_banner_file"]:
                _remove_ticket_panel_image(category_value["welcome_banner_file"], owner)
            category_value["welcome_banner_file"] = stored
    else:
        # Older forms don't send these fields; keep what the visual builder stored.
        for extra in ("welcome_title", "questions", "button_style", "claimed_category_id",
                      "welcome_banner_url", "welcome_banner_file", "channel_label"):
            if extra in previous:
                category_value[extra] = previous[extra]
    replaced = False
    for index, item in enumerate(definitions):
        if str(item.get("id") or "") == key:
            definitions[index] = category_value
            replaced = True
            break
    if not replaced:
        definitions.append(category_value)
    settings["ticket_categories"] = definitions
    pulse_db.set_setting("ticket_system_config", settings)
    target = return_to if re.fullmatch(r"/tickets/panels(\?[A-Za-z0-9_=&-]{0,120})?", str(return_to or "")) else "/tickets/panels"
    return back(target, f"Kategorie „{cleaned_name}“ wurde gespeichert und steht im Panel-Builder zur Auswahl.", True)


@app.post("/tickets/categories/delete")
async def pulse_ticket_category_delete(
    request: Request,
    category_id: str = Form(...),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, admin=True)
    if category_id in {"support", "bug", "unban", "highteam", "leadership"}:
        return back("/tickets/panels", "Standardkategorien können nicht gelöscht, aber zurückgesetzt werden.", False)
    settings = pulse_ticket_config()
    definitions = settings.get("ticket_categories", [])
    definitions = [item for item in definitions if isinstance(item, dict)] if isinstance(definitions, list) else []
    if not any(str(item.get("id") or "") == str(category_id) for item in definitions):
        return back("/tickets/panels", "Eigene Kategorie wurde nicht gefunden.", False)
    found = False
    for item in definitions:
        if str(item.get("id") or "") == str(category_id):
            # Keep metadata so already-open or historic tickets retain their privacy and display labels.
            item["active"] = False
            if item.get("welcome_banner_file"):
                _remove_ticket_panel_image(item["welcome_banner_file"], f"cat-{category_id}")
                item["welcome_banner_file"] = ""
            found = True
            break
    if not found:
        return back("/tickets/panels", "Eigene Kategorie wurde nicht gefunden.", False)
    settings["ticket_categories"] = definitions
    panels = settings.get("ticket_panels", [])
    if isinstance(panels, list):
        for panel in panels:
            if isinstance(panel, dict) and isinstance(panel.get("category_keys"), list):
                panel["category_keys"] = [key for key in panel["category_keys"] if str(key) != str(category_id)]
    pulse_db.set_setting("ticket_system_config", settings)
    return back("/tickets/panels", "Eigene Kategorie wurde deaktiviert und aus allen Panel-Auswahlen entfernt. Bestehende Tickets behalten ihre Rollen- und Kategorieinfos.", True)


@app.get("/tickets/transcript/{ticket_id}")
async def pulse_ticket_transcript(request: Request, ticket_id: str, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm="can_manage_tickets")
    ticket = pulse_db.get_ticket(ticket_id=ticket_id)
    if not ticket or str(ticket.get("guild_id")) != str(ctx.guild.id):
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden.")
    transcript_name = os.path.basename(str(ticket.get("transcript_path") or ""))
    if not transcript_name or transcript_name != f"{ticket_id}.txt":
        raise HTTPException(status_code=404, detail="Für dieses Ticket ist kein Transkript gespeichert.")
    transcript_dir = (BASE_DIR / "transcripts").resolve()
    transcript_file = (transcript_dir / transcript_name).resolve()
    if transcript_file.parent != transcript_dir or not transcript_file.is_file():
        raise HTTPException(status_code=404, detail="Transkriptdatei ist nicht vorhanden.")
    return FileResponse(path=str(transcript_file), media_type="text/plain; charset=utf-8", filename=transcript_name)


def _ticket_transcript_file(ticket):
    transcript_name = os.path.basename(str(ticket.get("transcript_path") or ""))
    if not transcript_name or transcript_name != f"{ticket.get('id')}.txt":
        return None
    transcript_dir = (BASE_DIR / "transcripts").resolve()
    transcript_file = (transcript_dir / transcript_name).resolve()
    return transcript_file if transcript_file.parent == transcript_dir and transcript_file.is_file() else None


@app.get("/tickets/transcript/{ticket_id}/view", response_class=HTMLResponse)
async def pulse_ticket_transcript_view(request: Request, ticket_id: str, user_session: str = Cookie(None)):
    """BOT-06: the stored transcript as a readable page (the download stays at /tickets/transcript/{id})."""
    import pulse_ticket_extras
    from pulse_pro import render_pro_page
    ctx = auth(request, user_session, perm="can_manage_tickets")
    ticket = pulse_db.get_ticket(ticket_id=ticket_id)
    if not ticket or str(ticket.get("guild_id")) != str(ctx.guild.id):
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden.")
    path = _ticket_transcript_file(ticket)
    if path is None:
        raise HTTPException(status_code=404, detail="Für dieses Ticket ist kein Transkript gespeichert.")
    text = path.read_text(encoding="utf-8", errors="replace")[:2_000_000]
    body = (f'<div class="pulse-topbar"><div><div class="pulse-section-title">📄 Transkript {esc(ticket_id)}</div>'
            f'<div class="pulse-section-sub">{esc(ticket.get("user_name"))} · {esc(ticket.get("category"))} · geschlossen {esc(str(ticket.get("closed_at") or "")[:16])}</div></div>'
            f'<div class="flex gap-2"><a class="pulse-btn ghost" href="/tickets?status=closed">← Tickets</a>'
            f'<a class="pulse-btn ghost" href="/tickets/transcript/{quote(ticket_id)}">⬇ Download</a></div></div>'
            '<style>.tr-msg{padding:8px 10px;border-radius:10px;background:var(--surface2);margin-bottom:6px}.tr-meta{font-size:11px;color:var(--muted)}'
            '.tr-meta b{color:inherit}.tr-text{font-size:13px;white-space:pre-wrap;word-break:break-word}.tr-line{font-size:11px;color:var(--muted);margin:4px 0}</style>'
            f'<section class="pulse-card"><div class="pulse-card-b">{pulse_ticket_extras.transcript_html(text)}</div></section>')
    import sys
    return HTMLResponse(render_pro_page(sys.modules[__name__], f"Transkript {ticket_id}", ctx, "tickets", body))


def _mod_case_ctx(request, user_session):
    ctx = auth(request, user_session, perm=None)
    if not (ctx.perms.get("is_admin") or ctx.perms.get("can_moderate_kicks") or ctx.perms.get("can_moderate_bans")):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir ein Moderationsrecht.")
    return ctx


@app.get("/mod-cases", response_class=HTMLResponse)
async def mod_cases_page(request: Request, user_session: str = Cookie(None)):
    """BOT-09: case file of the citizen moderation (/mod …) – search, pages, CSV."""
    import pulse_lists
    import pulse_mod_cases as mc
    from pulse_pro import render_pro_page
    ctx = _mod_case_ctx(request, user_session)
    params = pulse_lists.params(request.query_params)
    rows = pulse_lists.search(mc.list_cases(), params["q"],
                              lambda c: f"{c['id']} {c['target_name']} {c['target_id']} {c['reason']} {c['actor_name']} {c['action']}")
    page_rows, meta = pulse_lists.paginate(rows, params["page"], params["per"])
    state = {"active": ("aktiv", "warn"), "expired": ("abgelaufen", "good"), "failed": ("fehlgeschlagen", "bad")}
    items = "".join(
        f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title"><code>{esc(c["id"])}</code> · '
        f'{esc(mc.ACTIONS.get(c["action"], c["action"]))} · {esc(c["target_name"])} <span class="pulse-row-meta">({esc(c["target_id"])})</span> '
        f'<span class="pulse-pill {state.get(c["status"], ("", ""))[1]}">{esc(state.get(c["status"], (c["status"], ""))[0])}</span></div>'
        f'<div class="pulse-row-meta">{time_tag(c["created_at"])} · von {esc(c["actor_name"])}'
        f'{" · bis " + esc(str(c["expires_at"])[:16].replace("T", " ")) + " UTC" if c.get("expires_at") else ""}'
        f'{" · DM zugestellt" if c.get("dm_sent") else " · keine DM"}</div>'
        f'<div class="text-xs mt-1">{esc(c["reason"])}</div>'
        + (f'<div class="text-xs mt-1">Beweis: <a class="text-indigo-500" rel="noopener noreferrer" target="_blank" href="{esc(safe_url(c["proof"]))}">{esc(c["proof"][:80])}</a></div>'
           if c.get("proof") and safe_url(c["proof"]) else "")
        + '</div></div>' for c in page_rows) or empty_state("Noch keine Moderationsfälle.", "⚖")
    q = params["q"]
    body = (f'<div class="pulse-topbar"><div><div class="pulse-section-title">⚖ Bürger-Moderation</div>'
            f'<div class="pulse-section-sub">Fallakte aller /mod-Aktionen. Neue Fälle entstehen nur in Discord über /mod warn|timeout|kick|ban.</div></div>'
            f'<a class="pulse-btn ghost" href="/mod-cases.csv{("?q=" + quote(q)) if q else ""}">⬇ CSV</a></div>'
            f'<section class="pulse-card"><div class="pulse-card-b"><form method="get" class="pulse-search mb-4" role="search" style="max-width:none">'
            f'<span>⌕</span><input name="q" value="{esc(q)}" aria-label="Fälle durchsuchen" placeholder="Fall-ID, Name, Grund, Moderator…"><button class="pulse-btn primary">Suchen</button></form>'
            f'<div class="space-y-2">{items}</div>{pulse_lists.pager("/mod-cases", meta, {"q": q})}</div></section>')
    import sys
    return HTMLResponse(render_pro_page(sys.modules[__name__], "Bürger-Moderation", ctx, "mod-cases", body))


@app.get("/mod-cases.csv")
async def mod_cases_csv(request: Request, user_session: str = Cookie(None)):
    import pulse_lists
    import pulse_mod_cases as mc
    ctx = _mod_case_ctx(request, user_session)
    q = str(request.query_params.get("q") or "")
    rows = pulse_lists.search(mc.list_cases(), q, lambda c: f"{c['id']} {c['target_name']} {c['target_id']} {c['reason']} {c['actor_name']} {c['action']}")
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Moderationsfälle exportiert", f"{len(rows)} Fälle")
    return pulse_lists.csv_response(f"pulse_moderation_{now_de().strftime('%Y-%m-%d')}.csv",
                                    ["Fall", "Aktion", "Person", "Discord-ID", "Grund", "Beweis", "Bis", "Moderator", "Erstellt", "Status"],
                                    [(c["id"], mc.ACTIONS.get(c["action"], c["action"]), c["target_name"], c["target_id"], c["reason"],
                                      c.get("proof"), c.get("expires_at"), c["actor_name"], c["created_at"], c["status"]) for c in rows])


@app.get("/tickets/tools", response_class=HTMLResponse)
async def pulse_ticket_tools(request: Request, user_session: str = Cookie(None)):
    """BOT-06: quick replies (admins edit), ticket bans (ticket team), rating report."""
    import pulse_ticket_extras as tx
    from pulse_pro import render_pro_page
    ctx = auth(request, user_session, perm="can_manage_tickets")
    is_admin = bool(ctx.perms.get("is_admin"))
    try:
        from cogs.tickets import category_meta, ticket_category_definitions
        categories = [(key, category_meta(key)[0]) for key in ticket_category_definitions()]
    except Exception:
        categories = []
    reply_blocks = "".join(
        f'<label class="grid gap-1 text-xs font-semibold">{esc(label)}'
        f'<textarea class="pulse-input pulse-textarea" name="replies_{esc(key)}" rows="3" placeholder="Titel | Text (eine Antwort pro Zeile)"'
        f'{"" if is_admin else " readonly"}>{esc(tx.replies_as_text(key))}</textarea></label>'
        for key, label in [("*", "Für alle Kategorien")] + categories)
    replies_form = (f'<form method="post" action="/tickets/tools/replies" class="space-y-3">{reply_blocks}'
                    + ('<button class="pulse-btn primary" type="submit">💾 Schnellantworten speichern</button>' if is_admin
                       else '<div class="text-xs text-slate-400">Bearbeiten können nur Admins.</div>') + '</form>')
    blocks = tx.load_blocks()
    block_rows = "".join(
        f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title"><code>{esc(uid)}</code>'
        f'{" · " + esc(getattr(ctx.guild.get_member(int(uid)), "display_name", "")) if str(uid).isdigit() and ctx.guild.get_member(int(uid)) else ""}</div>'
        f'<div class="pulse-row-meta">bis {esc(str(entry.get("until"))[:10])} · {esc(entry.get("reason"))} · von {esc(entry.get("by"))}</div></div>'
        f'<form method="post" action="/tickets/tools/unblock"><input type="hidden" name="user_id" value="{esc(uid)}">'
        f'<button class="pulse-btn ghost" type="submit">Aufheben</button></form></div>'
        for uid, entry in blocks.items() if tx.block_for_entry(entry)) or '<div class="pulse-empty">Keine aktiven Ticket-Sperren. Setzen: /ticket sperre im Discord.</div>'
    report = tx.ratings_report(pulse_db.list_tickets(status="closed", limit=2000))
    names = dict(categories)

    def table(rows, label_of=lambda k: k):
        return "".join(f'<div class="pulse-row"><div class="pulse-row-title">{esc(label_of(k))}</div>'
                       f'<div class="pulse-row-meta">⭐ {avg:.2f} · {n} Bewertungen</div></div>' for k, avg, n in rows[:20]) \
            or '<div class="pulse-empty">Noch keine Bewertungen.</div>'
    rating_html = (f'<div class="text-xs mb-3">Gesamt: {"⭐ %.2f" % report["average"] if report["average"] else "–"} aus {report["count"]} Bewertungen</div>'
                   f'<div class="grid lg:grid-cols-2 gap-4"><div><div class="pulse-section-title mb-2">Nach Kategorie</div>{table(report["by_category"], lambda k: names.get(k, k))}</div>'
                   f'<div><div class="pulse-section-title mb-2">Nach Teammitglied</div>{table(report["by_staff"])}</div></div>')
    body = (f'<div class="pulse-topbar"><div><div class="pulse-section-title">🧰 Ticket-Werkzeuge</div>'
            f'<div class="pulse-section-sub">Schnellantworten für /ticket antwort, Ticket-Sperren und die Auswertung der Bewertungen.</div></div>'
            f'<a class="pulse-btn ghost" href="/tickets">← Tickets</a></div>'
            f'<section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">💬 Schnellantworten</div></div><div class="pulse-card-b">{replies_form}</div></section>'
            f'<section class="pulse-card mt-4"><div class="pulse-card-h"><div class="pulse-section-title">🚫 Ticket-Sperren</div></div><div class="pulse-card-b space-y-2">{block_rows}</div></section>'
            f'<section class="pulse-card mt-4"><div class="pulse-card-h"><div class="pulse-section-title">⭐ Bewertungen</div></div><div class="pulse-card-b">{rating_html}</div></section>')
    import sys
    return HTMLResponse(render_pro_page(sys.modules[__name__], "Ticket-Werkzeuge", ctx, "tickets", body))


@app.post("/tickets/tools/replies")
async def pulse_ticket_tools_replies(request: Request, user_session: str = Cookie(None)):
    import pulse_ticket_extras as tx
    ctx = auth(request, user_session, perm=None, admin=True)
    form = await request.form()
    try:
        from cogs.tickets import ticket_category_definitions
        keys = ["*", *ticket_category_definitions().keys()]
    except Exception:
        keys = ["*"]
    saved = tx.save_replies({key: form.get(f"replies_{key}", "") for key in keys})
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Ticket-Schnellantworten geändert",
              f"{sum(len(v) for v in saved.values())} Antworten")
    return back("/tickets/tools", "Schnellantworten gespeichert.")


@app.post("/tickets/tools/unblock")
async def pulse_ticket_tools_unblock(request: Request, user_session: str = Cookie(None)):
    import pulse_ticket_extras as tx
    ctx = auth(request, user_session, perm="can_manage_tickets")
    user_id = str((await request.form()).get("user_id") or "")
    if not tx.unblock(user_id):
        return back("/tickets/tools", "Für diese Person gab es keine Sperre.", False)
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Ticket-Sperre aufgehoben", user_id)
    return back("/tickets/tools", "Ticket-Sperre aufgehoben.")


@app.post("/tickets/action")
async def pulse_tickets_dashboard_action(
    request: Request,
    ticket_id: str = Form(...),
    action: str = Form(...),
    reason: str = Form(""),
    priority: str = Form("normal"),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, perm="can_manage_tickets")
    ticket = pulse_db.get_ticket(ticket_id=ticket_id)
    if not ticket or str(ticket.get("guild_id")) != str(ctx.guild.id):
        return back("/tickets", "Ticket nicht gefunden oder gehört zu einem anderen Server.", False)

    bot = getattr(request.app.state, "bot", None)
    if not bot or not bot.is_ready():
        return back("/tickets", "Pulse-Bot ist nicht erreichbar.", False)
    actor_id = int(ctx.member.id)
    actor_name = ctx.member.display_name
    channel_id = int(ticket.get("channel_id") or 0)
    channel = ctx.guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            channel = None

    if action == "reopen":
        if ticket.get("status") != "closed":
            return back("/tickets", "Nur geschlossene Tickets können wieder geöffnet werden.", False)
        try:
            from cogs.tickets import TICKET_PARENT_NAME, TicketControlView, category_channel_name, category_meta, category_roles, safe_channel_name, ticket_category_definitions
            owner = ctx.guild.get_member(int(ticket.get("user_id") or 0))
            if owner is None:
                try:
                    owner = await ctx.guild.fetch_member(int(ticket.get("user_id") or 0))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                    owner = None
            parent = discord.utils.get(ctx.guild.categories, name=TICKET_PARENT_NAME)
            if parent is None:
                parent = await ctx.guild.create_category(TICKET_PARENT_NAME, reason="Pulse-Ticket erneut geöffnet")
            roles = category_roles(ctx.guild, str(ticket.get("category") or "support"))
            overwrites = {
                ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                ctx.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_messages=True, manage_permissions=True, attach_files=True, embed_links=True),
            }
            if owner:
                overwrites[owner] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
            for role in roles:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True, attach_files=True, embed_links=True)
            short_id = re.sub(r"[^a-zA-Z0-9]", "", str(ticket.get("id") or ""))[-8:] or "ticket"
            channel = await ctx.guild.create_text_channel(
                category_channel_name(ticket_category_definitions().get(str(ticket.get("category") or "support"), {}),
                                      getattr(owner, "name", None) or str(ticket.get("user_name") or "user"),
                                      f"-r{short_id[-4:].lower()}"),
                category=parent,
                overwrites=overwrites,
                topic=f"Pulse Ticket | ticket:{ticket_id} | user:{ticket.get('user_id')} | category:{ticket.get('category')}",
                reason=f"Pulse-Ticket erneut geöffnet von {actor_name}",
            )
            if not pulse_db.reopen_ticket(ticket_id, channel.id, actor_id, actor_name):
                await channel.delete(reason="Ticket-Datenbank hat Wiederöffnung abgelehnt")
                return back("/tickets?status=closed", "Ticket konnte nicht wieder geöffnet werden.", False)
            updated = pulse_db.get_ticket(ticket_id=ticket_id) or ticket
            label, _, emoji, _, _ = category_meta(str(updated.get("category") or "support"))
            embed = discord.Embed(
                title=f"{emoji} {label} – Ticket erneut geöffnet",
                description=f"Dieses Ticket wurde von {ctx.member.mention} erneut geöffnet. Der bisherige Verlauf bleibt in der Transcript-Datei erhalten.",
                color=_pulse_color("success"),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Ticket-ID", value=str(ticket_id), inline=True)
            embed.add_field(name="Ersteller", value=str(updated.get("user_name") or "Unbekannt"), inline=True)
            embed.add_field(name="Status", value="🟢 Offen", inline=True)
            await send_styled(channel, embed=embed, view=TicketControlView(), allowed_mentions=discord.AllowedMentions.none())
            pulse_db.record_event("ticket_reopened_dashboard", "ticket", ticket_id, actor_id, actor_name, {"channel_id": channel.id})
            await _send_pulse_ticket_log(bot, ctx.guild, "reopened", updated, ctx.member)
            return back("/tickets?status=open", f"Ticket {ticket_id} wurde wieder geöffnet.")
        except (discord.Forbidden, discord.HTTPException, ImportError) as exc:
            print(f"⚠️ Pulse-Tickets: Wiederöffnung fehlgeschlagen ({type(exc).__name__}).")
            return back("/tickets?status=closed", "Ticket konnte nicht wieder geöffnet werden. Prüfe die Bot-Rechte.", False)

    if ticket.get("status") == "closed":
        return back("/tickets?status=closed", "Dieses Ticket ist bereits geschlossen.", False)

    if action == "claim":
        if not pulse_db.claim_ticket(ticket["id"], actor_id, actor_name):
            latest = pulse_db.get_ticket(ticket_id=ticket_id)
            who = latest.get("claimed_by_name") if latest else "jemand anderem"
            return back("/tickets?status=claimed", f"Ticket wurde bereits von {who} übernommen.", False)
        latest = pulse_db.get_ticket(ticket_id=ticket_id) or ticket
        pulse_db.record_event("ticket_claimed_dashboard", "ticket", ticket_id, actor_id, actor_name, {"channel_id": channel_id})
        try:
            from cogs.tickets import TicketControlView, refresh_ticket_embed
            await _refresh_pulse_ticket_discord_state(channel, latest)
        except Exception:
            pass
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(f"📥 {ctx.member.mention} hat dieses Ticket über das Pulse-Dashboard übernommen.", allowed_mentions=discord.AllowedMentions(users=[ctx.member], roles=False, everyone=False))
            except (discord.Forbidden, discord.HTTPException):
                pass
        await _send_pulse_ticket_log(bot, ctx.guild, "claimed", latest, ctx.member)
        return back("/tickets?status=claimed", f"Ticket {ticket_id} wurde übernommen.")

    if action == "unclaim":
        if str(ticket.get("claimed_by_id") or "") != str(actor_id) and not ctx.perms.get("is_admin"):
            return back("/tickets?status=claimed", "Du darfst nur deine eigenen Tickets freigeben (Admins dürfen alle freigeben).", False)
        if not pulse_db.unclaim_ticket(ticket["id"], actor_id, actor_name):
            return back("/tickets?status=claimed", "Ticket konnte nicht freigegeben werden.", False)
        latest = pulse_db.get_ticket(ticket_id=ticket_id) or ticket
        await _refresh_pulse_ticket_discord_state(channel, latest)
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(f"{actor_name} hat das Ticket im Pulse-Dashboard freigegeben.", allowed_mentions=discord.AllowedMentions.none())
            except (discord.Forbidden, discord.HTTPException):
                pass
        await _send_pulse_ticket_log(bot, ctx.guild, "unclaimed", latest, ctx.member)
        return back("/tickets?status=open", f"Ticket {ticket_id} wurde freigegeben.")

    if action == "priority":
        if priority not in PULSE_TICKET_PRIORITY_LABELS:
            return back("/tickets", "Ungültige Ticketpriorität.", False)
        if not pulse_db.update_ticket_priority(ticket_id, priority, actor_id, actor_name):
            return back("/tickets", "Priorität konnte nicht geändert werden.", False)
        latest = pulse_db.get_ticket(ticket_id=ticket_id) or ticket
        await _refresh_pulse_ticket_discord_state(channel, latest)
        pulse_db.record_event("ticket_priority_dashboard", "ticket", ticket_id, actor_id, actor_name, {"priority": priority})
        await _send_pulse_ticket_log(bot, ctx.guild, "priority", latest, ctx.member)
        return back(f"/tickets?status={('claimed' if latest.get('claimed_by_id') else 'open')}", f"Priorität für Ticket {ticket_id} geändert auf {PULSE_TICKET_PRIORITY_LABELS[priority]}.")

    if action == "close":
        reason = str(reason or "").strip()[:500]
        if not reason:
            return back("/tickets", "Beim Schließen ist ein Grund erforderlich.", False)
        if not ctx.perms.get("is_admin") and not ctx.perms.get("can_manage_tickets"):
            return back("/tickets", "Dafür fehlt dir die Berechtigung.", False)
        if not isinstance(channel, discord.TextChannel):
            return back("/tickets", "Der Ticketkanal ist nicht erreichbar; das Ticket wurde nicht verändert.", False)
        # K-06: without a complete transcript the channel is kept and the ticket stays open.
        from pulse_transcripts import TranscriptError, archive_transcript, keep_channel_after_failure
        log_channel = ctx.guild.get_channel(pulse_ticket_log_channel_id("closed"))
        try:
            transcript_name = await archive_transcript(channel, ticket["id"], log_channel=log_channel)
        except TranscriptError as exc:
            pulse_db.record_event("ticket_archive_failed", "ticket", ticket_id, actor_id, actor_name, {"error": str(exc)})
            await keep_channel_after_failure(channel, ticket, str(exc))
            return back("/tickets", f"Ticket {ticket_id} nicht geschlossen: {exc}. Der Kanal bleibt erhalten.", False)
        if not pulse_db.close_ticket(ticket["id"], reason, transcript_name, actor_id, actor_name):
            return back("/tickets", "Ticket konnte nicht geschlossen werden.", False)
        closed = pulse_db.get_ticket(ticket_id=ticket_id) or ticket
        pulse_db.record_event("ticket_closed_dashboard", "ticket", ticket_id, actor_id, actor_name, {"reason": reason})
        await _send_pulse_ticket_log(bot, ctx.guild, "closed", closed, ctx.member)
        try:
            owner = ctx.guild.get_member(int(ticket.get("user_id") or 0))
            if owner:
                dm = discord.Embed(title="🎫 Dein Ticket wurde geschlossen", color=_pulse_color("info"))
                dm.description = f"Ticket {ticket_id} wurde von {actor_name} geschlossen.\n\nGrund: {reason}"
                await send_styled(owner, embed=dm)
        except (discord.Forbidden, discord.HTTPException, ValueError):
            pass
        try:
            await channel.delete(reason=f"Pulse Ticket geschlossen aus dem Dashboard von {actor_name}")
        except (discord.Forbidden, discord.HTTPException):
            pass
        return back("/tickets?status=closed", f"Ticket {ticket_id} wurde geschlossen.")

    return back("/tickets", "Unbekannte Ticketaktion.", False)


# =============================================================
# ROUTE 2: TEAMLISTE (Wochenziel mit Fortschrittsbalken)
# =============================================================
def format_support_case_period_start(value: str) -> str:
    """Format the stored UTC period boundary in Pulse's configured local timezone."""
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(TZ)
        return stamp.strftime("%d.%m.%Y %H:%M Uhr")
    except (TypeError, ValueError, OverflowError):
        return str(value or "unbekannt")


def active_voice_support_case_counts(guild_id: int) -> dict[str, int]:
    """Count currently open voice-support cases by their assigned staff member."""
    cases = pulse_db.get_setting("voice_support_cases", {})
    counts: dict[str, int] = {}
    if not isinstance(cases, dict):
        return counts
    for case in cases.values():
        if not isinstance(case, dict) or str(case.get("guild_id", "")) != str(guild_id):
            continue
        staff_id = str(case.get("assigned_staff_id") or "").strip()
        if not staff_id or staff_id == "0":
            continue
        counts[staff_id] = counts.get(staff_id, 0) + 1
    return counts


@app.get("/team", response_class=HTMLResponse)
async def team_list_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    guild = ctx.guild
    weekly_goal = float(ctx.config.get("weekly_goal_hours", 3.0))
    team_role_ids = ordered_team_role_ids(guild, ctx.config.get("team_role_ids", []))
    shifts_db = load_shifts()
    active = shifts_db["active_shifts"]
    loas = get_loas()
    team_db = load_json(DATA_FILE, {})
    voice_audio_cfg = pulse_db.get_setting("voice_support_config", {})
    if not isinstance(voice_audio_cfg, dict):
        voice_audio_cfg = {}
    support_period_start = pulse_db.get_support_case_period_start()
    voice_support_counts = pulse_db.voice_support_case_counts(support_period_start, guild.id)
    voice_active_counts = active_voice_support_case_counts(guild.id)
    ticket_counts = pulse_db.ticket_member_activity_counts(support_period_start, guild.id)
    support_period_start_label = format_support_case_period_start(support_period_start)
    voice_period_total = sum(int(item.get("completed", 0)) for item in voice_support_counts.values())
    ticket_period_total = sum(int(item.get("completed", 0)) for item in ticket_counts.values())
    activity = get_activity_today(guild.id)
    activity_exists = bool(activity["check_id"])
    activity_confirmed = activity["confirmed"]

    members = []
    for member in guild.members:
        roles = [r for r in member.roles if r.id in team_role_ids]
        if not roles:
            continue
        top = max(roles, key=lambda r: r.position)
        secs = calculate_weekly_seconds(str(member.id), shifts_db["history"], active)
        hrs = secs / 3600
        loa = loas.get(str(member.id))
        on_loa = bool(loa and loa["active"])
        members.append({
            "id": member.id, "name": member.display_name, "username": member.name,
            "avatar": member.display_avatar.url, "role": top.name, "role_id": top.id,
            "team_rank_idx": team_rank(member, team_role_ids), "pos": top.position,
            "team_since": team_since_for(member, team_db.get(str(member.id)) if isinstance(team_db, dict) else None),
            "color": role_hex(top),
            # E-3: the warning badge is only shown to the person themself and can_warn/admins.
            "warns": (len(active_warns(user_entry(team_db, str(member.id)))) if isinstance(team_db, dict) else 0)
                     if can_see_warnings(ctx.perms, ctx.user.get("id"), member.id) else 0,
            "hrs": hrs, "reached": hrs >= weekly_goal, "on_loa": on_loa,
            "voice_cases_completed": int(voice_support_counts.get(str(member.id), {}).get("completed", 0)),
            "voice_cases_active": voice_active_counts.get(str(member.id), 0),
            "ticket_cases_completed": int(ticket_counts.get(str(member.id), {}).get("completed", 0)),
            "ticket_cases_active": int(ticket_counts.get(str(member.id), {}).get("active", 0)),
            "loa_until": fmt_date(loa["bis"]) if on_loa else "",
            "duty": active.get(str(member.id), {}).get("status"),
            "activity": (
                "confirmed"
                if activity_exists and (activity["eligible"] is None or member.id in activity["eligible"]) and member.id in activity_confirmed
                else "open"
                if activity_exists and (activity["eligible"] is None or member.id in activity["eligible"])
                else "not_in_snapshot"
                if activity_exists
                else "none"
            ),
            "discord_status": str(member.status),
            "discord_activity": next(
                (
                    getattr(a, "name", None) or getattr(a, "state", None)
                    for a in (member.activities or [])
                    if getattr(a, "name", None) or getattr(a, "state", None)
                ),
                None,
            ),
        })
    members.sort(key=lambda m: (-m["pos"], m["name"].lower()))

    below = len([m for m in members if not m["reached"] and not m["on_loa"]])
    activity_confirmed_count = sum(1 for m in members if m["activity"] == "confirmed")
    activity_open_count = sum(1 for m in members if m["activity"] == "open")
    activity_outside_snapshot_count = sum(1 for m in members if m["activity"] == "not_in_snapshot")
    activity_summary = (
        f"✅ {activity_confirmed_count} bestätigt · ⏳ {activity_open_count} offen"
        + (f" · ⚪ {activity_outside_snapshot_count} nicht im Check" if activity_outside_snapshot_count else "")
        if activity_exists else "⚪ Heute noch kein Activity Check"
    )
    summary = (f"{len(members)} Mitglieder · ✅ {len([m for m in members if m['reached']])} Ziel erreicht · "
               f"⚠️ {below} unter Ziel · 🟢 {len([m for m in members if m['duty']])} im Dienst · 🌴 {len([m for m in members if m['on_loa']])} abgemeldet · Activity: {activity_summary} · "
               f"🎧 {voice_period_total} Voice-Supportfälle · 🎫 {ticket_period_total} Tickets abgeschlossen seit {support_period_start_label}")

    confirmed_names = [m["name"] for m in members if m["activity"] == "confirmed"]
    open_names = [m["name"] for m in members if m["activity"] == "open"]
    not_in_snapshot_names = [m["name"] for m in members if m["activity"] == "not_in_snapshot"]
    activity_panel = f"""
    <section id="activity" class="{CARD} p-5 mb-5">
        <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
            <div><h2 class="text-base font-bold text-slate-900 dark:text-white">✅ Activity Check – Teamübersicht</h2>
            <p class="text-[11px] text-slate-500 dark:text-slate-400">{activity["check_date"]} · direkt aus dem Bot-Activity-Check</p></div>
            <div class="flex gap-2 text-[11px] font-semibold"><span class="px-3 py-1.5 rounded-xl border {BADGE_OK}">✅ {activity_confirmed_count} bestätigt</span><span class="px-3 py-1.5 rounded-xl border {BADGE_BAD}">⏳ {activity_open_count} offen</span></div>
        </div>
        <div class="grid md:grid-cols-2 gap-4 mt-4">
            <div class="rounded-xl bg-emerald-500/5 border border-emerald-500/20 p-3"><div class="text-[10px] font-bold uppercase tracking-wider text-emerald-600 mb-2">Bestätigt</div><div class="text-xs leading-6">{esc(", ".join(confirmed_names) or "Noch niemand bestätigt.")}</div></div>
            <div class="rounded-xl bg-rose-500/5 border border-rose-500/20 p-3"><div class="text-[10px] font-bold uppercase tracking-wider text-rose-600 mb-2">Noch offen</div><div class="text-xs leading-6">{esc(", ".join(open_names) or ("Niemand offen." if activity_exists else "Heute wurde noch kein Check gesendet."))}</div></div>
            <div class="rounded-xl bg-slate-500/5 border border-slate-500/20 p-3 md:col-span-2"><div class="text-[10px] font-bold uppercase tracking-wider text-slate-500 mb-2">Nicht Teil dieses Checks</div><div class="text-xs leading-6">{esc(", ".join(not_in_snapshot_names) or "Niemand.")}</div></div>
        </div>
    </section>"""

    rows_html = ""
    actor_team_rank = team_rank(ctx.member, team_role_ids) if ctx.member else -1
    for m in members:
        style = BADGE_WARN if m["on_loa"] else (BADGE_OK if m["reached"] else BADGE_BAD)
        can_manage_target = bool(
            not ctx.perms.get("is_admin")
            and ctx.member
            and team_rank(ctx.member, team_role_ids) > m["team_rank_idx"]
        ) or bool(ctx.perms.get("is_admin"))
        can_promote_target = bool(
            can_manage_target
            and m["team_rank_idx"] >= 0
            and m["team_rank_idx"] + 1 < len(team_role_ids)
            and (
                ctx.perms.get("is_admin")
                or (ctx.member and team_rank(ctx.member, team_role_ids) > m["team_rank_idx"] + 1)
            )
        )
        can_demote_target = bool(can_manage_target and m["team_rank_idx"] >= 0)
        role_actions = ""
        if can_manage_target:
            promote_form = f'''
                <form action="/action" method="post" class="inline" onsubmit="return fillActionReason(this, 'Beförderung');">
                    <input type="hidden" name="action" value="promote">
                    <input type="hidden" name="user_id" value="{m['id']}">
                    <input type="hidden" name="redirect_to_member" value="">
                    <input type="hidden" name="action_reason" value="">
                    <select name="probation_days" required class="{INPUT} text-xs"><option value="">Probezeit wählen</option><option value="3">3 Tage Probezeit</option><option value="7">7 Tage Probezeit</option></select>
                    <button class="w-full text-left px-3 py-2 rounded-lg text-xs hover:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" {'disabled title="Höchster Rang erreicht"' if not can_promote_target else ''}>⬆️ Befördern</button>
                </form>''' if can_promote_target else ""
            demote_form = f'''
                <form action="/action" method="post" class="inline" onsubmit="return fillActionReason(this, 'Degradierung');">
                    <input type="hidden" name="action" value="demote">
                    <input type="hidden" name="user_id" value="{m['id']}">
                    <input type="hidden" name="redirect_to_member" value="">
                    <input type="hidden" name="action_reason" value="">
                    <button class="w-full text-left px-3 py-2 rounded-lg text-xs hover:bg-amber-500/10 text-amber-600 dark:text-amber-400">⬇️ Degradieren</button>
                </form>''' if can_demote_target else ""
            kick_form = f'''
                <form action="/action" method="post" class="inline" onsubmit="return fillActionReason(this, 'Kick') && confirm('Dieses Mitglied wirklich vom Discord-Server kicken?');">
                    <input type="hidden" name="action" value="kick">
                    <input type="hidden" name="user_id" value="{m['id']}">
                    <input type="hidden" name="redirect_to_member" value="">
                    <input type="hidden" name="action_reason" value="">
                    <button class="w-full text-left px-3 py-2 rounded-lg text-xs hover:bg-rose-500/10 text-rose-600 dark:text-rose-400">🚪 Team-Kick</button>
                </form>'''
            role_actions = f'''
                <details class="relative">
                    <summary class="list-none cursor-pointer select-none inline-flex items-center gap-2 px-3.5 py-2 bg-indigo-500/10 hover:bg-indigo-600 hover:text-white rounded-xl text-indigo-600 dark:text-indigo-400 text-xs transition font-semibold shadow-sm">
                        👁️ Aktionen
                    </summary>
                    <div class="absolute right-0 top-full mt-2 z-30 w-52 p-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-[#141824] shadow-2xl">
                        <a href="/member/{m['id']}" class="block px-3 py-2 rounded-lg text-xs hover:bg-slate-100 dark:hover:bg-slate-800">👤 Teamakte öffnen</a>
                        {promote_form}
                        {demote_form}
                        <a href="/team/{m['id']}/roles" class="block px-3 py-2 rounded-lg text-xs hover:bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">⚙️ Rollen ändern</a>
                        <div class="my-1 border-t border-slate-100 dark:border-slate-800"></div>
                        {kick_form}
                    </div>
                </details>'''
        else:
            role_actions = f'<a href="/member/{m["id"]}" class="px-3.5 py-2 bg-indigo-500/10 hover:bg-indigo-600 hover:text-white rounded-xl text-indigo-600 dark:text-indigo-400 text-xs transition font-semibold shadow-sm">👁️ Details</a>'
        bar_color = "bg-amber-500" if m["on_loa"] else ("bg-emerald-500" if m["reached"] else "bg-rose-500")
        pct = min(100, int(m["hrs"] / weekly_goal * 100)) if weekly_goal > 0 else 100
        loa_badge = (f'<span class="border {BADGE_WARN} text-[10px] px-2.5 py-0.5 rounded-full font-semibold">Abgemeldet bis {esc(m["loa_until"])}</span>'
                     if m["on_loa"] else "")
        duty_badge = ""
        if m["duty"]:
            duty_badge = (f'<span class="border {BADGE_WARN if m["duty"] == "break" else BADGE_OK} text-[10px] px-2.5 py-0.5 rounded-full font-semibold">'
                          f'{"☕ Pause" if m["duty"] == "break" else "🟢 Im Dienst"}</span>')
        presence_labels = {
            "online": "🟢 Online",
            "idle": "🟡 Abwesend",
            "dnd": "🔴 Bitte nicht stören",
            "offline": "⚪ Offline",
        }
        presence_text = presence_labels.get(m["discord_status"], "⚪ Offline")
        presence_badge = f'<span class="text-[10px] px-2 py-0.5 rounded-full font-semibold border bg-slate-50 dark:bg-slate-900/30 text-slate-600 dark:text-slate-300 border-slate-200 dark:border-slate-700">{presence_text}</span>'
        activity_badge_class = (
            BADGE_OK if m["activity"] == "confirmed"
            else BADGE_BAD if m["activity"] == "open"
            else "bg-slate-100 dark:bg-slate-800 text-slate-500 border-slate-200 dark:border-slate-700"
        )
        activity_badge_text = (
            "✅ Aktiv bestätigt" if m["activity"] == "confirmed"
            else "⏳ Nicht bestätigt" if m["activity"] == "open"
            else "⚪ Nicht Teil dieses Checks" if m["activity"] == "not_in_snapshot"
            else "⚪ Kein Check"
        )
        activity_name = f'<span class="text-[10px] text-slate-400 truncate max-w-[220px]" title="{esc(m["discord_activity"])}">🎮 {esc(m["discord_activity"])}</span>' if m["discord_activity"] else ""
        warn_badge = (f'<span class="border border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400 text-[10px] px-2 py-0.5 rounded-full font-semibold">🚨 {m["warns"]}/5 Warnungen</span>' if m["warns"] >= 5 else
                      f'<span class="border border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400 text-[10px] px-2 py-0.5 rounded-full font-semibold">⚠ {m["warns"]}/5 Warnungen</span>' if m["warns"] else '')
        rows_html += f"""
        <div class="team-row {CARD} hover:bg-slate-50 dark:hover:bg-[#1a2030] transition px-5 py-4 flex flex-col md:flex-row md:items-center justify-between gap-3"
             data-search="{esc((m['name'] + ' ' + m['username'] + ' ' + m['role']).lower())}" data-below="{1 if (not m['reached'] and not m['on_loa']) else 0}">
            <div class="flex items-center gap-3.5 md:w-1/5 min-w-0">
                <img src="{esc(m['avatar'])}" alt="" class="w-11 h-11 rounded-full border border-slate-200 dark:border-slate-700 shadow-sm">
                <div class="truncate">
                    <div class="font-semibold text-sm text-slate-900 dark:text-white flex items-center gap-2 flex-wrap"><span>{esc(m['name'])}</span>{presence_badge}{loa_badge}{duty_badge}{warn_badge}<span class="text-[10px] px-2 py-0.5 rounded-full font-semibold border {activity_badge_class}">{activity_badge_text}</span>{activity_name}</div>
                    <div class="text-xs text-slate-400 font-mono">@{esc(m['username'])}</div>
                </div>
            </div>
            <div class="md:w-1/5 space-y-1.5">
                <div class="flex items-center gap-3 flex-wrap">
                    <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border" style="background-color:{m['color']}15;color:{m['color']};border-color:{m['color']}40;">
                        <span class="w-1.5 h-1.5 rounded-full" style="background-color:{m['color']}"></span>{esc(m['role'])}
                    </span>
                    <span class="text-xs font-mono font-bold px-2.5 py-1 rounded-lg border {style}" title="Soll-Ziel: {weekly_goal}h/Woche">⏱️ {m['hrs']:.1f}h / {weekly_goal:g}h</span>
                    <span class="text-xs font-mono font-bold px-2.5 py-1 rounded-lg border border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400" title="Abgeschlossene Voice-Supportfälle seit dem letzten Teammeeting; offen = aktuell zugewiesene Fälle">🎧 {m['voice_cases_completed']} erledigt · {m['voice_cases_active']} offen</span>
                    <span class="text-xs font-mono font-bold px-2.5 py-1 rounded-lg border border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400" title="Tickets seit dem letzten Teammeeting; offen = aktuell übernommene Tickets">🎫 {m['ticket_cases_completed']} erledigt · {m['ticket_cases_active']} offen</span>
                </div>
                <div class="text-[10px] text-slate-400">🗓️ Team seit <span class="font-semibold text-slate-500 dark:text-slate-300">{esc(m['team_since'])}</span></div>
                <div class="h-1.5 rounded-full bg-slate-200 dark:bg-slate-800 overflow-hidden"><div class="h-full {bar_color}" style="width:{pct}%"></div></div>
            </div>
            <div class="md:w-2/5 flex flex-wrap md:justify-end gap-2 items-center">
                {role_actions}
            </div>
        </div>"""

    head = """<style>
        details > summary::-webkit-details-marker { display: none; }
        details[open] > summary { background: rgba(79,70,229,.14); }
        .team-row details[open] { z-index: 40; }
    </style>
    <script>
        function fillActionReason(form, label) {
            const reason = prompt("Bitte gib den Grund für " + label + " ein:");
            if (reason === null) return false;
            const clean = reason.trim().slice(0, 500);
            if (!clean) {
                alert("Ein Grund ist Pflicht.");
                return false;
            }
            const field = form.querySelector('[name="action_reason"]');
            if (field) field.value = clean;
            return true;
        }
        function filterTeam() {
            const q = document.getElementById('searchInput').value.toLowerCase();
            const onlyBelow = document.getElementById('onlyBelow').checked;
            document.querySelectorAll('.team-row').forEach(r => {
                const ok = r.dataset.search.includes(q) && (!onlyBelow || r.dataset.below === '1');
                r.style.display = ok ? '' : 'none';
            });
        }
    </script>"""
    hire_panel = ""
    if ctx.perms.get("can_promote") or ctx.perms.get("is_admin"):
        hire_roles = [
            guild.get_role(rid) for rid in team_role_ids
            if guild.get_role(rid) and not guild.get_role(rid).managed and not guild.get_role(rid).is_default()
        ]
        if hire_roles:
            hire_role_options = "".join(
                f'<option value="{role.id}">{esc(role.name)}</option>'
                for role in hire_roles
            )
            hire_panel = f"""
            <section class="{CARD} p-5 mb-6">
                <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-3 mb-4">
                    <div>
                        <h2 class="text-base font-bold text-slate-900 dark:text-white">➕ Teamler manuell einstellen</h2>
                        <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">Discord-ID, Benutzername oder Anzeigename eingeben und die Teamrolle festlegen. Die Person muss bereits auf diesem Discord-Server sein.</p>
                    </div>
                    <span class="text-[10px] font-semibold px-3 py-1 rounded-full border {BADGE_OK}">Führung</span>
                </div>
                <form action="/action" method="post" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
                    <input type="hidden" name="action" value="hire">
                    <label class="text-[11px] font-semibold text-slate-600 dark:text-slate-300">Discord-Nutzer
                        <input name="target_user_id" required maxlength="100" autocomplete="off" class="mt-1 w-full bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-700 rounded-xl px-3 py-2.5 text-xs" placeholder="ID, @Benutzername oder Name">
                    </label>
                    <label class="text-[11px] font-semibold text-slate-600 dark:text-slate-300">Teamrolle
                        <select name="hire_role_id" required class="mt-1 w-full bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-700 rounded-xl px-3 py-2.5 text-xs">
                            <option value="">Teamrolle auswählen…</option>
                            {hire_role_options}
                        </select>
                    </label>
                    <label class="text-[11px] font-semibold text-slate-600 dark:text-slate-300">Pflichtgrund
                        <input name="action_reason" required maxlength="500" class="mt-1 w-full bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-700 rounded-xl px-3 py-2.5 text-xs" placeholder="Grund für die Einstellung">
                    </label>
                    <div class="flex items-end">
                        <button class="w-full px-4 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold shadow-sm" onclick="return confirm('Diese Person wirklich mit der ausgewählten Teamrolle einstellen?')">🎉 Teamler einstellen</button>
                    </div>
                </form>
            </section>"""
        else:
            hire_panel = f'<section class="{CARD} p-4 mb-6 text-sm text-amber-600">⚠️ Es sind keine gültigen Teamrollen konfiguriert. Bitte zuerst die Teamrollen in den Einstellungen festlegen.</section>'


    voice_audio_editor = ""
    if ctx.perms.get("can_promote") or ctx.perms.get("is_admin"):
        def audio_status(config_key, fallback_text):
            raw_path = str(voice_audio_cfg.get(config_key) or "").strip()
            if not raw_path:
                if config_key == "custom_opening_audio_path" and voice_audio_cfg.get("waiting_music_enabled") and voice_audio_cfg.get("waiting_music_path"):
                    return "ℹ️ Keine eigene Datei. Die bisher konfigurierte Wartemusik bleibt als Rückfalloption aktiv."
                return fallback_text
            try:
                candidate = Path(raw_path)
                if not candidate.is_absolute():
                    candidate = BASE_DIR / candidate
                candidate = candidate.resolve()
                if candidate.is_file():
                    return f"✅ Eigene Audiodatei aktiv: {esc(candidate.name)} · {round(candidate.stat().st_size / 1024, 1)} KB"
            except (OSError, RuntimeError):
                pass
            return "⚠️ Der gespeicherte Audiopfad ist nicht verfügbar. Bitte lade die Datei erneut hoch."

        def audio_remove_form(audio_type, config_key):
            if not str(voice_audio_cfg.get(config_key) or "").strip():
                return ""
            return f"""
                <form action="/team/voice-support/audio" method="post" onsubmit="return confirm('Eigene Audio wirklich entfernen?')">
                    <input type="hidden" name="audio_type" value="{audio_type}">
                    <input type="hidden" name="audio_action" value="remove">
                    <button class="px-3 py-2 rounded-xl text-xs font-semibold border border-rose-500/30 bg-rose-500/5 text-rose-600 dark:text-rose-400 hover:bg-rose-500/10">Eigene Audio entfernen</button>
                </form>"""

        opening_audio_status = audio_status(
            "custom_opening_audio_path",
            "⚪ Keine eigene Audio eingerichtet. Während der Supportzeiten wird dann keine eigene Wartemeldung abgespielt."
        )
        after_hours_audio_status = audio_status(
            "custom_after_hours_audio_path",
            "✅ Eingebaute deutsche Standardansage aktiv."
        )
        voice_audio_editor = f"""
        <section id="voice-audio" class="{CARD} p-5 md:p-6 mb-6 space-y-4">
            <div>
                <h2 class="text-base font-bold text-slate-900 dark:text-white">🔊 Voice-Support-Audios</h2>
                <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">Eigene Audiodateien direkt hier verwalten. Änderungen werden gespeichert und vom Voice-Support ohne Änderung an den Öffnungszeiten verwendet. Erlaubt: MP3, WAV, OGG, FLAC oder M4A · maximal {MAX_VOICE_SUPPORT_AUDIO_BYTES // (1024 * 1024)} MB.</p>
            </div>
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div class="rounded-2xl border border-slate-200 dark:border-slate-700 p-4 space-y-3">
                    <h3 class="text-sm font-bold text-slate-900 dark:text-white">🟢 Während der Supportzeiten</h3>
                    <p class="text-xs text-slate-500">Wird im privaten Support-Sprachraum wiederholt, solange der Bürger auf einen Supporter wartet. Sobald ein Teamler übernimmt, stoppt die Wiedergabe.</p>
                    <p class="text-xs font-semibold">{opening_audio_status}</p>
                    <form action="/team/voice-support/audio" method="post" enctype="multipart/form-data" class="space-y-3">
                        <input type="hidden" name="audio_type" value="opening">
                        <input type="hidden" name="audio_action" value="upload">
                        <input type="file" name="audio_file" accept=".mp3,.wav,.ogg,.flac,.m4a,audio/*" required class="block w-full text-xs text-slate-600 dark:text-slate-300">
                        <button class="px-4 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-bold">Audio während Supportzeiten speichern</button>
                    </form>
                    {audio_remove_form("opening", "custom_opening_audio_path")}
                </div>
                <div class="rounded-2xl border border-slate-200 dark:border-slate-700 p-4 space-y-3">
                    <h3 class="text-sm font-bold text-slate-900 dark:text-white">🌙 Außerhalb der Supportzeiten</h3>
                    <p class="text-xs text-slate-500">Wird einmal im gemeinsamen Warteraum abgespielt, wenn ein neuer Supportfall außerhalb der Öffnungszeiten aufgenommen wird. Ohne eigene Datei bleibt die deutsche Standardansage aktiv.</p>
                    <p class="text-xs font-semibold">{after_hours_audio_status}</p>
                    <form action="/team/voice-support/audio" method="post" enctype="multipart/form-data" class="space-y-3">
                        <input type="hidden" name="audio_type" value="after_hours">
                        <input type="hidden" name="audio_action" value="upload">
                        <input type="file" name="audio_file" accept=".mp3,.wav,.ogg,.flac,.m4a,audio/*" required class="block w-full text-xs text-slate-600 dark:text-slate-300">
                        <button class="px-4 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-bold">Audio außerhalb Supportzeiten speichern</button>
                    </form>
                    {audio_remove_form("after_hours", "custom_after_hours_audio_path")}
                </div>
            </div>
        </section>"""

    body = f"""
    <div class="flex flex-col md:flex-row md:justify-between md:items-center gap-4 mb-6">
        <div>
            <div class="text-xs text-slate-400 mb-1">Team / <span class="text-indigo-600 dark:text-indigo-400 font-medium">Teamliste</span></div>
            <h1 class="text-2xl font-bold text-slate-900 dark:text-white">Teamliste & Wochenziel ({weekly_goal:g}h)</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">{summary}</p>
            <div class="mt-2 inline-flex flex-wrap items-center gap-2 text-[11px] font-semibold">
                <span class="px-3 py-1.5 rounded-xl border {BADGE_OK if activity_exists else "bg-slate-100 dark:bg-slate-800 text-slate-500 border-slate-200 dark:border-slate-700"}">{"✅ Activity Check ausgewertet" if activity_exists else "⚪ Kein Activity Check heute"}</span>
                <a href="/team" class="px-3 py-1.5 rounded-xl bg-slate-100 dark:bg-slate-800 hover:bg-indigo-600 hover:text-white transition">🔄 Aktualisieren</a>
            </div>
        </div>
        <div class="flex items-center gap-3">
            <label class="text-xs flex items-center gap-1.5 cursor-pointer text-slate-600 dark:text-slate-300"><input type="checkbox" id="onlyBelow" onchange="filterTeam()" class="rounded"> Nur unter Ziel</label>
            <input type="text" id="searchInput" oninput="filterTeam()" placeholder="Teammitglied suchen..." class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-700 text-xs text-slate-900 dark:text-white rounded-xl px-4 py-2.5 w-56 focus:outline-none focus:border-indigo-500 shadow-sm">
        </div>
    </div>
    {hire_panel}
    {voice_audio_editor}
    <div class="space-y-3">{rows_html or f"<div class='text-center py-12 text-slate-400 text-sm {CARD}'>Keine Teammitglieder gefunden. Team-Rollen kannst du unter Einstellungen festlegen.</div>"}</div>"""
    return render_page("Teamliste", ctx, "team", body, head)


@app.post("/team/voice-support/audio")
async def manage_voice_support_audio(
    request: Request,
    audio_type: str = Form(...),
    audio_action: str = Form("upload"),
    audio_file: UploadFile | None = File(None),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, perm="can_promote")
    def audio_redirect(message, ok=True):
        response = back("/team", message, ok)
        response.headers["location"] = response.headers.get("location", "/team") + "#voice-audio"
        return response

    if audio_type not in {"opening", "after_hours"}:
        return audio_redirect( "Ungültiger Audio-Typ.", False)
    if audio_action not in {"upload", "remove"}:
        return audio_redirect( "Ungültige Audio-Aktion.", False)

    config_key = "custom_opening_audio_path" if audio_type == "opening" else "custom_after_hours_audio_path"
    expected_prefix = f"{ctx.guild.id}_support_{audio_type}_"
    audio_dir = VOICE_SUPPORT_AUDIO_DIR.resolve()

    def remove_owned_audio(raw_path):
        raw = str(raw_path or "").strip()
        if not raw:
            return
        try:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = BASE_DIR / candidate
            candidate = candidate.resolve()
            if candidate.parent == audio_dir and candidate.name.startswith(expected_prefix) and candidate.is_file():
                candidate.unlink()
        except (OSError, RuntimeError):
            pass

    config = pulse_db.get_setting("voice_support_config", {})
    if not isinstance(config, dict):
        config = {}
    old_path = str(config.get(config_key) or "").strip()

    if audio_action == "remove":
        config.pop(config_key, None)
        pulse_db.set_setting("voice_support_config", config)
        remove_owned_audio(old_path)
        actor_name = ctx.member.display_name if ctx.member else str(ctx.user.get("global_name") or "Teamleitung")
        log_audit(actor_name, str(ctx.user["id"]), "Voice-Support Audio entfernt",
                  f"{'Während der Supportzeiten' if audio_type == 'opening' else 'Außerhalb der Supportzeiten'} · eigene Datei entfernt")
        return audio_redirect( "Eigene Audio entfernt. Die Standardkonfiguration wird wieder verwendet.")

    if not audio_file or not audio_file.filename:
        return audio_redirect( "Bitte zuerst eine Audiodatei auswählen.", False)
    extension = Path(audio_file.filename).suffix.lower()
    if extension not in VOICE_SUPPORT_AUDIO_EXTENSIONS:
        return audio_redirect( "Dateiformat nicht unterstützt. Erlaubt sind MP3, WAV, OGG, FLAC und M4A.", False)

    try:
        raw = await audio_file.read(MAX_VOICE_SUPPORT_AUDIO_BYTES + 1)
    except Exception:
        return audio_redirect( "Die Audiodatei konnte nicht gelesen werden.", False)
    finally:
        try:
            await audio_file.close()
        except Exception:
            pass

    if len(raw) > MAX_VOICE_SUPPORT_AUDIO_BYTES:
        return audio_redirect( f"Die Audiodatei ist zu groß. Maximum: {MAX_VOICE_SUPPORT_AUDIO_BYTES // (1024 * 1024)} MB.", False)
    if len(raw) < 100:
        return audio_redirect( "Die Datei ist leer oder zu kurz, um eine gültige Audiodatei zu sein.", False)

    def valid_audio_signature(ext, data):
        if ext == ".mp3":
            return data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)
        if ext == ".wav":
            return data[:4] in {b"RIFF", b"RF64"} and data[8:12] == b"WAVE"
        if ext == ".ogg":
            return data.startswith(b"OggS")
        if ext == ".flac":
            return data.startswith(b"fLaC")
        if ext == ".m4a":
            return len(data) >= 12 and data[4:8] == b"ftyp"
        return False

    if not valid_audio_signature(extension, raw):
        return audio_redirect( "Die Dateiendung passt nicht zum Audioinhalt oder die Datei ist beschädigt.", False)

    try:
        VOICE_SUPPORT_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        target = VOICE_SUPPORT_AUDIO_DIR / f"{ctx.guild.id}_support_{audio_type}_{uuid.uuid4().hex[:8]}{extension}"
        temporary = target.with_name(target.name + ".uploading")
        temporary.write_bytes(raw)
        os.replace(temporary, target)
        relative_path = target.resolve().relative_to(BASE_DIR.resolve()).as_posix()
        config[config_key] = relative_path
        pulse_db.set_setting("voice_support_config", config)
        if old_path and old_path != relative_path:
            remove_owned_audio(old_path)
        actor_name = ctx.member.display_name if ctx.member else str(ctx.user.get("global_name") or "Teamleitung")
        human_type = "Während der Supportzeiten" if audio_type == "opening" else "Außerhalb der Supportzeiten"
        log_audit(actor_name, str(ctx.user["id"]), "Voice-Support Audio gespeichert",
                  f"{human_type} · {target.name} · {len(raw)} Bytes")
        return audio_redirect( f"{human_type}: Audiodatei erfolgreich gespeichert.")
    except (OSError, ValueError, RuntimeError) as exc:
        try:
            if "temporary" in locals() and temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        print(f"⚠️ Voice-Support Audio-Upload fehlgeschlagen: {type(exc).__name__}: {exc}")
        return audio_redirect( "Audio konnte nicht gespeichert werden. Prüfe den verfügbaren Speicherplatz.", False)


# =============================================================
# ROUTE: MITGLIEDER-DETAILSEITE
# =============================================================
@app.get("/team/{user_id}/roles", response_class=HTMLResponse)
async def team_role_manager(request: Request, user_id: int, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm="can_promote")
    guild, config = ctx.guild, ctx.config
    member = guild.get_member(user_id)
    if not member:
        raise HTTPException(status_code=404, detail="Mitglied nicht gefunden.")
    if member.bot or member.id == guild.owner_id or member.id == int(ctx.user["id"]):
        raise HTTPException(status_code=403, detail="Dieses Mitglied kann nicht über die Rollenverwaltung bearbeitet werden.")

    team_role_ids = ordered_team_role_ids(guild, config.get("team_role_ids", []))
    was_team_member = bool(team_role_ids and any(r.id in team_role_ids for r in member.roles))
    actor_idx = team_rank(ctx.member, team_role_ids) if ctx.member else -1
    target_idx = team_rank(member, team_role_ids)
    if not ctx.perms.get("is_admin") and actor_idx <= target_idx:
        raise HTTPException(status_code=403, detail="Du kannst nur Mitglieder mit niedrigerem Rang bearbeiten.")

    # Grouped, searchable manager; posts the same fields to the unchanged save handler below.
    from pulse_role_manager import ROLE_CSS, render_role_manager
    return render_page(
        f"Rollenverwaltung · {member.display_name}", ctx, "team",
        render_role_manager(esc, ctx, member, team_role_ids), ROLE_CSS,
    )


@app.post("/team/{user_id}/roles")
async def team_role_manager_save(
    request: Request,
    user_id: int,
    add_role_ids: List[int] = Form(default=[]),
    remove_role_ids: List[int] = Form(default=[]),
    action_reason: str = Form(...),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, perm="can_promote")
    guild, config = ctx.guild, ctx.config
    member = guild.get_member(user_id)
    if not member:
        return back("/team", "Mitglied nicht gefunden.", False)
    if member.bot or member.id == guild.owner_id or member.id == int(ctx.user["id"]):
        return back(f"/team/{user_id}/roles", "Dieses Mitglied kann nicht bearbeitet werden.", False)

    add_set, remove_set = set(add_role_ids or []), set(remove_role_ids or [])
    team_role_ids = ordered_team_role_ids(guild, config.get("team_role_ids", []))
    was_team_member = bool(team_role_ids and any(r.id in team_role_ids for r in member.roles))
    conflict = add_set & remove_set
    if conflict:
        return back(f"/team/{user_id}/roles", "Eine Rolle darf nicht gleichzeitig hinzugefügt und entfernt werden.", False)

    team_role_ids = ordered_team_role_ids(guild, config.get("team_role_ids", []))
    actor_idx = team_rank(ctx.member, team_role_ids) if ctx.member else -1
    target_idx = team_rank(member, team_role_ids)
    if not ctx.perms.get("is_admin") and actor_idx <= target_idx:
        return back(f"/team/{user_id}/roles", "Du kannst nur Mitglieder mit niedrigerem Rang bearbeiten.", False)

    action_reason = (action_reason or "").strip()[:500]
    if not action_reason:
        return back(f"/team/{user_id}/roles", "Bitte einen Grund für die Rollenänderung angeben.", False)

    all_ids = add_set | remove_set
    if not all_ids:
        return back(f"/team/{user_id}/roles", "Keine Rollenänderung ausgewählt.", False)

    selected = []
    for rid in sorted(all_ids):
        role = guild.get_role(rid)
        if not role or role.is_default() or role.managed:
            return back(f"/team/{user_id}/roles", f"Rolle {rid} ist nicht verwaltbar.", False)
        if guild.me and role.position >= guild.me.top_role.position:
            return back(f"/team/{user_id}/roles", f"Die Rolle **{role.name}** steht über der Bot-Rolle.", False)
        if not ctx.perms.get("is_admin") and role.position >= ctx.member.top_role.position:
            return back(f"/team/{user_id}/roles", f"Die Rolle **{role.name}** steht auf oder über deinem Rang.", False)
        selected.append(role)

    old_roles = [r for r in member.roles if r.id in all_ids]
    to_add = [r for r in selected if r.id in add_set and r not in member.roles]
    to_remove = [r for r in selected if r.id in remove_set and r in member.roles]

    try:
        if to_add:
            await member.add_roles(*to_add, reason=f"Rollenänderung durch {ctx.user.get('global_name') or ctx.user.get('username') or 'Team'}: {action_reason}")
        if to_remove:
            await member.remove_roles(*to_remove, reason=f"Rollenänderung durch {ctx.user.get('global_name') or ctx.user.get('username') or 'Team'}: {action_reason}")

        is_team_member = bool(team_role_ids and any(r.id in team_role_ids for r in member.roles))
        if not was_team_member and is_team_member:
            team_db = load_json(DATA_FILE, {})
            team_entry = user_entry(team_db, str(member.id))
            team_entry["team_since"] = now_de().isoformat(timespec="seconds")
            save_json(DATA_FILE, team_db)

        actor = ctx.user.get("global_name") or ctx.user.get("username") or "Team"
        add_text = ", ".join(r.mention for r in to_add) or "Keine"
        remove_text = ", ".join(r.mention for r in to_remove) or "Keine"
        await send_team_update_embed(
            guild,
            "⚙️ Team-Update: Rollenänderung",
            f"{member.mention} wurde über die Pulse-Rollenverwaltung geändert.",
            _pulse_color("info"),
            target=member.mention,
            action="Mehrfach-Rollenänderung",
            actor=actor,
            fields=[
                ("➕ Hinzugefügt", add_text, False),
                ("➖ Entfernt", remove_text, False),
                ("📝 Grund", action_reason, False),
            ],
            thumbnail=member.display_avatar.url,
        )
        log_audit(actor, ctx.user["id"], "Rollenänderung", f"{member.display_name}: +{[r.name for r in to_add]} -{[r.name for r in to_remove]} · Grund: {action_reason}")
        return back(f"/team/{user_id}/roles", "Rollenänderung erfolgreich gespeichert.")
    except discord.Forbidden:
        return back(f"/team/{user_id}/roles", "Discord verweigert die Rollenänderung. Prüfe Bot-Rolle und Rollenrechte.", False)
    except Exception as exc:
        print(f"Rollenverwaltung fehlgeschlagen: {exc}")
        return back(f"/team/{user_id}/roles", "Rollenänderung fehlgeschlagen.", False)

@app.get("/member/{user_id}", response_class=HTMLResponse)
async def member_detail(request: Request, user_id: int, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    guild = ctx.guild
    member = guild.get_member(user_id)
    if not member:
        raise HTTPException(status_code=404, detail=f"Mitglied mit ID {user_id} wurde nicht gefunden.")

    team_role_ids = ordered_team_role_ids(guild, ctx.config.get("team_role_ids", []))
    team_db = load_json(DATA_FILE, {})
    shifts_db = load_shifts()
    info = team_db.get(str(user_id), {"warns_list": [], "notes": [], "ticket_cases": 0, "support_cases": 0})
    if normalize_warns(info):
        team_db[str(user_id)] = info
        save_json(DATA_FILE, team_db)
    normalize_warns(info)
    warns = info.get("warns_list", [])
    active_warn_count = len(active_warns(info))
    weekly = calculate_weekly_seconds(str(user_id), shifts_db["history"], shifts_db["active_shifts"]) / 3600
    support_period_start = pulse_db.get_support_case_period_start()
    voice_case_stats = pulse_db.voice_support_case_counts(support_period_start, guild.id)
    voice_active_counts = active_voice_support_case_counts(guild.id)
    ticket_stats = pulse_db.ticket_member_activity_counts(support_period_start, guild.id)
    voice_case_completed = int(voice_case_stats.get(str(user_id), {}).get("completed", 0))
    voice_case_active = int(voice_active_counts.get(str(user_id), 0))
    ticket_case_stats = ticket_stats.get(str(user_id), {})
    ticket_case_completed = int(ticket_case_stats.get("completed", 0))
    ticket_case_active = int(ticket_case_stats.get("active", 0))
    support_period_start_label = format_support_case_period_start(support_period_start)

    roles = [r for r in member.roles if r.id in team_role_ids]
    top = max(roles, key=lambda r: r.position) if roles else member.top_role
    top_color = role_hex(top)
    is_self = str(user_id) == ctx.user["id"]

    warns_html = ""
    for w in warns:
        proof = safe_url(w.get("proof"))
        proof_btn = f'<a href="{esc(proof)}" target="_blank" rel="noopener noreferrer" class="text-indigo-600 dark:text-indigo-400 hover:underline ml-2 font-medium">🔗 Beweis</a>' if proof else ""
        remove = f"""
                <form action="/action" method="post" onsubmit="return confirm('Diesen Warn wirklich zurückziehen?');">
                    <input type="hidden" name="action" value="remove_warn"><input type="hidden" name="user_id" value="{user_id}">
                    <input type="hidden" name="warn_id" value="{esc(w.get('id'))}"><input type="hidden" name="redirect_to_member" value="1">
                    <input type="text" name="warn_revoke_reason" maxlength="500" placeholder="Grund für die Rücknahme..." required class="{INPUT} py-1.5 w-56">
                    <button class="text-rose-500 hover:underline font-semibold">🗑️ Zurückziehen</button>
                </form>""" if ctx.perms["can_warn"] else ""
        warns_html += f"""
        <div class="bg-slate-50 dark:bg-[#0b0e14] p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 text-xs space-y-2">
            <div class="flex justify-between items-center text-slate-400 font-mono text-[10px]">
                <span>Datum: {esc(w.get('date', 'N/A'))} | Von: {esc(w.get('by', 'System'))}</span>{remove}
            </div>
            <div class="text-slate-400">Gültigkeit: {esc((w.get("duration_days") or "Dauerhaft") if not w.get("expires_at") else str(w.get("duration_days") or "") + " Tage · bis " + str(w.get("expires_at")) )}</div>
        <div class="text-slate-700 dark:text-slate-200"><strong>Grund:</strong> {esc(w.get('reason', 'Kein Grund'))}{proof_btn}</div>
        </div>"""

    notes_html = "".join(f"<div class='text-xs bg-slate-50 dark:bg-[#0b0e14] p-3 rounded-xl border border-slate-200 dark:border-slate-800 text-slate-700 dark:text-slate-300'>• {esc(n)}</div>"
                         for n in info.get("notes", []))

    history = [h for h in shifts_db["history"] if str(h.get("mod_id")) == str(user_id)][-8:]
    shifts_html = "".join(f"""
        <div class="flex justify-between text-xs bg-slate-50 dark:bg-[#0b0e14] px-3.5 py-2 rounded-xl border border-slate-200 dark:border-slate-800">
            <span class="text-slate-500">{esc(fmt_date(h.get('date')))}{' · automatisch beendet' if h.get('auto_closed') else ''}</span>
            <span class="font-mono font-semibold text-slate-800 dark:text-slate-200">{fmt_duration(h.get('duration_seconds', 0))}</span>
        </div>""" for h in reversed(history))

    actions_html = ""
    if ctx.perms["can_promote"] and not is_self:
        can_manage_target = ctx.perms.get("is_admin") or (
            team_role_ids and team_rank(ctx.member, team_role_ids) > team_rank(member, team_role_ids)
        )
        if can_manage_target:
            actions_html = f"""
            <div class="rounded-2xl border border-slate-200 dark:border-slate-800 bg-slate-50/70 dark:bg-[#0b0e14]/50 p-4 space-y-4">
                <div>
                    <div class="text-xs font-bold text-slate-900 dark:text-white mb-2">Team-Rang ändern</div>
                    <form action="/action" method="post" class="flex flex-wrap gap-2 items-center">
                        <input type="hidden" name="user_id" value="{member.id}"><input type="hidden" name="redirect_to_member" value="1">
                        <input type="text" name="action_reason" maxlength="500" placeholder="Pflicht: Grund für Beförderung / Degradierung..." required class="{INPUT} min-w-64">
                        <select name="probation_days" class="{INPUT}" aria-label="Probezeit (nur bei Beförderung)"><option value="">Probezeit wählen</option><option value="3">3 Tage Probezeit</option><option value="7">7 Tage Probezeit</option></select>
                        <button name="action" value="promote" onclick="this.form.querySelector('[name=probation_days]').required=true; return confirm('Wirklich befördern?')" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">⬆️ Befördern</button>
                        <button name="action" value="demote" onclick="this.form.querySelector('[name=probation_days]').required=false; return confirm('Wirklich degradieren?')" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">⬇️ Degradieren</button>
                    </form>
                </div>
                <div class="flex flex-wrap gap-2">
                    <a href="/team/{member.id}/roles" class="bg-indigo-500/10 hover:bg-indigo-600 hover:text-white text-indigo-600 dark:text-indigo-400 border border-indigo-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">⚙️ Rollen verwalten</a>
                    <form action="/action" method="post" class="flex flex-wrap gap-2 items-center" onsubmit="return confirm('Dieses Mitglied wirklich aus dem Team entfernen? Es bleibt auf dem Discord-Server.');">
                        <input type="hidden" name="action" value="kick"><input type="hidden" name="user_id" value="{member.id}">
                        <input type="text" name="action_reason" maxlength="500" placeholder="Pflicht: Grund für den Kick..." required class="{INPUT} w-64">
                        <button class="bg-rose-500/10 hover:bg-rose-600 hover:text-white text-rose-600 dark:text-rose-400 border border-rose-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">🚪 Kicken</button>
                    </form>
                </div>
            </div>
            <hr class="border-slate-100 dark:border-slate-800 my-4">"""
    warn_form = f"""
        <h3 class="text-sm font-bold text-slate-900 dark:text-white">Verwarnung ausstellen</h3>
        <form action="/action" method="post" class="space-y-2.5 text-xs">
            <input type="hidden" name="user_id" value="{member.id}"><input type="hidden" name="action" value="warn_with_proof"><input type="hidden" name="redirect_to_member" value="1">
            <input type="text" name="warn_reason" maxlength="500" placeholder="Grund für die Verwarnung..." required class="{INPUT}">
            <label class="block text-xs font-semibold">Gültigkeit der Warnung (Tage)<input type="number" name="warn_duration_days" min="1" max="365" value="7" required class="{INPUT} w-full mt-1"><span class="text-slate-400 font-normal">Nach Ablauf wird die Warn automatisch deaktiviert.</span></label>
            <input type="url" name="warn_proof" placeholder="Beweis-Link (Screenshot / Video URL)..." class="{INPUT}">
            <button class="bg-amber-500 hover:bg-amber-600 px-4 py-2 rounded-xl font-semibold text-white shadow-sm transition">⚠️ Verwarnung eintragen</button>
        </form>
        <hr class="border-slate-100 dark:border-slate-800 my-4">""" if ctx.perms["can_warn"] else ""

    note_form = f"""
        <form action="/action" method="post" class="flex gap-2 pt-1 text-xs">
            <input type="hidden" name="user_id" value="{member.id}"><input type="hidden" name="action" value="add_note"><input type="hidden" name="redirect_to_member" value="1">
            <input type="text" name="note_text" maxlength="500" placeholder="Neue Notiz..." required class="{INPUT}">
            <button class="{BTN} px-4">Hinzufügen</button>
        </form>""" if ctx.perms["can_add_notes"] else ""

    warn_color = "text-rose-500" if active_warn_count >= 5 else "text-amber-500"
    body = f"""
    <div class="flex items-center gap-4 mb-8">
        <a href="/team" class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-700 dark:text-slate-300 p-2.5 rounded-xl transition shadow-sm">←</a>
        <img src="{esc(member.display_avatar.url)}" alt="" class="w-12 h-12 rounded-full border border-slate-200 dark:border-slate-700 shadow-sm">
        <div>
            <h1 class="text-xl font-bold text-slate-900 dark:text-white leading-tight">{esc(member.display_name)}</h1>
            <p class="text-xs text-slate-400 font-mono">@{esc(member.name)}</p>
        </div>
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div class="lg:col-span-2 space-y-6">
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div class="{CARD} p-5"><div class="text-xs text-slate-400 font-semibold mb-1">Voice-Support erledigt</div><div class="text-2xl font-bold text-sky-600 dark:text-sky-400">{voice_case_completed}</div><div class="text-[10px] text-slate-400 mt-1">Seit {esc(support_period_start_label)}</div></div>
                <div class="{CARD} p-5"><div class="text-xs text-slate-400 font-semibold mb-1">Aktive Supportfälle</div><div class="text-2xl font-bold">{voice_case_active}</div><div class="text-[10px] text-slate-400 mt-1">Aktuell zugewiesen</div></div>
                <div class="{CARD} p-5"><div class="text-xs text-slate-400 font-semibold mb-1">Tickets erledigt</div><div class="text-2xl font-bold text-violet-600 dark:text-violet-400">{ticket_case_completed}</div><div class="text-[10px] text-slate-400 mt-1">Seit {esc(support_period_start_label)}</div></div>
                <div class="{CARD} p-5"><div class="text-xs text-slate-400 font-semibold mb-1">Aktive Tickets</div><div class="text-2xl font-bold">{ticket_case_active}</div><div class="text-[10px] text-slate-400 mt-1">Aktuell übernommen</div></div>
                <div class="{CARD} p-5"><div class="text-xs text-slate-400 font-semibold mb-1">Wochenstunden</div><div class="text-2xl font-bold text-indigo-600 dark:text-indigo-400">{weekly:.1f}h</div></div>
                <div class="{CARD} p-5"><div class="text-xs text-slate-400 font-semibold mb-1">Verwarnungen</div><div class="text-2xl font-bold {warn_color}">{len(warns)}/5</div></div>
            </div>
            <div class="{CARD} p-6 space-y-4">
                {actions_html}{warn_form}
                <h3 class="text-sm font-bold text-slate-900 dark:text-white">Notizen</h3>
                <div class="space-y-2 max-h-36 overflow-y-auto">{notes_html or "<p class='text-xs text-slate-400 italic'>Keine Notizen hinterlegt.</p>"}</div>
                {note_form}
            </div>
            <div class="{CARD} p-6 space-y-3">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white">Verwarnungs-Historie ({active_warn_count} aktiv / {len(warns)} gesamt)</h3>
                <div class="space-y-2 max-h-64 overflow-y-auto">{warns_html or "<p class='text-xs text-slate-400 italic'>Keine Verwarnungen vorhanden.</p>"}</div>
            </div>
        </div>
        <div class="space-y-4">
            <div class="text-right text-xl font-extrabold uppercase tracking-widest opacity-90 break-words" style="color:{top_color};">» {esc(ctx.guild_name)} ✕ {esc(top.name)}</div>
            <div class="{CARD} p-6 space-y-4 text-xs">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white border-b border-slate-100 dark:border-slate-800 pb-2">Information</h3>
                <div><div class="text-slate-400 mb-0.5">Nutzername</div><div class="font-medium">[{esc(top.name)}] {esc(member.display_name)}</div></div>
                <div><div class="text-slate-400 mb-0.5">ID</div><div class="text-slate-600 dark:text-slate-300 font-mono">{member.id}</div></div>
                <div><div class="text-slate-400 mb-0.5">Team seit</div><div>{esc(team_since_for(member, info))}</div></div>
                <div><div class="text-slate-400 mb-0.5">Auf dem Server seit</div><div>{member.joined_at.strftime('%d.%m.%Y') if member.joined_at else 'unbekannt'}</div></div>
            </div>
            <div class="{CARD} p-6 space-y-2">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white">Letzte Schichten</h3>
                {shifts_html or "<p class='text-xs text-slate-400 italic'>Noch keine Schichten.</p>"}
            </div>
        </div>
    </div>"""
    return render_page(member.display_name, ctx, "team", body)

# =============================================================
# ROUTE: TEAM-BESPRECHUNGS-TOOL
# =============================================================
@app.get("/meetings", response_class=HTMLResponse)
async def meetings_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    guild = ctx.guild
    meetings = load_meetings()
    support_period_start = pulse_db.get_support_case_period_start()
    support_counts = pulse_db.support_case_counts(support_period_start)
    support_period_total = sum(support_counts.values())
    support_period_start_label = format_support_case_period_start(support_period_start)
    uid = ctx.user["id"]
    rsvps = meetings["rsvps"]
    user_status = rsvps.get(uid, {}).get("status", "none")
    is_manager = ctx.perms["can_promote"] or ctx.perms["is_admin"]

    accepted = [v.get("name") for v in rsvps.values() if v.get("status") == "accepted"]
    declined = [v.get("name") for v in rsvps.values() if v.get("status") == "declined"]

    # Wer hat noch nicht geantwortet?
    team_role_ids = ctx.config.get("team_role_ids", [])
    pending = sorted(m.display_name for m in guild.members
                     if any(r.id in team_role_ids for r in m.roles) and str(m.id) not in rsvps)

    def li(names, color):
        return "".join(f"<li class='{color}'>• {esc(n)}</li>" for n in names)

    topics_html = ""
    for t in meetings["topics"]:
        can_del = is_manager or t.get("by_id") == uid
        del_btn = f"""
            <form action="/action" method="post" onsubmit="return confirm('Thema entfernen?');">
                <input type="hidden" name="action" value="delete_meeting_topic"><input type="hidden" name="topic_id" value="{esc(t.get('id'))}">
                <button class="text-rose-500 hover:underline">🗑️</button>
            </form>""" if can_del else ""
        topics_html += f"""
        <div class="bg-slate-50 dark:bg-[#0b0e14] p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 text-xs space-y-1">
            <div class="flex justify-between items-center font-semibold text-slate-900 dark:text-white gap-2">
                <span>📌 {esc(t.get('title'))}</span>
                <span class="flex items-center gap-2 text-[10px] text-slate-400 font-mono">Von: {esc(t.get('by'))}{del_btn}</span>
            </div>
            <p class="text-slate-600 dark:text-slate-300 break-words">{esc(t.get('details'))}</p>
        </div>"""

    def rsvp_btn(value, label, active_cls, idle_cls):
        return f'<button name="rsvp_status" value="{value}" class="{active_cls if user_status == value else idle_cls} px-4 py-2 rounded-xl text-xs transition">{label}</button>'

    support_period_action = f"""
        <form action="/action" method="post" onsubmit="return confirm('Teammeeting als abgeschlossen markieren und einen neuen Supportfall-Zeitraum starten? Die bisherige Zählung wird archiviert.');">
            <input type="hidden" name="action" value="reset_support_case_period">
            <button class="{BTN} px-4 py-2.5">✅ Teammeeting abgeschlossen · neue Support-Woche starten</button>
        </form>""" if is_manager else '<p class="text-[11px] text-slate-500">Die nächste Support-Woche kann nach dem Teammeeting durch die Teamleitung gestartet werden.</p>'
    support_period_card = f"""
        <div class="{CARD} p-5 space-y-3">
            <div class="flex items-start justify-between gap-3">
                <div>
                    <h3 class="text-sm font-bold text-slate-900 dark:text-white">🎧 Supportfälle zwischen Teammeetings</h3>
                    <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">Gezählt werden abgeschlossene Pulse-Tickets, die einem Teammitglied zugeordnet sind.</p>
                    <p class="text-[11px] text-slate-500 dark:text-slate-400 mt-2">Aktueller Zeitraum seit <strong>{esc(support_period_start_label)}</strong></p>
                </div>
                <div class="text-3xl font-extrabold text-sky-600 dark:text-sky-400">{support_period_total}</div>
            </div>
            {support_period_action}
        </div>"""

    admin_card = f"""
    <div class="{CARD} p-6 space-y-3 text-xs">
        <h3 class="text-sm font-bold text-slate-900 dark:text-white">⚙️ Besprechung ansetzen</h3>
        <form action="/action" method="post" class="space-y-3">
            <input type="hidden" name="action" value="set_meeting_info">
            <input type="text" name="meeting_title" maxlength="100" placeholder="Titel..." required class="{INPUT}">
            <input type="text" name="meeting_datetime" maxlength="100" placeholder="z. B. Sonntag, 18:00 Uhr" required class="{INPUT}">
            <textarea name="meeting_desc" maxlength="500" placeholder="Kurze Beschreibung..." class="{INPUT} h-16"></textarea>
            <label class="flex items-center gap-2"><input type="checkbox" name="reset_rsvps" checked class="rounded"> Zu-/Absagen zurücksetzen</label>
            <label class="flex items-center gap-2"><input type="checkbox" name="clear_topics" class="rounded"> Themenliste leeren</label>
            <label class="flex items-center gap-2"><input type="checkbox" name="announce" checked class="rounded"> Im Team-Update-Kanal ankündigen</label>
            <button class="w-full {BTN} py-2.5">Besprechung speichern</button>
        </form>
    </div>""" if is_manager else ""

    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">🎙️ Team-Besprechungs-Tool</h1>
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div class="lg:col-span-2 space-y-6">
            <div class="{CARD} p-6 space-y-4">
                <div class="flex justify-between items-start border-b border-slate-100 dark:border-slate-800 pb-4 gap-3">
                    <div>
                        <h2 class="text-lg font-bold text-slate-900 dark:text-white">{esc(meetings['title'])}</h2>
                        <p class="text-xs text-indigo-600 dark:text-indigo-400 font-mono mt-1">📅 {esc(meetings['date_time'])}</p>
                    </div>
                    <span class="bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-500/20 text-xs px-3 py-1 rounded-full font-semibold shrink-0">Anstehend</span>
                </div>
                <p class="text-xs text-slate-600 dark:text-slate-300 break-words">{esc(meetings['description'])}</p>
                <div class="pt-2">
                    <label class="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-2">Dein Status für die Besprechung:</label>
                    <form action="/action" method="post" class="flex gap-3">
                        <input type="hidden" name="action" value="meeting_rsvp">
                        {rsvp_btn("accepted", "✅ Zusage", "bg-emerald-600 text-white font-bold", "bg-emerald-500/10 text-emerald-600 border border-emerald-500/30")}
                        {rsvp_btn("declined", "❌ Absage", "bg-rose-600 text-white font-bold", "bg-rose-500/10 text-rose-600 border border-rose-500/30")}
                    </form>
                </div>
            </div>
            {support_period_card}
            <div class="{CARD} p-6 space-y-4">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white">💡 Themenvorschläge einreichen</h3>
                <form action="/action" method="post" class="space-y-3 text-xs">
                    <input type="hidden" name="action" value="add_meeting_topic">
                    <input type="text" name="topic_title" maxlength="100" placeholder="Thema / Titel..." required class="{INPUT}">
                    <textarea name="topic_details" maxlength="600" placeholder="Beschreibung / Details zum Thema..." required class="{INPUT} h-20"></textarea>
                    <button class="{BTN} px-4 py-2.5">Thema auf Tagesordnung setzen</button>
                </form>
                <div class="pt-4 border-t border-slate-100 dark:border-slate-800 space-y-2">
                    <h4 class="text-xs font-bold text-slate-900 dark:text-white">Eingereichte Themenvorschläge ({len(meetings['topics'])})</h4>
                    <div class="space-y-2 max-h-72 overflow-y-auto">{topics_html or "<p class='text-xs text-slate-400 italic'>Noch keine Themenvorschläge eingereicht.</p>"}</div>
                </div>
            </div>
        </div>
        <div class="space-y-6">
            <div class="{CARD} p-6 space-y-4 text-xs">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white border-b border-slate-100 dark:border-slate-800 pb-2">Teilnehmer-Übersicht</h3>
                <div><div class="font-bold text-emerald-600 dark:text-emerald-400 mb-1">Zugesagt ({len(accepted)})</div>
                    <ul class="space-y-1">{li(accepted, 'text-emerald-600 dark:text-emerald-400') or "<li class='text-slate-400 italic'>Niemand</li>"}</ul></div>
                <hr class="border-slate-100 dark:border-slate-800">
                <div><div class="font-bold text-rose-600 dark:text-rose-400 mb-1">Abgesagt ({len(declined)})</div>
                    <ul class="space-y-1">{li(declined, 'text-rose-600 dark:text-rose-400') or "<li class='text-slate-400 italic'>Niemand</li>"}</ul></div>
                <hr class="border-slate-100 dark:border-slate-800">
                <div><div class="font-bold text-slate-500 mb-1">Noch keine Antwort ({len(pending)})</div>
                    <ul class="space-y-1">{li(pending, 'text-slate-500 dark:text-slate-400') or "<li class='text-slate-400 italic'>Alle haben geantwortet 🎉</li>"}</ul></div>
            </div>
            {admin_card}
        </div>
    </div>"""
    return render_page("Teambesprechung", ctx, "meetings", body)


# =============================================================
# ROUTE: ABWESENHEITEN (LOA)
# =============================================================
@app.get("/loa", response_class=HTMLResponse)
async def loa_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    guild = ctx.guild
    is_manager = ctx.perms["can_promote"] or ctx.perms["is_admin"]
    uid = ctx.user["id"]
    team_role_ids = ctx.config.get("team_role_ids", [])

    entries = sorted(get_loas().values(), key=lambda l: (not l["active"], str(l["bis"])))
    entries_html = ""
    for l in entries:
        member = guild.get_member(l["user_id"])
        name = member.display_name if member else l["name"]
        can_cancel = is_manager or str(l["user_id"]) == uid
        state = (f'<span class="border {BADGE_WARN} text-[10px] px-2 py-0.5 rounded-full font-semibold">Aktiv</span>' if l["active"]
                 else '<span class="border border-slate-300 dark:border-slate-700 text-slate-400 text-[10px] px-2 py-0.5 rounded-full font-semibold">Abgelaufen</span>')
        btn = f"""
            <form action="/action" method="post" onsubmit="return confirm('Abmeldung wirklich beenden?');">
                <input type="hidden" name="action" value="cancel_loa"><input type="hidden" name="target_user_id" value="{l['user_id']}">
                <button class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-xs px-3.5 py-2 rounded-xl font-medium transition">{'Beenden' if l['active'] else 'Entfernen'}</button>
            </form>""" if can_cancel else ""
        entries_html += f"""
        <div class="{CARD} p-4 flex justify-between items-center gap-3 {'' if l['active'] else 'opacity-60'}">
            <div class="min-w-0">
                <div class="font-bold text-slate-900 dark:text-white text-sm flex items-center gap-2">{esc(name)} {state}</div>
                <div class="text-xs text-slate-400 mt-0.5">📅 {esc(fmt_date(l['von']))} bis {esc(fmt_date(l['bis']))}</div>
                <div class="text-xs text-slate-600 dark:text-slate-300 mt-1.5 break-words"><strong>Grund:</strong> {esc(l['grund'])}</div>
            </div>{btn}
        </div>"""

    # Auswahl: Manager sehen alle Teammitglieder, alle anderen nur sich selbst
    if is_manager:
        options = sorted(((m.display_name, m.id) for m in guild.members if any(r.id in team_role_ids for r in m.roles)),
                         key=lambda x: x[0].lower())
        if int(uid) not in [o[1] for o in options]:
            options.insert(0, (ctx.user.get("global_name", "Ich"), int(uid)))
    else:
        options = [(ctx.user.get("global_name", "Ich"), int(uid))]
    options_html = "".join(f'<option value="{i}" {"selected" if str(i) == uid else ""}>{esc(n)}</option>' for n, i in options)
    today = now_de().strftime("%Y-%m-%d")

    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">Abwesenheiten (LOA)</h1>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
        <div class="{CARD} p-6 space-y-4">
            <h2 class="text-base font-bold text-slate-900 dark:text-white">Neue Abmeldung eintragen</h2>
            <form action="/action" method="post" class="space-y-3.5 text-xs">
                <input type="hidden" name="action" value="submit_loa">
                <div><label class="block text-slate-500 dark:text-slate-400 mb-1 font-medium">Mitglied</label>
                    <select name="user_id" required class="{INPUT}">{options_html}</select></div>
                <div class="grid grid-cols-2 gap-3">
                    <div><label class="block text-slate-500 dark:text-slate-400 mb-1 font-medium">Startdatum</label>
                        <input type="date" name="loa_start" value="{today}" min="{today}" required class="{INPUT}"></div>
                    <div><label class="block text-slate-500 dark:text-slate-400 mb-1 font-medium">Enddatum</label>
                        <input type="date" name="loa_end" min="{today}" required class="{INPUT}"></div>
                </div>
                <div><label class="block text-slate-500 dark:text-slate-400 mb-1 font-medium">Grund</label>
                    <textarea name="loa_reason" maxlength="300" placeholder="Grund für die Abmeldung..." required class="{INPUT} h-24"></textarea></div>
                <button class="w-full {BTN} py-3">Abmeldung speichern</button>
            </form>
        </div>
        <div class="space-y-4">
            <h2 class="text-base font-bold text-slate-900 dark:text-white">Abmeldungen</h2>
            <div class="space-y-3">{entries_html or f"<div class='text-xs text-slate-400 italic {CARD} p-6'>Keine Abmeldungen vorhanden.</div>"}</div>
        </div>
    </div>"""
    return render_page("Abmeldungen (LOA)", ctx, "loa", body)


async def verify_application_member(request: Request, applicant_id) -> tuple[bool, int, str, str]:
    """Fail-closed eligibility check for the public recruitment form."""
    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        return False, 503, "", "Pulse ist noch nicht bereit. Bitte versuche es gleich erneut."

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return False, 503, "", "Der Discord-Server konnte gerade nicht geprüft werden."

    eligible_role = guild.get_role(APPLICATION_REQUIRED_ROLE_ID)
    if eligible_role is None:
        return False, 503, "", "Die für Bewerbungen erforderliche Discord-Rolle wurde nicht gefunden. Bitte informiere die Serverleitung."

    try:
        uid = int(applicant_id)
    except (TypeError, ValueError):
        return False, 403, eligible_role.name, "Bitte verbinde zuerst deinen Discord-Account."

    member = guild.get_member(uid)
    if member is None:
        try:
            member = await guild.fetch_member(uid)
        except discord.NotFound:
            return False, 403, eligible_role.name, "Du musst dem Discord-Server beitreten und die erforderliche Mitgliedsrolle besitzen."
        except (discord.Forbidden, discord.HTTPException):
            return False, 503, eligible_role.name, "Deine Mitgliedschaft konnte gerade nicht sicher geprüft werden. Bitte versuche es erneut."

    if member.bot:
        return False, 403, eligible_role.name, "Bot-Accounts können keine Bewerbung einreichen."
    if not any(role.id == APPLICATION_REQUIRED_ROLE_ID for role in member.roles):
        return False, 403, eligible_role.name, f"Dieses Bewerbungsformular ist nur für Mitglieder mit der Rolle „{eligible_role.name}“ freigeschaltet."

    return True, 200, eligible_role.name, ""


async def dm_applicant(request: Request, user_id, template: str, **values) -> bool:
    """BOT-10: DM to an applicant from a template; applicants may not be server members yet."""
    import pulse_app_comms
    bot = getattr(request.app.state, "bot", None)
    if bot is None or not str(user_id or "").isdigit():
        return False
    guild = bot.get_guild(GUILD_ID)
    try:
        user = (guild.get_member(int(user_id)) if guild else None) or bot.get_user(int(user_id)) or await bot.fetch_user(int(user_id))
    except (discord.NotFound, discord.HTTPException):
        return False
    return await send_dm_notification(user, pulse_app_comms.render(template, server=getattr(guild, "name", "dem Server"), **values))


async def notify_new_application(request: Request, application_id: str, application: dict) -> bool:
    """Notify the configured team channel, pinging only the recruitment alert role."""
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    if guild is None:
        print("⚠️ Neue Bewerbung gespeichert, aber Team-Benachrichtigung übersprungen: Discord-Server nicht verfügbar.")
        return False

    channel = guild.get_channel(APPLICATION_ALERT_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(APPLICATION_ALERT_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Neue Bewerbung gespeichert, aber Bewerbungs-Kanal nicht erreichbar: {type(exc).__name__}.")
            return False

    channel_guild = getattr(channel, "guild", None)
    if channel_guild is not None and channel_guild.id != GUILD_ID:
        print("⚠️ Bewerbungs-Benachrichtigung abgebrochen: Der konfigurierte Kanal liegt auf einem anderen Server.")
        return False
    if not callable(getattr(channel, "send", None)):
        print("⚠️ Bewerbungs-Benachrichtigung abgebrochen: Der konfigurierte Kanal kann keine Nachrichten empfangen.")
        return False

    alert_role = guild.get_role(APPLICATION_ALERT_ROLE_ID)
    applicant_id = str(application.get("user_id") or "")
    applicant_name = str(application.get("name") or "Unbekannt")[:80]
    career_label = str(application.get("career_label") or "Nicht angegeben")[:100]
    score = int(application.get("test_score") or 0)
    total = max(0, int(application.get("test_total") or 0))
    percent = max(0, min(100, int(application.get("test_percent") or 0)))
    result_label = "Richtwert erreicht" if application.get("test_passed") else "Richtwert nicht erreicht"

    embed = discord.Embed(
        title="📝 Neue Bewerbung eingegangen",
        description="Eine neue Bewerbung wurde über das externe Pulse-Bewerbungsformular eingereicht.",
        color=_pulse_color("info"),
        timestamp=now_de(),
    )
    embed.add_field(name="Bewerber", value=f"{esc(applicant_name)}\nDiscord-ID: `{esc(applicant_id)}`", inline=True)
    embed.add_field(name="Laufbahn", value=esc(career_label), inline=True)
    embed.add_field(name="Online-Test", value=f"{score}/{total} richtig ({percent} %)\n{result_label}", inline=True)
    embed.add_field(
        name="Bewerbung öffnen",
        value=f"[In Pulse zur Bewerbungsübersicht]({request_origin(request)}/applications)",
        inline=False,
    )
    embed.set_footer(text=f"Pulse Recruiting · Bewerbungs-ID {application_id}")

    content = alert_role.mention if alert_role else f"<@&{APPLICATION_ALERT_ROLE_ID}>"
    try:
        await send_styled(
            channel,
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                roles=[alert_role] if alert_role else [],
                users=False,
                everyone=False,
                replied_user=False,
            ),
        )
        if alert_role is None:
            print(f"⚠️ Neue Bewerbung wurde gemeldet, aber Ping-Rolle {APPLICATION_ALERT_ROLE_ID} wurde im Server nicht gefunden.")
        return True
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"⚠️ Bewerbung {application_id} wurde gespeichert, aber Team-Benachrichtigung fehlgeschlagen: {type(exc).__name__}.")
        return False


# =============================================================
# ROUTE: ÖFFENTLICHES BEWERBUNGSFORMULAR MIT LAUFBAHN-TEST
# =============================================================
def apply_draft_key(applicant_id, career_path: str) -> str:
    return f"pulse:apply-draft:v1:{applicant_id}:{career_path}"


# K-01: browser-side draft (localStorage, per Discord ID + career) and session keep-alive.
APPLY_DRAFT_SCRIPT = """
<div id="applyDraftNote" hidden class="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-[11px]"></div>
<div id="applySessionNote" hidden class="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-[11px]"></div>
<script>
(function(){
  const form=document.querySelector('[data-apply-wizard]'); if(!form)return;
  const KEY=__KEY__, LOGIN=__LOGIN__;
  const note=document.getElementById('applyDraftNote'), sess=document.getElementById('applySessionNote');
  const skip=new Set(['action','applicant_id','applicant_name','career_path']);
  const fields=()=>Array.from(form.querySelectorAll('input,textarea,select')).filter(el=>el.name&&!skip.has(el.name));
  function store(){
    try{
      const data={};
      fields().forEach(el=>{
        if(el.type==='radio'||el.type==='checkbox'){ if(el.checked)(data[el.name]=data[el.name]||[]).push(el.value); }
        else data[el.name]=el.value;
      });
      localStorage.setItem(KEY,JSON.stringify({at:Date.now(),data:data}));
    }catch(_){}
  }
  function restore(){
    try{
      const raw=localStorage.getItem(KEY); if(!raw)return false;
      const saved=(JSON.parse(raw)||{}).data||{}; let n=0;
      fields().forEach(el=>{
        if(!(el.name in saved))return;
        const v=saved[el.name];
        if(el.type==='radio'||el.type==='checkbox')el.checked=Array.isArray(v)&&v.indexOf(el.value)>=0;
        else el.value=v;
        n++;
      });
      return n>0;
    }catch(_){return false;}
  }
  let timer=null, active=false;
  form.addEventListener('input',()=>{active=true;clearTimeout(timer);timer=setTimeout(store,400);});
  form.addEventListener('change',()=>{active=true;store();});
  form.addEventListener('submit',store);
  if(restore()){
    note.hidden=false;
    note.textContent='💾 Dein gespeicherter Entwurf wurde wiederhergestellt.';
    form.dispatchEvent(new Event('change'));
  }
  async function keepAlive(){
    if(!active)return; active=false;
    try{
      const r=await fetch('/apply/keepalive',{method:'POST',credentials:'same-origin'});
      if(r.status===401){
        sess.hidden=false;
        sess.innerHTML='⌛ Deine Sitzung ist abgelaufen. Deine Antworten sind in diesem Browser gespeichert. '
          +'<a class="underline font-semibold" href="'+LOGIN+'">Neu mit Discord verbinden</a> – danach ist alles wieder da.';
      }
    }catch(_){}
  }
  setInterval(keepAlive,10*60*1000);
})();
</script>"""

APPLY_DRAFT_CLEAR_SCRIPT = "<script>try{localStorage.removeItem(__KEY__)}catch(_){}</script>"


@app.get("/apply", response_class=HTMLResponse)
async def public_apply_page(request: Request, apply_session: str = Cookie(None)):
    bot = getattr(request.app.state, "bot", None)
    application_guild = bot.get_guild(GUILD_ID) if bot else None
    required_role_obj = application_guild.get_role(APPLICATION_REQUIRED_ROLE_ID) if application_guild else None
    required_role_display = esc(required_role_obj.name if required_role_obj else f"Discord-Rolle {APPLICATION_REQUIRED_ROLE_ID}")
    applicant = verify_session_token(apply_session, TOKEN_APPLY)
    if applicant:
        eligible, eligibility_status, eligibility_role, eligibility_message = await verify_application_member(
            request, applicant.get("id")
        )
        if not eligible:
            retry_link = '<a href="/apply" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Erneut prüfen</a>'
            if eligibility_status == 503:
                return HTMLResponse(
                    simple_page("⏳", "Bewerbung gerade nicht verfügbar", eligibility_message, retry_link),
                    status_code=503,
                )
            return HTMLResponse(
                simple_page("🔒", "Bewerbung nicht freigegeben", eligibility_message, retry_link),
                status_code=403,
            )
    if not applicant:
        return f"""
        <!DOCTYPE html><html lang="de"><head>{get_head_html("Team-Bewerbung")}</head>
        <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-900 dark:text-white min-h-screen flex items-center justify-center p-4 font-sans">
        <div class="{CARD} p-8 shadow-xl w-full max-w-lg text-center space-y-5">
            <div class="text-4xl">📝</div><h1 class="text-xl font-bold">Team-Bewerbung</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400">Dieses Formular ist ausschließlich für Discord-Mitglieder mit der Rolle <strong>{required_role_display}</strong> freigeschaltet. Verbinde deinen Discord-Account, damit Pulse deine Mitgliedschaft prüfen kann. Danach kannst du deine Laufbahn auswählen und den passenden Eignungstest online absolvieren.</p>
            <a href="/apply/login" class="block w-full bg-[#5865F2] hover:bg-[#4752C4] text-white font-semibold py-3 rounded-xl">Mit Discord verbinden</a>
            <a href="/" class="text-xs text-slate-400 hover:underline">← Zurück</a>
        </div></body></html>"""

    requested_career_path = (request.query_params.get("career") or "").strip()
    module_states = application_module_states(load_config())
    if requested_career_path in APPLICATION_CAREERS and not module_states.get(requested_career_path, True):
        return HTMLResponse(
            simple_page("⏸️", "Bewerbung derzeit geschlossen", "Diese Laufbahn nimmt aktuell keine Bewerbungen an. Bitte wähle eine andere aktive Laufbahn oder schau später wieder vorbei.", '<a href="/apply" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Aktive Laufbahnen</a>'),
            status_code=404,
        )
    career_path = requested_career_path if requested_career_path in APPLICATION_CAREERS else ""

    applicant_name = applicant.get("global_name") or applicant.get("username") or "Discord-Mitglied"
    applicant_id = esc(applicant.get("id"))
    avatar_hash = esc(applicant.get("avatar") or "0")
    avatar_url = f"https://cdn.discordapp.com/avatars/{applicant_id}/{avatar_hash}.png"

    if not career_path:
        career_cards = ""
        for key, career in APPLICATION_CAREERS.items():
            if not module_states.get(key, True):
                continue
            career_cards += f"""
            <a href="/apply?career={key}" class="block text-left rounded-2xl border border-slate-200 dark:border-slate-700 p-4 hover:border-indigo-500 hover:shadow-md transition">
                <div class="flex items-center justify-between gap-3">
                    <h2 class="font-bold text-base">{esc(career["label"])}</h2>
                    <span class="text-[10px] font-bold rounded-full bg-indigo-500/10 text-indigo-600 dark:text-indigo-300 px-2.5 py-1">Eignungstest + Praxis</span>
                </div>
                <p class="text-xs text-slate-500 dark:text-slate-400 mt-2">{esc(career["description"])}</p>
                <p class="text-[10px] text-slate-400 mt-3">{len(career["questions"])} Testfragen · {len(APPLICATION_PRACTICAL_TASKS.get(key, []))} Praxisaufgaben · Bestehens-Richtwert: {career["pass_percent"]}%</p>
            </a>"""
        if not career_cards:
            return HTMLResponse(simple_page("⏸️", "Aktuell keine Bewerbungen geöffnet", "Die Leitung hat vorübergehend alle Bewerbungsmodule deaktiviert. Bitte schau später wieder vorbei."), status_code=503)
        return f"""
        <!DOCTYPE html><html lang="de"><head>{get_head_html("Laufbahn wählen · Team-Bewerbung")}</head>
        <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-900 dark:text-white min-h-screen flex items-center justify-center p-4 font-sans">
        <main class="w-full max-w-2xl space-y-5">
            <div class="{CARD} p-6 sm:p-8 space-y-5">
                <div class="flex items-center gap-3">
                    <img src="{avatar_url}" class="w-11 h-11 rounded-full" alt="">
                    <div><div class="font-bold">{esc(applicant_name)}</div><div class="text-[10px] text-slate-400">Discord verifiziert · ID {applicant_id}</div></div>
                </div>
                <div><div class="text-xs uppercase tracking-widest font-bold text-indigo-500">Pulse Recruiting</div>
                    <h1 class="text-2xl font-bold mt-1">Welche Laufbahn möchtest du einschlagen?</h1>
                    <p class="text-xs text-slate-500 dark:text-slate-400 mt-2">Wähle die Ebene, für die du dich bewerben möchtest. Jede Laufbahn hat eigene Fragen. Dein Ergebnis wird automatisch ausgewertet und zusammen mit deiner Bewerbung an das Team übermittelt.</p>
                </div>
                <div class="grid gap-3">{career_cards}</div>
                <p class="text-[10px] text-slate-400">Hinweis: Die Auswahl und das Testergebnis vergeben keine Discord-Rollen. Über eine Einstellung und die passende Rolle entscheidet weiterhin die zuständige Führungsebene.</p>
                <div class="text-center"><a href="/" class="text-xs text-slate-400 hover:underline">← Zurück</a></div>
            </div>
        </main></body></html>"""

    career = APPLICATION_CAREERS[career_path]
    question_markup = render_career_test(career_path)
    practical_markup = render_practical_tasks(career_path)
    import pulse_retention
    privacy_notice = pulse_retention.privacy_notice_html(esc)  # D-08
    draft_script = (APPLY_DRAFT_SCRIPT
                    .replace("__KEY__", json.dumps(apply_draft_key(applicant.get("id"), career_path)))
                    .replace("__LOGIN__", json.dumps(f"/apply/login?career={quote(career_path)}")))
    page = f"""
    <!DOCTYPE html><html lang="de"><head>{get_head_html("Online-Eignungstest · " + career["label"])}</head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-900 dark:text-white min-h-screen p-4 sm:p-6 font-sans">
        <main class="mx-auto w-full max-w-3xl space-y-5">
            <div class="{CARD} p-6 sm:p-8 space-y-5">
                <div class="flex items-center gap-3">
                    <img src="{avatar_url}" class="w-11 h-11 rounded-full" alt="">
                    <div><div class="font-bold">{esc(applicant_name)}</div><div class="text-[10px] text-slate-400">Discord verifiziert</div></div>
                    <a href="/apply" class="ml-auto text-xs text-indigo-500 hover:underline">Laufbahn ändern</a>
                </div>
                <div class="rounded-2xl bg-indigo-500/10 border border-indigo-500/20 p-4">
                    <div class="text-[10px] uppercase tracking-widest font-bold text-indigo-500">Deine Bewerbung</div>
                    <h1 class="text-2xl font-bold mt-1">{esc(career["label"])}</h1>
                    <p class="text-xs text-slate-500 dark:text-slate-400 mt-2">{esc(career["description"])}</p>
                    <p class="text-[10px] text-slate-500 dark:text-slate-400 mt-3">{len(career["questions"])} Fragen · Bestehens-Richtwert: {career["pass_percent"]}%</p>
                </div>
                <form action="/action" method="post" class="space-y-5 text-xs" data-apply-wizard novalidate>
                    <input type="hidden" name="action" value="submit_application">
                    <input type="hidden" name="applicant_id" value="{applicant_id}">
                    <input type="hidden" name="applicant_name" value="{esc(applicant_name)}">
                    <input type="hidden" name="career_path" value="{career_path}">
                    <div class="rounded-2xl border border-indigo-500/20 bg-gradient-to-r from-indigo-500/10 to-violet-500/10 p-4 space-y-3">
                        <div class="flex flex-wrap items-center justify-between gap-2">
                            <div><p class="text-[10px] uppercase tracking-widest font-bold text-indigo-500">Pulse Candidate Experience</p>
                            <p id="applicationProgressLabel" class="text-sm font-bold mt-1">Dein Fortschritt · Vorbereitung</p></div>
                            <span id="applicationProgressPercent" class="text-lg font-extrabold text-indigo-500">0%</span>
                        </div>
                        <div class="h-2 rounded-full overflow-hidden bg-slate-200 dark:bg-slate-800"><div id="applicationProgressBar" class="h-full rounded-full bg-gradient-to-r from-indigo-500 to-violet-500 transition-all duration-300" style="width:0%"></div></div>
                        <div class="grid grid-cols-3 gap-2">
                            <div data-apply-step-indicator="0" class="rounded-xl border border-indigo-500/30 bg-indigo-500/10 p-2"><p class="text-[10px] font-bold">01 · Profil</p><p class="text-[10px] text-slate-500 mt-1">Motivation</p></div>
                            <div data-apply-step-indicator="1" class="rounded-xl border border-slate-200 dark:border-slate-700 p-2"><p class="text-[10px] font-bold">02 · Wissen</p><p class="text-[10px] text-slate-500 mt-1">Regeltest</p></div>
                            <div data-apply-step-indicator="2" class="rounded-xl border border-slate-200 dark:border-slate-700 p-2"><p class="text-[10px] font-bold">03 · Praxis</p><p class="text-[10px] text-slate-500 mt-1">Fallsimulation</p></div>
                        </div>
                        <p class="text-[10px] text-slate-500 dark:text-slate-400">Deine Angaben bleiben bis zum Absenden in diesem Formular. Die Praxisantworten werden von Menschen geprüft — keine automatische Einstellungsentscheidung.</p>
                    </div>
                    <section data-apply-step="0" class="space-y-2">
                        <h2 class="font-bold text-base">1. Motivation</h2>
                        <label for="applicant_text" class="block text-slate-500 dark:text-slate-400">Warum möchtest du diese Laufbahn einschlagen? Beschreibe kurz deine Erfahrung, Stärken und Motivation.</label>
                        <textarea id="applicant_text" name="applicant_text" maxlength="2000" placeholder="Erzähle etwas über dich, deine Erfahrung und deine Motivation…" required class="{INPUT} h-32"></textarea>
                    </section>
                    <section data-apply-step="1" class="space-y-3">
                        <div><h2 class="font-bold text-base">2. Online-Eignungstest</h2>
                            <p class="text-slate-500 dark:text-slate-400 mt-1">Beantworte alle Fragen. Das System prüft deine Antworten automatisch. Das Ergebnis ist eine Entscheidungshilfe und führt nicht allein automatisch zur Annahme oder Ablehnung.</p>
                        </div>
                        {question_markup}
                    </section>
                    {practical_markup}
                    {privacy_notice}
                    <div class="flex flex-wrap items-center justify-between gap-3 pt-3 border-t border-slate-200 dark:border-slate-800">
                        <button type="button" id="applicationPrev" hidden class="px-4 py-3 rounded-xl border border-slate-200 dark:border-slate-700 font-semibold">← Zurück</button>
                        <span id="applicationStepLabel" class="text-[10px] text-slate-500 dark:text-slate-400">Schritt 1 von 3</span>
                        <div class="ml-auto flex gap-2">
                            <button type="button" id="applicationNext" hidden class="px-5 py-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold">Weiter <span aria-hidden="true">→</span></button>
                            <button type="submit" id="applicationSubmit" class="px-5 py-3 rounded-xl bg-emerald-600 hover:bg-emerald-500 font-semibold text-white">Bewerbung abschicken ✓</button>
                        </div>
                    </div>
                    <script>
                    (function(){{
                        const form=document.querySelector('[data-apply-wizard]');
                        if(!form)return;
                        const panels=Array.from(form.querySelectorAll('[data-apply-step]'));
                        const prev=document.getElementById('applicationPrev');
                        const next=document.getElementById('applicationNext');
                        const submit=document.getElementById('applicationSubmit');
                        const stepLabel=document.getElementById('applicationStepLabel');
                        const progressLabel=document.getElementById('applicationProgressLabel');
                        const progressPercent=document.getElementById('applicationProgressPercent');
                        const progressBar=document.getElementById('applicationProgressBar');
                        const indicators=Array.from(form.querySelectorAll('[data-apply-step-indicator]'));
                        const names=['Profil & Motivation','Regelwissen','Praxis-Simulation'];
                        let active=0;
                        function updateProgress(){{
                            const controls=Array.from(form.querySelectorAll('[required]'));
                            const groups=new Map();
                            controls.forEach((el,index)=>{{
                                const key=el.type==='radio'?'radio:'+el.name:(el.name||'field:'+index);
                                if(!groups.has(key))groups.set(key,[]);
                                groups.get(key).push(el);
                            }});
                            let complete=0;
                            groups.forEach((group)=>{{
                                const first=group[0];
                                const done=first.type==='radio'?group.some(el=>el.checked):
                                    first.tagName==='SELECT'?Boolean(first.value):
                                    Boolean(String(first.value||'').trim())&&first.checkValidity();
                                if(done)complete++;
                            }});
                            const total=Math.max(1,groups.size);
                            const pct=Math.round(complete/total*100);
                            progressPercent.textContent=pct+'%';
                            progressBar.style.width=pct+'%';
                            progressLabel.textContent=complete+' von '+groups.size+' Pflichtfeldern erledigt';
                        }}
                        function showStep(index){{
                            active=Math.max(0,Math.min(index,panels.length-1));
                            panels.forEach((panel,i)=>panel.hidden=i!==active);
                            prev.hidden=active===0;
                            next.hidden=active===panels.length-1;
                            submit.hidden=active!==panels.length-1;
                            stepLabel.textContent='Schritt '+(active+1)+' von '+panels.length;
                            progressLabel.dataset.activeStep=String(active);
                            indicators.forEach((el,i)=>{{
                                el.classList.toggle('border-indigo-500/40',i===active);
                                el.classList.toggle('bg-indigo-500/10',i===active);
                                el.classList.toggle('border-slate-200',i!==active);
                            }});
                            const heading=panels[active].querySelector('h2,legend');
                            const label=heading?heading.textContent.trim():names[active];
                            stepLabel.setAttribute('aria-label',label);
                            window.scrollTo({{top:0,behavior:'smooth'}});
                            updateProgress();
                        }}
                        function firstInvalid(panel){{
                            return Array.from(panel.querySelectorAll('input,textarea,select')).find(el=>el.required&&!el.checkValidity());
                        }}
                        function validatePanel(panel){{
                            const invalid=firstInvalid(panel);
                            if(invalid){{invalid.reportValidity();invalid.focus({{preventScroll:true}});return false;}}
                            return true;
                        }}
                        prev.addEventListener('click',()=>showStep(active-1));
                        next.addEventListener('click',()=>{{
                            if(validatePanel(panels[active]))showStep(active+1);
                        }});
                        form.addEventListener('input',updateProgress);
                        form.addEventListener('change',updateProgress);
                        form.addEventListener('submit',event=>{{
                            for(let i=0;i<panels.length;i++){{
                                if(!validatePanel(panels[i])){{
                                    event.preventDefault();
                                    showStep(i);
                                    const invalid=firstInvalid(panels[i]);
                                    if(invalid)invalid.reportValidity();
                                    return;
                                }}
                            }}
                            submit.disabled=true;
                            submit.textContent='Wird sicher übermittelt …';
                        }});
                        showStep(0);
                    }})();
                    </script>
                </form>
                {draft_script}
                <div class="text-center"><a href="/" class="text-xs text-slate-400 hover:underline">← Zurück</a></div>
            </div>
        </main>
    </body></html>"""
    response = HTMLResponse(page)
    set_apply_session_cookie(response, request, applicant)  # sliding 3-hour session (E-12)
    return response


@app.get("/apply/login")
async def apply_login(request: Request, career: str = ""):
    state = secrets.token_urlsafe(24)
    redirect_apply = oauth_redirect_uri(request, application=True)
    url = (f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}"
           f"&redirect_uri={quote(redirect_apply, safe='')}"
           f"&response_type=code&scope=identify&state={state}")
    response=RedirectResponse(url=url,status_code=303)
    response.set_cookie("apply_oauth_state",state,max_age=600,httponly=True,samesite="lax",secure=oauth_cookie_secure(request))
    if career in APPLICATION_CAREERS:
        response.set_cookie(APPLY_RETURN_COOKIE,career,max_age=600,httponly=True,samesite="lax",secure=oauth_cookie_secure(request))
    return response


@app.get("/apply/callback")
async def apply_callback(request: Request, code: str=None, state: str=None, error: str=None, apply_oauth_state: str=Cookie(None)):
    retry='<a href="/apply" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Zur Bewerbung</a>'
    if error or not code or not state or not apply_oauth_state or not hmac.compare_digest(state,apply_oauth_state):
        return HTMLResponse(simple_page('❌','Bewerbungs-Login fehlgeschlagen','Bitte erneut versuchen.',retry),status_code=400)
    redirect_apply=oauth_redirect_uri(request, application=True)
    ud, problem = await discord_oauth_identify(code, redirect_apply)  # P-12
    if ud is None: return HTMLResponse(simple_page('❌','Bewerbungs-Login fehlgeschlagen',problem,retry),status_code=502)
    session={'id':str(ud.get('id')),'username':ud.get('username'),'global_name':ud.get('global_name') or ud.get('username'),'avatar':ud.get('avatar')}
    response=RedirectResponse(apply_return_path(request),status_code=303); set_apply_session_cookie(response, request, session); response.delete_cookie('apply_oauth_state'); response.delete_cookie(APPLY_RETURN_COOKIE); return response


@app.post("/apply/keepalive")
async def apply_keepalive(request: Request, apply_session: str = Cookie(None)):
    """K-01: renews the 3-hour application session while the applicant is typing (sliding expiry)."""
    applicant = verify_session_token(apply_session, TOKEN_APPLY)
    if not applicant:
        return JSONResponse({"ok": False, "expired": True}, status_code=401)
    response = JSONResponse({"ok": True, "expires_in": APPLY_SESSION_SECONDS})
    set_apply_session_cookie(response, request, applicant)
    return response


# =============================================================
# ROUTE: BEWERBUNGEN ÜBERSICHT (offen + Verlauf)
# =============================================================
@app.get("/applications", response_class=HTMLResponse)
async def applications_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None)
    if not (
        ctx.perms.get("can_view_dashboard")
        or ctx.perms.get("can_manage_applications")
        or ctx.perms.get("can_promote")
        or ctx.perms.get("is_admin")
    ):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung für Bewerbungen.")
    uid = ctx.user["id"]
    show_history = request.query_params.get("view") == "history"
    can_decide = ctx.perms["can_manage_applications"] or ctx.perms["can_promote"] or ctx.perms["is_admin"]
    apps = load_json(APPS_FILE, {})

    active_application_statuses = {"pending", "interview"}
    items = [(k, v) for k, v in apps.items() if (v.get("status") not in active_application_statuses) == show_history]
    items.reverse()  # neueste zuerst

    apps_html = ""
    for app_id, item in items:
        up, down = item.get("upvotes", []), item.get("downvotes", [])
        voted_up, voted_down = uid in up, uid in down
        career_summary = (
            f'<span class="rounded-full bg-indigo-500/10 text-indigo-600 dark:text-indigo-300 px-2.5 py-1">🧭 Laufbahn: {esc(item.get("career_label") or "Nicht angegeben")}</span>'
        )
        test_summary = ""
        test_review_html = ""
        if item.get("test_percent") is not None:
            test_passed = bool(item.get("test_passed"))
            test_badge_class = BADGE_OK if test_passed else BADGE_BAD
            test_status_label = "Richtwert erreicht" if test_passed else "Richtwert nicht erreicht"
            test_summary = (
                f'<span class="rounded-full border px-2.5 py-1 {test_badge_class}">'
                f'🧪 Online-Test: {int(item.get("test_score", 0))}/{int(item.get("test_total", 0))} '
                f'({int(item.get("test_percent", 0))} %) · {test_status_label}</span>'
            )
            detail_parts = []
            for index, detail in enumerate(item.get("test_details", []), start=1):
                correct_answer_html = ""
                if not detail.get("correct"):
                    correct_answer_html = (
                        f'<p class="text-emerald-600 dark:text-emerald-400">'
                        f'Richtige Antwort: {esc(detail.get("correct_answer", "—"))}</p>'
                    )
                detail_parts.append(
                    f'<div class="rounded-xl bg-slate-50 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-800 p-3 space-y-1">'
                    f'<p class="font-semibold">{index}. {esc(detail.get("question", ""))}</p>'
                    f'<p class="text-slate-600 dark:text-slate-300">Antwort: {esc(detail.get("selected_answer", "—"))} '
                    f'{"✅" if detail.get("correct") else "❌"}</p>'
                    f'{correct_answer_html}</div>'
                )
            if detail_parts:
                test_review_html = (
                    f'<details class="mt-3"><summary class="cursor-pointer text-xs font-semibold text-indigo-600 dark:text-indigo-300">'
                    f'Online-Testantworten anzeigen</summary><div class="grid gap-2 mt-3">{"".join(detail_parts)}</div></details>'
                )
        practical_answers = item.get("practical_answers", [])
        practical_review_html = ""
        if practical_answers:
            practical_parts = []
            for practical_index, detail in enumerate(practical_answers, start=1):
                practical_parts.append(
                    '<div class="rounded-xl bg-slate-50 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-800 p-3 space-y-2">'
                    f'<p class="font-bold">{practical_index}. {esc(detail.get("title", "Praxisaufgabe"))}</p>'
                    f'<p class="text-slate-600 dark:text-slate-300">{esc(detail.get("prompt", ""))}</p>'
                    f'<div class="rounded-lg bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 p-3 whitespace-pre-wrap break-words">{esc(detail.get("answer", ""))}</div>'
                    f'<p class="text-[10px] text-amber-600 dark:text-amber-300">Prüfhilfe: {esc(detail.get("review_guide", ""))}</p>'
                    '</div>'
                )
            practical_review_html = (
                '<details class="mt-3"><summary class="cursor-pointer text-xs font-semibold text-amber-600 dark:text-amber-300">'
                'Praxisaufgaben prüfen · manuelle Bewertung erforderlich</summary><div class="grid gap-2 mt-3">'
                + "".join(practical_parts) + '</div></details>'
            )

        reviews = item.get("reviews", {})
        if not isinstance(reviews, dict):
            reviews = {}
        all_scores = [
            int(value)
            for review in reviews.values() if isinstance(review, dict)
            for value in (review.get("ratings", {}) or {}).values()
            if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5
        ]
        if all_scores:
            review_average = sum(all_scores) / len(all_scores)
            review_summary_html = (
                f'<span class="rounded-full border border-violet-500/25 bg-violet-500/10 px-2.5 py-1 text-violet-700 dark:text-violet-300">'
                f'★ Team-Score: {review_average:.1f}/5 · {len(reviews)} Review(s)</span>'
            )
        else:
            review_summary_html = (
                '<span class="rounded-full border border-slate-200 dark:border-slate-700 px-2.5 py-1 text-slate-500">'
                'Noch keine strukturierte Team-Bewertung</span>'
            )

        review_form_html = ""
        if can_decide and not show_history and item.get("status") in {"pending", "interview"}:
            my_review = reviews.get(str(uid), {})
            if not isinstance(my_review, dict):
                my_review = {}
            my_ratings = my_review.get("ratings", {})
            if not isinstance(my_ratings, dict):
                my_ratings = {}
            rating_controls = []
            for criterion_key, criterion_label in APPLICATION_REVIEW_CRITERIA:
                selected_value = my_ratings.get(criterion_key)
                choices = '<option value="">Wert wählen …</option>' + "".join(
                    f'<option value="{score}" {"selected" if selected_value == score else ""}>{score} / 5'
                    f' · {("Unter Erwartung", "Ausbaufähig", "Solide", "Stark", "Hervorragend")[score - 1]}</option>'
                    for score in range(1, 6)
                )
                rating_controls.append(
                    f'<label class="block text-[11px] font-semibold">{esc(criterion_label)}'
                    f'<select name="rating_{esc(criterion_key)}" required class="mt-1 w-full rounded-xl border border-slate-200 '
                    f'dark:border-slate-700 bg-white dark:bg-slate-900 p-2 text-xs">{choices}</select></label>'
                )
            review_notes = esc(str(my_review.get("notes") or "")[:1000])
            review_updated = esc(str(my_review.get("updated_at") or "Noch nicht bewertet"))
            review_form_html = (
                '<details class="mt-3 rounded-2xl border border-violet-500/20 p-4">'
                '<summary class="cursor-pointer text-xs font-bold text-violet-600 dark:text-violet-300">Bewertungsmatrix öffnen · 5 Kompetenzen</summary>'
                '<div class="mt-3 space-y-3"><p class="text-[10px] text-slate-500">Bewerte anhand der Antworten und belegbarer Beobachtungen. 1 = Unter Erwartung, 5 = Hervorragend. Deine letzte Bewertung: '
                + review_updated
                + '</p><form action="/applications/review" method="post" class="space-y-3">'
                f'<input type="hidden" name="app_id" value="{esc(app_id)}">'
                f'<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">{"".join(rating_controls)}</div>'
                '<label class="block text-[11px] font-semibold">Interne Beobachtungen (optional)'
                f'<textarea name="review_notes" maxlength="1000" class="mt-1 w-full min-h-24 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-3 text-xs" placeholder="Stärken, offene Punkte, konkrete Beispiele …">{review_notes}</textarea></label>'
                '<button class="rounded-xl bg-violet-600 hover:bg-violet-500 px-4 py-2.5 text-xs font-semibold text-white">Bewertung für mich speichern</button>'
                '</form></div></details>'
            )
        if show_history:
            hired = item.get("status") in {"hired", "accepted"}  # accepted = Altbestand aus früherer Version
            history_label = "✅ Eingestellt" if hired else "❌ Abgelehnt"
            footer = f'<span class="text-xs font-semibold border px-3 py-1 rounded-full {BADGE_OK if hired else BADGE_BAD}">{history_label}{(" von " + esc(item.get("decided_by"))) if item.get("decided_by") else ""}</span>'
        elif item.get("status") == "interview":
            interview_form = f"""
                <form action="/action" method="post" class="flex flex-wrap gap-2 items-center" onsubmit="return confirm('Wurde das verpflichtende Bewerbungsgespräch durchgeführt?');">
                    <input type="hidden" name="action" value="decide_app"><input type="hidden" name="app_id" value="{esc(app_id)}">
                    <select name="hire_role_id" class="{INPUT} py-1.5 text-[11px] w-auto min-w-44" required>
                        <option value="">Team-Einstiegsrolle nach Gespräch wählen…</option>
                        {''.join(f'<option value="{r.id}">{esc(r.name)}</option>' for r in sorted((ctx.guild.get_role(x) for x in ctx.config.get("team_role_ids", []) if str(x) != str(APPLICATION_ROLE_ID)), key=lambda z: z.position if z else -1) if r and r.id != APPLICATION_ROLE_ID)}
                    </select>
                    <input type="text" name="action_reason" maxlength="500" placeholder="Gesprächsnotiz / Entscheidungsgrund..." required class="{INPUT} py-1.5 min-w-56">
                    <button name="decision" value="hire_after_interview" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 text-xs px-3.5 py-1.5 rounded-xl font-semibold transition">✅ Gespräch geführt – einstellen</button>
                    <button name="decision" value="reject_after_interview" class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-xs px-3.5 py-1.5 rounded-xl font-semibold transition">Nach Gespräch ablehnen</button>
                </form>"""
            footer = f'<div class="w-full space-y-3"><span class="inline-flex text-xs font-semibold border px-3 py-1 rounded-full border-amber-500/30 text-amber-600">🎙️ Bewerbungsgespräch erforderlich · noch keine Einstellung</span>{interview_slot_html(app_id, item) if can_decide else ""}{interview_form if can_decide else ""}</div>'
        else:
            decide = f"""
                <form action="/action" method="post" class="flex flex-wrap gap-2 items-center" onsubmit="return confirm('Bewerber zum verpflichtenden Bewerbungsgespräch einladen?');">
                    <input type="hidden" name="action" value="decide_app"><input type="hidden" name="app_id" value="{esc(app_id)}">
                    <input type="text" name="action_reason" maxlength="500" placeholder="Grund für Einladung oder Ablehnung..." required class="{INPUT} py-1.5 min-w-56">
                    <button name="decision" value="accept" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 text-xs px-3.5 py-1.5 rounded-xl font-semibold transition">🎙️ Zum Bewerbungsgespräch einladen</button>
                    <button name="decision" value="reject" class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-xs px-3.5 py-1.5 rounded-xl font-semibold transition">Ablehnen</button>
                </form>""" if can_decide else ""
            footer = f"""
                <form action="/action" method="post" class="flex gap-2">
                    <input type="hidden" name="action" value="vote_app"><input type="hidden" name="app_id" value="{esc(app_id)}">
                    <button name="vote" value="up" class="{'bg-emerald-600 text-white' if voted_up else 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700'} text-xs px-3 py-1.5 rounded-xl transition shadow-sm font-medium">👍 Dafür</button>
                    <button name="vote" value="down" class="{'bg-rose-600 text-white' if voted_down else 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700'} text-xs px-3 py-1.5 rounded-xl transition shadow-sm font-medium">👎 Dagegen</button>
                </form>{decide}"""
        apps_html += f"""
        <div class="{CARD} p-5 space-y-3.5">
            <div class="flex justify-between items-center">
                <div>
                    <h3 class="font-bold text-slate-900 dark:text-white text-sm">{esc(item.get('name'))}</h3>
                    <span class="text-[10px] text-slate-400 font-mono">ID: {esc(item.get('user_id'))} · {esc(item.get('created_at'))}</span>
                    <div class="flex flex-wrap gap-2 mt-2 text-[10px]">{career_summary}{test_summary}</div>
                </div>
                <div class="flex items-center gap-2 text-xs font-semibold">
                    <span class="text-emerald-600 dark:text-emerald-400">👍 {len(up)}</span>
                    <span class="text-rose-600 dark:text-rose-400">👎 {len(down)}</span>
                </div>
            </div>
            <p class="text-xs text-slate-700 dark:text-slate-300 bg-slate-50 dark:bg-[#0b0e14] p-3.5 rounded-xl border border-slate-200 dark:border-slate-800/80 whitespace-pre-wrap break-words">{esc(item.get('text'))}</p>
            {review_summary_html}
            {test_review_html}
            {practical_review_html}
            {review_form_html}
            <div class="flex flex-wrap justify-between items-center gap-2 pt-2 border-t border-slate-100 dark:border-slate-800/80">{footer}</div>
        </div>"""

    def tab(label, href, active):
        cls = "bg-indigo-600 text-white" if active else "bg-white dark:bg-[#141824] text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-700"
        return f'<a href="{href}" class="px-3.5 py-1.5 rounded-xl text-xs font-semibold {cls}">{label}</a>'

    empty = "Noch keine entschiedenen Bewerbungen." if show_history else "Keine offenen Bewerbungen vorhanden."
    module_settings_link = (
        '<a href="/applications/config" class="px-3.5 py-1.5 rounded-xl text-xs font-semibold bg-indigo-600 text-white">⚙️ Module verwalten</a>'
        if can_decide else ""
    )
    body = f"""
    <div class="flex flex-wrap items-center justify-between gap-3 mb-6 max-w-3xl">
        <h1 class="text-2xl font-bold text-slate-900 dark:text-white">{'Bewerbungs-Verlauf' if show_history else 'Offene Bewerbungen'}</h1>
        <div class="flex flex-wrap gap-2">{module_settings_link}{tab('Offen', '/applications', not show_history)}{tab('Verlauf', '/applications?view=history', show_history)}</div>
    </div>
    <div class="space-y-4 max-w-3xl">{apps_html or f"<p class='text-xs text-slate-400 italic {CARD} p-6'>{empty}</p>"}</div>"""
    return render_page("Bewerbungen", ctx, "apps", body)



# BOT-10: UTC for calendar entries and reminder checks (webserver imports no 'timezone' name)
_UTC = __import__("datetime").timezone.utc


def interview_slot_html(app_id: str, item: dict) -> str:
    """BOT-10: set or change the interview date (DM to the applicant, calendar entry, reminder 1 h before)."""
    current = str(item.get("interview_at") or "")
    shown = ""
    try:
        shown = datetime.fromisoformat(current).astimezone(now_de().tzinfo).strftime("%d.%m.%Y %H:%M") if current else ""
    except ValueError:
        shown = ""
    value = current[:16] if current else ""
    try:
        value = datetime.fromisoformat(current).astimezone(now_de().tzinfo).strftime("%Y-%m-%dT%H:%M") if current else ""
    except ValueError:
        value = ""
    return (f'<form action="/applications/interview-slot" method="post" class="flex flex-wrap gap-2 items-center">'
            f'<input type="hidden" name="app_id" value="{esc(app_id)}">'
            f'<label class="text-[11px] font-semibold">🗓️ Gesprächstermin <input type="datetime-local" name="interview_at" value="{esc(value)}" required class="{INPUT} py-1.5 w-auto"></label>'
            f'<button class="text-xs px-3.5 py-1.5 rounded-xl border border-indigo-500/30 text-indigo-600 font-semibold">{"Termin ändern" if shown else "Termin festlegen"}</button>'
            f'{f"<span class=\"text-[11px] text-slate-400\">aktuell: {esc(shown)}</span>" if shown else ""}</form>')


def application_templates_section(ctx) -> str:
    """BOT-10: edit the DM texts (admins); empty field = built-in text."""
    import pulse_app_comms
    if not ctx.perms.get("is_admin"):
        return ""
    current = pulse_app_comms.load()
    fields = "".join(
        f'<label class="grid gap-1 text-xs font-semibold">{esc(label)}'
        f'<textarea name="tpl_{key}" rows="2" class="{INPUT} text-xs">{esc(current[key])}</textarea></label>'
        for key, label in pulse_app_comms.LABELS.items())
    return (f'<form method="post" action="/applications/templates" class="{CARD} p-5 mt-5 space-y-3">'
            f'<h2 class="text-sm font-bold">✉️ Nachrichten an Bewerber</h2>'
            f'<p class="text-xs text-slate-400">Platzhalter: {esc(pulse_app_comms.PLACEHOLDERS)}. Ein geleertes Feld nutzt wieder den Standardtext.</p>'
            f'{fields}<button class="{BTN} px-4 py-2 text-xs">Vorlagen speichern</button></form>')


@app.post("/applications/interview-slot")
async def application_interview_slot(request: Request, user_session: str = Cookie(None)):
    import pulse_app_comms
    ctx = auth(request, user_session, perm=None)
    if not (ctx.perms.get("can_manage_applications") or ctx.perms.get("can_promote") or ctx.perms.get("is_admin")):
        raise HTTPException(status_code=403, detail="Dir fehlt die Berechtigung für Bewerbungen.")
    form = await request.form()
    app_id = str(form.get("app_id") or "")
    try:
        when = pulse_app_comms.parse_slot(str(form.get("interview_at") or ""), now_de().tzinfo)
    except ValueError as exc:
        return back("/applications", str(exc), False)
    apps = load_json(APPS_FILE, {})
    item = apps.get(app_id) if isinstance(apps, dict) else None
    if not isinstance(item, dict) or item.get("status") != "interview":
        return back("/applications", "Einen Termin gibt es nur für Bewerbungen in der Gesprächsphase.", False)
    actor = ctx.user.get("global_name") or ctx.user.get("username") or "Team"
    item["interview_at"] = when.isoformat(timespec="minutes")
    item["interview_scheduled_by"] = actor
    item["interview_scheduled_by_id"] = str(ctx.user["id"])
    item["interview_reminded"] = False
    save_json(APPS_FILE, apps)
    termin = when.strftime("%d.%m.%Y %H:%M")
    pulse_db.create_calendar_event(f"Bewerbungsgespräch {item.get('name') or app_id}"[:120], when.astimezone(_UTC).isoformat(timespec="seconds"),
                                   (when + timedelta(minutes=30)).astimezone(_UTC).isoformat(timespec="seconds"),
                                   "event", "Discord", f"Bewerbung {app_id}", ctx.user["id"], actor)
    sent = await dm_applicant(request, item.get("user_id"), "scheduled", id=app_id, termin=termin)
    log_audit(actor, ctx.user["id"], "Bewerbungsgespräch geplant", f"{app_id} · {termin}")
    return back("/applications", f"Gesprächstermin {termin} gespeichert" + ("" if sent else " – DM an den Bewerber nicht zustellbar") + ".", sent)


@app.post("/applications/templates")
async def application_templates_save(request: Request, user_session: str = Cookie(None)):
    import pulse_app_comms
    ctx = auth(request, user_session, perm=None, admin=True)
    form = await request.form()
    pulse_app_comms.save({key: form.get(f"tpl_{key}", "") for key in pulse_app_comms.DEFAULTS})
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Bewerber-Nachrichten geändert", "Vorlagen")
    return back("/applications/config", "Vorlagen gespeichert.")


async def send_interview_reminders(bot) -> int:
    """BOT-10: called by the existing automation loop – one reminder DM per interview, about an hour before."""
    import pulse_app_comms
    guild = bot.get_guild(GUILD_ID) if bot else None
    due = pulse_app_comms.due_reminders(load_json(APPS_FILE, {}), datetime.now(_UTC))
    for app_id in due:
        item = load_json(APPS_FILE, {}).get(app_id) or {}
        termin = datetime.fromisoformat(item["interview_at"]).astimezone(now_de().tzinfo).strftime("%H:%M")
        text = pulse_app_comms.render("reminder", server=getattr(guild, "name", "dem Server"), id=app_id, termin=termin)
        try:
            user = (guild.get_member(int(item.get("user_id"))) if guild else None) or await bot.fetch_user(int(item.get("user_id")))
            await send_dm_notification(user, text)
        except (discord.HTTPException, TypeError, ValueError):
            pass
        if str(item.get("interview_scheduled_by_id") or "").isdigit():
            pulse_db.notify(item["interview_scheduled_by_id"], "🎙️ Bewerbungsgespräch gleich", f"{item.get('name') or app_id} um {termin}",
                            "info", "/applications", f"interview:{app_id}", 7200)
        apps = load_json(APPS_FILE, {})  # fresh copy: no await between reading and writing
        if isinstance(apps.get(app_id), dict):
            apps[app_id]["interview_reminded"] = True
            save_json(APPS_FILE, apps)
    return len(due)


@app.post("/applications/review")
async def save_application_review(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None)
    can_manage = bool(
        ctx.perms.get("can_manage_applications")
        or ctx.perms.get("can_promote")
        or ctx.perms.get("is_admin")
    )
    if not can_manage:
        raise HTTPException(status_code=403, detail="Dir fehlt die Berechtigung, Bewerbungen zu bewerten.")

    form = await request.form()
    app_id = str(form.get("app_id") or "").strip()
    apps = load_json(APPS_FILE, {})
    item = apps.get(app_id) if isinstance(apps, dict) else None
    if not isinstance(item, dict):
        return back("/applications", "Bewerbung wurde nicht gefunden.", False)
    if item.get("status") not in {"pending", "interview"}:
        return back("/applications", "Abgeschlossene Bewerbungen können nicht mehr bewertet werden.", False)

    ratings = {}
    for criterion_key, criterion_label in APPLICATION_REVIEW_CRITERIA:
        try:
            score = int(str(form.get(f"rating_{criterion_key}") or "").strip())
        except (TypeError, ValueError):
            return back("/applications", f"Bitte bewerte „{criterion_label}“.", False)
        if score not in range(1, 6):
            return back("/applications", f"Ungültiger Wert für „{criterion_label}“.", False)
        ratings[criterion_key] = score

    reviewer_id = str(ctx.user.get("id") or "")
    if not reviewer_id:
        raise HTTPException(status_code=403, detail="Die Reviewer-ID konnte nicht ermittelt werden.")
    reviewer_name = str(ctx.user.get("global_name") or ctx.user.get("username") or "Teammitglied")[:80]
    notes = str(form.get("review_notes") or "").strip()[:1000]
    reviews = item.setdefault("reviews", {})
    if not isinstance(reviews, dict):
        reviews = {}
        item["reviews"] = reviews
    reviews[reviewer_id] = {
        "reviewer_id": reviewer_id,
        "reviewer_name": reviewer_name,
        "ratings": ratings,
        "notes": notes,
        "updated_at": now_de().strftime("%d.%m.%Y %H:%M"),
    }
    save_json(APPS_FILE, apps)
    average = sum(ratings.values()) / len(ratings)
    log_audit(
        reviewer_name,
        reviewer_id,
        "Bewerbung bewertet",
        f"{item.get('name', 'Unbekannt')} · {item.get('career_label', 'Laufbahn unbekannt')} · eigener Score {average:.1f}/5",
    )
    return back("/applications", f"Bewertung gespeichert: {average:.1f}/5. Deine Bewertung bleibt separat von der anderer Reviewer.")



@app.get("/applications/config", response_class=HTMLResponse)
async def application_modules_config_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None)
    can_manage = bool(
        ctx.perms.get("can_manage_applications")
        or ctx.perms.get("can_promote")
        or ctx.perms.get("is_admin")
    )
    if not can_manage:
        raise HTTPException(status_code=403, detail="Dir fehlt die Berechtigung, Bewerbungsmodule zu verwalten.")

    states = application_module_states(ctx.config)
    module_cards = []
    for key, career in APPLICATION_CAREERS.items():
        enabled = states.get(key, True)
        checkbox = "checked" if enabled else ""
        status_class = "text-emerald-600 dark:text-emerald-400" if enabled else "text-amber-600 dark:text-amber-300"
        status_label = "Aktiv" if enabled else "Pausiert"
        module_cards.append(
            f'<label class="{CARD} p-5 flex items-start gap-4 cursor-pointer hover:border-indigo-400 transition">'
            f'<input type="checkbox" name="module_{esc(key)}" value="1" {checkbox} class="mt-1 h-4 w-4 accent-indigo-600">'
            f'<span class="flex-1 min-w-0"><span class="flex flex-wrap items-center gap-2 font-bold">{esc(career["label"])}'
            f'<span class="text-[10px] {status_class}">{status_label}</span></span>'
            f'<span class="block text-xs text-slate-500 dark:text-slate-400 mt-2">{esc(career["description"])}</span>'
            f'<span class="block text-[10px] text-slate-400 mt-3">{len(career["questions"])} automatische Testfragen · '
            f'{len(APPLICATION_PRACTICAL_TASKS.get(key, []))} manuell geprüfte Praxisaufgaben</span></span></label>'
        )
    body = f"""
    <div class="max-w-3xl space-y-5">
        <div class="flex flex-wrap items-center justify-between gap-3">
            <div><h1 class="text-2xl font-bold text-slate-900 dark:text-white">Bewerbungsmodule</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400 mt-2">Schalte jede Laufbahn unabhängig frei oder pausiere sie, ohne bestehende Bewerbungen zu löschen.</p></div>
            <a href="/applications" class="px-3.5 py-2 rounded-xl text-xs font-semibold border border-slate-200 dark:border-slate-700">← Zur Bewerbungsübersicht</a>
        </div>
        <form action="/applications/config" method="post" class="space-y-4">
            <div class="grid gap-3">{"".join(module_cards)}</div>
            <div class="rounded-2xl bg-indigo-500/10 border border-indigo-500/20 p-4 text-xs text-slate-500 dark:text-slate-300">
                Ausgeschaltete Module verschwinden aus der öffentlichen Auswahl. Direkte Links und manipulierte Einreichungen werden serverseitig blockiert. Bereits eingereichte Bewerbungen bleiben erhalten.
            </div>
            <button class="bg-indigo-600 hover:bg-indigo-500 text-white font-semibold px-5 py-3 rounded-xl">Änderungen speichern</button>
        </form>
    </div>
    """
    body += application_templates_section(ctx)  # BOT-10
    return render_page("Bewerbungsmodule", ctx, "apps", body)


@app.post("/applications/config")
async def save_application_modules_config(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None)
    can_manage = bool(
        ctx.perms.get("can_manage_applications")
        or ctx.perms.get("can_promote")
        or ctx.perms.get("is_admin")
    )
    if not can_manage:
        raise HTTPException(status_code=403, detail="Dir fehlt die Berechtigung, Bewerbungsmodule zu verwalten.")

    form = await request.form()
    states = {key: bool(form.get(f"module_{key}")) for key in APPLICATION_CAREERS}
    ctx.config["application_modules"] = states
    save_json(CONFIG_FILE, ctx.config)
    enabled_count = sum(states.values())
    log_audit(
        ctx.user.get("global_name") or ctx.user.get("username") or "Unbekannt",
        str(ctx.user.get("id") or ""),
        "Bewerbungsmodule geändert",
        ", ".join(f"{APPLICATION_CAREERS[key]['label']}: {'aktiv' if enabled else 'pausiert'}" for key, enabled in states.items()),
    )
    return back("/applications/config", f"Bewerbungsmodule gespeichert: {enabled_count} von {len(states)} aktiv.")



# =============================================================
# DISCORD BACKUP SYSTEM (nur Admins, mit Auto-Backup)
# =============================================================
BACKUP_NAME_RE = re.compile(r"^(?:(?:auto_|pre_restore_|uploaded_))?backup_[0-9_\-]+\.json$")


def safe_backup_path(filename: str):
    """Verhindert Path-Traversal (vorher: filename=../../.env möglich)."""
    name = os.path.basename(filename or "")
    if not BACKUP_NAME_RE.fullmatch(name):
        return None
    path = os.path.join(BACKUP_DIR, name)
    return path if os.path.isfile(path) else None


def _overwrites(channel):
    out = []
    for target, ow in channel.overwrites.items():
        allow, deny = ow.pair()
        out.append({
            "type": "role" if isinstance(target, discord.Role) else "member",
            "id": int(target.id),
            "name": getattr(target, "name", str(target)),
            "allow": int(allow.value),
            "deny": int(deny.value),
        })
    return out


def _enum_int(value, default=None):
    raw = getattr(value, "value", value)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _channel_dict(ch):
    """Serialize restorable channel structure without message history."""
    data = {
        "id": int(ch.id),
        "name": str(ch.name),
        "type": str(ch.type),
        "topic": getattr(ch, "topic", None),
        "position": int(getattr(ch, "position", 0) or 0),
        "nsfw": getattr(ch, "nsfw", None),
        "slowmode": getattr(ch, "slowmode_delay", None),
        "overwrites": _overwrites(ch),
        "category_id": int(ch.category_id) if getattr(ch, "category_id", None) else None,
        "bitrate": getattr(ch, "bitrate", None),
        "user_limit": getattr(ch, "user_limit", None),
        "rtc_region": getattr(ch, "rtc_region", None),
        "video_quality_mode": _enum_int(getattr(ch, "video_quality_mode", None)),
        "default_auto_archive_duration": getattr(ch, "default_auto_archive_duration", None),
        "default_thread_slowmode_delay": getattr(ch, "default_thread_slowmode_delay", None),
        "default_sort_order": _enum_int(getattr(ch, "default_sort_order", None)),
        "default_layout": _enum_int(getattr(ch, "default_layout", None)),
    }
    available_tags = getattr(ch, "available_tags", None)
    if available_tags is not None:
        data["available_tags"] = [
            {
                "name": str(tag.name),
                "moderated": bool(getattr(tag, "moderated", False)),
                "emoji_name": getattr(getattr(tag, "emoji", None), "name", None),
                "emoji_id": getattr(getattr(tag, "emoji", None), "id", None),
            }
            for tag in available_tags
        ]
    return data


def build_backup(guild, guild_icon_base64: str | None = None) -> dict:
    """Build a JSON-safe snapshot of Pulse data and Discord's restorable structure."""
    return add_panel_data(build_discord_structure(guild, guild_icon_base64))


def build_discord_structure(guild, guild_icon_base64: str | None = None) -> dict:
    """D-03: Discord part of the backup – reads discord.py caches, so it runs in the event loop."""
    icon = getattr(guild, "icon", None)
    data = {
        "backup_version": 3,
        "pulse_version": PULSE_VERSION,
        "guild_name": guild.name,
        "guild_id": int(guild.id),
        "created_at": now_de().strftime("%Y-%m-%d_%H-%M-%S"),
        "guild_settings": {
            "name": guild.name,
            "description": getattr(guild, "description", None),
            "verification_level": _enum_int(getattr(guild, "verification_level", None)),
            "default_notifications": _enum_int(getattr(guild, "default_notifications", None)),
            "explicit_content_filter": _enum_int(getattr(guild, "explicit_content_filter", None)),
            "afk_timeout": _enum_int(getattr(guild, "afk_timeout", None)),
            "preferred_locale": str(getattr(guild, "preferred_locale", "") or ""),
            "premium_progress_bar_enabled": getattr(guild, "premium_progress_bar_enabled", None),
            "system_channel_id": getattr(getattr(guild, "system_channel", None), "id", None),
            "rules_channel_id": getattr(getattr(guild, "rules_channel", None), "id", None),
            "public_updates_channel_id": getattr(getattr(guild, "public_updates_channel", None), "id", None),
            "afk_channel_id": getattr(getattr(guild, "afk_channel", None), "id", None),
            "system_channel_flags": _enum_int(getattr(guild, "system_channel_flags", None)),
            "icon_base64": guild_icon_base64,
            "icon_key": getattr(icon, "key", None),
            "icon_restored_from_backup": bool(guild_icon_base64),
        },
        "roles": [],
        "categories": [],
        "uncategorized_channels": [],
        "member_assignments": [],
        "panel_data": {},
    }

    for role in guild.roles:
        if role.is_default():
            continue
        data["roles"].append({
            "id": int(role.id),
            "name": str(role.name),
            "color": int(role.color.value),
            "permissions": int(role.permissions.value),
            "hoist": bool(role.hoist),
            "mentionable": bool(role.mentionable),
            "position": int(role.position),
            "managed": bool(role.managed),
            "unicode_emoji": getattr(role, "unicode_emoji", None),
        })

    for category in guild.categories:
        data["categories"].append({
            "id": int(category.id),
            "name": str(category.name),
            "position": int(category.position),
            "overwrites": _overwrites(category),
            "channels": [_channel_dict(ch) for ch in category.channels],
        })

    data["uncategorized_channels"] = [
        _channel_dict(ch) for ch in guild.channels
        if ch.category is None and not isinstance(ch, discord.CategoryChannel)
    ]

    # Membership is never recreated or kicked. Existing members' role assignments
    # and server nicknames can, however, be restored by a privileged administrator.
    data["member_assignments"] = [
        {
            "id": int(member.id),
            "nick": member.nick,
            "role_ids": [int(role.id) for role in member.roles if not role.is_default()],
        }
        for member in guild.members
    ]

    return data


def add_panel_data(data: dict) -> dict:
    """D-03: file/database part of the backup – no Discord objects, safe in a worker thread."""
    backup_defaults = {
        "team_data": {}, "config": {}, "logs": [],
        "shifts": {}, "meetings": {}, "applications": {},
    }
    for key, path in (("team_data", DATA_FILE), ("config", CONFIG_FILE), ("logs", LOGS_FILE),
                      ("shifts", SHIFTS_FILE), ("meetings", MEETINGS_FILE), ("applications", APPS_FILE)):
        data["panel_data"][key] = load_json(path, backup_defaults[key])

    try:
        data["panel_data"]["pulse_db"] = pulse_db.export_state()
    except Exception as exc:
        # Fail closed rather than claiming this backup contains a database that
        # could not be read. The UI surfaces this as a warning to the operator.
        data["panel_data"]["pulse_db_error"] = f"{type(exc).__name__}: {exc}"

    # The Activity Check database includes immutable participant snapshots and
    # warning idempotency state, so it must travel with the backup as well.
    sqlite_files = {}
    for db_name in ("activity_check.db",):
        db_path = str(BASE_DIR / db_name)
        if not os.path.isfile(db_path):
            continue
        try:
            with closing(sqlite3.connect(db_path, timeout=3.0)) as checkpoint_db, checkpoint_db:
                checkpoint_db.execute("PRAGMA wal_checkpoint(FULL)")
            if os.path.getsize(db_path) <= 20 * 1024 * 1024:
                with open(db_path, "rb") as fh:
                    sqlite_files[db_name] = base64.b64encode(fh.read()).decode("ascii")
        except (OSError, sqlite3.Error):
            continue
    data["panel_data"]["sqlite_files_base64"] = sqlite_files

    # Ticket transcripts are included within a combined size budget, preventing
    # a large transcript directory from exhausting memory during backup creation.
    transcripts = {}
    transcript_bytes = 0
    transcript_dir = os.path.join(BASE_DIR, "transcripts")
    if os.path.isdir(transcript_dir):
        for name in sorted(os.listdir(transcript_dir)):
            path = os.path.join(transcript_dir, name)
            if not (name.endswith(".txt") and os.path.isfile(path)):
                continue
            try:
                size = os.path.getsize(path)
                if size > 2 * 1024 * 1024 or transcript_bytes + size > 10 * 1024 * 1024:
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    transcripts[name] = fh.read()
                transcript_bytes += size
            except OSError:
                continue
    data["panel_data"]["transcripts"] = transcripts

    try:
        with closing(sqlite3.connect(DB_ABMELDUNGEN, timeout=3.0)) as conn, conn:
            rows = conn.execute(
                "SELECT user_id,user_name,grund,von,bis,original_nick,guild_id FROM abmeldungen"
            ).fetchall()
        data["panel_data"]["loas"] = [
            dict(zip(["user_id", "user_name", "grund", "von", "bis", "original_nick", "guild_id"], row))
            for row in rows
        ]
    except Exception:
        data["panel_data"]["loas"] = []

    return data


def validate_backup_document(backup: dict, guild_id: int | None = None) -> None:
    """Validate imported/local backup shape before it can touch runtime data."""
    if not isinstance(backup, dict) or not isinstance(backup.get("panel_data"), dict):
        raise ValueError("Kein gültiges Pulse-Backup: Panel-Daten fehlen.")
    try:
        version = int(backup.get("backup_version", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("Ungültige Backup-Version.") from exc
    if version < 1 or version > 3:
        raise ValueError(f"Nicht unterstützte Backup-Version: {version}")
    stored_guild_id = backup.get("guild_id")
    if guild_id is not None and str(stored_guild_id) != str(guild_id):
        raise ValueError("Dieses Backup gehört zu einem anderen Discord-Server; Wiederherstellung abgebrochen.")

    panel = backup["panel_data"]
    expected_types = {
        "team_data": dict, "config": dict, "logs": list,
        "shifts": dict, "meetings": dict, "applications": dict,
        "pulse_db": dict, "loas": list, "transcripts": dict,
        "sqlite_files_base64": dict,
    }
    if panel.get("logs") == {}:
        panel["logs"] = []
    for key, expected_type in expected_types.items():
        if key in panel and not isinstance(panel[key], expected_type):
            raise ValueError(f"Ungültiger Datentyp im Backup-Feld: {key}")

    # Bound collection counts before any API calls.
    roles = backup.get("roles", [])
    categories = backup.get("categories", [])
    channels = backup.get("uncategorized_channels", [])
    members = backup.get("member_assignments", [])
    settings = backup.get("guild_settings", {})
    if not isinstance(roles, list) or len(roles) > 250:
        raise ValueError("Ungültige Rollenliste im Backup.")
    if not isinstance(categories, list) or len(categories) > 500:
        raise ValueError("Ungültige Kategorienliste im Backup.")
    if not isinstance(channels, list) or len(channels) > 5000:
        raise ValueError("Ungültige Kanalliste im Backup.")
    if not isinstance(members, list) or len(members) > 100000:
        raise ValueError("Ungültige Mitgliederliste im Backup.")
    if not isinstance(settings, dict):
        raise ValueError("Ungültige Servereinstellungen im Backup.")
    for role in roles:
        if not isinstance(role, dict) or not isinstance(role.get("name"), str) or not role["name"] or len(role["name"]) > 100:
            raise ValueError("Ungültiger Rolleneintrag im Backup.")
    total_channels = len(channels)
    for category in categories:
        if not isinstance(category, dict) or not isinstance(category.get("name"), str):
            raise ValueError("Ungültiger Kategorieeintrag im Backup.")
        category_channels = category.get("channels", [])
        if not isinstance(category_channels, list) or len(category_channels) > 5000:
            raise ValueError("Ungültige Kanalliste in einer Kategorie.")
        total_channels += len(category_channels)
        if any(not isinstance(channel, dict) or not isinstance(channel.get("name"), str) for channel in category_channels):
            raise ValueError("Ungültiger Kanal in einer Kategorie.")
    if total_channels > 5000:
        raise ValueError("Das Backup enthält zu viele Kanäle für eine sichere Wiederherstellung.")
    if any(not isinstance(channel, dict) or not isinstance(channel.get("name"), str) for channel in channels):
        raise ValueError("Ungültiger nicht kategorisierter Kanal im Backup.")
    if any(not isinstance(member, dict) or not str(member.get("id", "")).isdigit() for member in members):
        raise ValueError("Ungültiger Mitgliedseintrag im Backup.")
    for role in roles:
        if not str(role.get("id", "")).isdigit():
            raise ValueError("Ungültige Rollen-ID im Backup.")
        for key in ("color", "permissions", "position"):
            if key in role and not isinstance(role[key], (int, float)):
                raise ValueError(f"Ungültiger Rollenwert: {key}.")
    for category in categories:
        if "id" in category and not str(category.get("id", "")).isdigit():
            raise ValueError("Ungültige Kategorie-ID im Backup.")
        for channel in category.get("channels", []):
            if "id" in channel and not str(channel.get("id", "")).isdigit():
                raise ValueError("Ungültige Kanal-ID im Backup.")
            if "overwrites" in channel and not isinstance(channel["overwrites"], list):
                raise ValueError("Ungültige Kanalrechte im Backup.")
    for channel in channels:
        if "id" in channel and not str(channel.get("id", "")).isdigit():
            raise ValueError("Ungültige Kanal-ID im Backup.")
        if "overwrites" in channel and not isinstance(channel["overwrites"], list):
            raise ValueError("Ungültige Kanalrechte im Backup.")
    for member in members:
        role_ids = member.get("role_ids", [])
        if not isinstance(role_ids, list) or any(not str(role_id).isdigit() for role_id in role_ids):
            raise ValueError("Ungültige Rollenliste eines Mitglieds im Backup.")
    sqlite_files = panel.get("sqlite_files_base64", {})
    if isinstance(sqlite_files, dict) and any(key != "activity_check.db" for key in sqlite_files):
        raise ValueError("Das Backup enthält eine nicht zulässige Datenbankdatei.")


def prune_auto_backups():
    autos = sorted(
        (name for name in os.listdir(BACKUP_DIR) if name.startswith("auto_") and name.endswith(".json")),
        key=lambda name: os.path.getmtime(os.path.join(BACKUP_DIR, name)),
        reverse=True,
    )
    for old in autos[MAX_BACKUPS:]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass


def create_backup_file(guild, prefix: str = "") -> str:
    """Synchronous compatibility helper; new UI/automatic backups use the async version."""
    filename = f"{prefix}backup_{now_de().strftime('%Y-%m-%d_%H-%M-%S_%f')}.json"
    save_json(os.path.join(BACKUP_DIR, filename), build_backup(guild))
    if prefix == "auto_":
        prune_auto_backups()
    return filename


async def create_backup_file_async(guild, prefix: str = "") -> str:
    """Create a richer snapshot, including server icon bytes when Discord allows it."""
    icon_base64 = None
    icon = getattr(guild, "icon", None)
    if icon is not None:
        try:
            image_bytes = await asyncio.wait_for((icon.replace(format="png") if hasattr(icon, "replace") else icon).read(), timeout=10.0)
            if image_bytes and len(image_bytes) <= 8 * 1024 * 1024:
                icon_base64 = base64.b64encode(image_bytes).decode("ascii")
        except Exception as exc:
            print(f"⚠️ Server-Icon konnte nicht gesichert werden ({type(exc).__name__}).")
    filename = f"{prefix}backup_{now_de().strftime('%Y-%m-%d_%H-%M-%S_%f')}.json"
    # D-03: discord.py caches are not thread-safe – copy the structure in the loop, files in a thread.
    snapshot = build_discord_structure(guild, icon_base64)
    await asyncio.to_thread(add_panel_data, snapshot)
    path = os.path.join(BACKUP_DIR, filename)
    await asyncio.to_thread(save_json, path, snapshot)
    for warning in snapshot.get("snapshot_warnings", []):
        print(f"⚠️ Backup-Hinweis: {warning}")
    if prefix == "auto_":
        await asyncio.to_thread(prune_auto_backups)
    return filename


def _backup_channel_type(channel_data: dict) -> str:
    return str(channel_data.get("type") or "text").lower()


def _make_permission_overwrites(guild, entries, role_map, backup_guild_id: int, warnings: list[str]):
    overwrites = {}
    if not isinstance(entries, list):
        return overwrites
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            target_id = int(entry.get("id"))
            if entry.get("type") == "role":
                target = role_map.get(target_id)
                if target is None and target_id == backup_guild_id:
                    target = guild.default_role
                if target is None:
                    target = guild.get_role(target_id)
            elif entry.get("type") == "member":
                target = guild.get_member(target_id)
            else:
                continue
            if target is None:
                warnings.append(f"Überschreibung für nicht vorhandene Rolle/Mitglied {target_id} übersprungen.")
                continue
            allow = discord.Permissions(int(entry.get("allow", 0)))
            deny = discord.Permissions(int(entry.get("deny", 0)))
            overwrites[target] = discord.PermissionOverwrite.from_pair(allow, deny)
        except (TypeError, ValueError):
            warnings.append("Ungültige Kanalberechtigung im Backup übersprungen.")
        except Exception as exc:
            warnings.append(f"Kanalberechtigung konnte nicht gelesen werden ({type(exc).__name__}).")
    return overwrites


async def _create_restored_channel(guild, source: dict, category, overwrites, warnings: list[str]):
    name = str(source.get("name") or "restored-channel")[:100]
    kind = _backup_channel_type(source)
    position = max(0, int(source.get("position", 0) or 0))
    reason = "Pulse Server-Backup wiederherstellen"

    if kind == "category":
        return await guild.create_category(
            name, overwrites=overwrites, position=position, reason=reason
        )

    if kind in ("voice", "stage_voice"):
        kwargs = {
            "category": category, "overwrites": overwrites, "position": position,
            "reason": reason,
        }
        if source.get("bitrate") is not None:
            kwargs["bitrate"] = max(8000, min(384000, int(source["bitrate"])))
        if source.get("user_limit") is not None:
            kwargs["user_limit"] = max(0, min(99, int(source["user_limit"])))
        if source.get("rtc_region") is not None:
            kwargs["rtc_region"] = str(source["rtc_region"])
        if source.get("nsfw") is not None:
            kwargs["nsfw"] = bool(source["nsfw"])
        if kind == "stage_voice" and hasattr(guild, "create_stage_channel"):
            # Stage APIs need a topic and don't accept the ordinary voice bitrate/user limit.
            stage_kwargs = {
                "topic": str(source.get("topic") or "Stage-Kanal wiederhergestellt")[:120],
                "category": category,
                "overwrites": overwrites,
                "position": position,
                "reason": reason,
            }
            try:
                return await guild.create_stage_channel(name, **stage_kwargs)
            except TypeError:
                # Compatibility fallback for older supported discord.py releases.
                warnings.append(f"Stage-Kanal {name}: API-Signatur nicht kompatibel, als Sprachkanal erstellt.")
        if kind == "stage_voice":
            warnings.append(f"Stage-Kanal {name}: API nicht verfügbar, als Sprachkanal erstellt.")
        return await guild.create_voice_channel(name, **kwargs)

    if kind in ("forum", "media") and hasattr(guild, "create_forum"):
        kwargs = {
            "category": category, "overwrites": overwrites, "position": position,
            "reason": reason, "nsfw": bool(source.get("nsfw", False)),
        }
        if source.get("topic") is not None:
            kwargs["topic"] = str(source["topic"])[:1024]
        if source.get("slowmode") is not None:
            kwargs["slowmode_delay"] = max(0, min(21600, int(source["slowmode"])))
        if source.get("default_auto_archive_duration") is not None:
            kwargs["default_auto_archive_duration"] = int(source["default_auto_archive_duration"])
        if source.get("default_thread_slowmode_delay") is not None:
            kwargs["default_thread_slowmode_delay"] = int(source["default_thread_slowmode_delay"])
        tags = []
        for tag in source.get("available_tags", [])[:20]:
            if not isinstance(tag, dict) or not tag.get("name"):
                continue
            emoji = None
            if tag.get("emoji_id"):
                try:
                    emoji = discord.PartialEmoji(name=tag.get("emoji_name") or "emoji", id=int(tag["emoji_id"]))
                except (TypeError, ValueError):
                    emoji = None
            elif tag.get("emoji_name"):
                emoji = str(tag["emoji_name"])
            try:
                tags.append(discord.ForumTag(name=str(tag["name"])[:20], moderated=bool(tag.get("moderated")), emoji=emoji))
            except (TypeError, ValueError):
                warnings.append(f"Forum-Tag in {name} wurde übersprungen.")
        if tags:
            kwargs["available_tags"] = tags
        if kind == "media":
            kwargs["media"] = True
        try:
            return await guild.create_forum(name, **kwargs)
        except TypeError:
            # Older discord.py builds may lack the media flag or advanced forum fields.
            kwargs.pop("media", None)
            kwargs.pop("available_tags", None)
            return await guild.create_forum(name, **kwargs)

    if kind == "news":
        creator = getattr(guild, "create_news_channel", None)
        kwargs = {
            "category": category, "overwrites": overwrites, "position": position,
            "reason": reason, "nsfw": bool(source.get("nsfw", False)),
        }
        if source.get("topic") is not None:
            kwargs["topic"] = str(source["topic"])[:1024]
        if source.get("slowmode") is not None:
            kwargs["slowmode_delay"] = max(0, min(21600, int(source["slowmode"])))
        if creator is not None:
            return await creator(name, **kwargs)
        warnings.append(f"Ankündigungskanal {name}: API nicht verfügbar, als Textkanal erstellt.")
        kind = "text"

    if kind in ("text", "news", "store"):
        kwargs = {
            "category": category, "overwrites": overwrites, "position": position,
            "reason": reason, "nsfw": bool(source.get("nsfw", False)),
        }
        if source.get("topic") is not None:
            kwargs["topic"] = str(source["topic"])[:1024]
        if source.get("slowmode") is not None:
            kwargs["slowmode_delay"] = max(0, min(21600, int(source["slowmode"])))
        if source.get("default_auto_archive_duration") is not None:
            kwargs["default_auto_archive_duration"] = int(source["default_auto_archive_duration"])
        if source.get("default_thread_slowmode_delay") is not None:
            kwargs["default_thread_slowmode_delay"] = int(source["default_thread_slowmode_delay"])
        return await guild.create_text_channel(name, **kwargs)

    warnings.append(f"Kanaltyp {kind} ({name}) wird von diesem Restore nicht automatisch angelegt.")
    return None


async def _edit_restored_channel(channel, source: dict, category, overwrites):
    """Apply only real differences, avoiding hundreds of no-op PATCH requests."""
    kwargs = {}
    desired_name = str(source.get("name") or channel.name)[:100]
    desired_position = max(0, int(source.get("position", 0) or 0))
    if channel.name != desired_name:
        kwargs["name"] = desired_name
    if int(getattr(channel, "position", 0) or 0) != desired_position:
        kwargs["position"] = desired_position
    if dict(getattr(channel, "overwrites", {}) or {}) != overwrites:
        kwargs["overwrites"] = overwrites

    if isinstance(channel, discord.CategoryChannel):
        if kwargs:
            kwargs["reason"] = "Pulse Server-Backup wiederherstellen"
            await channel.edit(**kwargs)
        return channel

    desired_category_id = category.id if category else None
    if getattr(channel, "category_id", None) != desired_category_id:
        kwargs["category"] = category
    if hasattr(channel, "topic") and getattr(channel, "topic", None) != source.get("topic"):
        kwargs["topic"] = source.get("topic")
    if hasattr(channel, "nsfw") and bool(getattr(channel, "nsfw", False)) != bool(source.get("nsfw", False)):
        kwargs["nsfw"] = bool(source.get("nsfw", False))
    if hasattr(channel, "slowmode_delay") and source.get("slowmode") is not None:
        desired = max(0, min(21600, int(source["slowmode"])))
        if int(getattr(channel, "slowmode_delay", 0) or 0) != desired:
            kwargs["slowmode_delay"] = desired
    if hasattr(channel, "bitrate") and source.get("bitrate") is not None:
        desired = max(8000, min(384000, int(source["bitrate"])))
        if int(getattr(channel, "bitrate", 0) or 0) != desired:
            kwargs["bitrate"] = desired
    if hasattr(channel, "user_limit") and source.get("user_limit") is not None:
        desired = max(0, min(99, int(source["user_limit"])))
        if int(getattr(channel, "user_limit", 0) or 0) != desired:
            kwargs["user_limit"] = desired
    if hasattr(channel, "rtc_region") and getattr(channel, "rtc_region", None) != source.get("rtc_region"):
        kwargs["rtc_region"] = source.get("rtc_region")
    if hasattr(channel, "default_auto_archive_duration") and source.get("default_auto_archive_duration") is not None:
        desired = int(source["default_auto_archive_duration"])
        if int(getattr(channel, "default_auto_archive_duration", 0) or 0) != desired:
            kwargs["default_auto_archive_duration"] = desired
    if hasattr(channel, "default_thread_slowmode_delay") and source.get("default_thread_slowmode_delay") is not None:
        desired = int(source["default_thread_slowmode_delay"])
        if int(getattr(channel, "default_thread_slowmode_delay", 0) or 0) != desired:
            kwargs["default_thread_slowmode_delay"] = desired

    if hasattr(channel, "available_tags") and isinstance(source.get("available_tags"), list):
        desired_tags = []
        for tag in source.get("available_tags", [])[:20]:
            if not isinstance(tag, dict) or not tag.get("name"):
                continue
            emoji = None
            if tag.get("emoji_id"):
                try:
                    emoji = discord.PartialEmoji(name=tag.get("emoji_name") or "emoji", id=int(tag["emoji_id"]))
                except (TypeError, ValueError):
                    emoji = None
            elif tag.get("emoji_name"):
                emoji = str(tag["emoji_name"])
            try:
                desired_tags.append(discord.ForumTag(name=str(tag["name"])[:20], moderated=bool(tag.get("moderated")), emoji=emoji))
            except (TypeError, ValueError):
                continue
        current_tags = list(getattr(channel, "available_tags", []) or [])
        current_signature = [(tag.name, bool(tag.moderated), getattr(tag.emoji, "name", None), getattr(tag.emoji, "id", None)) for tag in current_tags]
        desired_signature = [(tag.name, bool(tag.moderated), getattr(tag.emoji, "name", None), getattr(tag.emoji, "id", None)) for tag in desired_tags]
        if current_signature != desired_signature:
            kwargs["available_tags"] = desired_tags

    if kwargs:
        kwargs["reason"] = "Pulse Server-Backup wiederherstellen"
        await channel.edit(**kwargs)
    return channel

async def restore_guild_structure(guild, backup: dict, exact_restore: bool = False) -> dict:
    """Restore roles, categories/channels, overwrites, server settings and known member assignments."""
    validate_backup_document(backup, guild.id)
    backup_guild_id = int(backup["guild_id"])
    warnings: list[str] = []
    role_map = {backup_guild_id: guild.default_role}
    channel_map = {}
    restored_object_ids = set()
    restored_roles = 0
    restored_channels = 0
    restored_members = 0
    skipped_members = 0

    current_roles_by_id = {int(role.id): role for role in guild.roles}
    current_roles_by_name = {role.name: role for role in guild.roles}
    role_records = backup.get("roles", [])
    for source in role_records:
        old_id = int(source.get("id", 0) or 0)
        name = str(source.get("name") or "restored-role")[:100]
        role = current_roles_by_id.get(old_id) or current_roles_by_name.get(name)
        is_managed = bool(source.get("managed"))
        if role is None and is_managed:
            warnings.append(f"Verwaltete Discord-Rolle {name} kann nicht neu erstellt werden.")
            continue
        try:
            if role is None:
                role = await guild.create_role(
                    name=name,
                    colour=discord.Colour(int(source.get("color", 0) or 0)),
                    permissions=discord.Permissions(int(source.get("permissions", 0) or 0)),
                    hoist=bool(source.get("hoist", False)),
                    mentionable=bool(source.get("mentionable", False)),
                    reason="Pulse Server-Backup wiederherstellen",
                )
                restored_roles += 1
            elif not role.managed:
                desired = {
                    "name": name,
                    "colour": discord.Colour(int(source.get("color", 0) or 0)),
                    "permissions": discord.Permissions(int(source.get("permissions", 0) or 0)),
                    "hoist": bool(source.get("hoist", False)),
                    "mentionable": bool(source.get("mentionable", False)),
                }
                if (
                    role.name != desired["name"]
                    or role.color.value != desired["colour"].value
                    or role.permissions.value != desired["permissions"].value
                    or role.hoist != desired["hoist"]
                    or role.mentionable != desired["mentionable"]
                ):
                    await role.edit(reason="Pulse Server-Backup wiederherstellen", **desired)
                    restored_roles += 1
            role_map[old_id] = role
        except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
            if role is not None:
                role_map[old_id] = role
            warnings.append(f"Rolle {name} konnte nicht wiederhergestellt werden ({type(exc).__name__}).")

    try:
        positions = {}
        position_changed = False
        for source in role_records:
            role = role_map.get(int(source.get("id", 0) or 0))
            if role is not None and not role.managed and not role.is_default():
                desired_position = max(1, int(source.get("position", 1) or 1))
                positions[role] = desired_position
                if int(role.position) != desired_position:
                    position_changed = True
        if positions and position_changed:
            await guild.edit_role_positions(positions=positions, reason="Pulse Server-Backup wiederherstellen")
    except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
        warnings.append(f"Rollenreihenfolge konnte nicht vollständig wiederhergestellt werden ({type(exc).__name__}).")

    category_records = backup.get("categories", [])
    current_channels_by_id = {int(channel.id): channel for channel in guild.channels}
    current_categories_by_name = {channel.name: channel for channel in guild.categories}
    for source in category_records:
        old_id = int(source.get("id", 0) or 0)
        name = str(source.get("name") or "restored-category")[:100]
        category = current_channels_by_id.get(old_id)
        if not isinstance(category, discord.CategoryChannel):
            possible = current_categories_by_name.get(name)
            category = possible if isinstance(possible, discord.CategoryChannel) else None
        overwrites = _make_permission_overwrites(guild, source.get("overwrites", []), role_map, backup_guild_id, warnings)
        if category is not None:
            restored_object_ids.add(id(category))
            if old_id:
                channel_map[old_id] = category
        try:
            if category is None:
                category = await guild.create_category(
                    name, overwrites=overwrites,
                    position=max(0, int(source.get("position", 0) or 0)),
                    reason="Pulse Server-Backup wiederherstellen",
                )
                restored_channels += 1
            else:
                await _edit_restored_channel(category, source, None, overwrites)
            restored_object_ids.add(id(category))
            if old_id:
                channel_map[old_id] = category
        except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
            if category is not None:
                restored_object_ids.add(id(category))
                if old_id:
                    channel_map[old_id] = category
            warnings.append(f"Kategorie {name} konnte nicht wiederhergestellt werden ({type(exc).__name__}).")

    all_channel_records = []
    for category in category_records:
        parent_id = int(category.get("id", 0) or 0)
        for child in category.get("channels", []):
            if isinstance(child, dict):
                child_copy = dict(child)
                if not child_copy.get("category_id") and parent_id:
                    child_copy["category_id"] = parent_id
                child_copy["category_name"] = str(category.get("name") or "")
                all_channel_records.append(child_copy)
    all_channel_records.extend(backup.get("uncategorized_channels", []))

    for source in all_channel_records:
        if not isinstance(source, dict):
            continue
        old_id = int(source.get("id", 0) or 0)
        name = str(source.get("name") or "restored-channel")[:100]
        kind = _backup_channel_type(source)
        parent_id = int(source.get("category_id", 0) or 0)
        category = channel_map.get(parent_id)
        if category is None and parent_id:
            candidate = guild.get_channel(parent_id)
            category = candidate if isinstance(candidate, discord.CategoryChannel) else None
        if category is None and source.get("category_name"):
            category = next(
                (item for item in guild.categories if item.name == str(source["category_name"])),
                None,
            )
        overwrites = _make_permission_overwrites(guild, source.get("overwrites", []), role_map, backup_guild_id, warnings)

        channel = guild.get_channel(old_id) if old_id else None
        expected_type = kind
        if isinstance(channel, discord.CategoryChannel) or channel is not None and str(channel.type) != expected_type:
            channel = None
        if channel is None:
            channel = next(
                (
                    item for item in guild.channels
                    if not isinstance(item, discord.CategoryChannel)
                    and item.name == name
                    and str(item.type) == expected_type
                    and (getattr(item, "category_id", None) or 0) == (category.id if category else 0)
                ),
                None,
            )
        if channel is not None:
            restored_object_ids.add(id(channel))
            if old_id:
                channel_map[old_id] = channel
        try:
            if channel is None:
                channel = await _create_restored_channel(guild, source, category, overwrites, warnings)
                if channel is not None:
                    restored_channels += 1
            else:
                await _edit_restored_channel(channel, source, category, overwrites)
                restored_channels += 1
            if channel is not None:
                restored_object_ids.add(id(channel))
                if old_id:
                    channel_map[old_id] = channel
        except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
            if channel is not None:
                restored_object_ids.add(id(channel))
                if old_id:
                    channel_map[old_id] = channel
            warnings.append(f"Kanal {name} konnte nicht wiederhergestellt werden ({type(exc).__name__}).")

    # Restore server properties only after the channels they refer to exist.
    settings = backup.get("guild_settings", {}) if isinstance(backup.get("guild_settings"), dict) else {}
    settings_kwargs = {}
    if settings.get("name") and guild.name != str(settings["name"])[:100]:
        settings_kwargs["name"] = str(settings["name"])[:100]
    if "description" in settings and settings.get("description") != getattr(guild, "description", None):
        settings_kwargs["description"] = settings.get("description")
    enum_fields = (
        ("verification_level", "verification_level", discord.VerificationLevel),
        ("default_notifications", "default_notifications", discord.NotificationLevel),
        ("explicit_content_filter", "explicit_content_filter", discord.ContentFilter),
    )
    for source_key, target_key, enum_type in enum_fields:
        value = settings.get(source_key)
        if value is not None:
            try:
                current_value = _enum_int(getattr(guild, target_key, None))
                if current_value != int(value):
                    settings_kwargs[target_key] = enum_type(int(value))
            except (TypeError, ValueError):
                warnings.append(f"Servereinstellung {source_key} war ungültig und wurde übersprungen.")
    if settings.get("afk_timeout") is not None:
        try:
            if _enum_int(getattr(guild, "afk_timeout", None)) != int(settings["afk_timeout"]):
                settings_kwargs["afk_timeout"] = int(settings["afk_timeout"])
        except (TypeError, ValueError):
            pass
    if settings.get("premium_progress_bar_enabled") is not None:
        if bool(getattr(guild, "premium_progress_bar_enabled", False)) != bool(settings["premium_progress_bar_enabled"]):
            settings_kwargs["premium_progress_bar_enabled"] = bool(settings["premium_progress_bar_enabled"])
    if settings.get("preferred_locale"):
        try:
            restored_locale = discord.Locale(str(settings["preferred_locale"]))
            if str(getattr(guild, "preferred_locale", "")) != str(restored_locale):
                settings_kwargs["preferred_locale"] = restored_locale
        except (TypeError, ValueError):
            warnings.append("Server-Sprache wurde nicht wiederhergestellt (unbekannte Discord-Locale).")

    for key, source_key in (
        ("system_channel", "system_channel_id"),
        ("rules_channel", "rules_channel_id"),
        ("public_updates_channel", "public_updates_channel_id"),
        ("afk_channel", "afk_channel_id"),
    ):
        if source_key not in settings:
            continue
        old_channel_id = settings.get(source_key)
        if old_channel_id is None:
            if getattr(guild, key, None) is not None:
                settings_kwargs[key] = None
            continue
        mapped = channel_map.get(int(old_channel_id))
        if mapped is None:
            mapped = guild.get_channel(int(old_channel_id))
        if mapped is not None and getattr(guild, key, None) != mapped:
            settings_kwargs[key] = mapped

    if settings_kwargs:
        try:
            await guild.edit(reason="Pulse Server-Backup wiederherstellen", **settings_kwargs)
        except (discord.Forbidden, discord.HTTPException, TypeError, ValueError) as exc:
            warnings.append(f"Servereinstellungen konnten nicht vollständig gesetzt werden ({type(exc).__name__}).")
            for key, value in settings_kwargs.items():
                try:
                    await guild.edit(reason="Pulse Server-Backup wiederherstellen", **{key: value})
                except (discord.Forbidden, discord.HTTPException, TypeError, ValueError):
                    warnings.append(f"Servereinstellung {key} konnte nicht wiederhergestellt werden.")

    icon_b64 = settings.get("icon_base64")
    stored_icon_key = settings.get("icon_key")
    current_icon_key = getattr(getattr(guild, "icon", None), "key", None)
    if isinstance(icon_b64, str) and icon_b64 and stored_icon_key != current_icon_key:
        try:
            icon_bytes = base64.b64decode(icon_b64, validate=True)
            if 0 < len(icon_bytes) <= 8 * 1024 * 1024:
                await guild.edit(icon=icon_bytes, reason="Pulse Server-Backup wiederherstellen")
            else:
                warnings.append("Server-Icon im Backup überschreitet das erlaubte Größenlimit.")
        except (ValueError, TypeError, discord.Forbidden, discord.HTTPException) as exc:
            warnings.append(f"Server-Icon konnte nicht wiederhergestellt werden ({type(exc).__name__}).")
    elif "icon_base64" in settings and "icon_key" in settings and not icon_b64 and stored_icon_key is None and current_icon_key is not None:
        try:
            await guild.edit(icon=None, reason="Pulse Server-Backup wiederherstellen")
        except (TypeError, discord.Forbidden, discord.HTTPException) as exc:
            warnings.append(f"Server-Icon konnte nicht zurückgesetzt werden ({type(exc).__name__}).")

    mapped_channels = set(restored_object_ids)
    mapped_channels.update(id(channel) for channel in channel_map.values())
    mapped_roles = {id(role) for role in role_map.values()}
    if exact_restore:
        bot_member = guild.me
        bot_top = bot_member.top_role.position if bot_member else 0
        extra_channels = [
            channel for channel in guild.channels
            if id(channel) not in mapped_channels
        ]
        extra_channels.sort(key=lambda item: isinstance(item, discord.CategoryChannel))
        for channel in extra_channels:
            try:
                await channel.delete(reason="Pulse exakte Server-Backup-Wiederherstellung")
            except (discord.Forbidden, discord.HTTPException):
                warnings.append(f"Zusätzlicher Kanal {channel.name} konnte nicht entfernt werden.")
        for role in list(guild.roles):
            if role.is_default() or role.managed or id(role) in mapped_roles:
                continue
            if bot_member and role.position >= bot_top:
                warnings.append(f"Zusätzliche Rolle {role.name} bleibt wegen der Bot-Rollenhierarchie erhalten.")
                continue
            try:
                await role.delete(reason="Pulse exakte Server-Backup-Wiederherstellung")
            except (discord.Forbidden, discord.HTTPException):
                warnings.append(f"Zusätzliche Rolle {role.name} konnte nicht entfernt werden.")

    member_records = backup.get("member_assignments", [])
    if isinstance(member_records, list):
        snapshot_role_objects = {role.id for key, role in role_map.items() if key != backup_guild_id}
        for source in member_records:
            try:
                member_id = int(source.get("id"))
                member = guild.get_member(member_id)
                if member is None:
                    skipped_members += 1
                    continue
                desired_roles = []
                for old_role_id in source.get("role_ids", []):
                    role = role_map.get(int(old_role_id))
                    if role is not None and not role.is_default() and not role.managed and role not in desired_roles:
                        desired_roles.append(role)
                for role in member.roles:
                    if role.is_default() or role in desired_roles:
                        continue
                    # Safe mode is additive for member roles; exact mode may reset
                    # assignable role assignments, but integration-managed roles remain.
                    if role.managed or not exact_restore:
                        desired_roles.append(role)
                current_role_ids = {role.id for role in member.roles if not role.is_default()}
                desired_role_ids = {role.id for role in desired_roles}
                desired_nick = source.get("nick")
                if current_role_ids == desired_role_ids and member.nick == desired_nick:
                    continue
                await member.edit(
                    roles=desired_roles,
                    nick=desired_nick,
                    reason="Pulse Server-Backup wiederherstellen",
                )
                restored_members += 1
            except (discord.Forbidden, discord.HTTPException, TypeError, ValueError) as exc:
                warnings.append(f"Mitgliedsrollen/-name konnten nicht wiederhergestellt werden ({type(exc).__name__}).")

    id_remap = {}
    for old_id, role in role_map.items():
        if role is not None:
            id_remap[str(int(old_id))] = int(role.id)
    for old_id, channel in channel_map.items():
        if channel is not None:
            id_remap[str(int(old_id))] = int(channel.id)

    return {
        "roles_changed": restored_roles,
        "channels_changed": restored_channels,
        "members_changed": restored_members,
        "members_skipped": skipped_members,
        "exact_restore": bool(exact_restore),
        "id_remap": id_remap,
        "warnings": warnings[:50],
        "warning_count": len(warnings),
    }


MAX_BACKUP_UPLOAD_BYTES = 50 * 1024 * 1024


def safe_backup_path(filename: str):
    """Prevent path traversal while permitting generated and imported backup names."""
    name = os.path.basename(filename or "")
    if not BACKUP_NAME_RE.fullmatch(name):
        return None
    path = os.path.join(BACKUP_DIR, name)
    return path if os.path.isfile(path) else None


async def auto_backup_loop():
    if AUTO_BACKUP_HOURS <= 0:
        return
    import pulse_jobs  # P-06: the auto-backup check is visible in the job registry
    while True:
        await asyncio.sleep(300)
        try:
            async with pulse_jobs.job("Auto-Backup-Prüfung (5 min)", 300):
                bot = getattr(app.state, "bot", None)
                guild = bot.get_guild(GUILD_ID) if bot else None
                if guild:
                    times = [
                        os.path.getmtime(os.path.join(BACKUP_DIR, f))
                        for f in os.listdir(BACKUP_DIR)
                        if BACKUP_NAME_RE.fullmatch(f)
                    ]
                    if not times or time.time() - max(times) > AUTO_BACKUP_HOURS * 3600:
                        name = await create_backup_file_async(guild, "auto_")
                        log_audit("System", "0", "Auto-Backup", name)
                        await run_auto_full_backup(guild)  # D-01/D-04: complete ZIP, retention, weekly restore test

                        # Optional off-host copy. The local backup remains valid even
                        # when Discord uploads fail or the attachment exceeds the cap.
                        raw_channel_id = os.getenv("PULSE_BACKUP_DISCORD_CHANNEL_ID", "").strip()
                        if not raw_channel_id:
                            raw_channel_id = str(load_config().get("backup_discord_channel_id", "") or "")
                        try:
                            channel_id = int(raw_channel_id)
                        except (TypeError, ValueError):
                            channel_id = 0
                        channel = (bot.get_channel(channel_id) or guild.get_channel(channel_id)) if channel_id else None
                        if channel and hasattr(channel, "send"):
                            full_path = os.path.join(BACKUP_DIR, name)
                            size = os.path.getsize(full_path)
                            embed = discord.Embed(
                                title="💾 Automatische Pulse-Sicherung",
                                description=(
                                    "Eine vollständige Pulse-Panel-Sicherung wurde erstellt. "
                                    "Die lokale Kopie bleibt zusätzlich im Dashboard unter /backups verfügbar."
                                ),
                                color=_pulse_color("success") if size <= 7_500_000 else _pulse_color("warning"),
                                timestamp=now_de(),
                            )
                            embed.add_field(name="Datei", value=os.path.basename(name), inline=False)
                            embed.add_field(name="Größe", value=f"{size / (1024 * 1024):.2f} MiB", inline=True)
                            try:
                                if size <= 7_500_000:
                                    await send_styled(
                                        channel,
                                        embed=embed,
                                        file=discord.File(full_path, filename=os.path.basename(name)),
                                        allowed_mentions=discord.AllowedMentions.none(),
                                    )
                                else:
                                    embed.add_field(
                                        name="Offsite-Kopie",
                                        value="Upload übersprungen: Datei ist zu groß. Nur die lokale Sicherung wurde erstellt.",
                                        inline=False,
                                    )
                                    await send_styled(
                                        channel,
                                        embed=embed,
                                        allowed_mentions=discord.AllowedMentions.none(),
                                    )
                                log_audit("System", "0", "Auto-Backup Discord-Kopie", f"{name} · {size} Bytes")
                            except (discord.Forbidden, discord.HTTPException, OSError) as upload_error:
                                log_audit(
                                    "System", "0", "Auto-Backup Upload Fehler",
                                    f"{name} · {type(upload_error).__name__}",
                                )
                                print(f"⚠️ Auto-Backup ist lokal vorhanden; Discord-Kopie fehlgeschlagen ({type(upload_error).__name__}).")
        except Exception as e:
            print(f"Auto-Backup fehlgeschlagen: {type(e).__name__}: {e}")


async def _start_background_tasks():
    # Initialise one small shared HTTP pool and keep the backup worker singular.
    _roblox_http_client()
    task = getattr(app.state, "backup_task", None)
    if task is None or task.done():
        app.state.backup_task = asyncio.create_task(auto_backup_loop())


async def _stop_background_tasks():
    task = getattr(app.state, "backup_task", None)
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    client = getattr(app.state, "roblox_http_client", None)
    if client is not None and not client.is_closed:
        await client.aclose()


@app.get("/backups", response_class=HTMLResponse)
async def backups_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    files = sorted(
        (name for name in os.listdir(BACKUP_DIR) if BACKUP_NAME_RE.fullmatch(name)),
        key=lambda name: os.path.getmtime(os.path.join(BACKUP_DIR, name)),
        reverse=True,
    )
    rows = ""
    for name in files:
        path = os.path.join(BACKUP_DIR, name)
        created = datetime.fromtimestamp(os.path.getmtime(path), TZ).strftime("%d.%m.%Y %H:%M") if TZ else \
            datetime.fromtimestamp(os.path.getmtime(path)).strftime("%d.%m.%Y %H:%M")
        size = os.path.getsize(path)
        auto = ' <span class="text-[10px] px-2 py-0.5 rounded-full border border-indigo-500/30 bg-indigo-500/10 text-indigo-500 font-sans">AUTO</span>' if name.startswith("auto_") else ""
        uploaded = ' <span class="text-[10px] px-2 py-0.5 rounded-full border border-cyan-500/30 bg-cyan-500/10 text-cyan-500 font-sans">IMPORT</span>' if name.startswith("uploaded_") else ""
        rows += f"""
        <div class="{CARD} p-4 flex flex-col gap-3">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div>
                    <div class="font-bold text-slate-900 dark:text-white text-sm font-mono break-all">{esc(name)}{auto}{uploaded}</div>
                    <div class="text-xs text-slate-400 mt-0.5">{created} · {round(size / 1024, 1)} KB</div>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                    <a href="/backup/download/{esc(name)}" class="bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-xs px-3.5 py-2 rounded-xl text-slate-700 dark:text-slate-300 font-medium transition shadow-sm">📥 JSON herunterladen</a>
                    <form action="/backup/delete" method="post" onsubmit="return confirm('Backup wirklich endgültig löschen?');">
                        <input type="hidden" name="filename" value="{esc(name)}">
                        <button class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-xs px-3.5 py-2 rounded-xl font-medium transition">🗑️ Löschen</button>
                    </form>
                </div>
            </div>
            <form action="/backup/restore" method="post" class="border-t border-slate-200 dark:border-slate-700 pt-3 flex flex-col gap-2" onsubmit="return confirm('Vollständige Server- und Pulse-Wiederherstellung starten? Es wird vorher automatisch eine Sicherheitskopie des aktuellen Zustands angelegt.');">
                <input type="hidden" name="filename" value="{esc(name)}">
                <label class="flex items-start gap-2 text-xs text-slate-600 dark:text-slate-300">
                    <input type="checkbox" name="exact_restore" value="true" class="mt-0.5 rounded">
                    <span><strong>Exakte Struktur erzwingen:</strong> löscht zusätzliche, nicht im Backup enthaltene Kanäle und nicht verwaltete Rollen (nur soweit die Bot-Hierarchie es erlaubt).</span>
                </label>
                <button class="self-start bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-xs px-3.5 py-2 rounded-xl font-medium transition">↩️ Server + Pulse wiederherstellen</button>
            </form>
        </div>"""

    auto_text = (
        f"Automatische Sicherungen alle {AUTO_BACKUP_HOURS:g} Stunden; bis zu {MAX_BACKUPS} automatische Dateien bleiben erhalten."
        if AUTO_BACKUP_HOURS > 0 else "Automatische Sicherung ist deaktiviert (AUTO_BACKUP_HOURS=0)."
    )
    body = f"""
    <div class="flex flex-col gap-5 mb-6 max-w-4xl">
        <div>
            <h1 class="text-2xl font-bold text-slate-900 dark:text-white">Discord Server Backups</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400 mt-2">Sichert Servername/-einstellungen, Rollen samt Berechtigungen und Reihenfolge, Kategorien, Kanäle samt Rechten, vorhandene Mitgliedsrollen/-Spitznamen sowie Pulse-Datenbanken, Konfiguration, Warnhistorien und Transkripte. {auto_text}</p>
        </div>
        <div class="{CARD} p-4 flex flex-col gap-3">
            <h2 class="font-bold text-slate-900 dark:text-white text-sm">💾 Neues vollständiges Backup</h2>
            <p class="text-xs text-slate-500 dark:text-slate-400">Erstellt eine herunterladbare JSON-Datei. Für jede Wiederherstellung legt Pulse vorher automatisch eine Rückfall-Sicherung an.</p>
            <form action="/backup/create" method="post">
                <button class="{BTN} text-xs py-3 px-5">💾 Server + Pulse sichern</button>
            </form>
        </div>
        {full_backup_section()}
        <div class="{CARD} p-4 flex flex-col gap-3">
            <h2 class="font-bold text-slate-900 dark:text-white text-sm">📤 Backup hochladen</h2>
            <p class="text-xs text-slate-500 dark:text-slate-400">Importiert ein zuvor von Pulse heruntergeladenes JSON-Backup (max. {MAX_BACKUP_UPLOAD_BYTES // (1024 * 1024)} MiB). Das Backup muss zu diesem Discord-Server gehören und wird vor dem Speichern geprüft.</p>
            <form action="/backup/upload" method="post" enctype="multipart/form-data" class="flex flex-col sm:flex-row gap-3">
                <input type="file" name="backup_file" accept=".json,application/json" required class="block w-full text-xs text-slate-600 dark:text-slate-300">
                <button class="{BTN} text-xs py-3 px-5 shrink-0">⬆️ Backup importieren</button>
            </form>
        </div>
        <div class="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-xs text-slate-600 dark:text-slate-300">
            <strong>Grenzen von Discord:</strong> Bot-Backups können keine alten Nachrichten/Verläufe, privaten DMs, externe Integrationen, Webhooks, Einladungen, Server-Boosts oder nicht mehr verfügbare benutzerdefinierte Emojis rekonstruieren. Mitglieder werden weder eingeladen noch entfernt. Für das Wiederherstellen von Rollen/Kanälen braucht Pulse passende Discord-Rechte und eine ausreichend hohe Bot-Rolle.
        </div>
    </div>
    <div class="space-y-3 max-w-4xl">{rows or f"<div class='text-xs text-slate-400 italic {CARD} p-6 text-center'>Noch keine Backups vorhanden.</div>"}</div>"""
    return render_page("Server Backups", ctx, "backups", body)


@app.post("/backup/create")
async def create_backup(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    try:
        name = await create_backup_file_async(ctx.guild)
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Server-Backup Erstellt", f"Filename: {name}")
        return back("/backups", f"Server- und Pulse-Backup erstellt: {name}")
    except Exception as exc:
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Server-Backup Fehlgeschlagen", type(exc).__name__)
        return back("/backups", f"Backup fehlgeschlagen ({type(exc).__name__}). Vorhandene Sicherungen wurden nicht verändert.", False)


@app.post("/backup/upload")
async def upload_backup(
    request: Request,
    backup_file: UploadFile = File(...),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, perm=None, admin=True)
    try:
        if not backup_file.filename or not backup_file.filename.lower().endswith(".json"):
            return back("/backups", "Bitte eine Pulse-Backup-Datei mit der Endung .json auswählen.", False)
        raw = await backup_file.read(MAX_BACKUP_UPLOAD_BYTES + 1)
        if len(raw) > MAX_BACKUP_UPLOAD_BYTES:
            return back("/backups", f"Datei zu groß. Maximum: {MAX_BACKUP_UPLOAD_BYTES // (1024 * 1024)} MiB.", False)
        try:
            backup = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Die Datei ist keine gültige UTF-8-JSON-Datei.") from exc
        validate_backup_document(backup, ctx.guild.id)
        settings = backup.get("guild_settings", {})
        if isinstance(settings, dict) and isinstance(settings.get("icon_base64"), str):
            icon_raw = base64.b64decode(settings["icon_base64"], validate=True)
            if len(icon_raw) > 8 * 1024 * 1024:
                raise ValueError("Das Server-Icon im Backup ist zu groß.")
        sqlite_files = backup.get("panel_data", {}).get("sqlite_files_base64", {})
        for database_name, encoded in sqlite_files.items():
            if database_name != "activity_check.db" or not isinstance(encoded, str):
                raise ValueError("Unerlaubte Datenbankdatei im Backup.")
            if len(encoded) > 28 * 1024 * 1024:
                raise ValueError("Die Datenbankdatei im Backup ist zu groß.")
            decoded = base64.b64decode(encoded, validate=True)
            if len(decoded) > 20 * 1024 * 1024 or not decoded.startswith(b"SQLite format 3\x00"):
                raise ValueError("Die SQLite-Datei im Backup ist beschädigt oder ungültig.")
        name = f"uploaded_backup_{now_de().strftime('%Y-%m-%d_%H-%M-%S_%f')}.json"
        safe_path = os.path.join(BACKUP_DIR, name)
        # Normalize accepted JSON and write atomically; never trust the user-supplied filename.
        temp_path = safe_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as fh:
            json.dump(backup, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(temp_path, safe_path)
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Backup Importiert", f"Filename: {name}")
        return back("/backups", f"Backup geprüft und importiert: {name}")
    except (ValueError, OSError) as exc:
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Backup Import Abgelehnt", f"{type(exc).__name__}: {str(exc)[:180]}")
        return back("/backups", f"Backup-Import abgelehnt: {str(exc)[:240]}", False)
    except Exception as exc:
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Backup Import Fehlgeschlagen", type(exc).__name__)
        return back("/backups", f"Backup konnte nicht importiert werden ({type(exc).__name__}).", False)
    finally:
        await backup_file.close()


@app.get("/backup/download/{filename}")
async def download_backup(request: Request, filename: str, user_session: str = Cookie(None)):
    auth(request, user_session, perm=None, admin=True)
    path = safe_backup_path(filename)
    if not path:
        raise HTTPException(status_code=404, detail="Backup nicht gefunden.")
    return FileResponse(path, media_type="application/json", filename=os.path.basename(path))


def _remap_backup_ids(value, id_map: dict[str, int]):
    """Replace only exact Discord snowflake references, preserving all other values."""
    if isinstance(value, dict):
        return {key: _remap_backup_ids(item, id_map) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap_backup_ids(item, id_map) for item in value]
    if isinstance(value, int) and not isinstance(value, bool):
        return id_map.get(str(value), value)
    if isinstance(value, str) and value.isdigit():
        return str(id_map.get(value, int(value)))
    return value


@app.post("/backup/restore")
async def restore_backup(
    request: Request,
    filename: str = Form(...),
    exact_restore: bool = Form(False),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, perm=None, admin=True)
    path = safe_backup_path(filename)
    if not path:
        return back("/backups", "Backup nicht gefunden.", False)
    try:
        if os.path.getsize(path) > MAX_BACKUP_UPLOAD_BYTES:
            raise ValueError("Backup überschreitet das zulässige Dateigrößenlimit.")
        with open(path, "r", encoding="utf-8") as fh:
            backup = json.load(fh)
        validate_backup_document(backup, ctx.guild.id)
        panel = backup["panel_data"]

        # Current state is snapshotted before touching Discord or Pulse runtime data.
        safety_snapshot = await create_backup_file_async(ctx.guild, "pre_restore_")
        structure_report = await restore_guild_structure(ctx.guild, backup, exact_restore=exact_restore)

        # Re-map IDs if deleted roles/channels had to be recreated with new Discord IDs.
        id_remap = structure_report.pop("id_remap", {})
        if id_remap:
            panel = _remap_backup_ids(panel, id_remap)

        restored_config = panel.get("config")
        if isinstance(restored_config, dict):
            aliases = restored_config.get("backup_role_id_aliases", {})
            if not isinstance(aliases, dict):
                aliases = {}
            built_in_role_ids = (
                list(WARN_ROLE_IDS.values())
                + [WARN_UPRANK_LOCK_ROLE_ID, WARN_IC_LOCK_ROLE_ID, TEST_SUPPORTER_ROLE_ID]
                + list(TEAM_KICK_KEEP_ROLE_IDS)
            )
            for original_id in built_in_role_ids:
                replacement = id_remap.get(str(original_id))
                if replacement is not None and int(replacement) != int(original_id):
                    aliases[str(int(original_id))] = int(replacement)
            restored_config["backup_role_id_aliases"] = aliases

        mapping = {
            "team_data": DATA_FILE, "config": CONFIG_FILE, "logs": LOGS_FILE,
            "shifts": SHIFTS_FILE, "meetings": MEETINGS_FILE, "applications": APPS_FILE,
        }
        for key, target in mapping.items():
            if key in panel:
                save_json(target, panel[key])
        if isinstance(panel.get("pulse_db"), dict):
            pulse_db.import_state(_remap_backup_ids(panel["pulse_db"], id_remap))

        if isinstance(panel.get("loas"), list):
            with closing(sqlite3.connect(DB_ABMELDUNGEN, timeout=5.0)) as conn, conn:
                conn.execute("DELETE FROM abmeldungen")
                conn.executemany(
                    "INSERT OR REPLACE INTO abmeldungen(user_id,user_name,grund,von,bis,original_nick,guild_id) VALUES (?,?,?,?,?,?,?)",
                    [
                        (item.get("user_id"), item.get("user_name", ""), item.get("grund", ""),
                         item.get("von", ""), item.get("bis", ""), item.get("original_nick", ""),
                         item.get("guild_id", GUILD_ID))
                        for item in panel["loas"] if isinstance(item, dict) and item.get("user_id") is not None
                    ],
                )

        sqlite_files = panel.get("sqlite_files_base64", {})
        if isinstance(sqlite_files, dict) and "activity_check.db" in sqlite_files:
            decoded = base64.b64decode(sqlite_files["activity_check.db"], validate=True)
            if not decoded.startswith(b"SQLite format 3\x00") or len(decoded) > 20 * 1024 * 1024:
                raise ValueError("Activity-Check-Datenbank im Backup ist beschädigt.")
            db_path = str(BASE_DIR / "activity_check.db")
            temp_db = db_path + ".restore.tmp"
            with open(temp_db, "wb") as fh:
                fh.write(decoded)
            os.replace(temp_db, db_path)

        os.makedirs(os.path.join(BASE_DIR, "transcripts"), exist_ok=True)
        for name, transcript in (panel.get("transcripts") or {}).items():
            safe = os.path.basename(str(name))
            if safe.endswith(".txt") and isinstance(transcript, str) and len(transcript) <= 2 * 1024 * 1024:
                with open(os.path.join(BASE_DIR, "transcripts", safe), "w", encoding="utf-8") as fh:
                    fh.write(transcript)

        warning_text = ""
        if structure_report["warnings"]:
            warning_text = (
                f" Hinweis: {structure_report['warning_count']} Strukturwarnung(en); "
                "Details stehen im Hosting-Log/Audit."
            )
            print("⚠️ Server-Backup mit Strukturwarnungen: " + " | ".join(structure_report["warnings"][:12]))

        details = (
            f"Datei: {os.path.basename(path)} · Sicherheitskopie: {safety_snapshot} · "
            f"Rollenänderungen: {structure_report['roles_changed']} · "
            f"Kanaländerungen: {structure_report['channels_changed']} · "
            f"Mitglieder aktualisiert: {structure_report['members_changed']} · "
            f"Exakte Struktur: {structure_report['exact_restore']} · "
            f"Strukturwarnungen: {structure_report['warning_count']}"
        )
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Server + Pulse wiederhergestellt", details)
        return back(
            "/backups",
            "Wiederherstellung abgeschlossen. "
            f"Rollen: {structure_report['roles_changed']}, Kanäle/Kategorien: {structure_report['channels_changed']}, "
            f"Mitglieder: {structure_report['members_changed']}. Sicherheitskopie: {safety_snapshot}.{warning_text}",
        )
    except Exception as exc:
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Backup Wiederherstellung Fehlgeschlagen", f"{os.path.basename(path)} · {type(exc).__name__}: {str(exc)[:180]}")
        print(f"❌ Backup-Wiederherstellung fehlgeschlagen: {type(exc).__name__}: {exc}")
        return back("/backups", f"Wiederherstellung fehlgeschlagen ({type(exc).__name__}). Sicherheitskopie vor Restore wurde, falls begonnen, behalten.", False)


@app.post("/backup/delete")
async def delete_backup(request: Request, filename: str = Form(...), user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    path = safe_backup_path(filename)
    if not path:
        return back("/backups", "Backup nicht gefunden.", False)
    os.remove(path)
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Backup Gelöscht", f"Filename: {os.path.basename(path)}")
    return back("/backups", "Backup gelöscht.")


# =============================================================
# D-01..D-04: Komplett-Backup (ZIP mit Manifest) – alle Datenbanken, JSON-Dateien und Uploads
# =============================================================
FULL_BACKUP_RESTORE_TEST_KEY = "backup_restore_test"
FULL_BACKUP_RESTORE_TEST_DAYS = 7
FULL_BACKUP_MIN_FREE_BYTES = 200 * 1024 * 1024


def full_backup_dir() -> str:
    return os.path.abspath(BACKUP_DIR)


def safe_full_backup_path(filename: str):
    import pulse_backup
    name = os.path.basename(filename or "")
    if not pulse_backup.NAME_RE.fullmatch(name):
        return None
    path = os.path.join(full_backup_dir(), name)
    return path if os.path.isfile(path) else None


async def create_full_backup_async(guild, prefix: str = ""):
    """D-03: Discord structure is copied in the event loop, all file/ZIP work runs in a thread."""
    import pulse_backup
    structure = build_discord_structure(guild) if guild is not None else None
    return await asyncio.to_thread(pulse_backup.create_full_backup, BASE_DIR, full_backup_dir(),
                                   discord_structure=structure, prefix=prefix)


def full_backup_status() -> dict:
    """Latest complete backup and the last automatic restore test (for system status)."""
    import pulse_backup
    backups = pulse_backup.list_backups(full_backup_dir())
    test = pulse_db.get_setting(FULL_BACKUP_RESTORE_TEST_KEY, {}) or {}
    total = sum(p.stat().st_size for p in backups)
    return {"latest": backups[0].name if backups else "", "count": len(backups), "restore_test": test,
            "total_mb": round(total / (1024 * 1024), 1), "total_ok": total <= pulse_backup.TOTAL_MAX_BYTES}


async def run_auto_full_backup(guild) -> None:
    """Called from the existing auto-backup loop (no extra loop): backup, retention, weekly restore test."""
    import pulse_backup
    try:
        os.makedirs(full_backup_dir(), exist_ok=True)
        free = shutil.disk_usage(full_backup_dir()).free
        latest = pulse_backup.list_backups(full_backup_dir())
        needed = FULL_BACKUP_MIN_FREE_BYTES + (2 * latest[0].stat().st_size if latest else 0)
        if free < needed:
            log_audit("System", "0", "Komplett-Backup übersprungen", f"Zu wenig Speicher frei ({free // (1024 * 1024)} MB)")
            print("⚠️ Komplett-Backup übersprungen: zu wenig freier Speicherplatz.")
            return
        await asyncio.to_thread(pulse_backup.prune_auto_backups, full_backup_dir())
        if pulse_backup.total_size(full_backup_dir()) > pulse_backup.TOTAL_MAX_BYTES:
            log_audit("System", "0", "Komplett-Backup übersprungen",
                      f"Gesamtgröße der Sicherungen über {pulse_backup.TOTAL_MAX_BYTES // (1024 * 1024)} MB (PULSE_BACKUP_TOTAL_MAX_MB) – "
                      "bitte alte manuelle Sicherungen löschen")
            print("⚠️ Komplett-Backup übersprungen: Gesamtgrenze der Sicherungen erreicht.")
            return
        path, manifest = await create_full_backup_async(guild, "auto_")
        removed = await asyncio.to_thread(pulse_backup.prune_auto_backups, full_backup_dir())
        details = f"{path.name} · {len(manifest['files'])} Dateien · {len(removed)} alte entfernt"
        if manifest["warnings"]:
            details += " · Hinweise: " + "; ".join(manifest["warnings"])[:300]
            print("⚠️ Komplett-Backup: " + "; ".join(manifest["warnings"]))
        log_audit("System", "0", "Komplett-Backup", details)
        last = parse_timestamp((pulse_db.get_setting(FULL_BACKUP_RESTORE_TEST_KEY, {}) or {}).get("at"))
        if last is None or (datetime.now(last.tzinfo) - last).days >= FULL_BACKUP_RESTORE_TEST_DAYS:
            result = await asyncio.to_thread(pulse_backup.restore_test, path)
            pulse_db.set_setting(FULL_BACKUP_RESTORE_TEST_KEY, result)
            log_audit("System", "0", "Restore-Test " + ("bestanden" if result["ok"] else "FEHLGESCHLAGEN"),
                      f"{result['backup']} · " + ("; ".join(result["problems"])[:300] or "alle Datenbanken intakt"))
            if not result["ok"]:
                print("🔴 Restore-Test fehlgeschlagen: " + "; ".join(result["problems"])[:300])
    except Exception as exc:
        print(f"⚠️ Komplett-Backup fehlgeschlagen: {type(exc).__name__}: {exc}")


def full_backup_section() -> str:
    import pulse_backup
    rows = ""
    for path in pulse_backup.list_backups(full_backup_dir())[:40]:
        created = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
        tag = " · AUTO" if "_auto_" in path.name else (" · SICHERHEITSKOPIE" if "_pre_restore_" in path.name else "")
        rows += f"""
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-t border-slate-200 dark:border-slate-800 pt-3">
            <div><div class="font-mono text-xs break-all">{esc(path.name)}{tag}</div>
                 <div class="text-[11px] text-slate-400">{created} · {round(path.stat().st_size / (1024 * 1024), 2)} MB</div></div>
            <div class="flex flex-wrap gap-2">
                <a href="/backup/full/download/{esc(path.name)}" class="bg-slate-100 dark:bg-slate-800 text-xs px-3 py-2 rounded-xl">📥 ZIP</a>
                <a href="/backup/full/preview/{esc(path.name)}" class="bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-xs px-3 py-2 rounded-xl">🔍 Probelauf / Wiederherstellen</a>
                <form action="/backup/full/delete" method="post" onsubmit="return confirm('Komplett-Backup endgültig löschen?');">
                    <input type="hidden" name="filename" value="{esc(path.name)}">
                    <button class="bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-xs px-3 py-2 rounded-xl">🗑️</button></form>
            </div>
        </div>"""
    status = full_backup_status()
    test = status["restore_test"]
    test_line = ("Noch kein automatischer Restore-Test." if not test else
                 f"Letzter Restore-Test: {esc(display(test.get('at')))} · {'✅ bestanden' if test.get('ok') else '❌ fehlgeschlagen: ' + esc('; '.join(test.get('problems', []))[:200])}")
    return f"""
        <div class="{CARD} p-4 flex flex-col gap-3">
            <h2 class="font-bold text-slate-900 dark:text-white text-sm">🗄️ Komplett-Backup (alle Pulse-Daten)</h2>
            <p class="text-xs text-slate-500 dark:text-slate-400">Ein ZIP mit allen Datenbanken ({len(pulse_backup.SQLITE_FILES)}), allen JSON-Dateien ({len(pulse_backup.JSON_FILES)}), Uploads, Beweisen und Transkripten plus Prüfsummen-Manifest und Discord-Struktur. Nicht enthalten: <code>.env</code> und <code>.session_secret</code>. Automatisch mit jeder Auto-Sicherung; aufbewahrt werden 7 tägliche, 4 wöchentliche und 6 monatliche. {test_line}</p>
            <form action="/backup/full/create" method="post"><button class="{BTN} text-xs py-3 px-5">🗄️ Komplett-Backup jetzt erstellen</button></form>
            {rows or '<div class="text-xs text-slate-400">Noch kein Komplett-Backup vorhanden.</div>'}
        </div>"""


@app.post("/backup/full/create")
async def full_backup_create(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    try:
        path, manifest = await create_full_backup_async(ctx.guild)
    except Exception as exc:
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Komplett-Backup fehlgeschlagen", type(exc).__name__)
        return back("/backups", f"Komplett-Backup fehlgeschlagen ({type(exc).__name__}). Vorhandene Sicherungen bleiben unverändert.", False)
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Komplett-Backup erstellt", f"{path.name} · {len(manifest['files'])} Dateien")
    hint = (" Hinweis: " + "; ".join(manifest["warnings"])) if manifest["warnings"] else ""
    return back("/backups", f"Komplett-Backup erstellt: {path.name} ({len(manifest['files'])} Dateien).{hint}", not manifest["warnings"])


@app.get("/backup/full/download/{filename}")
async def full_backup_download(request: Request, filename: str, user_session: str = Cookie(None)):
    auth(request, user_session, perm=None, admin=True)
    path = safe_full_backup_path(filename)
    if not path:
        raise HTTPException(status_code=404, detail="Backup nicht gefunden.")
    return FileResponse(path, media_type="application/zip", filename=os.path.basename(path))


@app.get("/backup/full/preview/{filename}", response_class=HTMLResponse)
async def full_backup_preview(request: Request, filename: str, user_session: str = Cookie(None)):
    """D-01/D-02: dry run – shows what a restore would change. Writes nothing."""
    import pulse_backup
    ctx = auth(request, user_session, perm=None, admin=True)
    path = safe_full_backup_path(filename)
    if not path:
        raise HTTPException(status_code=404, detail="Backup nicht gefunden.")
    try:
        preview = await asyncio.to_thread(pulse_backup.preview_restore, path, BASE_DIR)
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        return back("/backups", f"Backup nicht lesbar: {str(exc)[:200]}", False)
    manifest = preview["manifest"]
    changed = [f for f in preview["files"] if f["status"] != "unverändert"]
    file_rows = "".join(f'<tr><td class="py-1 pr-3 font-mono break-all">{esc(f["name"])}</td><td>{esc(f["status"])}</td></tr>' for f in changed[:300])
    table_rows = "".join(f'<tr><td class="py-1 pr-3 font-mono">{esc(t["db"])} · {esc(t["table"])}</td><td>{esc(t["current"])} → {esc(t["backup"])}</td></tr>' for t in preview["tables"][:300])
    problems = "".join(f"<li>{esc(p)}</li>" for p in preview["problems"])
    problem_box = (f'<div class="rounded-xl border border-rose-500/40 bg-rose-500/5 p-3 text-xs"><strong>Backup fehlerhaft – Wiederherstellung gesperrt:</strong><ul class="list-disc pl-5">{problems}</ul></div>'
                   if problems else "")
    restore_form = "" if problems else f"""
        <form action="/backup/full/restore" method="post" class="{CARD} p-4 flex flex-col gap-3" onsubmit="return confirm('Pulse-Daten jetzt aus diesem Backup wiederherstellen?');">
            <input type="hidden" name="filename" value="{esc(os.path.basename(path))}">
            <p class="text-xs text-slate-500 dark:text-slate-400">Vorher wird automatisch eine Sicherheitskopie des aktuellen Stands angelegt. Datenbanken werden in die laufenden Dateien kopiert und auf Integrität geprüft; Uploads werden ergänzt, aber nichts gelöscht. Die Discord-Struktur (Rollen/Kanäle) wird hier <strong>nicht</strong> verändert.</p>
            <label class="text-xs font-semibold">Zur Bestätigung <code>WIEDERHERSTELLEN</code> eingeben<input name="confirm" class="{INPUT} mt-1" autocomplete="off" required></label>
            <button class="self-start bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-xs px-4 py-2 rounded-xl font-semibold">↩️ Pulse-Daten wiederherstellen</button>
        </form>"""
    skipped = len(manifest.get("skipped", []))
    body = f"""
    <div class="max-w-4xl space-y-4">
        <a href="/backups" class="text-xs text-indigo-500">← Backups</a>
        <h1 class="text-2xl font-bold">Probelauf: {esc(os.path.basename(path))}</h1>
        <p class="text-xs text-slate-500">Erstellt {esc(display(manifest.get("created_at")))} mit Pulse {esc(manifest.get("pulse_version"))} · {len(manifest.get("files", []))} Dateien · {skipped} wegen Größenbudget/Fehler nicht enthalten.</p>
        {problem_box}
        <div class="{CARD} p-4"><h2 class="font-bold text-sm mb-2">Tabellen mit anderer Zeilenzahl (jetzt → Backup)</h2>
            <table class="text-xs w-full">{table_rows or '<tr><td class="text-slate-400">Keine Unterschiede in den Zeilenzahlen.</td></tr>'}</table></div>
        <div class="{CARD} p-4"><h2 class="font-bold text-sm mb-2">Dateien, die sich ändern oder neu entstehen ({len(changed)})</h2>
            <table class="text-xs w-full">{file_rows or '<tr><td class="text-slate-400">Keine Dateiänderungen.</td></tr>'}</table></div>
        {restore_form}
    </div>"""
    return render_page("Backup-Probelauf", ctx, "backups", body)


@app.post("/backup/full/restore")
async def full_backup_restore(request: Request, filename: str = Form(...), confirm: str = Form(""),
                              user_session: str = Cookie(None)):
    import pulse_backup
    ctx = auth(request, user_session, perm=None, admin=True)
    path = safe_full_backup_path(filename)
    if not path:
        return back("/backups", "Backup nicht gefunden.", False)
    if confirm.strip() != "WIEDERHERSTELLEN":
        return back(f"/backup/full/preview/{os.path.basename(path)}", "Bitte zur Bestätigung WIEDERHERSTELLEN eingeben.", False)
    actor = ctx.user.get("global_name") or ctx.user.get("username")
    log_audit(actor, ctx.user["id"], "Komplett-Wiederherstellung gestartet", os.path.basename(path))
    try:
        # The JSON I/O lock is held while files are written, so no load-modify-save can interleave.
        report = await asyncio.to_thread(pulse_backup.restore_full_backup, path, BASE_DIR, full_backup_dir(), lock=_io_lock)
    except Exception as exc:
        log_audit(actor, ctx.user["id"], "Komplett-Wiederherstellung fehlgeschlagen", f"{type(exc).__name__}: {str(exc)[:200]}")
        return back("/backups", f"Wiederherstellung fehlgeschlagen: {str(exc)[:240]}", False)
    details = (f"{os.path.basename(path)} · Sicherheitskopie {report['safety_backup']} · DBs: {', '.join(report['databases'])} · "
               f"JSON: {len(report['json'])} · Dateien: {report['files']}")
    log_audit(actor, ctx.user["id"], "Komplett-Wiederherstellung abgeschlossen", details)
    return back("/backups", f"Pulse-Daten wiederhergestellt ({len(report['databases'])} Datenbanken, {len(report['json'])} JSON-Dateien, "
                            f"{report['files']} Dateien). Sicherheitskopie: {report['safety_backup']}. "
                            "Bitte den Bot jetzt neu starten, damit Module mit Zwischenspeicher (Social Media, KI-Moderation) "
                            "die wiederhergestellten Daten laden.")


@app.post("/backup/full/delete")
async def full_backup_delete(request: Request, filename: str = Form(...), user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    path = safe_full_backup_path(filename)
    if not path:
        return back("/backups", "Backup nicht gefunden.", False)
    os.remove(path)
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Komplett-Backup gelöscht", os.path.basename(path))
    return back("/backups", "Komplett-Backup gelöscht.")

# =============================================================
# ROUTE: EINSTELLUNGEN, RECHTE & AUDIT-LOG (nur Admins)
# =============================================================
@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    guild, config = ctx.guild, ctx.config
    team_role_ids = config.get("team_role_ids", [])
    perms_cfg = config.get("permissions", {})
    weekly_goal = float(config.get("weekly_goal_hours", 3.0))

    role_boxes = "".join(f"""
        <label class="flex items-center gap-2 cursor-pointer text-xs py-1">
            <input type="checkbox" name="team_roles" value="{r.id}" {'checked' if r.id in team_role_ids else ''} class="rounded">
            <span class="font-semibold" style="color:{role_hex(r)};">{esc(r.name)}</span>
        </label>""" for r in sorted(guild.roles, key=lambda r: -r.position) if not r.is_default() and not r.managed)

    perm_cards = ""
    for rid in team_role_ids:
        role = guild.get_role(rid)
        if not role:
            continue
        rp = perms_cfg.get(str(rid), {"can_view_dashboard": True})
        def cb(name, label):
            return (f'<label class="flex items-center gap-2 cursor-pointer text-slate-700 dark:text-slate-300">'
                    f'<input type="checkbox" name="{name}" {"checked" if rp.get(name) else ""} class="rounded"><span>{label}</span></label>')
        perm_cards += f"""
        <div class="{CARD} p-5 space-y-4">
            <div class="flex justify-between items-center border-b border-slate-100 dark:border-slate-800 pb-2.5">
                <span class="font-bold text-sm" style="color:{role_hex(role)};">{esc(role.name)}</span>
                <span class="text-[10px] text-slate-400 font-mono">ID: {role.id}</span>
            </div>
            <form action="/action" method="post" class="grid grid-cols-2 gap-3 text-xs">
                <input type="hidden" name="action" value="save_role_permissions"><input type="hidden" name="role_id" value="{role.id}">
                {cb('can_view_dashboard', 'Dashboard sehen')}{cb('can_warn', 'Verwarnen')}
                {cb('can_promote', 'Befördern/Degradieren/Kicken')}{cb('can_add_notes', 'Notizen erstellen')}
                {cb('can_manage_tickets', 'Tickets verwalten')}{cb('can_manage_applications', 'Bewerbungen verwalten')}
                {cb('can_manage_tasks', 'Aufgaben verwalten')}{cb('can_manage_training', 'Schulungen verwalten')}
                {cb('can_manage_wiki', 'Wiki verwalten')}{cb('can_view_analytics', 'Statistiken sehen')}
                <button class="col-span-2 mt-2 {BTN} py-2.5">Rechte Speichern</button>
            </form>
        </div>"""

    # Interne Dashboard-Profile werden echten Discord-Teamrollen zugeordnet.
    dashboard_role_cfg = config.get("dashboard_roles", {})

    def dashboard_permission_boxes(profile_permissions, default_dashboard=False):
        return "".join(
            f'<label class="flex items-start gap-2 text-xs text-slate-700 dark:text-slate-300">'
            f'<input type="checkbox" name="{key}" {"checked" if (profile_permissions.get(key) or (default_dashboard and key == "can_view_dashboard")) else ""} class="rounded mt-0.5">'
            f'<span>{esc(label)}</span></label>'
            for key, label in DASHBOARD_PERMISSION_LABELS
        )

    def dashboard_discord_role_boxes(selected_role_ids):
        selected = {int(value) for value in selected_role_ids if str(value).isdigit()}
        role_items = []
        for rid in team_role_ids:
            role = guild.get_role(int(rid))
            if not role or role.is_default() or role.managed:
                continue
            role_items.append(
                f'<label class="flex items-start gap-2 text-xs text-slate-700 dark:text-slate-300">'
                f'<input type="checkbox" name="dashboard_discord_role_ids" value="{role.id}" '
                f'{"checked" if role.id in selected else ""} class="rounded mt-0.5">'
                f'<span><span class="font-semibold">{esc(role.name)}</span> '
                f'<span class="text-slate-400">({role.id})</span></span></label>'
            )
        return "".join(role_items) or "<p class='text-xs text-slate-400'>Zuerst unter Team-Rollen mindestens eine Discord-Teamrolle konfigurieren.</p>"

    dashboard_role_cards = ""
    for dashboard_role_key, profile in sorted(dashboard_role_cfg.items(), key=lambda item: str(item[1].get("name", item[0]) if isinstance(item[1], dict) else item[0]).casefold()):
        if not isinstance(profile, dict):
            continue
        profile_permissions = profile.get("permissions", {})
        dashboard_role_name = str(profile.get("name") or dashboard_role_key)
        mapped_role_ids = profile.get("discord_role_ids", [])
        mapped_role_names = [
            guild.get_role(int(rid)).name
            for rid in mapped_role_ids
            if str(rid).isdigit() and guild.get_role(int(rid))
        ]
        dashboard_role_cards += f"""
        <div class="{CARD} p-5 space-y-4">
            <form action="/action" method="post" class="space-y-3">
                <input type="hidden" name="action" value="save_dashboard_role">
                <input type="hidden" name="dashboard_role_id" value="{esc(dashboard_role_key)}">
                <label class="block text-xs font-semibold">Name des digitalen Profils
                    <input required maxlength="40" name="dashboard_role_name" value="{esc(dashboard_role_name)}" class="{INPUT} w-full mt-1">
                </label>
                <div class="space-y-2">
                    <p class="text-xs font-bold">Discord-Teamrollen, die dieses Profil erhalten</p>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">{dashboard_discord_role_boxes(mapped_role_ids)}</div>
                </div>
                <div class="space-y-2">
                    <p class="text-xs font-bold">Dashboard-Berechtigungen</p>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">{dashboard_permission_boxes(profile_permissions)}</div>
                </div>
                <button class="{BTN} w-full py-2.5">Profil & Zuordnung speichern</button>
            </form>
            <p class="text-[11px] text-slate-500">Aktuell zugeordnet: {esc(", ".join(mapped_role_names) if mapped_role_names else "Keine Discord-Teamrolle")}</p>
            <form action="/action" method="post" onsubmit="return confirm('Dieses digitale Dashboard-Profil wirklich löschen?')">
                <input type="hidden" name="action" value="delete_dashboard_role">
                <input type="hidden" name="dashboard_role_id" value="{esc(dashboard_role_key)}">
                <button class="w-full rounded-xl border border-rose-300 dark:border-rose-900 text-rose-600 dark:text-rose-400 px-3 py-2 text-xs font-semibold">Digitales Profil löschen</button>
            </form>
        </div>"""

    dashboard_new_role_form = f"""
    <div class="{CARD} p-5 space-y-4">
        <h3 class="font-bold text-sm">➕ Neues digitales Dashboard-Profil</h3>
        <form action="/action" method="post" class="space-y-3">
            <input type="hidden" name="action" value="save_dashboard_role">
            <label class="block text-xs font-semibold">Profilname
                <input required maxlength="40" name="dashboard_role_name" placeholder="z. B. Support, Personalverwaltung, Leitung" class="{INPUT} w-full mt-1">
            </label>
            <div class="space-y-2">
                <p class="text-xs font-bold">Zugeordnete Discord-Teamrollen</p>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">{dashboard_discord_role_boxes([])}</div>
            </div>
            <div class="space-y-2">
                <p class="text-xs font-bold">Dashboard-Berechtigungen</p>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">{dashboard_permission_boxes({}, default_dashboard=True)}</div>
            </div>
            <p class="text-[11px] text-slate-500">Speichern legt eine digitale Rolle in Pulse an. Es wird keine neue Discord-Rolle erstellt oder verändert.</p>
            <button class="{BTN} w-full py-2.5">Digitales Profil erstellen</button>
        </form>
    </div>"""

    dashboard_role_section = f"""
    <section class="{CARD} p-6 space-y-5">
        <div>
            <h2 class="text-lg font-bold text-slate-900 dark:text-white">🧩 Digitale Dashboard-Rollen</h2>
            <p class="text-xs text-slate-500 mt-1">Diese Rollen existieren nur in Pulse. Du erstellst ein Profil, wählst seine Rechte und ordnest ihm eine oder mehrere bereits konfigurierte Discord-Teamrollen zu. Alle Mitglieder dieser Discord-Rollen erhalten das Profil automatisch. Discord-Rollen, Serverrechte und Rollenhierarchie werden nicht verändert.</p>
        </div>
        {dashboard_new_role_form}
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">{dashboard_role_cards or "<p class='text-xs text-slate-400 italic'>Noch keine digitalen Dashboard-Profile angelegt.</p>"}</div>
    </section>"""

    audit = load_json(AUDIT_FILE, [])
    audit_html = "".join(f"""
        <div class="audit-row bg-slate-50 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-800 rounded-xl p-3 text-xs flex flex-col sm:flex-row sm:justify-between sm:items-center gap-1" data-search="{esc((str(e.get('actor')) + ' ' + str(e.get('action')) + ' ' + str(e.get('details'))).lower())}">
            <div class="break-words"><span class="font-bold text-slate-900 dark:text-white">{esc(e.get('actor'))}</span>
                <span class="text-indigo-600 dark:text-indigo-400 font-semibold px-2">[{esc(e.get('action'))}]</span>
                <span class="text-slate-600 dark:text-slate-300">{esc(e.get('details'))}</span></div>
            <span class="text-[10px] text-slate-400 font-mono shrink-0">{esc(e.get('timestamp'))}</span>
        </div>""" for e in reversed(audit[-100:]))

    head = """<script>function filterAudit(){const q=document.getElementById('auditSearch').value.toLowerCase();
        document.querySelectorAll('.audit-row').forEach(r=>r.style.display=r.dataset.search.includes(q)?'':'none');}</script>"""
    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-2">Einstellungen & Panel-Audit-Log</h1>
    <p class="text-xs text-slate-500 dark:text-slate-400 mb-6">Server-ID: <code class="text-indigo-600 dark:text-indigo-400 font-mono">{GUILD_ID}</code></p>
    <div class="space-y-8">
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div class="{CARD} p-6 space-y-3">
                <h2 class="text-base font-bold text-slate-900 dark:text-white">🎯 Wochenziel</h2>
                <form action="/action" method="post" class="flex gap-2 items-center text-xs">
                    <input type="hidden" name="action" value="set_weekly_goal">
                    <input type="number" step="0.5" min="0.5" max="100" name="weekly_goal" value="{weekly_goal:g}" class="{INPUT} w-28"> <span>Stunden pro Woche</span>
                    <button class="{BTN} px-4 py-3">Speichern</button>
                </form>
            </div>
            <div class="{CARD} p-6 space-y-3">
                <h2 class="text-base font-bold text-slate-900 dark:text-white">👥 Team-Rollen</h2>
                <p class="text-[11px] text-slate-500">Die Reihenfolge der Beförderungen richtet sich nach der Rollen-Position im Server (niedrig → hoch).</p>
                <form action="/action" method="post" class="space-y-2">
                    <input type="hidden" name="action" value="save_team_roles">
                    <div class="max-h-48 overflow-y-auto grid grid-cols-1 sm:grid-cols-2">{role_boxes}</div>
                    <button class="{BTN} px-4 py-2.5 text-xs">Team-Rollen speichern</button>
                </form>
            </div>
        </div>
        <div>
            <h2 class="text-lg font-bold text-slate-900 dark:text-white mb-1">Rollen-Berechtigungen</h2>
            <p class="text-[11px] text-slate-500 mb-4">Admins/Server-Owner haben immer alle Rechte. Neue Team-Rollen dürfen standardmäßig nur das Dashboard sehen.</p>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">{perm_cards or "<p class='text-xs text-slate-400 italic'>Keine Team-Rollen konfiguriert.</p>"}</div>
        </div>
        {dashboard_role_section}
        <div class="{CARD} p-6 space-y-4">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <h2 class="text-lg font-bold text-slate-900 dark:text-white">📜 Panel-Audit-Log (letzte 100)</h2>
                <input type="text" id="auditSearch" oninput="filterAudit()" placeholder="🔎 Filtern..." class="bg-slate-50 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-700 rounded-xl px-3 py-2 text-xs w-full sm:w-56">
            </div>
            <div class="space-y-2 max-h-96 overflow-y-auto pr-1">{audit_html or "<p class='text-xs text-slate-400 italic'>Keine Audit-Einträge vorhanden.</p>"}</div>
        </div>
    </div>"""
    return render_page("Einstellungen & Rechte", ctx, "settings", body, head)


# =============================================================
# ZENTRALER ACTION-HANDLER (jede Aktion mit Rechteprüfung)
# =============================================================
ACTION_PERMS = {
    "promote": "can_promote", "demote": "can_promote", "kick": "can_promote", "hire": "can_promote",
    "warn_with_proof": "can_warn", "remove_warn": "can_warn", "add_note": "can_add_notes",
    "submit_loa": "can_view_dashboard", "cancel_loa": "can_view_dashboard",
    "vote_app": "can_view_dashboard", "decide_app": "can_manage_applications",
    "meeting_rsvp": "can_view_dashboard", "add_meeting_topic": "can_view_dashboard",
    "delete_meeting_topic": "can_view_dashboard", "set_meeting_info": "can_promote",
    "reset_support_case_period": "can_promote",
    "save_role_permissions": "admin", "set_weekly_goal": "admin", "save_team_roles": "admin",
    "save_dashboard_role": "admin", "delete_dashboard_role": "admin",
}



def user_entry(team_db: dict, key: str) -> dict:
    entry = team_db.setdefault(key, {})
    for k, v in (("warns_list", []), ("notes", []), ("ticket_cases", 0), ("support_cases", 0)):
        entry.setdefault(k, v)
    normalize_warns(entry)
    return entry


def can_see_warnings(perms: dict, viewer_id, target_id) -> bool:
    """E-3 / D-08: team warnings (incl. count and reason) only for the person themself, can_warn and admins."""
    perms = perms or {}
    return str(viewer_id) == str(target_id) or bool(perms.get("can_warn") or perms.get("is_admin"))


def can_see_notes(perms: dict) -> bool:
    """E-3 / D-08: internal team notes only for can_add_notes, leadership (can_promote) and admins."""
    perms = perms or {}
    return bool(perms.get("can_add_notes") or perms.get("can_promote") or perms.get("is_admin"))


def commit_member_entry(uid, before: dict, ours: dict) -> dict:
    """K-04: merge our changes of one member into the *current* team_data.json (no await inside)."""
    from pulse_store import merge_entry
    with _io_lock:
        team_db = load_json(DATA_FILE, {})
        if not isinstance(team_db, dict):
            team_db = {}
        merged = merge_entry(before, ours, user_entry(team_db, str(uid)))
        team_db[str(uid)] = merged
        save_json(DATA_FILE, team_db)
        return merged


def track_member_entry(uid, entry: dict):
    """Snapshot of a member entry; `.commit()` writes only our changes back (see pulse_store)."""
    from pulse_store import EntryTracker
    return EntryTracker(uid, entry, commit_member_entry)


def commit_shift_event(event: dict) -> None:
    """K-04: store one changed history event without writing back a stale copy of all shifts."""
    event_id = event.get("shift_event_id")
    identity = (event.get("mod_id"), event.get("started_at_iso"), event.get("ended_at_iso"))
    with _io_lock:
        shifts_db = load_json(SHIFTS_FILE, {})
        history = shifts_db.setdefault("history", [])
        for pos, item in enumerate(history):
            if not isinstance(item, dict):
                continue
            same = (item.get("shift_event_id") == event_id) if event_id else (
                (item.get("mod_id"), item.get("started_at_iso"), item.get("ended_at_iso")) == identity)
            if same:
                history[pos] = dict(event)
                break
        else:
            return
        save_json(SHIFTS_FILE, shifts_db)


def team_since_for(member, entry=None) -> str:
    """Bestmögliches Team-Eintrittsdatum aus Pulse-Historie bzw. Discord."""
    entry = entry if isinstance(entry, dict) else {}
    raw = str(entry.get("team_since") or "").strip()
    if raw:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%d.%m.%Y %H:%M"):
            try:
                return datetime.strptime(raw, fmt).strftime("%d.%m.%Y")
            except Exception:
                pass

    try:
        apps = load_json(APPS_FILE, {})
        candidates = []
        for item in apps.values() if isinstance(apps, dict) else []:
            if str(item.get("user_id")) != str(member.id) or str(item.get("status")) not in {"hired", "accepted"}:
                continue
            decided = parse_timestamp(item.get("decided_at"))  # D-09: every stored format
            if decided is not None:
                candidates.append(decided)
        if candidates:
            return display(min(candidates), with_time=False)  # Berlin date
    except Exception:
        pass

    return member.joined_at.strftime("%d.%m.%Y") if member.joined_at else "unbekannt"


def migrate_warning_data():
    """Normalisiert bestehende Warn-Datensätze einmalig beim Start."""
    team_db = load_json(DATA_FILE, {})
    if not isinstance(team_db, dict):
        return
    changed = False
    for entry in team_db.values():
        if isinstance(entry, dict):
            changed = normalize_warns(entry) or changed
    if changed:
        save_json(DATA_FILE, team_db)


migrate_warning_data()


# ---------- BOT-05: what follows a rank change (dashboard and slash commands) ----------
async def finish_promotion(guild, *, member, previous_role, new_role, probation_days: int, actor_name: str,
                           actor_id: str, reason: str, source: str = "Dashboard") -> dict:
    """After the roles were changed: probation entry (3 or 7 days, kept history), DM, team update, audit.
    Without a previous team role there is nothing to fall back to, so no probation is recorded."""
    probation_days = int(probation_days)
    probation_started = now_de()
    probation_expires = probation_started + timedelta(days=probation_days)
    if previous_role is not None:
        team_db = load_json(DATA_FILE, {})
        team_entry = user_entry(team_db, str(member.id))
        tracker = track_member_entry(member.id, team_entry)  # K-04
        previous_probation = team_entry.get("probation")
        if isinstance(previous_probation, dict) and previous_probation.get("active"):
            history = team_entry.setdefault("probation_history", [])
            history.append(dict(previous_probation))
            team_entry["probation_history"] = history[-10:]
        team_entry["probation"] = {
            "active": True,
            "status": "active",
            "days": probation_days,
            "previous_role_id": previous_role.id,
            "new_role_id": new_role.id,
            "started_at": probation_started.isoformat(timespec="seconds"),
            "expires_at": probation_expires.isoformat(timespec="seconds"),
            "promoted_by": actor_name,
            "promoted_by_id": actor_id,
            "reason": reason,
        }
        tracker.commit()
    until = probation_expires.strftime("%d.%m.%Y %H:%M")
    probation_text = f"{probation_days} Tage · bis {until}" if previous_role is not None else "keine (vorher keine Teamrolle)"
    await send_dm_notification(
        member,
        f"🎉 **Herzlichen Glückwunsch!** Du wurdest auf **{guild.name}** zum **{new_role.name}** befördert. "
        + (f"Deine Probezeit beträgt **{probation_days} Tage** und endet am **{until}**." if previous_role is not None else ""),
    )
    await send_team_update_embed(
        guild,
        "⬆️ Team-Update: Beförderung",
        f"{member.mention} wurde befördert.",
        _pulse_color("success"),
        target=member.mention,
        action="Beförderung",
        actor=actor_name,
        fields=[("Vorherige Rolle", previous_role.mention if previous_role is not None else "Keine", True),
                ("Neue Rolle", new_role.mention, True), ("Probezeit", probation_text, True), ("Grund", reason, False),
                ("Quelle", source, True)],
        thumbnail=member.display_avatar.url,
    )
    log_audit(actor_name, actor_id, "Beförderung",
              f"{member.display_name} -> {new_role.name} · Probezeit: {probation_text} · Grund: {reason}")
    return {"probation": previous_role is not None, "probation_text": probation_text}


async def finish_demotion(guild, *, member, previous_role, new_role, actor_name: str, actor_id: str, reason: str,
                          source: str = "Dashboard") -> None:
    """After the roles were changed: DM, team update and audit for a demotion (new_role None = no team rank left)."""
    if new_role is not None:
        await send_dm_notification(member, f"⚠️ Du wurdest auf **{guild.name}** auf die Rolle **{new_role.name}** degradiert.")
        await send_team_update_embed(
            guild,
            "⬇️ Team-Update: Degradierung",
            f"{member.mention} wurde degradiert.",
            _pulse_color("warning"),
            target=member.mention,
            action="Degradierung",
            actor=actor_name,
            fields=[("Vorherige Rolle", previous_role.mention if previous_role is not None else "Unbekannt", True),
                    ("Neue Rolle", new_role.mention, True), ("Grund", reason, False), ("Quelle", source, True)],
            thumbnail=member.display_avatar.url,
        )
        log_audit(actor_name, actor_id, "Degradierung", f"{member.display_name} -> {new_role.name} · Grund: {reason}")
        return
    await send_dm_notification(member, f"⚠️ Du wurdest aus dem Team-Rollenrang auf **{guild.name}** entfernt.")
    await send_team_update_embed(
        guild,
        "⬇️ Team-Update: Teamrolle entfernt",
        f"{member.mention} hat keine Teamrolle mehr.",
        _pulse_color("danger"),
        target=member.mention,
        action="Teamrolle entfernt",
        actor=actor_name,
        fields=[("Grund", reason, False), ("Quelle", source, True)],
        thumbnail=member.display_avatar.url,
    )
    log_audit(actor_name, actor_id, "Degradierung", f"{member.display_name} -> Keine Teamrolle · Grund: {reason}")


# ---------- BOT-05: one logic for team warnings (dashboard and slash commands) ----------
WARN_MIN_DAYS, WARN_MAX_DAYS = 1, 365


async def issue_team_warning(guild, *, actor_member, actor_name: str, actor_id: str, member, reason: str,
                             duration_days: int, proof: str = "", config: dict | None = None,
                             actor_is_admin: bool = False, source: str = "Dashboard") -> dict:
    """Checks, stores (K-04 merge), sets warn roles and consequences, posts the team update, DMs the
    person and escalates 5/5 to leadership. Returns {"ok", "message", "warn", "count", "role_report",
    "consequence_report"}; ok=False before anything was stored means the warning was refused."""
    config = config if config is not None else load_config()
    team_role_ids = config.get("team_role_ids", [])
    reason = (reason or "").strip()[:500]
    refused = lambda message: {"ok": False, "stored": False, "message": message}
    if not reason:
        return refused("Bitte einen Grund angeben.")
    try:
        duration_days = int(duration_days)
    except (TypeError, ValueError):
        duration_days = 0
    if not WARN_MIN_DAYS <= duration_days <= WARN_MAX_DAYS:
        return refused(f"Die Warn-Gültigkeit muss zwischen {WARN_MIN_DAYS} und {WARN_MAX_DAYS} Tagen liegen.")
    if member is None:
        return refused("Mitglied nicht gefunden.")
    if member.bot:
        return refused("Bots können keine Team-Verwarnung erhalten.")
    if str(member.id) == str(actor_id):
        return refused("Du kannst dir selbst keine Team-Verwarnung geben.")
    if team_role_ids and not any(r.id in team_role_ids for r in member.roles):
        return refused("Team-Verwarnungen können nur an Teammitglieder vergeben werden.")
    if not actor_is_admin and actor_member:
        actor_rank = team_rank(actor_member, team_role_ids)
        target_rank = team_rank(member, team_role_ids)
        if target_rank >= 0 and (actor_rank < 0 or target_rank >= actor_rank):
            return refused("Du darfst keine Team-Warnung an ein Mitglied mit gleichem oder höherem Teamrang vergeben.")

    team_db = load_json(DATA_FILE, {})
    entry = user_entry(team_db, str(member.id))
    tracker = track_member_entry(member.id, entry)  # K-04: merge-on-save after awaits
    current_count = len(active_warns(entry))
    if current_count >= 5:
        return refused("Dieses Teammitglied hat bereits die maximale Anzahl von 5 aktiven Verwarnungen.")
    allowed_warn, protection_message = await enforce_team_warn_protection(guild, actor_member, member, config, source=source)
    if not allowed_warn:
        return refused(protection_message)
    allowed_impact, impact_message = await enforce_high_impact_warning_approval(
        guild, actor_member, member, entry, current_count, config, source=source,
    )
    if not allowed_impact:
        return refused(impact_message)

    proof_url = safe_url(proof)
    warn = {
        "id": f"warn_{uuid.uuid4().hex[:6]}", "reason": reason, "proof": proof_url,
        "by": actor_name, "date": now_de().strftime("%d.%m.%Y %H:%M"),
        "duration_days": duration_days,
        "expires_at": (now_de() + timedelta(days=duration_days)).isoformat(timespec="seconds"),
        "active": True, "revoked_at": None, "revoked_by": None, "revoked_reason": "",
    }
    entry["warns_list"].append(warn)
    count = len(active_warns(entry))
    tracker.commit()
    log_audit(actor_name, actor_id, "Verwarnung", f"User-ID {member.id} ({count}/5): {reason}")
    role_report = await sync_warn_roles(guild, member, count, config)
    consequence_report = await apply_warn_consequences(guild, member, warn, count, config, entry=entry)
    tracker.commit()
    await send_team_update_embed(
        guild,
        "⚠️ Team-Update: Verwarnung",
        f"{member.mention} hat eine neue Verwarnung erhalten.",
        _pulse_color("warning"),
        target=f"{member.mention}\nWarn-Stufe: {count}/5",
        action=f"Verwarnung {count}/5",
        actor=actor_name,
        fields=[
            ("Grund", reason, False),
            ("Beweis", proof_url or "Kein Beweis-Link", False),
            ("Gültig bis", warn["expires_at"], True),
            ("Quelle", source, True),
            ("Discord-Warnrolle", f"✅ {role_report.get('role').mention}" if role_report.get("ok") and role_report.get("role") else (f"✅ keine Warnrolle bei 0" if count == 0 else f"❌ {role_report.get('message')}"), False),
        ],
        thumbnail=member.display_avatar.url,
    )
    consequence_text = "; ".join(x for x in consequence_report.get("actions", []) if x)
    dm_suffix = f"\n**Automatische Folge:** {consequence_text}" if consequence_text else ""
    await send_dm_notification(member, f"⚠️ Du hast eine Verwarnung erhalten ({count}/5)!\n**Grund:** {reason}\n**Von:** {actor_name}{dm_suffix}")
    if count >= 5:
        # Führungskräfte erhalten zusätzlich eine Pulse-Inbox-Meldung.
        for manager in guild.members:
            if manager.bot or manager.id == member.id:
                continue
            manager_perms, _ = compute_perms(guild, manager.id, config)
            if manager_perms.get("can_promote") or manager_perms.get("is_admin"):
                try:
                    pulse_db.notify(manager.id, "🚨 5/5 Team-Warnungen",
                                    f"{member.display_name} hat 5 aktive Verwarnungen. Bitte Fall prüfen.",
                                    "warning", "/warns", f"warn-escalation:{member.id}:{count}", 86400)
                except Exception as exc:
                    print(f"Warn-Eskalationsbenachrichtigung fehlgeschlagen: {exc}")
        await send_team_update_embed(
            guild,
            "🚨 Team-Update: 5 Verwarnungen",
            f"**Mitglied:** {member.mention} ({member.display_name})\n**Status:** Automatisch aus dem Team entfernt.",
            _pulse_color("danger"),
            target=member.mention,
            action="Warn-Schwelle 5/5",
            actor=actor_name,
            fields=[("Warnrollen-Sync", "✅ Erfolgreich" if role_report.get("ok") else f"❌ {role_report.get('message')}")],
            thumbnail=member.display_avatar.url,
        )
    ok = bool(role_report.get("ok") and consequence_report.get("ok"))
    if ok:
        message = f"Verwarnung eingetragen ({count}/5). Automatische Folge ausgeführt."
    else:
        problems = []
        if not role_report.get("ok"):
            problems.append(f"Warnrolle: {role_report.get('message')}")
        if not consequence_report.get("ok"):
            problems.extend(consequence_report.get("actions", []))
        suffix = " · ".join(x for x in problems if x) or "Eine automatische Folge konnte nicht vollständig ausgeführt werden."
        message = f"Verwarnung eingetragen ({count}/5), aber: {suffix}"
    return {"ok": ok, "stored": True, "message": message, "warn": warn, "count": count,
            "role_report": role_report, "consequence_report": consequence_report}


async def revoke_team_warning(guild, *, member, warn_id: str, revoke_reason: str, actor_name: str, actor_id: str,
                              config: dict | None = None, source: str = "Team-Dashboard") -> dict:
    """Withdraws one warning: marks it, lifts its temporary consequences (unless another active warning
    still justifies them), syncs warn roles, posts the team update and writes the audit."""
    config = config if config is not None else load_config()
    revoke_reason = (revoke_reason or "").strip()[:500]
    if member is None:
        return {"ok": False, "stored": False, "message": "Mitglied nicht gefunden."}
    if not revoke_reason:
        return {"ok": False, "stored": False, "message": "Bitte einen Grund für die Rücknahme der Verwarnung angeben."}
    team_db = load_json(DATA_FILE, {})
    entry = user_entry(team_db, str(member.id))
    tracker = track_member_entry(member.id, entry)  # K-04: merge-on-save after awaits
    target = next((w for w in entry["warns_list"] if str(w.get("id")) == str(warn_id)), None)
    if target is None or not target.get("active", True) or target.get("revoked_at"):
        return {"ok": False, "stored": False,
                "message": "Diese Verwarnung wurde bereits zurückgezogen oder ist nicht mehr vorhanden. Bitte die Seite aktualisieren."}
    target["active"] = False
    target["revoked_at"] = now_de().strftime("%d.%m.%Y %H:%M")
    target["revoked_by"] = actor_name
    target["revoked_reason"] = revoke_reason
    count = len(active_warns(entry))
    tracker.commit()

    original_reason = str(target.get("reason") or "Kein Grund")
    original_by = str(target.get("by") or "System")
    original_date = str(target.get("date") or "N/A")
    # Temporäre Folgen dieser konkreten Warnung beim Zurückziehen sofort entfernen.
    temp_roles = (target.get("consequences") or {}).get("temporary_roles", [])
    temp_remove_failed = False
    temp_roles_to_keep = []
    for restriction in temp_roles:
        role_id = int(restriction.get("role_id", 0) or 0)
        role = guild.get_role(role_id)
        if not role:
            continue
        # A different active warning keeps temporary punishments in place,
        # even if the warning that originally granted the role was revoked.
        other_active_warning = any(
            other is not target
            and isinstance(other, dict)
            and other.get("active", True)
            and not other.get("revoked_at")
            and not warning_is_expired(other)
            for other in entry.get("warns_list", [])
        )
        if other_active_warning:
            temp_roles_to_keep.append(restriction)
            continue
        try:
            if role in member.roles:
                if guild.me and (not guild.me.guild_permissions.manage_roles or role.position >= guild.me.top_role.position):
                    temp_remove_failed = True
                    temp_roles_to_keep.append(restriction)
                    continue
                await member.remove_roles(role, reason=f"Warn zurückgezogen · {revoke_reason}")
        except discord.Forbidden:
            temp_remove_failed = True
            temp_roles_to_keep.append(restriction)
        except Exception:
            temp_remove_failed = True
            temp_roles_to_keep.append(restriction)
    target.setdefault("consequences", {})["temporary_roles"] = temp_roles if temp_remove_failed else temp_roles_to_keep
    tracker.commit()
    role_report = await sync_warn_roles(guild, member, count, config)

    await send_team_update_embed(
        guild,
        "✅ Team-Update: Verwarnung zurückgezogen",
        f"Die Verwarnung von {member.mention} wurde durch {source} zurückgezogen.",
        _pulse_color("success"),
        target=f"{member.mention}\nAktive Warnungen: {count}/5",
        action="Warn zurückgezogen",
        actor=actor_name,
        fields=[
            ("Ursprünglicher Grund", original_reason, False),
            ("Ausgestellt von", original_by, True),
            ("Ausgestellt am", original_date, True),
            ("Rücknahmegrund", revoke_reason, False),
            ("Warn-ID", warn_id, True),
            ("Discord-Warnrolle", "✅ Synchronisiert" if role_report.get("ok") else f"❌ {role_report.get('message')}"),
        ],
        thumbnail=member.display_avatar.url,
    )
    log_audit(actor_name, actor_id, "Warn Zurückgezogen",
              f"User-ID {member.id}, Warn-ID {warn_id}, Grund: {original_reason}, Rücknahme: {revoke_reason}")
    if role_report.get("ok") and not temp_remove_failed:
        message = f"Verwarnung zurückgezogen ({count}/5)."
    elif not role_report.get("ok"):
        message = f"Verwarnung zurückgezogen ({count}/5), aber Discord-Warnrolle konnte nicht synchronisiert werden: {role_report.get('message')}"
    else:
        message = f"Verwarnung zurückgezogen ({count}/5), aber eine temporäre Sperrrolle konnte nicht entfernt werden – Rollenhierarchie prüfen."
    return {"ok": bool(role_report.get("ok")) and not temp_remove_failed, "stored": True, "message": message,
            "count": count, "role_report": role_report}


@app.post("/action")
async def handle_action(
    request: Request,
    action: str = Form(...),
    user_id: int = Form(None),
    role_id: int = Form(None),
    redirect_to_member: str = Form(None),
    warn_reason: str = Form(None),
    warn_proof: str = Form(None),
    warn_duration_days: int = Form(7),
    probation_days: int = Form(None),
    warn_id: str = Form(None),
    warn_revoke_reason: str = Form(None),
    action_reason: str = Form(None),
    note_text: str = Form(None),
    loa_start: str = Form(None),
    loa_end: str = Form(None),
    loa_reason: str = Form(None),
    target_user_id: str = Form(None),
    applicant_id: int = Form(None),
    applicant_name: str = Form(None),
    applicant_text: str = Form(None),
    career_path: str = Form(None),
    app_id: str = Form(None),
    vote: str = Form(None),
    decision: str = Form(None),
    hire_role_id: int = Form(None),
    rsvp_status: str = Form(None),
    topic_title: str = Form(None),
    topic_details: str = Form(None),
    topic_id: str = Form(None),
    meeting_title: str = Form(None),
    meeting_datetime: str = Form(None),
    meeting_desc: str = Form(None),
    reset_rsvps: bool = Form(False),
    clear_topics: bool = Form(False),
    announce: bool = Form(False),
    weekly_goal: float = Form(None),
    team_roles: List[int] = Form(default=[]),
    dashboard_role_id: str = Form(None),
    dashboard_role_name: str = Form(None),
    dashboard_discord_role_ids: List[int] = Form(default=[]),
    can_view_dashboard: bool = Form(False),
    can_warn: bool = Form(False),
    can_promote: bool = Form(False),
    can_add_notes: bool = Form(False),
    can_manage_tickets: bool = Form(False),
    can_manage_applications: bool = Form(False),
    can_manage_tasks: bool = Form(False),
    can_manage_training: bool = Form(False),
    can_manage_wiki: bool = Form(False),
    can_view_analytics: bool = Form(False),
    user_session: str = Cookie(None),
    apply_session: str = Cookie(None)
):
    # ---------- Öffentliche Bewerbung inklusive serverseitig bewerteten Laufbahn-Test ----------
    if action == "submit_application":
        ip = request.client.host if request.client else "unknown"
        # K-01: every error page leads back to the same career form; the browser draft refills it.
        return_career = career_path if career_path in APPLICATION_CAREERS else ""
        form_url = f"/apply?career={quote(return_career)}" if return_career else "/apply"
        back_link = (f'<a href="{form_url}" class="px-4 py-2 rounded-xl bg-slate-100 dark:bg-slate-800">'
                     'Zurück zum Formular – deine Antworten sind noch da</a>')
        login_link = (f'<a href="/apply/login?career={quote(return_career)}" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">'
                      'Neu mit Discord verbinden – deine Antworten bleiben erhalten</a>')
        if rate_limited(f"apply:{ip}"):
            return HTMLResponse(simple_page("⏳", "Zu viele Bewerbungen", "Bitte versuche es später erneut.", back_link), status_code=429)
        if career_path not in APPLICATION_CAREERS or not application_module_states(load_config()).get(career_path, False):
            return HTMLResponse(simple_page("⏸️", "Bewerbungsmodul geschlossen", "Für diese Laufbahn werden aktuell keine Bewerbungen angenommen.", back_link), status_code=403)
        apply_user = verify_session_token(apply_session, TOKEN_APPLY)
        if not apply_user or str(apply_user.get("id")) != str(applicant_id):
            return HTMLResponse(simple_page("🔒", "Discord-Verknüpfung fehlt",
                                            "Deine Sitzung ist abgelaufen oder fehlt. Deine Antworten sind in diesem Browser gespeichert – "
                                            "nach dem erneuten Verbinden mit Discord ist alles wieder da.", login_link), status_code=403)
        eligible, eligibility_status, eligibility_role, eligibility_message = await verify_application_member(
            request, apply_user.get("id")
        )
        if not eligible:
            title = "Bewerbung nicht freigegeben" if eligibility_status == 403 else "Mitgliedschaft gerade nicht prüfbar"
            return HTMLResponse(
                simple_page("🔒", title, eligibility_message, back_link),
                status_code=eligibility_status,
            )
        name = (apply_user.get("global_name") or apply_user.get("username") or applicant_name or "").strip()[:80]
        text = (applicant_text or "").strip()[:2000]
        if not applicant_id or not name or not text:
            return HTMLResponse(simple_page("⚠️", "Angaben fehlen", "Bitte fülle die Motivation und alle Pflichtfelder aus.", back_link), status_code=400)
        try:
            # Die Laufbahnen haben unterschiedliche Testlängen (12/22/30).
            # Antworten anhand der serverseitig definierten Fragezahl aus dem Formular
            # lesen; zusätzliche oder manipulierte Felder können den Score nicht erhöhen.
            submitted_form = await request.form()
            career_definition = APPLICATION_CAREERS.get(career_path or "")
            answer_count = len(career_definition["questions"]) if career_definition else 0
            submitted_answers = [
                str(submitted_form.get(f"answer_{index}", ""))
                for index in range(1, answer_count + 1)
            ]
            test_result = grade_career_test(career_path or "", submitted_answers)
            practical_result = grade_practical_tasks(career_path or "", submitted_form)
        except ValueError as exc:
            return HTMLResponse(simple_page("⚠️", "Online-Test unvollständig", esc(str(exc)), back_link), status_code=400)

        apps = load_json(APPS_FILE, {})
        if any(a.get("user_id") == str(applicant_id) and a.get("status") in {"pending", "interview"} for a in apps.values()):
            return HTMLResponse(simple_page("ℹ️", "Bewerbung liegt bereits vor", "Deine Bewerbung wird gerade geprüft. Ein weiterer Antrag ist währenddessen nicht möglich.", back_link))
        app_id_new = f"app_{uuid.uuid4().hex[:10]}"
        apps[app_id_new] = {
            "user_id": str(applicant_id),
            "name": name,
            "text": text,
            "status": "pending",
            "career_path": test_result["career_path"],
            "career_label": test_result["career_label"],
            "test_score": test_result["score"],
            "test_total": test_result["total"],
            "test_percent": test_result["percent"],
            "test_pass_percent": test_result["pass_percent"],
            "test_passed": test_result["passed"],
            "test_details": test_result["details"],
            "test_version": 3,
            "practical_answers": practical_result["answers"],
            "practical_review_status": "pending",
            "practical_version": 1,
            "upvotes": [],
            "downvotes": [],
            "created_at": now_de().strftime("%d.%m.%Y %H:%M"),
        }
        save_json(APPS_FILE, apps)
        await notify_new_application(request, app_id_new, apps[app_id_new])
        await dm_applicant(request, applicant_id, "received", id=app_id_new)  # BOT-10
        result_status = "Bestanden" if test_result["passed"] else "noch nicht bestanden"
        result_text = (
            f"Deine Bewerbung wurde mit der Laufbahn {esc(test_result['career_label'])} gespeichert. "
            f"Online-Test: {test_result['score']}/{test_result['total']} richtig ({test_result['percent']} %), "
            f"Status: {result_status}. Deine Praxisaufgaben wurden ebenfalls gespeichert und werden vom Team manuell geprüft."
        )
        clear_draft = APPLY_DRAFT_CLEAR_SCRIPT.replace("__KEY__", json.dumps(apply_draft_key(applicant_id, career_path)))
        return HTMLResponse(simple_page("✅", "Bewerbung erfolgreich abgesendet!", result_text,
                                        '<a href="/apply" class="px-4 py-2 rounded-xl bg-slate-100 dark:bg-slate-800">Zurück</a>' + clear_draft))

    # ---------- Ab hier: Login + Recht je nach Aktion ----------
    required = ACTION_PERMS.get(action)
    if required is None:
        raise HTTPException(status_code=400, detail="Unbekannte Aktion.")
    ctx = auth(request, user_session, perm=None if required == "admin" else required, admin=(required == "admin"))
    guild, config = ctx.guild, ctx.config
    actor, actor_id = ctx.user.get("global_name"), ctx.user["id"]
    is_manager = ctx.perms["can_promote"] or ctx.perms["is_admin"]
    team_role_ids = ordered_team_role_ids(guild, config.get("team_role_ids", []))
    if action == "decide_app" and not (ctx.perms.get("can_manage_applications") or ctx.perms.get("can_promote") or ctx.perms.get("is_admin")):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
    member_url = f"/member/{user_id}" if redirect_to_member and user_id else "/dashboard"

    # Active safety lockouts also block alternate high-impact dashboard actions.
    if action in {"promote", "demote", "kick", "hire", "remove_warn"}:
        actor_member = ctx.member or guild.get_member(int(actor_id)) if str(actor_id).isdigit() else ctx.member
        guard_target = guild.get_member(user_id) if user_id else None
        allowed_safety_action, safety_message = await enforce_team_safety_action_lockout(
            guild, actor_member, guard_target, action, config, source="Dashboard",
        )
        if not allowed_safety_action:
            return back(member_url, safety_message, False)

    # ---------- Manuelle Einstellung über Discord-ID oder Namen ----------
    if action == "hire":
        query = (target_user_id or "").strip()
        action_reason = (action_reason or "").strip()[:500]
        if not query:
            return back("/team", "Bitte gib eine Discord-Nutzer-ID oder einen Discord-Namen ein.", False)
        if not action_reason:
            return back("/team", "Bitte gib einen Pflichtgrund für die Einstellung an.", False)
        if not hire_role_id:
            return back("/team", "Bitte wähle eine Teamrolle aus.", False)

        role = guild.get_role(hire_role_id)
        configured_team_role_ids = set(team_role_ids)
        if role is None or role.id not in configured_team_role_ids or role.managed or role.is_default():
            return back("/team", "Die ausgewählte Rolle ist keine gültige, konfigurierte Teamrolle.", False)
        if not guild.me:
            return back("/team", "Der Bot ist auf diesem Discord-Server noch nicht vollständig verfügbar.", False)
        if not guild.me.guild_permissions.manage_roles:
            return back("/team", "Dem Bot fehlt die Berechtigung 'Rollen verwalten'.", False)
        if role.position >= guild.me.top_role.position:
            return back("/team", "Der Bot muss in der Rollenliste über der ausgewählten Teamrolle stehen.", False)

        lookup = query
        if lookup.startswith("<@") and lookup.endswith(">"):
            lookup = lookup[2:-1].lstrip("!").strip()
        target = None
        if lookup.isdigit():
            target_id = int(lookup)
            target = guild.get_member(target_id)
            if target is None:
                try:
                    target = await guild.fetch_member(target_id)
                except discord.NotFound:
                    return back("/team", "Diese Discord-ID gehört zu keinem Mitglied dieses Servers. Bitte prüfe die ID und lade die Person zuerst auf den Server ein.", False)
                except discord.Forbidden:
                    return back("/team", "Discord verweigert das Abrufen dieses Mitglieds. Bitte prüfe die Bot-Berechtigungen.", False)
                except discord.HTTPException:
                    return back("/team", "Das Discord-Mitglied konnte gerade nicht geladen werden. Bitte versuche es erneut.", False)
        else:
            name_query = lookup.lstrip("@").strip().casefold()
            if not name_query:
                return back("/team", "Bitte gib einen gültigen Discord-Namen ein.", False)
            try:
                if not getattr(guild, "chunked", True):
                    await guild.chunk(cache=True)
            except (discord.Forbidden, discord.HTTPException):
                pass
            pool = [m for m in list(getattr(guild, "members", []) or []) if not m.bot]
            lookup_fields = (
                lambda m: str(getattr(m, "name", "") or "").casefold(),
                lambda m: str(getattr(m, "global_name", "") or "").casefold(),
                lambda m: str(getattr(m, "display_name", "") or "").casefold(),
            )
            for field in lookup_fields:
                exact = [m for m in pool if field(m) == name_query]
                if exact:
                    target = exact[0] if len(exact) == 1 else None
                    if len(exact) > 1:
                        examples = ", ".join(f"{m.display_name} (ID {m.id})" for m in exact[:5])
                        return back("/team", f"Der Name ist nicht eindeutig. Bitte verwende die Discord-ID. Treffer: {examples}", False)
                    break
            if target is None:
                partial = [
                    m for m in pool
                    if any(name_query in field(m) for field in lookup_fields)
                ]
                unique_partial = {m.id: m for m in partial}
                if len(unique_partial) == 1:
                    target = next(iter(unique_partial.values()))
                elif len(unique_partial) > 1:
                    examples = ", ".join(f"{m.display_name} (ID {m.id})" for m in list(unique_partial.values())[:5])
                    return back("/team", f"Mehrere Discord-Mitglieder passen zu dieser Eingabe. Bitte verwende die ID. Treffer: {examples}", False)

        if target is None:
            return back("/team", "Kein passendes Discord-Mitglied gefunden. Nutze am besten die Nutzer-ID oder prüfe, ob die Person bereits auf dem Server ist.", False)
        if target.bot or target.id == guild.owner_id or str(target.id) == str(actor_id):
            return back("/team", "Bots, der Serverinhaber und du selbst können nicht über diese Funktion eingestellt werden.", False)
        if target.top_role.position >= guild.me.top_role.position:
            return back("/team", "Der Bot kann dieses Mitglied wegen der Discord-Rollenhierarchie nicht verwalten. Die Bot-Rolle muss über der höchsten Rolle der Person stehen.", False)
        if any(r.id in configured_team_role_ids for r in target.roles):
            return back("/team", f"{target.display_name} besitzt bereits eine Teamrolle. Verwende stattdessen die Rollenverwaltung.", False)

        selected_rank = team_role_ids.index(role.id)
        actor_rank = team_rank(ctx.member, team_role_ids) if ctx.member else -1
        if not ctx.perms.get("is_admin") and actor_rank <= selected_rank:
            return back("/team", "Du darfst niemanden auf deinen eigenen Rang oder einen höheren Rang einstellen.", False)

        try:
            await target.add_roles(role, reason=f"Manuelle Teameinstellung durch {actor}: {action_reason}")
        except discord.Forbidden:
            return back("/team", "Discord hat die Rollenänderung verweigert. Prüfe die Rollenrechte und die Position der Bot-Rolle.", False)
        except discord.HTTPException:
            return back("/team", "Die Teamrolle konnte gerade nicht vergeben werden. Bitte versuche es erneut.", False)

        team_db = load_json(DATA_FILE, {})
        team_entry = user_entry(team_db, str(target.id))
        team_entry["team_since"] = now_de().isoformat(timespec="seconds")
        team_entry["manual_hire_by"] = str(actor)
        team_entry["manual_hire_reason"] = action_reason
        save_json(DATA_FILE, team_db)

        await send_dm_notification(
            target,
            f"🎉 Du wurdest von **{guild.name}** in das Team aufgenommen. Deine neue Teamrolle: **{role.name}**. Bei Fragen wende dich bitte an die Führungsebene."
        )
        await send_team_update_embed(
            guild,
            "🎉 Team-Update: Manuelle Einstellung",
            f"{target.mention} wurde manuell in das Team aufgenommen.",
            _pulse_color("success"),
            target=target.mention,
            action="Manuelle Einstellung",
            actor=actor,
            fields=[
                ("Teamrolle", role.mention, True),
                ("Discord-ID", str(target.id), True),
                ("Grund", action_reason, False),
                ("Zeitpunkt", now_de().strftime("%d.%m.%Y %H:%M"), True),
            ],
            thumbnail=target.display_avatar.url,
        )
        log_audit(actor, actor_id, "Manuelle Einstellung", f"{target.display_name} ({target.id}) -> {role.name} · Grund: {action_reason}")
        return back("/team", f"{target.display_name} wurde mit der Rolle {role.name} erfolgreich eingestellt.")

    # ---------- Befördern / Degradieren / Kicken ----------
    if action in ("promote", "demote", "kick"):
        member = guild.get_member(user_id) if user_id else None
        if not member:
            return back("/team", "Mitglied nicht gefunden.", False)
        if str(member.id) == actor_id:
            return back(member_url, "Du kannst diese Aktion nicht bei dir selbst ausführen.", False)
        if member.id == guild.owner_id or member.bot:
            return back(member_url, "Dieses Mitglied kann nicht bearbeitet werden.", False)
        actor_idx = team_rank(ctx.member, team_role_ids) if ctx.member else -1
        target_idx = team_rank(member, team_role_ids)
        if not ctx.perms["is_admin"] and actor_idx <= target_idx:
            return back(member_url, "Du kannst nur Mitglieder mit niedrigerem Rang bearbeiten.", False)

        action_reason = (action_reason or "").strip()[:500]
        if action in ("promote", "demote", "kick") and not action_reason:
            return back(member_url, "Bitte einen Grund für die Maßnahme angeben.", False)
        if action == "promote" and probation_days not in VALID_PROBATION_DAYS:
            return back(member_url, "Für jede Beförderung muss eine Probezeit von 3 oder 7 Tagen ausgewählt werden.", False)

        try:
            if action == "kick":
                if not guild.me:
                    return back(member_url, "Der Bot ist auf diesem Server noch nicht verfügbar.", False)
                if not guild.me.guild_permissions.manage_roles:
                    return back(member_url, "Der Bot hat keine Berechtigung 'Rollen verwalten'.", False)

                team_roles = [r for r in member.roles if r.id in team_role_ids]
                if not team_roles:
                    return back(member_url, "Dieses Mitglied hat keine Teamrolle mehr.", False)

                # Rollen vor jeder Änderung vollständig prüfen, damit ein Kick nicht
                # mitten im Rollen-Reset endet und zusätzliche Rollen zurückbleiben.
                resolved_keep_role_ids = tuple(
                    resolve_backup_role_id(role_id, config) for role_id in TEAM_KICK_KEEP_ROLE_IDS
                )
                keep_roles = [guild.get_role(role_id) for role_id in resolved_keep_role_ids]
                if any(role is None for role in keep_roles):
                    return back(member_url, "Team-Kick abgebrochen: Die Bürgerrolle oder die zweite Pflichtrolle wurde auf dem Server nicht gefunden.", False)
                if any(role.managed or role.position >= guild.me.top_role.position for role in keep_roles):
                    return back(member_url, "Team-Kick abgebrochen: Der Bot kann die Bürgerrolle oder die zweite Pflichtrolle wegen der Rollenverwaltung/-hierarchie nicht setzen.", False)

                keep_role_ids = set(resolved_keep_role_ids)
                roles_to_remove = [
                    role for role in member.roles
                    if role.id != guild.id and role.id not in keep_role_ids
                ]
                blocked_roles = [
                    role for role in roles_to_remove
                    if role.managed or role.position >= guild.me.top_role.position
                ]
                if blocked_roles:
                    blocked_names = ", ".join(role.name for role in blocked_roles)
                    return back(
                        member_url,
                        f"Team-Kick abgebrochen: Der Bot kann folgende Rollen nicht entfernen: {blocked_names}. Bitte Rollen-Hierarchie bzw. verwaltete Rollen prüfen.",
                        False,
                    )

                removed_role_names = [role.name for role in roles_to_remove]
                # Ein einzelner Member-Edit setzt die Rollen gezielt auf die beiden
                # gewünschten Rollen zurück. Das ist kein Discord-Server-Kick.
                await member.edit(
                    roles=keep_roles,
                    reason=f"Team-Kick durch {actor}: {action_reason}",
                )
                await send_dm_notification(
                    member,
                    f"❌ Du wurdest aus dem Team von **{guild.name}** entfernt. "
                    f"Du bleibst auf dem Discord-Server und erhältst nur noch die Rollen "
                    f"{keep_roles[0].mention} und {keep_roles[1].mention}. Grund: {action_reason}",
                )
                await send_team_update_embed(
                    guild,
                    "🚪 Team-Update: Team-Kick",
                    f"{member.mention} wurde aus dem Team entfernt. Die übrigen Rollen wurden entfernt; "
                    f"die Person bleibt auf dem Discord-Server.",
                    _pulse_color("danger"),
                    target=member.mention,
                    action="Team-Kick",
                    actor=actor,
                    fields=[
                        ("Entfernte Rollen", ", ".join(removed_role_names) if removed_role_names else "Keine zusätzlichen Rollen", False),
                        ("Verbleibende Rollen", ", ".join(role.mention for role in keep_roles), False),
                        ("Grund", action_reason, False),
                    ],
                    thumbnail=member.display_avatar.url,
                )
                log_audit(
                    actor,
                    actor_id,
                    "Team-Kick",
                    f"{member.display_name}: Rollen zurückgesetzt auf {', '.join(role.name for role in keep_roles)} · Grund: {action_reason}",
                )
                return back(
                    "/team",
                    f"{member.display_name} wurde aus dem Team entfernt. Es bleiben nur Bürgerrolle und die festgelegte Zusatzrolle; der Discord-Server-Kick wurde nicht ausgeführt.",
                )

            if action == "promote":
                new_idx = target_idx + 1
                if new_idx >= len(team_role_ids):
                    return back(member_url, "Höchster Rang bereits erreicht.", False)
                if not ctx.perms["is_admin"] and new_idx >= actor_idx:
                    return back(member_url, "Du kannst nicht auf deinen eigenen Rang oder höher befördern.", False)
                new_role = guild.get_role(team_role_ids[new_idx])
                if not new_role:
                    return back(member_url, "Die Zielrolle existiert nicht mehr (Team-Rollen in den Einstellungen prüfen).", False)
                old = [r for r in member.roles if r.id in team_role_ids and r.id != new_role.id]
                previous_role = max(old, key=lambda role: role.position) if old else None
                if previous_role is None:
                    return back(member_url, "Für eine Beförderung wurde keine vorherige Teamrolle gefunden. Bitte Teamrollen prüfen.", False)
                await member.add_roles(new_role, reason=f"Beförderung durch {actor}: {action_reason}")
                if old:
                    await member.remove_roles(*old, reason=f"Beförderung durch {actor}: {action_reason}")

                await finish_promotion(  # BOT-05: same follow-up as /promote
                    guild, member=member, previous_role=previous_role, new_role=new_role, probation_days=probation_days,
                    actor_name=actor, actor_id=actor_id, reason=action_reason, source="Dashboard",
                )
                return back(member_url, f"{member.display_name} wurde zum {new_role.name} befördert; Probezeit: {probation_days} Tage.")

            # demote
            if target_idx < 0:
                return back(member_url, "Dieses Mitglied hat keine Team-Rolle.", False)
            old = [r for r in member.roles if r.id in team_role_ids]
            new_idx = target_idx - 1
            if new_idx >= 0:
                new_role = guild.get_role(team_role_ids[new_idx])
                if not new_role:
                    return back(member_url, "Die Zielrolle existiert nicht mehr (Team-Rollen in den Einstellungen prüfen).", False)
                await member.add_roles(new_role, reason=f"Degradierung durch {actor}: {action_reason}")
                await member.remove_roles(*[r for r in old if r.id != new_role.id], reason=f"Degradierung durch {actor}: {action_reason}")
                await finish_demotion(  # BOT-05: same follow-up as /demote
                    guild, member=member, previous_role=old[0] if old else None, new_role=new_role,
                    actor_name=actor, actor_id=actor_id, reason=action_reason, source="Dashboard",
                )
                return back(member_url, f"{member.display_name} wurde zum {new_role.name} degradiert.")
            await member.remove_roles(*old, reason=f"Degradierung durch {actor}")
            await finish_demotion(guild, member=member, previous_role=old[0] if old else None, new_role=None,
                                  actor_name=actor, actor_id=actor_id, reason=action_reason, source="Dashboard")
            return back(member_url, f"{member.display_name} wurde aus dem Team-Rang entfernt.")
        except discord.Forbidden:
            return back(member_url, "Dem Bot fehlen Rechte – seine Rolle muss über den Team-Rollen stehen.", False)
        except Exception as e:
            print(f"Rang-Aktion fehlgeschlagen: {e}")
            return back(member_url, "Aktion fehlgeschlagen (Details in der Bot-Konsole).", False)

    # ---------- Verwarnungen ----------
    if action == "warn_with_proof":
        if not user_id or not (warn_reason or "").strip():
            return back(member_url, "Bitte einen Grund angeben.", False)
        result = await issue_team_warning(  # BOT-05: same logic as /warn
            guild, actor_member=ctx.member or guild.get_member(int(actor_id)), actor_name=actor, actor_id=actor_id,
            member=guild.get_member(user_id), reason=warn_reason, duration_days=warn_duration_days, proof=warn_proof,
            config=config, actor_is_admin=bool(ctx.perms.get("is_admin")), source="Dashboard",
        )
        return back(member_url, result["message"], result["ok"])

    if action == "remove_warn":
        if not user_id or not warn_id:
            return back(member_url, "Ungültige Anfrage.", False)
        result = await revoke_team_warning(  # BOT-05: same logic as /warn-remove
            guild, member=guild.get_member(user_id), warn_id=warn_id, revoke_reason=warn_revoke_reason,
            actor_name=actor, actor_id=actor_id, config=config,
        )
        return back(member_url, result["message"], result["ok"])


    if action == "add_note":
        note = (note_text or "").strip()[:500]
        if not user_id or not note:
            return back(member_url, "Notiz ist leer.", False)
        team_db = load_json(DATA_FILE, {})
        user_entry(team_db, str(user_id))["notes"].append(f"[{now_de().strftime('%d.%m.%Y')}] {note} (von {actor})")
        save_json(DATA_FILE, team_db)
        log_audit(actor, actor_id, "Notiz Erstellt", f"User-ID {user_id}: {note}")
        return back(member_url, "Notiz gespeichert.")

    # ---------- Abmeldungen ----------
    if action == "submit_loa":
        target = user_id or int(actor_id)
        if str(target) != actor_id and not is_manager:
            return back("/loa", "Du kannst nur dich selbst abmelden.", False)
        try:
            d_start = datetime.strptime(loa_start or "", "%Y-%m-%d").date()
            d_end = datetime.strptime(loa_end or "", "%Y-%m-%d").date()
        except ValueError:
            return back("/loa", "Ungültiges Datum.", False)
        if d_end < d_start:
            return back("/loa", "Das Enddatum liegt vor dem Startdatum.", False)
        if d_end < now_de().date():
            return back("/loa", "Das Enddatum liegt in der Vergangenheit.", False)
        m = guild.get_member(target)
        display_n = m.display_name if m else f"User-{target}"
        conn = sqlite3.connect(DB_ABMELDUNGEN)
        # Named columns: migrated databases have 'von' appended at the end, so a
        # positional insert stored the end date as original_nick (nickname became a date).
        # original_nick is the real Discord nickname, like the slash command stores it.
        conn.execute(
            "INSERT OR REPLACE INTO abmeldungen(user_id,user_name,grund,von,bis,original_nick,guild_id) VALUES (?,?,?,?,?,?,?)",
            (target, display_n, (loa_reason or "Kein Grund").strip()[:300], loa_start, loa_end,
             (m.nick if m else None), GUILD_ID),
        )
        conn.commit()
        conn.close()
        log_audit(actor, actor_id, "LOA Eingetragen", f"{display_n} bis {loa_end}")
        return back("/loa", f"Abmeldung für {display_n} gespeichert.")

    if action == "cancel_loa":
        try:
            target = int(target_user_id)
        except (TypeError, ValueError):
            return back("/loa", "Ungültige Anfrage.", False)
        if str(target) != actor_id and not is_manager:
            return back("/loa", "Du kannst nur deine eigene Abmeldung beenden.", False)
        conn = sqlite3.connect(DB_ABMELDUNGEN)
        conn.execute("DELETE FROM abmeldungen WHERE user_id = ?", (target,))
        conn.commit()
        conn.close()
        log_audit(actor, actor_id, "LOA Storniert", f"User-ID {target}")
        return back("/loa", "Abmeldung beendet.")

    # ---------- Bewerbungen ----------
    if action == "vote_app":
        apps = load_json(APPS_FILE, {})
        item = apps.get(app_id or "")
        if not item or item.get("status") != "pending" or vote not in ("up", "down"):
            return back("/applications", "Bewerbung nicht mehr offen.", False)
        for key in ("upvotes", "downvotes"):
            item[key] = [u for u in item.get(key, []) if u != actor_id]
        item["upvotes" if vote == "up" else "downvotes"].append(actor_id)
        save_json(APPS_FILE, apps)
        return back("/applications")

    if action == "decide_app":
        apps = load_json(APPS_FILE, {})
        item = apps.get(app_id or "")
        if not item:
            return back("/applications", "Bewerbung nicht gefunden.", False)

        current_status = str(item.get("status") or "")
        allowed_decisions = {
            "pending": {"accept", "reject"},
            "interview": {"hire_after_interview", "reject_after_interview"},
        }
        if decision not in allowed_decisions.get(current_status, set()):
            return back("/applications", "Diese Entscheidung passt nicht zum aktuellen Bewerbungsstatus.", False)

        applicant = None
        try:
            applicant = guild.get_member(int(item.get("user_id")))
        except Exception:
            applicant = None

        action_reason = (action_reason or "").strip()[:500]
        if not action_reason:
            return back("/applications", "Bitte einen Grund bzw. eine Gesprächsnotiz angeben.", False)

        if decision == "accept":
            # Erste Annahme = Einladung zum Bewerbungsgespräch. Das ist ausdrücklich
            # KEINE Einstellung; die Team-Einstiegsrolle wird erst nach dem Gespräch vergeben.
            if not applicant:
                return back("/applications", "Der Bewerber ist nicht mehr auf dem Discord-Server.", False)
            application_role = guild.get_role(APPLICATION_ROLE_ID)
            if application_role is None:
                return back(
                    "/applications",
                    f"Die Bewerberolle {APPLICATION_ROLE_ID} wurde auf Discord nicht gefunden. Bitte die Rolle prüfen.",
                    False,
                )
            if application_role.is_default() or application_role.managed:
                return back("/applications", "Die Bewerberolle ist nicht verwaltbar.", False)
            if guild.me and (
                not guild.me.guild_permissions.manage_roles
                or application_role.position >= guild.me.top_role.position
            ):
                return back(
                    "/applications",
                    "Der Bot benötigt „Rollen verwalten“ und muss in der Rollen-Hierarchie über der Bewerberolle stehen.",
                    False,
                )
            try:
                if application_role not in applicant.roles:
                    await applicant.add_roles(
                        application_role,
                        reason=f"Zum Bewerbungsgespräch eingeladen durch {actor}: {action_reason}",
                    )
            except discord.Forbidden:
                return back("/applications", "Discord hat die Bewerberolle verweigert. Bitte Bot-Rechte und Rollen-Hierarchie prüfen.", False)
            except discord.HTTPException as exc:
                print(f"Bewerberolle für {applicant.id} konnte nicht vergeben werden: {exc}")
                return back("/applications", "Die Bewerberolle konnte wegen eines Discord-Fehlers nicht vergeben werden.", False)

            invited_at = now_de().strftime("%d.%m.%Y %H:%M")
            item["status"] = "interview"
            item["application_role_id"] = application_role.id
            item["application_role"] = application_role.name
            item["interview_invited_by"] = actor
            item["interview_invited_at"] = invited_at
            item["interview_invitation_reason"] = action_reason
            save_json(APPS_FILE, apps)
            import pulse_app_comms  # BOT-10: same text by default, editable template
            await send_dm_notification(
                applicant,
                pulse_app_comms.render("interview", server=guild.name, rolle=application_role.name, id=app_id),
            )
            await send_team_update_embed(
                guild,
                "🎙️ Bewerbungsgespräch erforderlich",
                f"{applicant.mention} wurde zum verpflichtenden Bewerbungsgespräch eingeladen. Eine Einstellung ist noch nicht erfolgt.",
                _pulse_color("info"),
                target=applicant.mention,
                action="Zum Bewerbungsgespräch eingeladen",
                actor=actor,
                fields=[
                    ("Bewerberolle", application_role.mention, True),
                    ("Laufbahn", item.get("career_label", "Nicht angegeben"), True),
                    ("Bewerbung", app_id or "—", True),
                    ("Grund / Hinweis", action_reason, False),
                    ("Eingeladen am", invited_at, True),
                ],
                thumbnail=applicant.display_avatar.url,
            )
            log_audit(
                actor,
                actor_id,
                "Bewerbungsgespräch eingeladen",
                f"{applicant.display_name} ({applicant.id}) erhielt Bewerberolle {application_role.id}; noch keine Einstellung. Grund: {action_reason}",
            )
            return back("/applications", f"{applicant.display_name} wurde zum Bewerbungsgespräch eingeladen. Noch keine Teamrolle vergeben.")

        if decision == "hire_after_interview":
            if current_status != "interview" or not item.get("interview_invited_at"):
                return back("/applications", "Vor der Einstellung muss die Einladung zum Bewerbungsgespräch dokumentiert sein.", False)
            if not applicant:
                return back("/applications", "Der Bewerber ist nicht mehr auf dem Discord-Server.", False)
            configured_roles = [
                guild.get_role(rid) for rid in team_role_ids
                if str(rid) != str(APPLICATION_ROLE_ID)
            ]
            configured_roles = [r for r in configured_roles if r and not r.managed]
            role = guild.get_role(hire_role_id) if hire_role_id else (
                min(configured_roles, key=lambda r: r.position) if configured_roles else None
            )
            if role is None or role.id not in team_role_ids or role.id == APPLICATION_ROLE_ID:
                return back("/applications", "Bitte nach dem Bewerbungsgespräch eine gültige Team-Einstiegsrolle auswählen.", False)
            application_role = guild.get_role(APPLICATION_ROLE_ID)
            if guild.me and (
                not guild.me.guild_permissions.manage_roles
                or role.position >= guild.me.top_role.position
                or (application_role and application_role.position >= guild.me.top_role.position)
            ):
                return back(
                    "/applications",
                    "Der Bot benötigt „Rollen verwalten“ und muss über der Team-Einstiegsrolle und der Bewerberolle stehen.",
                    False,
                )
            # Ein Rollen-Edit ersetzt die Teamrangrolle und entfernt die Bewerberolle
            # in einem Discord-Aufruf, während alle anderen Rollen erhalten bleiben.
            remove_role_ids = set(team_role_ids) | {APPLICATION_ROLE_ID}
            roles_to_keep = [r for r in applicant.roles if r.id not in remove_role_ids]
            roles_to_keep.append(role)
            try:
                await applicant.edit(
                    roles=roles_to_keep,
                    reason=f"Einstellung nach Bewerbungsgespräch durch {actor}: {action_reason}",
                )
            except discord.Forbidden:
                return back("/applications", "Discord hat die Einstellung verweigert. Bitte Bot-Rechte und Rollen-Hierarchie prüfen.", False)
            except discord.HTTPException as exc:
                print(f"Einstellung nach Bewerbungsgespräch für {applicant.id} fehlgeschlagen: {exc}")
                return back("/applications", "Die Einstellung konnte wegen eines Discord-Fehlers nicht durchgeführt werden.", False)

            decided_at = now_de().strftime("%d.%m.%Y %H:%M")
            item["status"] = "hired"
            item["hired_role_id"] = role.id
            item["hired_role"] = role.name
            item["application_role_id"] = APPLICATION_ROLE_ID
            item["interview_completed_by"] = actor
            item["interview_completed_at"] = decided_at
            item["decision_reason"] = action_reason
            item["decided_by"] = actor
            item["decided_at"] = decided_at
            team_db = load_json(DATA_FILE, {})
            team_entry = user_entry(team_db, str(applicant.id))
            team_entry["team_since"] = now_de().isoformat(timespec="seconds")
            if item.get("career_path"):
                team_entry["career_path"] = item.get("career_path")
                team_entry["career_label"] = item.get("career_label")
                team_entry["application_test_percent"] = item.get("test_percent")
                team_entry["application_test_passed"] = item.get("test_passed")
            save_json(DATA_FILE, team_db)
            save_json(APPS_FILE, apps)
            import pulse_app_comms  # BOT-10
            await send_dm_notification(
                applicant,
                pulse_app_comms.render("hired", server=guild.name, rolle=role.name, id=app_id),
            )
            await send_team_update_embed(
                guild,
                "🎉 Team-Update: Einstellung nach Bewerbungsgespräch",
                f"{applicant.mention} wurde nach dem verpflichtenden Bewerbungsgespräch eingestellt.",
                _pulse_color("success"),
                target=applicant.mention,
                action="Einstellung nach Bewerbungsgespräch",
                actor=actor,
                fields=[
                    ("Einstiegsrolle", role.mention, True),
                    ("Bewerberolle", f"Entfernt (<@&{APPLICATION_ROLE_ID}>)", True),
                    ("Bewerbung", app_id or "—", True),
                    ("Gesprächsnotiz / Grund", action_reason, False),
                    ("Zeitpunkt", decided_at, True),
                ],
                thumbnail=applicant.display_avatar.url,
            )
            log_audit(
                actor, actor_id, "Einstellung nach Bewerbungsgespräch",
                f"{applicant.display_name} -> {role.name}; Bewerberolle {APPLICATION_ROLE_ID} entfernt. Grund: {action_reason}",
            )
            return back("/applications", f"{applicant.display_name} wurde nach dem Bewerbungsgespräch als {role.name} eingestellt.")

        if decision == "reject_after_interview" and applicant:
            application_role = guild.get_role(APPLICATION_ROLE_ID)
            if application_role and application_role in applicant.roles:
                try:
                    await applicant.remove_roles(application_role, reason=f"Bewerbung nach Gespräch abgelehnt durch {actor}: {action_reason}")
                except discord.Forbidden:
                    return back("/applications", "Die Ablehnung kann nicht abgeschlossen werden, weil Discord das Entfernen der Bewerberolle verweigert.", False)
                except discord.HTTPException as exc:
                    print(f"Bewerberolle nach Ablehnung für {applicant.id} konnte nicht entfernt werden: {exc}")
                    return back("/applications", "Die Bewerberolle konnte nicht entfernt werden. Bitte Discord-Rechte prüfen.", False)

        item["status"] = "rejected"
        item["decided_by"] = actor
        item["decided_at"] = now_de().strftime("%d.%m.%Y %H:%M")
        item["decision_reason"] = action_reason
        save_json(APPS_FILE, apps)
        if applicant:
            import pulse_app_comms  # BOT-10
            notice = pulse_app_comms.render("rejected_after_interview" if decision == "reject_after_interview" else "rejected",
                                            server=guild.name, id=app_id)
            await send_dm_notification(applicant, notice)
        await send_team_update_embed(
            guild,
            "📄 Team-Update: Bewerbung abgelehnt",
            f"Die Bewerbung von **{item.get('name','Unbekannt')}** wurde abgelehnt.",
            _pulse_color("danger"),
            target=item.get("name","Unbekannt"),
            action="Bewerbung nach Gespräch abgelehnt" if decision == "reject_after_interview" else "Bewerbung abgelehnt",
            actor=actor,
            fields=[("Bewerbung", app_id or "—", True), ("Grund", action_reason, False), ("Zeitpunkt", now_de().strftime("%d.%m.%Y %H:%M"), True)],
        )
        log_audit(actor, actor_id, "Bewerbung Entschieden", f"{item.get('name')}: abgelehnt · Grund: {action_reason}")
        return back("/applications", f"Bewerbung von {item.get('name')} abgelehnt.")
    # ---------- Meetings ----------
    if action == "meeting_rsvp":
        if rsvp_status not in ("accepted", "declined"):
            return back("/meetings", "Ungültiger Status.", False)
        meetings = load_meetings()
        meetings["rsvps"][actor_id] = {"name": actor, "status": rsvp_status}
        save_json(MEETINGS_FILE, meetings)
        return back("/meetings", "Zusage gespeichert." if rsvp_status == "accepted" else "Absage gespeichert.")

    if action == "add_meeting_topic":
        title, details = (topic_title or "").strip()[:100], (topic_details or "").strip()[:600]
        if not title or not details:
            return back("/meetings", "Titel und Beschreibung sind Pflicht.", False)
        meetings = load_meetings()
        meetings["topics"].append({"id": f"topic_{uuid.uuid4().hex[:6]}", "title": title, "details": details,
                                   "by": actor, "by_id": actor_id})
        save_json(MEETINGS_FILE, meetings)
        return back("/meetings", "Thema hinzugefügt.")

    if action == "delete_meeting_topic":
        meetings = load_meetings()
        topic = next((t for t in meetings["topics"] if t.get("id") == topic_id), None)
        if not topic:
            return back("/meetings", "Thema nicht gefunden.", False)
        if not (is_manager or topic.get("by_id") == actor_id):
            return back("/meetings", "Du darfst nur eigene Themen entfernen.", False)
        meetings["topics"] = [t for t in meetings["topics"] if t.get("id") != topic_id]
        save_json(MEETINGS_FILE, meetings)
        return back("/meetings", "Thema entfernt.")

    if action == "set_meeting_info":
        title, when = (meeting_title or "").strip()[:100], (meeting_datetime or "").strip()[:100]
        if not title or not when:
            return back("/meetings", "Titel und Zeitpunkt sind Pflicht.", False)
        meetings = load_meetings()
        # Vorherige Besprechung automatisch archivieren, sobald eine neue angesetzt wird.
        try:
            previous_title = meetings.get("title")
            previous_when = meetings.get("date_time")
            if previous_title and previous_when and previous_when != "Noch nicht angesetzt":
                pulse_db.save_meeting_history(previous_title, previous_when, meetings.get("description", ""), "", meetings.get("rsvps", {}))
        except Exception as e:
            print(f"Meeting-Historie konnte nicht gespeichert werden: {e}")
        meetings.update({"title": title, "date_time": when, "description": (meeting_desc or "").strip()[:500]})
        if reset_rsvps:
            meetings["rsvps"] = {}
        if clear_topics:
            meetings["topics"] = []
        save_json(MEETINGS_FILE, meetings)
        log_audit(actor, actor_id, "Meeting Aktualisiert", title)
        if announce:
            await send_team_update_embed(guild, "🎙️ Teambesprechung angesetzt",
                f"**{title}**\n📅 {when}\n{meetings['description']}\n\nZu-/Absagen im Dashboard unter *Teambesprechung*.", _pulse_color("info"))
        return back("/meetings", "Besprechung gespeichert.")

    if action == "reset_support_case_period":
        try:
            completed_period = pulse_db.finish_support_case_period(actor_id, actor)
            log_audit(
                actor, actor_id, "Supportfall-Zeitraum abgeschlossen",
                f"{completed_period['total']} abgeschlossene Pulse-Tickets · "
                f"{completed_period['start_at']} bis {completed_period['end_at']}",
            )
            return back(
                "/meetings",
                f"Teammeeting-Zeitraum archiviert: {completed_period['total']} abgeschlossene Supportfälle. "
                "Die neue Zählung läuft ab jetzt.",
            )
        except Exception as exc:
            print(f"Supportfall-Zeitraum konnte nicht zurückgesetzt werden: {type(exc).__name__}: {exc}")
            return back("/meetings", "Der Supportfall-Zeitraum konnte nicht gespeichert werden. Bitte erneut versuchen.", False)

    # ---------- Dashboard-only Rollenprofile (keine Discord-Rollenänderung) ----------
    if action == "save_dashboard_role":
        name = (dashboard_role_name or "").strip()[:40]
        if not name:
            return back("/settings", "Bitte gib einen Namen für das Dashboard-Profil an.", False)

        profiles = config.setdefault("dashboard_roles", {})
        profile_key = (dashboard_role_id or "").strip()
        if profile_key and profile_key not in profiles:
            return back("/settings", "Dieses Dashboard-Profil wurde nicht gefunden. Bitte lade die Einstellungen neu.", False)
        if any(
            str(existing_key) != profile_key and isinstance(existing, dict)
            and str(existing.get("name", "")).casefold() == name.casefold()
            for existing_key, existing in profiles.items()
        ):
            return back("/settings", "Es gibt bereits ein Dashboard-Profil mit diesem Namen.", False)
        if not profile_key:
            if len(profiles) >= 50:
                return back("/settings", "Es können maximal 50 Dashboard-Profile angelegt werden.", False)
            profile_key = f"dash_{uuid.uuid4().hex[:10]}"

        allowed_role_ids = {int(value) for value in config.get("team_role_ids", []) if str(value).isdigit()}
        selected_ids = {int(value) for value in dashboard_discord_role_ids}
        if selected_ids - allowed_role_ids:
            return back("/settings", "Dashboard-Profile dürfen nur vorhandene, konfigurierte Discord-Teamrollen verwenden.", False)
        valid_role_ids = []
        for selected_id in sorted(selected_ids):
            role = guild.get_role(selected_id)
            if role and not role.managed and not role.is_default():
                valid_role_ids.append(role.id)

        profile_permissions = {
            "can_view_dashboard": can_view_dashboard, "can_warn": can_warn,
            "can_promote": can_promote, "can_add_notes": can_add_notes,
            "can_manage_tickets": can_manage_tickets, "can_manage_applications": can_manage_applications,
            "can_manage_tasks": can_manage_tasks, "can_manage_training": can_manage_training,
            "can_manage_wiki": can_manage_wiki, "can_view_analytics": can_view_analytics,
        }
        profiles[profile_key] = {
            "name": name,
            "permissions": profile_permissions,
            "discord_role_ids": valid_role_ids,
        }
        save_json(CONFIG_FILE, config)
        enabled = ", ".join(label for key, label in DASHBOARD_PERMISSION_LABELS if profile_permissions.get(key)) or "keine Rechte"
        log_audit(actor, actor_id, "Dashboard-Profil gespeichert", f"{name} ({profile_key}) · {enabled}")
        return back("/settings", f"Dashboard-Profil „{name}“ gespeichert.")

    if action == "delete_dashboard_role":
        profile_key = (dashboard_role_id or "").strip()
        profiles = config.setdefault("dashboard_roles", {})
        if not profile_key or profile_key not in profiles:
            return back("/settings", "Dieses Dashboard-Profil wurde nicht gefunden.", False)
        profile_value = profiles[profile_key]
        profile_name = str(profile_value.get("name") or profile_key) if isinstance(profile_value, dict) else profile_key
        del profiles[profile_key]
        save_json(CONFIG_FILE, config)
        log_audit(actor, actor_id, "Dashboard-Profil gelöscht", f"{profile_name} ({profile_key}); Discord-Teamrollen-Zuordnungen entfernt")
        return back("/settings", f"Digitales Dashboard-Profil „{profile_name}“ wurde gelöscht.")

    # ---------- Einstellungen (nur Admins) ----------
    if action == "save_role_permissions":
        if role_id not in team_role_ids:
            return back("/settings", "Diese Rolle ist keine Team-Rolle.", False)
        config["permissions"][str(role_id)] = {
            "can_view_dashboard": can_view_dashboard, "can_warn": can_warn,
            "can_promote": can_promote, "can_add_notes": can_add_notes,
            "can_manage_tickets": can_manage_tickets, "can_manage_applications": can_manage_applications,
            "can_manage_tasks": can_manage_tasks, "can_manage_training": can_manage_training,
            "can_manage_wiki": can_manage_wiki, "can_view_analytics": can_view_analytics,
        }
        save_json(CONFIG_FILE, config)
        log_audit(actor, actor_id, "Rechte Gespeichert", f"Rolle {role_id}")
        return back("/settings", "Rechte gespeichert.")

    if action == "set_weekly_goal":
        if weekly_goal is None or not (0 < weekly_goal <= 100):
            return back("/settings", "Bitte einen Wert zwischen 0,5 und 100 angeben.", False)
        config["weekly_goal_hours"] = round(weekly_goal, 1)
        save_json(CONFIG_FILE, config)
        log_audit(actor, actor_id, "Wochenziel Geändert", f"{weekly_goal:g}h")
        return back("/settings", "Wochenziel gespeichert.")

    if action == "save_team_roles":
        valid = [guild.get_role(r) for r in team_roles]
        valid = sorted((r for r in valid if r), key=lambda r: r.position)
        config["team_role_ids"] = [r.id for r in valid]
        save_json(CONFIG_FILE, config)
        log_audit(actor, actor_id, "Team-Rollen Geändert", ", ".join(r.name for r in valid) or "keine")
        return back("/settings", f"{len(valid)} Team-Rollen gespeichert.")

    return back("/dashboard")

# =============================================================
# PULSE v4 – ERWEITERTE TEAMOS MODULE
# =============================================================
try:
    from pulse_features import register as register_pulse_features
    register_pulse_features(app)
except Exception as _feature_error:
    print(f"❌ Pulse-v4 Module konnten nicht registriert werden: {_feature_error}")


try:
    from pulse_pro import register as register_pulse_pro
    register_pulse_pro(app)
except Exception as _pro_error:
    print(f"❌ Pulse Pro Module konnten nicht registriert werden: {_pro_error}")


try:
    from pulse_ultimate import register as register_pulse_ultimate
    register_pulse_ultimate(app)
except Exception as _ultimate_error:
    print(f"❌ Pulse Ultimate Module konnten nicht registriert werden: {_ultimate_error}")

try:
    from pulse_next import register as register_pulse_next
    register_pulse_next(app)
except Exception as _next_error:
    print(f"❌ Pulse TeamOS 2.0 Module konnten nicht registriert werden: {_next_error}")

try:
    from pulse_intelligence import register as register_pulse_intelligence
    register_pulse_intelligence(app)
except Exception as _intelligence_error:
    print(f"❌ Pulse Intelligence Module konnten nicht registriert werden: {_intelligence_error}")

try:
    from pulse_home import register as register_pulse_home
    register_pulse_home(app)
except Exception as _home_error:
    print(f"❌ Mein Pulse konnte nicht registriert werden: {_home_error}")

try:
    from pulse_cockpit import register as register_pulse_cockpit
    register_pulse_cockpit(app)
except Exception as _cockpit_error:
    print(f"❌ Leitungs-Cockpit konnte nicht registriert werden: {_cockpit_error}")

try:
    from pulse_social_dashboard import register as register_pulse_social
    register_pulse_social(app)
except Exception as _social_error:
    print(f"❌ Social-Media-Seite konnte nicht registriert werden: {_social_error}")

try:
    from pulse_admin import register as register_pulse_admin
    register_pulse_admin(app)
except Exception as _admin_error:
    print(f"❌ Verwaltungsbereich konnte nicht registriert werden: {_admin_error}")
