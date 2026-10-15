# =============================================================
# BOCHUM RP – TEAM DASHBOARD  (v3.0)
# =============================================================
import asyncio
import base64
import csv
import hashlib
import hmac
import html
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from collections import OrderedDict
from types import SimpleNamespace
from typing import List
from pathlib import Path
from urllib.parse import quote, urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, Cookie, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
import httpx
import discord
import pulse_db as pulse_db

from pulse_shift_utils import AUTO_SHIFT_END_HOURS, AUTO_SHIFT_END_SECONDS, shift_age_seconds, shift_event_id
from career_applications import APPLICATION_CAREERS, grade_career_test, render_career_test
from pulse_melonly import can_manage_melonly_type, rank_roblox_candidates, roblox_similarity_score

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

def oauth_redirect_uri(request: Request, application: bool = False) -> str:
    """Nutzt immer die konfigurierte oder öffentlich aufgerufene Callback-URL."""
    configured = APPLICATION_REDIRECT_URI if application else REDIRECT_URI
    if configured:
        return configured.rstrip("/")
    return f"{request_origin(request)}/{ 'apply/callback' if application else 'callback' }"

def oauth_cookie_secure(request: Request) -> bool:
    return oauth_redirect_uri(request).startswith("https://") or oauth_redirect_uri(request, application=True).startswith("https://")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "1474514929351524616"))
TEAM_UPDATE_CHANNEL_NAME = os.getenv("TEAM_UPDATE_CHANNEL_NAME", "╚『⚡』𝐓𝐞𝐚𝐦-𝐔𝐩𝐝𝐚𝐭𝐞𝐬")
TEAM_UPDATE_CHANNEL_ID = 1531132354272170115  # zentraler Team-Updates-Kanal
# Nach einem manuellen Team-Kick bleiben ausschließlich diese beiden Rollen erhalten.
TEAM_KICK_KEEP_ROLE_IDS = (1531303733311307997, 1489912887949394082)

WARN_ROLE_IDS = {
    1: 1489221948348043395,
    2: 1489222076370780232,
    3: 1531760107971416135,
    4: 1556344459422081045,
    5: 1556344484198088814,
}
WARN_UPRANK_LOCK_ROLE_ID = 1506055179362107494
WARN_IC_LOCK_ROLE_ID = 1506054747554189472
WARN_TEMP_ROLE_DAYS = 7
SYNC_WARN_ROLES = os.getenv("SYNC_WARN_ROLES", "1") == "1"      # Warn-Rollen automatisch vergeben
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "7"))              # Login-Dauer
# Vergessene Schichten werden nach genau 24 Stunden automatisch beendet und verwarnt.
# MAX_SHIFT_HOURS bleibt als kompatible Konstante für alte Integrationen bestehen.
MAX_SHIFT_HOURS = AUTO_SHIFT_END_HOURS
AUTO_BACKUP_HOURS = float(os.getenv("AUTO_BACKUP_HOURS", "24")) # 0 = aus
MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", "30"))
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

os.makedirs(BACKUP_DIR, exist_ok=True)

try:
    TZ = ZoneInfo(os.getenv("PANEL_TIMEZONE", "Europe/Berlin")) if ZoneInfo else None
except Exception:
    TZ = None

PULSE_VERSION = "7.6.2"
app = FastAPI(title="Pulse TeamOS", version=PULSE_VERSION)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Setzt sichere Standard-Header ohne das bestehende Inline-UI/CSS zu brechen."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path not in {"/sw.js", "/manifest.json"}:
        response.headers.setdefault("Cache-Control", "no-store")
    if oauth_redirect_uri(request).startswith("https://") or oauth_redirect_uri(request, application=True).startswith("https://"):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
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


init_db()

# =============================================================
# DATEI-HELFER (thread-safe + atomares Schreiben)
# =============================================================
_io_lock = threading.RLock()


def load_json(filepath, default):
    with _io_lock:
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return default
    return default


def save_json(filepath, data):
    with _io_lock:
        tmp = f"{filepath}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, filepath)  # verhindert kaputte Dateien bei Absturz


def load_config() -> dict:
    cfg = load_json(CONFIG_FILE, {})
    cfg.setdefault("team_role_ids", [])
    cfg.setdefault("weekly_goal_hours", 3.0)
    cfg.setdefault("permissions", {})
    return cfg


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


def _load_secret() -> bytes:
    env = os.getenv("SESSION_SECRET")
    if env:
        return env.encode()
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


SESSION_KEY = _load_secret()


def sign_payload(data: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(SESSION_KEY, raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def verify_payload(token: str):
    try:
        raw, sig = token.rsplit(".", 1)
        expected = hmac.new(SESSION_KEY, raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode())
    except Exception:
        return None


def get_current_user(user_session: str = Cookie(None)) -> dict:
    """Vorher konnte JEDER den Cookie selbst schreiben (= Login als beliebiger User). Jetzt signiert."""
    data = verify_payload(user_session) if user_session else None
    if not data or data.get("exp", 0) < time.time() or not data.get("id"):
        raise HTTPException(status_code=303, headers={"Location": "/"})
    return data


def compute_perms(guild, user_id, config):
    perms = {k: False for k in PERM_KEYS}
    perms["is_admin"] = False
    try:
        member = guild.get_member(int(user_id))
    except Exception:
        member = None
    if not member:
        return perms, None
    if member.id == guild.owner_id or member.guild_permissions.administrator:
        return {**{k: True for k in PERM_KEYS}, "is_admin": True}, member
    team_ids = config.get("team_role_ids", [])
    cfg = config.get("permissions", {})
    for role in member.roles:
        if role.id not in team_ids:
            continue
        rp = cfg.get(str(role.id))
        if rp is None:  # Rolle noch nie konfiguriert -> Standard: Dashboard + Analytics
            rp = {"can_view_dashboard": True, "can_view_analytics": True}
        for k in PERM_KEYS:
            if rp.get(k):
                perms[k] = True
    # Bestehende Manager-Konfigurationen bleiben kompatibel mit den neuen Modulen.
    if perms.get("can_promote"):
        perms["can_manage_applications"] = True
        perms["can_manage_tickets"] = True
    return perms, member


def auth(request: Request, user_session, perm="can_view_dashboard", admin=False):
    """Zentrale Prüfung: Login gültig? Noch auf dem Server? Hat die Rolle das Recht?"""
    user = get_current_user(user_session)
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    if not guild:
        raise HTTPException(status_code=503, detail="Der Bot ist noch nicht bereit. Bitte in ein paar Sekunden neu laden.")
    config = load_config()
    perms, member = compute_perms(guild, user["id"], config)
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
        url = f"{url}{'&' if '?' in url else '?'}msg={quote(msg)}&t={'ok' if ok else 'err'}"
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


def load_shifts() -> dict:
    """Lädt Schichten und beendet vergessene Schichten nach 24 Stunden."""
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
    if changed:
        save_json(SHIFTS_FILE, db)
    return db


async def process_overdue_shifts(guild, config: dict | None = None) -> dict:
    """Schließt Schichten nach 24h, vergibt genau eine Team-Warn und protokolliert sie."""
    report = {"checked": 0, "closed": 0, "warned": 0, "failed": 0, "max_warns": 0}
    if not guild:
        report["failed"] = 1
        return report

    cfg = config or load_config()
    shifts_db = load_shifts()
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
    team_db = load_json(DATA_FILE, {})
    if not isinstance(team_db, dict):
        team_db = {}
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
            save_json(SHIFTS_FILE, shifts_db)
            continue

        member = guild.get_member(int(uid))
        if member is None:
            try:
                member = await guild.fetch_member(int(uid))
            except discord.NotFound:
                shift_event["auto_warn_status"] = "member_left_server"
                save_json(SHIFTS_FILE, shifts_db)
                continue
            except Exception as exc:
                shift_event["auto_warn_error"] = str(exc)[:400]
                report["failed"] += 1
                continue
        if member.bot:
            shift_event["auto_warn_status"] = "not_team_member"
            save_json(SHIFTS_FILE, shifts_db)
            continue

        entry = user_entry(team_db, uid)
        warnings = entry.setdefault("warns_list", [])
        existing_warn = next(
            (item for item in warnings if isinstance(item, dict) and item.get("source_shift_id") == event_id),
            None,
        )
        if existing_warn:
            count = len(active_warns(entry))
            await sync_warn_roles(guild, member, count, cfg)
            if count >= 2:
                await apply_warn_consequences(guild, member, existing_warn, count, cfg)
            shift_event["auto_warn_status"] = "issued"
            shift_event["auto_warn_issued_at"] = shift_event.get("auto_warn_issued_at") or now.isoformat(timespec="seconds")
            shift_event.pop("auto_warn_error", None)
            save_json(SHIFTS_FILE, shifts_db)
            continue

        if team_role_ids and not any(role.id in team_role_ids for role in member.roles):
            shift_event["auto_warn_status"] = "not_team_member"
            save_json(SHIFTS_FILE, shifts_db)
            continue

        count_before = len(active_warns(entry))
        if count_before >= 5:
            shift_event["auto_warn_status"] = "max_warns"
            shift_event["auto_warn_processed_at"] = now.isoformat(timespec="seconds")
            save_json(SHIFTS_FILE, shifts_db)
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
            "source": "auto_shift_timeout",
            "source_shift_id": event_id,
            "shift_started_at_iso": shift_event.get("started_at_iso"),
        }
        warnings.append(warn)
        count = len(active_warns(entry))
        save_json(DATA_FILE, team_db)

        try:
            role_report = await sync_warn_roles(guild, member, count, cfg)
            consequence_report = (
                await apply_warn_consequences(guild, member, warn, count, cfg)
                if count >= 2 else {"actions": [], "ok": True}
            )
            try:
                await send_team_update_embed(
                    guild,
                    "⚠️ Team-Warn: Schicht nicht beendet",
                    f"{member.mention} hat die Schicht nach 24 Stunden nicht beendet.",
                    discord.Color.orange(),
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
            save_json(SHIFTS_FILE, shifts_db)
        except Exception as exc:
            shift_event["auto_warn_error"] = str(exc)[:400]
            report["failed"] += 1
            save_json(SHIFTS_FILE, shifts_db)
            print(f"Automatische Schicht-Warnung fehlgeschlagen für {uid}: {exc}")

    return report


def calculate_weekly_seconds(mod_id, history, active_shifts=None) -> int:
    total = 0
    ws = week_start()
    for entry in history:
        if str(entry.get("mod_id")) == str(mod_id):
            try:
                if datetime.strptime(entry.get("date"), "%Y-%m-%d") >= ws:
                    total += int(entry.get("duration_seconds", 0))
            except Exception:
                pass
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
            await user_or_member.send(content=message, embed=embed)
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
    color = color or discord.Color.blurple()
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
        return await channel.send(embed=embed)
    except Exception as e:
        print(f"Fehler beim Senden des Team-Updates in Discord: {e}")
        return None


def get_warn_role_ids(config: dict | None = None) -> dict:
    """Gibt die festgelegten fünf Pulse-Team-Warnrollen zurück.
    
    Legacy-Konfigurationen und alte ENV-Werte werden absichtlich ignoriert,
    damit das Warnsystem im Live-Betrieb immer dieselben fünf Rollen verwendet.
    """
    return dict(WARN_ROLE_IDS)

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
        normalized.append(item)

    if normalized != entry.get("warns_list"):
        entry["warns_list"] = normalized
        changed = True
    return changed


def active_warns(entry: dict) -> list[dict]:
    """Nur aktive Warns zählen; zurückgezogene Warns bleiben als Historie erhalten."""
    normalize_warns(entry)
    return [w for w in entry.get("warns_list", []) if isinstance(w, dict) and w.get("active", True) and not w.get("revoked_at")]


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
    """Vergibt eine einwöchige Warn-Folgerolle und speichert ihr Ablaufdatum dauerhaft."""
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
        return {"ok": True, "message": "Alle Team-Rollen wurden automatisch entfernt.", "changed": True}
    except discord.Forbidden:
        consequences["team_kick_applied"] = False
        return {"ok": False, "message": "Discord verweigert den automatischen Team-Kick.", "changed": False}
    except Exception as exc:
        consequences["team_kick_applied"] = False
        return {"ok": False, "message": f"Automatischer Team-Kick fehlgeschlagen: {exc}", "changed": False}


async def apply_warn_consequences(guild, member, warn: dict, count: int, config: dict | None = None):
    """Wendet die automatische Eskalation der aktuellen Warnstufe an."""
    result = {"level": int(count or 0), "actions": [], "ok": True}
    team_role_ids = [int(x) for x in (config or load_config()).get("team_role_ids", [])]

    if count == 2:
        role = await _add_temporary_warn_role(guild, member, WARN_UPRANK_LOCK_ROLE_ID, warn, "Uprank-Sperre")
        result["actions"].append(role["message"] if role["ok"] else role["message"])
        result["ok"] = result["ok"] and role["ok"]

    elif count == 3:
        role = await _add_temporary_warn_role(guild, member, WARN_IC_LOCK_ROLE_ID, warn, "IC-Rechte-Sperre")
        result["actions"].append(role["message"] if role["ok"] else role["message"])
        result["ok"] = result["ok"] and role["ok"]

    elif count == 4:
        downrank = await _apply_warn4_downrank(guild, member, warn, team_role_ids)
        result["actions"].append(downrank["message"])
        result["ok"] = result["ok"] and downrank["ok"]

    elif count == 5:
        team_kick = await _apply_warn5_team_kick(guild, member, warn, team_role_ids)
        result["actions"].append(team_kick["message"])
        result["ok"] = result["ok"] and team_kick["ok"]

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
                try:
                    expires = parse_dt(str(restriction.get("expires_at") or ""))
                except Exception:
                    keep.append(restriction)
                    continue

                warn_is_active = bool(warn.get("active", True)) and not warn.get("revoked_at")
                if warn_is_active and current < expires:
                    keep.append(restriction)
                    continue

                # Bei einer zurückgezogenen Warnung wird die Sperrrolle ebenfalls
                # automatisch entfernt; bei fehlenden Rechten wird im nächsten
                # Bereinigungszyklus erneut versucht.
                result["checked"] += 1
                role_id = int(restriction.get("role_id", 0) or 0)
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

HEAD_SCRIPT = """
<script src="https://cdn.tailwindcss.com"></script>
<script>
    tailwind.config = { darkMode: 'class' }
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
"""


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
        toast = (f'<div id="toast" class="mb-5 border {color} rounded-xl px-4 py-3 text-xs font-semibold flex items-center justify-between">'
                 f'<span>{icon} {esc(ctx.msg)}</span>'
                 f'<button onclick="this.parentElement.remove()" class="opacity-60 hover:opacity-100">✕</button></div>'
                 '<script>setTimeout(()=>{const t=document.getElementById("toast"); if(t) t.remove();}, 6000);'
                 'history.replaceState(null,"",location.pathname);</script>')
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
@app.get("/", response_class=HTMLResponse)
async def home(user_session: str = Cookie(None)):
    if user_session and verify_payload(user_session):
        return RedirectResponse(url="/ultimate", status_code=303)
    return f"""
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


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key="user_session")
    return response


@app.get("/callback")
async def callback(request: Request, code: str = None, state: str = None, error: str = None,
                   oauth_state: str = Cookie(None)):
    retry = '<a href="/" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Erneut versuchen</a>'
    if error or not code:
        return HTMLResponse(simple_page("❌", "Login abgebrochen", "Du hast die Anmeldung nicht bestätigt.", retry))
    if not state or not oauth_state or not hmac.compare_digest(state, oauth_state):
        return HTMLResponse(simple_page("❌", "Login fehlgeschlagen", "Ungültiger Sicherheits-Token. Bitte erneut anmelden.", retry), status_code=400)

    async with httpx.AsyncClient() as client:
        redirect_uri = oauth_redirect_uri(request)
        token_res = await client.post(
            "https://discord.com/api/v10/oauth2/token",
            data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "grant_type": "authorization_code",
                  "code": code, "redirect_uri": redirect_uri},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        access_token = token_res.json().get("access_token")
        if not access_token:
            return HTMLResponse(simple_page("❌", "Login fehlgeschlagen", "Discord hat den Login abgelehnt.", retry))
        user_data = (await client.get("https://discord.com/api/v10/users/@me",
                                      headers={"Authorization": f"Bearer {access_token}"})).json()

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
    response = RedirectResponse(url="/ultimate", status_code=303)
    response.set_cookie(
        "user_session",
        sign_payload(session),
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
                    if (exact && idInput && /^\d+$/.test(String(exact.id || ""))) {
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
        if (idInput) idInput.value = /^\d+$/.test(String(user.id || "")) ? String(user.id) : "";
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
    pending_apps_count = len([a for a in apps_db.values() if a.get("status") == "pending"])

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
    for log in reversed(logs_db[-50:]):
        ltype = log.get("type", "Log")
        badge = LOG_TYPE_STYLE.get(ltype, "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300")
        search = f"{log.get('target_user','')} {log.get('roblox_id','')} {log.get('moderator','')} {ltype} {log.get('reason','')}".lower()
        can_edit = can_manage_melonly_entry(ctx, log)
        edit_button = (
            f'<a href="/dashboard?edit_log={quote(str(log.get("id") or ""))}" class="text-indigo-500 hover:underline font-semibold">✏️ Bearbeiten</a>'
            if can_edit else ""
        )
        delete_form = f"""
                <form action="/log/delete" method="post" onsubmit="return confirm('Diesen Log-Eintrag wirklich löschen?');">
                    <input type="hidden" name="log_id" value="{esc(log.get('id'))}">
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
            <div class="flex flex-wrap gap-1.5">{chips}</div>
            <input type="text" id="logSearch" oninput="filterLogs()" placeholder="🔎 Name, ID, Moderator oder Grund..." class="w-full bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-xl px-3.5 py-2 text-xs text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500 shadow-sm">
            <div class="space-y-3 max-h-[calc(100vh-300px)] overflow-y-auto pr-1">
                {logs_html or "<div class='text-xs text-slate-400 italic bg-white dark:bg-[#141824] p-6 rounded-2xl border border-slate-200 dark:border-slate-800 text-center shadow-sm'>Noch keine Logs eingetragen.</div>"}
            </div>
            <p class="text-[10px] text-slate-400">Angezeigt werden die letzten 50 Einträge – der CSV-Export enthält alle.</p>
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
# MELOONLY ALIAS – öffnet das Moderationscenter im Dashboard
# =============================================================
@app.get("/melonly")
async def melonly_alias(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    if not (ctx.perms.get("can_warn") or ctx.perms.get("can_add_notes") or ctx.perms.get("is_admin")):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
    return RedirectResponse(url="/dashboard#playerlog", status_code=303)

# =============================================================
# ACTION: SCHICHT-SYSTEM (Start / Pause / Fortsetzen / Ende)
# =============================================================
@app.post("/shift/action")
async def handle_shift_action(request: Request, shift_action: str = Form(...), user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    mod_id = ctx.user["id"]
    shifts_db = load_shifts()
    team_db = load_json(DATA_FILE, {})
    active = shifts_db["active_shifts"]
    shift = active.get(mod_id)
    now = now_de()

    if shift_action == "start":
        if shift:
            return back("/dashboard", "Du hast bereits eine laufende Schicht.", False)
        if len(active_warns(user_entry(team_db, mod_id))) >= 5:
            return back("/dashboard", "Schicht-Start gesperrt: Du hast bereits 5 aktive Verwarnungen!", False)
        loa = get_loas().get(mod_id)
        if loa and loa["active"]:
            return back("/dashboard", f"Du bist bis {fmt_date(loa['bis'])} abgemeldet – beende erst deine Abmeldung.", False)
        active[mod_id] = {
            "status": "online",
            "started_at_iso": now.isoformat(),
            "segment_start_iso": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "accumulated_seconds": 0,
        }
        msg = "Schicht gestartet – viel Erfolg!"

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
    else:
        return back("/dashboard", "Diese Aktion ist gerade nicht möglich.", False)

    save_json(SHIFTS_FILE, shifts_db)
    return back("/dashboard", msg)


# =============================================================
# ACTION: LOGS ERSTELLEN & LÖSCHEN, CSV-EXPORT
# =============================================================
@app.post("/log/create")
async def create_log(request: Request, target_user: str = Form(...), roblox_id: str = Form("N/A"),
                     log_type: str = Form(...), reason: str = Form(...), user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    target_user = target_user.strip()[:50]
    reason = reason.strip()[:1000]
    roblox_id = roblox_id.strip()
    if log_type not in VALID_LOG_TYPES or not target_user or not reason:
        return back("/dashboard", "Ungültige Eingabe.", False)
    if log_type in ("Warn", "Kick", "Ban", "Ban BOLO") and not (ctx.perms.get("can_warn") or ctx.perms.get("is_admin")):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung für Strafmaßnahmen.")
    if log_type == "Notiz" and not (ctx.perms.get("can_add_notes") or ctx.perms.get("is_admin")):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung für Notizen.")
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
    return back("/dashboard", f"{log_type}-Log für {target_user} gespeichert.")


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
    return_to = "/melonly" if return_to == "/melonly" else "/dashboard"
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
    return_to = "/melonly" if return_to == "/melonly" else "/dashboard"
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
    """Enforce Melonly role permissions consistently in UI and POST endpoints."""
    perms = ctx.perms
    manager = bool(perms.get("can_promote") or perms.get("is_admin"))
    can_warn = bool(perms.get("can_warn"))
    can_add_notes = bool(perms.get("can_add_notes"))
    existing_type = str(entry.get("type") or "Log")
    if not can_manage_melonly_type(existing_type, can_warn=can_warn, can_add_notes=can_add_notes, manager=manager):
        return False
    if target_type is not None and not can_manage_melonly_type(target_type, can_warn=can_warn, can_add_notes=can_add_notes, manager=manager):
        return False
    return True


def csv_safe(value) -> str:
    """Schützt vor Excel-Formel-Injection (=, +, -, @)."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@app.get("/export/logs")
async def export_logs(request: Request, user_session: str = Cookie(None)):
    auth(request, user_session)
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
        with sqlite3.connect(ACTIVITY_DB) as conn:
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
PULSE_TICKET_DEFAULT_PANEL_CHANNEL_IDS = (
    1511390378752741386,
    1494338404224340179,
    1494397389635719269,
    1488972966774706186,
    1559242374658662602,
    1488699195157254436,
)
PULSE_TICKET_OPEN_LOG_CHANNEL_ID = 1543914075308818503
PULSE_TICKET_CLAIM_LOG_CHANNEL_ID = 1535025111722098778
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
        color=discord.Color.green() if kind in {"created", "reopened"} else (
            discord.Color.red() if kind == "closed" else (
                discord.Color.orange() if kind == "priority" else discord.Color.blurple()
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
        from cogs.tickets import TicketControlView, refresh_ticket_embed
        view = TicketControlView()
        claimed_by = ticket.get("claimed_by_name") or "Teammitglied"
        claimed = bool(ticket.get("claimed_by_id"))
        for item in view.children:
            if getattr(item, "custom_id", None) == "pulse_ticket_claim":
                item.disabled = claimed
                item.label = (f"Übernommen · {claimed_by}"[:80] if claimed else "Übernehmen")
        await refresh_ticket_embed(channel, ticket, view)
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
</style></head><body><main class="wrap">
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
    settings["ticket_panels"] = panels
    pulse_db.set_setting("ticket_system_config", settings)
    return back("/tickets/panels", f"Panel „{cleaned_name}“ wurde gespeichert. Klicke auf „Panel veröffentlichen / aktualisieren“, um es in Discord anzuzeigen.", True)


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
    opening_message: str = Form(""),
    group: str = Form("team"),
    category_id: str = Form(""),
    role_ids: list[str] = Form(default=[]),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, admin=True)
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
        "opening_message": cleaned_opening,
        "active": True,
    }
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
    return back("/tickets/panels", f"Kategorie „{cleaned_name}“ wurde gespeichert und steht im Panel-Builder zur Auswahl.", True)


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
            from cogs.tickets import TICKET_PARENT_NAME, TicketControlView, category_meta, category_roles, safe_channel_name
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
                f"ticket-{safe_channel_name(str(ticket.get('category') or 'support'))}-{safe_channel_name(str(ticket.get('user_name') or 'user'))}-r{short_id}"[:95],
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
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Ticket-ID", value=str(ticket_id), inline=True)
            embed.add_field(name="Ersteller", value=str(updated.get("user_name") or "Unbekannt"), inline=True)
            embed.add_field(name="Status", value="🟢 Offen", inline=True)
            await channel.send(embed=embed, view=TicketControlView())
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
        lines = []
        try:
            async for message in channel.history(limit=1000, oldest_first=True):
                stamp = message.created_at.strftime("%d.%m.%Y %H:%M:%S")
                line = message.content or "[kein Text]"
                if message.attachments:
                    line += " | Anhänge: " + ", ".join(att.url for att in message.attachments)
                if message.embeds:
                    line += " | Embeds: " + ", ".join(embed.title or "Embed" for embed in message.embeds)
                lines.append(f"[{stamp}] {message.author} ({message.author.id}): {line}")
        except (discord.Forbidden, discord.HTTPException):
            lines.append("[Transkript konnte von Pulse nicht vollständig gelesen werden.]")
        os.makedirs("transcripts", exist_ok=True)
        transcript_name = f"{ticket['id']}.txt"
        transcript_path = os.path.join("transcripts", transcript_name)
        with open(transcript_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        try:
            await channel.send(file=discord.File(transcript_path, filename=transcript_name))
        except (discord.Forbidden, discord.HTTPException):
            pass
        if not pulse_db.close_ticket(ticket["id"], reason, transcript_name, actor_id, actor_name):
            return back("/tickets", "Ticket konnte nicht geschlossen werden.", False)
        closed = pulse_db.get_ticket(ticket_id=ticket_id) or ticket
        pulse_db.record_event("ticket_closed_dashboard", "ticket", ticket_id, actor_id, actor_name, {"reason": reason})
        await _send_pulse_ticket_log(bot, ctx.guild, "closed", closed, ctx.member)
        try:
            owner = ctx.guild.get_member(int(ticket.get("user_id") or 0))
            if owner:
                dm = discord.Embed(title="🎫 Dein Ticket wurde geschlossen", color=discord.Color.blurple())
                dm.description = f"Ticket {ticket_id} wurde von {actor_name} geschlossen.\n\nGrund: {reason}"
                await owner.send(embed=dm)
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
            "warns": len(active_warns(user_entry(team_db, str(member.id)))) if isinstance(team_db, dict) else 0,
            "hrs": hrs, "reached": hrs >= weekly_goal, "on_loa": on_loa,
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
               f"⚠️ {below} unter Ziel · 🟢 {len([m for m in members if m['duty']])} im Dienst · 🌴 {len([m for m in members if m['on_loa']])} abgemeldet · Activity: {activity_summary}")

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
    <div class="space-y-3">{rows_html or f"<div class='text-center py-12 text-slate-400 text-sm {CARD}'>Keine Teammitglieder gefunden. Team-Rollen kannst du unter Einstellungen festlegen.</div>"}</div>"""
    return render_page("Teamliste", ctx, "team", body, head)


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

    roles = sorted(
        [r for r in guild.roles if not r.is_default() and not r.managed],
        key=lambda r: -r.position,
    )
    bot_top = getattr(guild.me, "top_role", None)
    cards = []
    for role in roles:
        current = role in member.roles
        if role == guild.default_role:
            continue
        if bot_top and role.position >= bot_top.position:
            state = '<span class="text-[10px] text-rose-500 font-semibold">Bot kann diese Rolle nicht verwalten</span>'
            checkbox = ""
        elif not ctx.perms.get("is_admin") and role.position >= ctx.member.top_role.position:
            state = '<span class="text-[10px] text-amber-500 font-semibold">Über deiner Rolle</span>'
            checkbox = ""
        else:
            state = '<span class="text-[10px] text-emerald-500 font-semibold">verwaltbar</span>'
            checkbox = f'<input type="checkbox" name="add_role_ids" value="{role.id}" class="w-4 h-4" {"checked" if False else ""}>'
        remove_checkbox = ""
        if current and role not in team_role_ids and checkbox:
            remove_checkbox = f'<input type="checkbox" name="remove_role_ids" value="{role.id}" class="w-4 h-4">'
        elif current and role in team_role_ids and checkbox:
            remove_checkbox = f'<input type="checkbox" name="remove_role_ids" value="{role.id}" class="w-4 h-4">'
        cards.append(
            f'<div class="{CARD} p-4 grid md:grid-cols-[1fr_auto_auto] gap-3 items-center">'
            f'<div><div class="font-semibold text-sm">{esc(role.name)}</div>'
            f'<div class="text-[10px] text-slate-400 font-mono">ID {role.id} · Position {role.position} · {"Aktiv" if current else "Nicht vergeben"}</div></div>'
            f'<label class="text-xs flex items-center gap-2 text-emerald-600"><input type="checkbox" name="add_role_ids" value="{role.id}" {"disabled" if not checkbox else ""}> Hinzufügen</label>'
            f'<label class="text-xs flex items-center gap-2 text-rose-600"><input type="checkbox" name="remove_role_ids" value="{role.id}" {"disabled" if not current or not checkbox else ""}> Entfernen</label>'
            f'</div>'
        )

    body=f"""
    <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-4 mb-6">
        <div>
            <div class="text-xs text-slate-400 mb-1">Team / Rollenverwaltung</div>
            <h1 class="text-2xl font-bold text-slate-900 dark:text-white">⚙ Rollen verwalten</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">{esc(member.display_name)} · Mehrere Rollen gleichzeitig hinzufügen oder entfernen.</p>
        </div>
        <a href="/team" class="px-4 py-2.5 rounded-xl bg-slate-100 dark:bg-slate-800 text-xs font-semibold">← Zur Teamliste</a>
    </div>
    <form action="/team/{member.id}/roles" method="post" onsubmit="return confirm('Die ausgewählten Rollenänderungen wirklich anwenden?');">
        <div class="grid gap-3">{''.join(cards)}</div>
        <div class="{CARD} p-5 mt-5 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
            <div class="flex-1">
                <div class="font-bold text-sm">Änderung ausführen</div>
                <div class="text-[11px] text-slate-500 mb-2">Alle Änderungen werden gemeinsam durchgeführt und im Team-Updates-Kanal protokolliert.</div>
                <input type="text" name="action_reason" maxlength="500" placeholder="Pflichtangabe: Grund für die Rollenänderung..." required class="{INPUT}">
            </div>
            <button class="{BTN} px-5 py-2.5 text-xs whitespace-nowrap">💾 Rollen ändern</button>
        </div>
    </form>
    """
    return render_page(f"Rollenverwaltung · {member.display_name}", ctx, "team", body)


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
            discord.Color.blurple(),
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
                        <button name="action" value="promote" onclick="return confirm('Wirklich befördern?')" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">⬆️ Befördern</button>
                        <button name="action" value="demote" onclick="return confirm('Wirklich degradieren?')" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">⬇️ Degradieren</button>
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
                <div class="{CARD} p-5"><div class="text-xs text-slate-400 font-semibold mb-1">Ticket-Cases</div><div class="text-2xl font-bold">{info.get('ticket_cases', 0)}</div></div>
                <div class="{CARD} p-5"><div class="text-xs text-slate-400 font-semibold mb-1">Support-Cases</div><div class="text-2xl font-bold">{info.get('support_cases', 0)}</div></div>
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


# =============================================================
# ROUTE: ÖFFENTLICHES BEWERBUNGSFORMULAR MIT LAUFBAHN-TEST
# =============================================================
@app.get("/apply", response_class=HTMLResponse)
async def public_apply_page(request: Request, apply_session: str = Cookie(None)):
    applicant = verify_payload(apply_session) if apply_session else None
    if applicant and applicant.get("apply_exp", 0) < time.time():
        applicant = None
    if not applicant:
        return f"""
        <!DOCTYPE html><html lang="de"><head>{get_head_html("Team-Bewerbung")}</head>
        <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-900 dark:text-white min-h-screen flex items-center justify-center p-4 font-sans">
        <div class="{CARD} p-8 shadow-xl w-full max-w-lg text-center space-y-5">
            <div class="text-4xl">📝</div><h1 class="text-xl font-bold">Team-Bewerbung</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400">Verbinde deine Bewerbung zuerst sicher mit deinem Discord-Account. Danach kannst du deine Laufbahn auswählen und den passenden Eignungstest direkt online absolvieren.</p>
            <a href="/apply/login" class="block w-full bg-[#5865F2] hover:bg-[#4752C4] text-white font-semibold py-3 rounded-xl">Mit Discord verbinden</a>
            <a href="/" class="text-xs text-slate-400 hover:underline">← Zurück</a>
        </div></body></html>"""

    career_path = (request.query_params.get("career") or "").strip()
    if career_path not in APPLICATION_CAREERS:
        career_path = ""

    applicant_name = applicant.get("global_name") or applicant.get("username") or "Discord-Mitglied"
    applicant_id = esc(applicant.get("id"))
    avatar_hash = esc(applicant.get("avatar") or "0")
    avatar_url = f"https://cdn.discordapp.com/avatars/{applicant_id}/{avatar_hash}.png"

    if not career_path:
        career_cards = ""
        for key, career in APPLICATION_CAREERS.items():
            career_cards += f"""
            <a href="/apply?career={key}" class="block text-left rounded-2xl border border-slate-200 dark:border-slate-700 p-4 hover:border-indigo-500 hover:shadow-md transition">
                <div class="flex items-center justify-between gap-3">
                    <h2 class="font-bold text-base">{esc(career["label"])}</h2>
                    <span class="text-[10px] font-bold rounded-full bg-indigo-500/10 text-indigo-600 dark:text-indigo-300 px-2.5 py-1">Eignungstest</span>
                </div>
                <p class="text-xs text-slate-500 dark:text-slate-400 mt-2">{esc(career["description"])}</p>
                <p class="text-[10px] text-slate-400 mt-3">{len(career["questions"])} Fragen · Richtwert zum Bestehen: {career["pass_percent"]}%</p>
            </a>"""
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
    return f"""
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
                <form action="/action" method="post" class="space-y-5 text-xs">
                    <input type="hidden" name="action" value="submit_application">
                    <input type="hidden" name="applicant_id" value="{applicant_id}">
                    <input type="hidden" name="applicant_name" value="{esc(applicant_name)}">
                    <input type="hidden" name="career_path" value="{career_path}">
                    <section class="space-y-2">
                        <h2 class="font-bold text-base">1. Motivation</h2>
                        <label for="applicant_text" class="block text-slate-500 dark:text-slate-400">Warum möchtest du diese Laufbahn einschlagen? Beschreibe kurz deine Erfahrung, Stärken und Motivation.</label>
                        <textarea id="applicant_text" name="applicant_text" maxlength="2000" placeholder="Erzähle etwas über dich, deine Erfahrung und deine Motivation…" required class="{INPUT} h-32"></textarea>
                    </section>
                    <section class="space-y-3">
                        <div><h2 class="font-bold text-base">2. Online-Eignungstest</h2>
                            <p class="text-slate-500 dark:text-slate-400 mt-1">Beantworte alle Fragen. Das System prüft deine Antworten automatisch. Das Ergebnis ist eine Entscheidungshilfe und führt nicht allein automatisch zur Annahme oder Ablehnung.</p>
                        </div>
                        {question_markup}
                    </section>
                    <button class="w-full bg-emerald-600 hover:bg-emerald-500 font-semibold py-3 rounded-xl text-white">Bewerbung und Test absenden</button>
                </form>
                <div class="text-center"><a href="/" class="text-xs text-slate-400 hover:underline">← Zurück</a></div>
            </div>
        </main>
    </body></html>"""


@app.get("/apply/login")
async def apply_login(request: Request):
    state = secrets.token_urlsafe(24)
    redirect_apply = oauth_redirect_uri(request, application=True)
    url = (f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}"
           f"&redirect_uri={quote(redirect_apply, safe='')}"
           f"&response_type=code&scope=identify&state={state}")
    response=RedirectResponse(url=url,status_code=303)
    response.set_cookie("apply_oauth_state",state,max_age=600,httponly=True,samesite="lax",secure=oauth_cookie_secure(request))
    return response


@app.get("/apply/callback")
async def apply_callback(request: Request, code: str=None, state: str=None, error: str=None, apply_oauth_state: str=Cookie(None)):
    retry='<a href="/apply" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Zur Bewerbung</a>'
    if error or not code or not state or not apply_oauth_state or not hmac.compare_digest(state,apply_oauth_state):
        return HTMLResponse(simple_page('❌','Bewerbungs-Login fehlgeschlagen','Bitte erneut versuchen.',retry),status_code=400)
    redirect_apply=oauth_redirect_uri(request, application=True)
    async with httpx.AsyncClient() as client:
        tr=await client.post('https://discord.com/api/v10/oauth2/token',data={'client_id':CLIENT_ID,'client_secret':CLIENT_SECRET,'grant_type':'authorization_code','code':code,'redirect_uri':redirect_apply},headers={'Content-Type':'application/x-www-form-urlencoded'})
        token=tr.json().get('access_token')
        if not token: return HTMLResponse(simple_page('❌','Bewerbungs-Login fehlgeschlagen','Discord konnte den Login nicht bestätigen.',retry),status_code=400)
        ud=(await client.get('https://discord.com/api/v10/users/@me',headers={'Authorization':f'Bearer {token}'})).json()
    session={'id':str(ud.get('id')),'username':ud.get('username'),'global_name':ud.get('global_name') or ud.get('username'),'avatar':ud.get('avatar'),'apply_exp':int(time.time()+1800)}
    response=RedirectResponse('/apply',status_code=303); response.set_cookie('apply_session',sign_payload(session),httponly=True,samesite='lax',secure=oauth_cookie_secure(request),max_age=1800); response.delete_cookie('apply_oauth_state'); return response


# =============================================================
# ROUTE: BEWERBUNGEN ÜBERSICHT (offen + Verlauf)
# =============================================================
@app.get("/applications", response_class=HTMLResponse)
async def applications_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    uid = ctx.user["id"]
    show_history = request.query_params.get("view") == "history"
    can_decide = ctx.perms["can_promote"] or ctx.perms["is_admin"]
    apps = load_json(APPS_FILE, {})

    items = [(k, v) for k, v in apps.items() if (v.get("status") != "pending") == show_history]
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
        if show_history:
            accepted = item.get("status") == "accepted"
            footer = f'<span class="text-xs font-semibold border px-3 py-1 rounded-full {BADGE_OK if accepted else BADGE_BAD}">{"✅ Angenommen" if accepted else "❌ Abgelehnt"}{(" von " + esc(item.get("decided_by"))) if item.get("decided_by") else ""}</span>'
        else:
            decide = f"""
                <form action="/action" method="post" class="flex flex-wrap gap-2 items-center" onsubmit="return confirm('Entscheidung endgültig treffen?');">
                    <input type="hidden" name="action" value="decide_app"><input type="hidden" name="app_id" value="{esc(app_id)}">
                    <select name="hire_role_id" class="{INPUT} py-1.5 text-[11px] w-auto min-w-44">
                        <option value="">Einstiegsrolle wählen…</option>
                        {''.join(f'<option value="{r.id}">{esc(r.name)}</option>' for r in sorted((ctx.guild.get_role(x) for x in ctx.config.get("team_role_ids", [])), key=lambda z: z.position if z else -1) if r)}
                    </select>
                    <input type="text" name="action_reason" maxlength="500" placeholder="Grund für die Entscheidung..." required class="{INPUT} py-1.5 min-w-56">
                    <button name="decision" value="accept" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 text-xs px-3.5 py-1.5 rounded-xl font-semibold transition">✅ Einstellen</button>
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
            {test_review_html}
            <div class="flex flex-wrap justify-between items-center gap-2 pt-2 border-t border-slate-100 dark:border-slate-800/80">{footer}</div>
        </div>"""

    def tab(label, href, active):
        cls = "bg-indigo-600 text-white" if active else "bg-white dark:bg-[#141824] text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-700"
        return f'<a href="{href}" class="px-3.5 py-1.5 rounded-xl text-xs font-semibold {cls}">{label}</a>'

    empty = "Noch keine entschiedenen Bewerbungen." if show_history else "Keine offenen Bewerbungen vorhanden."
    body = f"""
    <div class="flex items-center justify-between mb-6 max-w-3xl">
        <h1 class="text-2xl font-bold text-slate-900 dark:text-white">{'Bewerbungs-Verlauf' if show_history else 'Offene Bewerbungen'}</h1>
        <div class="flex gap-2">{tab('Offen', '/applications', not show_history)}{tab('Verlauf', '/applications?view=history', show_history)}</div>
    </div>
    <div class="space-y-4 max-w-3xl">{apps_html or f"<p class='text-xs text-slate-400 italic {CARD} p-6'>{empty}</p>"}</div>"""
    return render_page("Bewerbungen", ctx, "apps", body)

# =============================================================
# DISCORD BACKUP SYSTEM (nur Admins, mit Auto-Backup)
# =============================================================
BACKUP_NAME_RE = re.compile(r"^(?:(?:auto_|pre_restore_))?backup_[0-9_\-]+\.json$")


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
        out.append({"type": "role" if isinstance(target, discord.Role) else "member", "id": target.id,
                    "name": getattr(target, "name", str(target)), "allow": allow.value, "deny": deny.value})
    return out


def _channel_dict(ch):
    return {"name": ch.name, "type": str(ch.type), "topic": getattr(ch, "topic", None), "position": ch.position,
            "nsfw": getattr(ch, "nsfw", None), "slowmode": getattr(ch, "slowmode_delay", None),
            "overwrites": _overwrites(ch)}


def build_backup(guild) -> dict:
    data = {
        "backup_version": 2,
        "pulse_version": "5.0",
        "guild_name": guild.name, "guild_id": guild.id,
        "created_at": now_de().strftime("%Y-%m-%d_%H-%M-%S"),
        "roles": [], "categories": [], "uncategorized_channels": [],
        "panel_data": {},  # Panel-Daten (Verwarnungen, Logs, Schichten, Einstellungen ...)
    }
    for role in guild.roles:
        if role.is_default():
            continue
        data["roles"].append({"id": role.id, "name": role.name, "color": role.color.value,
                              "permissions": role.permissions.value, "hoist": role.hoist,
                              "mentionable": role.mentionable, "position": role.position})
    for category in guild.categories:
        data["categories"].append({"name": category.name, "position": category.position,
                                   "overwrites": _overwrites(category),
                                   "channels": [_channel_dict(ch) for ch in category.channels]})
    data["uncategorized_channels"] = [_channel_dict(ch) for ch in guild.channels
                                      if ch.category is None and not isinstance(ch, discord.CategoryChannel)]
    backup_defaults = {
        "team_data": {}, "config": {}, "logs": [],
        "shifts": {}, "meetings": {}, "applications": {},
    }
    for key, path in (("team_data", DATA_FILE), ("config", CONFIG_FILE), ("logs", LOGS_FILE),
                      ("shifts", SHIFTS_FILE), ("meetings", MEETINGS_FILE), ("applications", APPS_FILE)):
        data["panel_data"][key] = load_json(path, backup_defaults[key])
    try:
        data["panel_data"]["pulse_db"] = pulse_db.export_state()
    except Exception as e:
        data["panel_data"]["pulse_db_error"] = str(e)
    # Ticket transcripts are small text files; include them so support history is preserved in backups.
    transcripts = {}
    transcript_dir = os.path.join(BASE_DIR, "transcripts")
    if os.path.isdir(transcript_dir):
        for name in os.listdir(transcript_dir):
            path = os.path.join(transcript_dir, name)
            if not (name.endswith(".txt") and os.path.isfile(path)):
                continue
            try:
                if os.path.getsize(path) <= 2 * 1024 * 1024:
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        transcripts[name] = fh.read()
            except OSError:
                continue
    data["panel_data"]["transcripts"] = transcripts
    try:
        conn = sqlite3.connect(DB_ABMELDUNGEN)
        rows = conn.execute("SELECT user_id,user_name,grund,von,bis,original_nick,guild_id FROM abmeldungen").fetchall()
        conn.close()
        data["panel_data"]["loas"] = [dict(zip(["user_id","user_name","grund","von","bis","original_nick","guild_id"], r)) for r in rows]
    except Exception:
        data["panel_data"]["loas"] = []
    return data


def prune_auto_backups():
    autos = sorted((f for f in os.listdir(BACKUP_DIR) if f.startswith("auto_") and f.endswith(".json")),
                   key=lambda f: os.path.getmtime(os.path.join(BACKUP_DIR, f)), reverse=True)
    for old in autos[MAX_BACKUPS:]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except Exception:
            pass


def create_backup_file(guild, prefix: str = "") -> str:
    filename = f"{prefix}backup_{now_de().strftime('%Y-%m-%d_%H-%M-%S')}.json"
    save_json(os.path.join(BACKUP_DIR, filename), build_backup(guild))
    if prefix == "auto_":
        prune_auto_backups()
    return filename


async def auto_backup_loop():
    if AUTO_BACKUP_HOURS <= 0:
        return
    while True:
        await asyncio.sleep(300)
        try:
            bot = getattr(app.state, "bot", None)
            guild = bot.get_guild(GUILD_ID) if bot else None
            if guild:
                times = [
                    os.path.getmtime(os.path.join(BACKUP_DIR, f))
                    for f in os.listdir(BACKUP_DIR)
                    if BACKUP_NAME_RE.fullmatch(f)
                ]
                if not times or time.time() - max(times) > AUTO_BACKUP_HOURS * 3600:
                    name = create_backup_file(guild, "auto_")
                    log_audit("System", "0", "Auto-Backup", name)

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
                            color=discord.Color.green() if size <= 7_500_000 else discord.Color.orange(),
                            timestamp=now_de(),
                        )
                        embed.add_field(name="Datei", value=os.path.basename(name), inline=False)
                        embed.add_field(name="Größe", value=f"{size / (1024 * 1024):.2f} MiB", inline=True)
                        try:
                            if size <= 7_500_000:
                                await channel.send(
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
                                await channel.send(
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


@app.on_event("startup")
async def _start_background_tasks():
    # Initialise one small shared HTTP pool and keep the backup worker singular.
    _roblox_http_client()
    task = getattr(app.state, "backup_task", None)
    if task is None or task.done():
        app.state.backup_task = asyncio.create_task(auto_backup_loop())


@app.on_event("shutdown")
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
    files = sorted((f for f in os.listdir(BACKUP_DIR) if BACKUP_NAME_RE.fullmatch(f)),
                   key=lambda f: os.path.getmtime(os.path.join(BACKUP_DIR, f)), reverse=True)
    rows = ""
    for f in files:
        path = os.path.join(BACKUP_DIR, f)
        created = datetime.fromtimestamp(os.path.getmtime(path), TZ).strftime("%d.%m.%Y %H:%M") if TZ else \
            datetime.fromtimestamp(os.path.getmtime(path)).strftime("%d.%m.%Y %H:%M")
        auto = ' <span class="text-[10px] px-2 py-0.5 rounded-full border border-indigo-500/30 bg-indigo-500/10 text-indigo-500 font-sans">AUTO</span>' if f.startswith("auto_") else ""
        rows += f"""
        <div class="{CARD} p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
                <div class="font-bold text-slate-900 dark:text-white text-sm font-mono break-all">{esc(f)}{auto}</div>
                <div class="text-xs text-slate-400 mt-0.5">{created} · {round(os.path.getsize(path) / 1024, 1)} KB</div>
            </div>
            <div class="flex items-center gap-2">
                <a href="/backup/download/{esc(f)}" class="bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-xs px-3.5 py-2 rounded-xl text-slate-700 dark:text-slate-300 font-medium transition shadow-sm">📥 Herunterladen</a>
                <form action="/backup/restore" method="post" onsubmit="return confirm('Panel-Daten dieses Backups wiederherstellen? Aktuelle Panel-Daten werden überschrieben.');">
                    <input type="hidden" name="filename" value="{esc(f)}">
                    <button class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-xs px-3.5 py-2 rounded-xl font-medium transition">↩️ Panel wiederherstellen</button>
                </form>
                <form action="/backup/delete" method="post" onsubmit="return confirm('Backup wirklich löschen?');">
                    <input type="hidden" name="filename" value="{esc(f)}">
                    <button class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-xs px-3.5 py-2 rounded-xl font-medium transition">🗑️ Löschen</button>
                </form>
            </div>
        </div>"""
    auto_text = (f"Automatisches Backup alle {AUTO_BACKUP_HOURS:g}h aktiv (letzte {MAX_BACKUPS} automatische Backups werden behalten)."
                 if AUTO_BACKUP_HOURS > 0 else "Automatisches Backup ist deaktiviert (AUTO_BACKUP_HOURS=0).")
    body = f"""
    <div class="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-4 mb-6">
        <div>
            <h1 class="text-2xl font-bold text-slate-900 dark:text-white">Discord Server Backups</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400">Rollen, Kanäle, Kanal-Rechte und alle Panel-Daten. {auto_text}</p>
        </div>
        <form action="/backup/create" method="post"><button class="{BTN} text-xs py-3 px-5">💾 Neues Backup erstellen</button></form>
    </div>
    <div class="space-y-3 max-w-3xl">{rows or f"<div class='text-xs text-slate-400 italic {CARD} p-6 text-center'>Noch keine Backups vorhanden.</div>"}</div>"""
    return render_page("Server Backups", ctx, "backups", body)


@app.post("/backup/create")
async def create_backup(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    name = create_backup_file(ctx.guild)
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Backup Erstellt", f"Filename: {name}")
    return back("/backups", "Backup erstellt.")


@app.get("/backup/download/{filename}")
async def download_backup(request: Request, filename: str, user_session: str = Cookie(None)):
    auth(request, user_session, perm=None, admin=True)
    path = safe_backup_path(filename)
    if not path:
        raise HTTPException(status_code=404, detail="Backup nicht gefunden.")
    return FileResponse(path, media_type="application/json", filename=os.path.basename(path))


@app.post("/backup/restore")
async def restore_backup(request: Request, filename: str = Form(...), user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    path = safe_backup_path(filename)
    if not path:
        return back("/backups", "Backup nicht gefunden.", False)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            backup = json.load(fh)
        if not isinstance(backup, dict) or not isinstance(backup.get("panel_data"), dict):
            raise ValueError("Kein gültiges Pulse-Backup")
        try:
            backup_version = int(backup.get("backup_version", 1))
        except (TypeError, ValueError):
            raise ValueError("Ungültige Backup-Version")
        if backup_version < 1 or backup_version > 2:
            raise ValueError(f"Nicht unterstützte Backup-Version: {backup_version}")
        backup_guild_id = backup.get("guild_id")
        if backup_guild_id is not None and str(backup_guild_id) != str(ctx.guild.id):
            raise ValueError("Dieses Backup gehört zu einem anderen Discord-Server; Wiederherstellung abgebrochen.")
        panel = backup["panel_data"]
        # Version-2 snapshots created before the typed-default fix used an
        # empty object when logs.json did not exist; migrate that legacy case.
        if panel.get("logs") == {}:
            panel["logs"] = []
        expected_types = {
            "team_data": dict, "config": dict, "logs": list,
            "shifts": dict, "meetings": dict, "applications": dict,
            "pulse_db": dict, "loas": list, "transcripts": dict,
        }
        for key, expected_type in expected_types.items():
            if key in panel and not isinstance(panel[key], expected_type):
                raise ValueError(f"Ungültiger Datentyp im Backup-Feld: {key}")
        # Vor jeder Wiederherstellung zuerst den aktuellen Zustand sichern.
        # So kann ein Admin bei fehlerhaften/unerwarteten Daten zurückwechseln.
        safety_snapshot = create_backup_file(ctx.guild, "pre_restore_")
        mapping = {"team_data": DATA_FILE, "config": CONFIG_FILE, "logs": LOGS_FILE, "shifts": SHIFTS_FILE, "meetings": MEETINGS_FILE, "applications": APPS_FILE}
        for key, target in mapping.items():
            if key in panel:
                save_json(target, panel[key])
        if isinstance(panel.get("pulse_db"), dict):
            pulse_db.import_state(panel["pulse_db"])
        if isinstance(panel.get("loas"), list):
            conn = sqlite3.connect(DB_ABMELDUNGEN)
            conn.execute("DELETE FROM abmeldungen")
            conn.executemany("INSERT OR REPLACE INTO abmeldungen(user_id,user_name,grund,von,bis,original_nick,guild_id) VALUES (?,?,?,?,?,?,?)", [
                (x.get("user_id"), x.get("user_name", ""), x.get("grund", ""), x.get("von", ""), x.get("bis", ""), x.get("original_nick", ""), x.get("guild_id", GUILD_ID))
                for x in panel["loas"] if isinstance(x, dict) and x.get("user_id") is not None
            ])
            conn.commit(); conn.close()
        os.makedirs(os.path.join(BASE_DIR, "transcripts"), exist_ok=True)
        for name, content in (panel.get("transcripts") or {}).items():
            safe = os.path.basename(str(name))
            if safe.endswith(".txt") and isinstance(content, str) and len(content) <= 2 * 1024 * 1024:
                with open(os.path.join(BASE_DIR, "transcripts", safe), "w", encoding="utf-8") as fh:
                    fh.write(content)
        log_audit(
            ctx.user.get("global_name"), ctx.user["id"], "Backup Wiederhergestellt",
            f"Filename: {os.path.basename(path)} · Sicherheitskopie: {safety_snapshot}",
        )
        return back(
            "/backups",
            f"Panel-Daten und Pulse-Historien wurden wiederhergestellt. Sicherheitskopie angelegt: {safety_snapshot}",
        )
    except Exception as exc:
        log_audit(ctx.user.get("global_name"), ctx.user["id"], "Backup Wiederherstellung Fehlgeschlagen", f"{os.path.basename(path)} · {exc}")
        return back("/backups", f"Wiederherstellung fehlgeschlagen: {exc}", False)


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
    "save_role_permissions": "admin", "set_weekly_goal": "admin", "save_team_roles": "admin",
}



def user_entry(team_db: dict, key: str) -> dict:
    entry = team_db.setdefault(key, {})
    for k, v in (("warns_list", []), ("notes", []), ("ticket_cases", 0), ("support_cases", 0)):
        entry.setdefault(k, v)
    normalize_warns(entry)
    return entry


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
            if str(item.get("user_id")) != str(member.id) or str(item.get("status")) != "accepted":
                continue
            try:
                candidates.append(datetime.strptime(str(item.get("decided_at") or ""), "%d.%m.%Y %H:%M"))
            except Exception:
                pass
        if candidates:
            return min(candidates).strftime("%d.%m.%Y")
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


@app.post("/action")
async def handle_action(
    request: Request,
    action: str = Form(...),
    user_id: int = Form(None),
    role_id: int = Form(None),
    redirect_to_member: str = Form(None),
    warn_reason: str = Form(None),
    warn_proof: str = Form(None),
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
    answer_1: str = Form(None),
    answer_2: str = Form(None),
    answer_3: str = Form(None),
    answer_4: str = Form(None),
    answer_5: str = Form(None),
    answer_6: str = Form(None),
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
        back_link = '<a href="/apply" class="px-4 py-2 rounded-xl bg-slate-100 dark:bg-slate-800">Zurück</a>'
        if rate_limited(f"apply:{ip}"):
            return HTMLResponse(simple_page("⏳", "Zu viele Bewerbungen", "Bitte versuche es später erneut.", back_link), status_code=429)
        apply_user = verify_payload(apply_session) if apply_session else None
        if not apply_user or apply_user.get("apply_exp", 0) < time.time() or str(apply_user.get("id")) != str(applicant_id):
            return HTMLResponse(simple_page("🔒", "Discord-Verknüpfung fehlt", "Bitte starte die Bewerbung über Mit Discord verbinden.", back_link), status_code=403)
        name = (apply_user.get("global_name") or apply_user.get("username") or applicant_name or "").strip()[:80]
        text = (applicant_text or "").strip()[:2000]
        if not applicant_id or not name or not text:
            return HTMLResponse(simple_page("⚠️", "Angaben fehlen", "Bitte fülle die Motivation und alle Pflichtfelder aus.", back_link), status_code=400)
        try:
            test_result = grade_career_test(
                career_path or "",
                [answer_1, answer_2, answer_3, answer_4, answer_5, answer_6],
            )
        except ValueError as exc:
            return HTMLResponse(simple_page("⚠️", "Online-Test unvollständig", esc(str(exc)), back_link), status_code=400)

        apps = load_json(APPS_FILE, {})
        if any(a.get("user_id") == str(applicant_id) and a.get("status") == "pending" for a in apps.values()):
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
            "test_version": 1,
            "upvotes": [],
            "downvotes": [],
            "created_at": now_de().strftime("%d.%m.%Y %H:%M"),
        }
        save_json(APPS_FILE, apps)
        result_status = "Bestanden" if test_result["passed"] else "noch nicht bestanden"
        result_text = (
            f"Deine Bewerbung wurde mit der Laufbahn {esc(test_result['career_label'])} gespeichert. "
            f"Online-Test: {test_result['score']}/{test_result['total']} richtig ({test_result['percent']} %), "
            f"Status: {result_status}. Das Team prüft Motivation und Testergebnis gemeinsam."
        )
        return HTMLResponse(simple_page("✅", "Bewerbung erfolgreich abgesendet!", result_text, back_link))

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
            discord.Color.green(),
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
                keep_roles = [guild.get_role(role_id) for role_id in TEAM_KICK_KEEP_ROLE_IDS]
                if any(role is None for role in keep_roles):
                    return back(member_url, "Team-Kick abgebrochen: Die Bürgerrolle oder die zweite Pflichtrolle wurde auf dem Server nicht gefunden.", False)
                if any(role.managed or role.position >= guild.me.top_role.position for role in keep_roles):
                    return back(member_url, "Team-Kick abgebrochen: Der Bot kann die Bürgerrolle oder die zweite Pflichtrolle wegen der Rollenverwaltung/-hierarchie nicht setzen.", False)

                keep_role_ids = set(TEAM_KICK_KEEP_ROLE_IDS)
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
                    discord.Color.red(),
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
                await member.add_roles(new_role, reason=f"Beförderung durch {actor}: {action_reason}")
                if old:
                    await member.remove_roles(*old, reason=f"Beförderung durch {actor}: {action_reason}")
                await send_dm_notification(member, f"🎉 **Herzlichen Glückwunsch!** Du wurdest auf **{guild.name}** zum **{new_role.name}** befördert!")
                await send_team_update_embed(
                    guild,
                    "⬆️ Team-Update: Beförderung",
                    f"{member.mention} wurde befördert.",
                    discord.Color.green(),
                    target=member.mention,
                    action="Beförderung",
                    actor=actor,
                    fields=[("Vorherige Rolle", old[0].mention if old else "Keine", True), ("Neue Rolle", new_role.mention, True), ("Grund", action_reason, False)],
                    thumbnail=member.display_avatar.url,
                )
                log_audit(actor, actor_id, "Beförderung", f"{member.display_name} -> {new_role.name} · Grund: {action_reason}")
                return back(member_url, f"{member.display_name} wurde zum {new_role.name} befördert.")

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
                await send_dm_notification(member, f"⚠️ Du wurdest auf **{guild.name}** auf die Rolle **{new_role.name}** degradiert.")
                await send_team_update_embed(
                    guild,
                    "⬇️ Team-Update: Degradierung",
                    f"{member.mention} wurde degradiert.",
                    discord.Color.orange(),
                    target=member.mention,
                    action="Degradierung",
                    actor=actor,
                    fields=[("Vorherige Rolle", old[0].mention if old else "Unbekannt", True), ("Neue Rolle", new_role.mention, True), ("Grund", action_reason, False)],
                    thumbnail=member.display_avatar.url,
                )
                log_audit(actor, actor_id, "Degradierung", f"{member.display_name} -> {new_role.name} · Grund: {action_reason}")
                return back(member_url, f"{member.display_name} wurde zum {new_role.name} degradiert.")
            await member.remove_roles(*old, reason=f"Degradierung durch {actor}")
            await send_dm_notification(member, f"⚠️ Du wurdest aus dem Team-Rollenrang auf **{guild.name}** entfernt.")
            await send_team_update_embed(
                guild,
                "⬇️ Team-Update: Teamrolle entfernt",
                f"{member.mention} hat keine Teamrolle mehr.",
                discord.Color.red(),
                target=member.mention,
                action="Teamrolle entfernt",
                actor=actor,
                fields=[("Grund", action_reason, False)],
                thumbnail=member.display_avatar.url,
            )
            log_audit(actor, actor_id, "Degradierung", f"{member.display_name} -> Keine Teamrolle · Grund: {action_reason}")
            return back(member_url, f"{member.display_name} wurde aus dem Team-Rang entfernt.")
        except discord.Forbidden:
            return back(member_url, "Dem Bot fehlen Rechte – seine Rolle muss über den Team-Rollen stehen.", False)
        except Exception as e:
            print(f"Rang-Aktion fehlgeschlagen: {e}")
            return back(member_url, "Aktion fehlgeschlagen (Details in der Bot-Konsole).", False)

    # ---------- Verwarnungen ----------
    if action == "warn_with_proof":
        reason = (warn_reason or "").strip()[:500]
        if not user_id or not reason:
            return back(member_url, "Bitte einen Grund angeben.", False)
        m = guild.get_member(user_id)
        if not m:
            return back(member_url, "Mitglied nicht gefunden.", False)
        if m.bot:
            return back(member_url, "Bots können keine Team-Verwarnung erhalten.", False)
        if str(m.id) == actor_id:
            return back(member_url, "Du kannst dir selbst keine Team-Verwarnung geben.", False)
        if team_role_ids and not any(r.id in team_role_ids for r in m.roles):
            return back(member_url, "Team-Verwarnungen können nur an Teammitglieder vergeben werden.", False)

        team_db = load_json(DATA_FILE, {})
        entry = user_entry(team_db, str(user_id))
        current_count = len(active_warns(entry))
        if current_count >= 5:
            return back(member_url, "Dieses Teammitglied hat bereits die maximale Anzahl von 5 aktiven Verwarnungen.", False)
        entry["warns_list"].append({
            "id": f"warn_{uuid.uuid4().hex[:6]}", "reason": reason, "proof": safe_url(warn_proof),
            "by": actor, "date": now_de().strftime("%d.%m.%Y %H:%M"),
            "active": True, "revoked_at": None, "revoked_by": None, "revoked_reason": "",
        })
        count = len(active_warns(entry))
        save_json(DATA_FILE, team_db)
        log_audit(actor, actor_id, "Verwarnung", f"User-ID {user_id} ({count}/5): {reason}")
        role_report = await sync_warn_roles(guild, m, count, config)
        consequence_report = await apply_warn_consequences(guild, m, entry["warns_list"][-1], count, config) if count >= 2 else {"level": count, "actions": [], "ok": True}
        if m:
            await send_team_update_embed(
                guild,
                "⚠️ Team-Update: Verwarnung",
                f"{m.mention} hat eine neue Verwarnung erhalten.",
                discord.Color.orange(),
                target=f"{m.mention}\nWarn-Stufe: {count}/5",
                action=f"Verwarnung {count}/5",
                actor=actor,
                fields=[
                    ("Grund", reason, False),
                    ("Beweis", safe_url(warn_proof) or "Kein Beweis-Link", False),
                    ("Discord-Warnrolle", f"✅ {role_report.get('role').mention}" if role_report.get("ok") and role_report.get("role") else (f"✅ keine Warnrolle bei 0" if count == 0 else f"❌ {role_report.get('message')}"), False),
                ],
                thumbnail=m.display_avatar.url,
            )
            consequence_text = "; ".join(x for x in consequence_report.get("actions", []) if x)
            dm_suffix = f"\n**Automatische Folge:** {consequence_text}" if consequence_text else ""
            await send_dm_notification(m, f"⚠️ Du hast eine Verwarnung erhalten ({count}/5)!\n**Grund:** {reason}\n**Von:** {actor}{dm_suffix}")
            if count >= 5:
                # Führungskräfte erhalten zusätzlich eine Pulse-Inbox-Meldung.
                for manager in guild.members:
                    if manager.bot or manager.id == m.id:
                        continue
                    manager_perms, _ = compute_perms(guild, manager.id, config)
                    if manager_perms.get("can_promote") or manager_perms.get("is_admin"):
                        try:
                            pulse_db.notify(
                                manager.id,
                                "🚨 5/5 Team-Warnungen",
                                f"{m.display_name} hat 5 aktive Verwarnungen. Bitte Fall prüfen.",
                                "warning",
                                "/warns",
                                f"warn-escalation:{m.id}:{count}",
                                86400,
                            )
                        except Exception as exc:
                            print(f"Warn-Eskalationsbenachrichtigung fehlgeschlagen: {exc}")
                await send_team_update_embed(
                    guild,
                    "🚨 Team-Update: 5 Verwarnungen",
                    f"**Mitglied:** {m.mention} ({m.display_name})\n**Status:** Automatisch aus dem Team entfernt.",
                    discord.Color.red(),
                    target=m.mention,
                    action="Warn-Schwelle 5/5",
                    actor=actor,
                    fields=[("Warnrollen-Sync", "✅ Erfolgreich" if role_report.get("ok") else f"❌ {role_report.get('message')}")],
                    thumbnail=m.display_avatar.url,
                )
        if role_report.get("ok") and consequence_report.get("ok"):
            return back(member_url, f"Verwarnung eingetragen ({count}/5). Automatische Folge ausgeführt.")
        problems = []
        if not role_report.get("ok"):
            problems.append(f"Warnrolle: {role_report.get('message')}")
        if not consequence_report.get("ok"):
            problems.extend(consequence_report.get("actions", []))
        suffix = " · ".join(x for x in problems if x) or "Eine automatische Folge konnte nicht vollständig ausgeführt werden."
        return back(member_url, f"Verwarnung eingetragen ({count}/5), aber: {suffix}", False)

    if action == "remove_warn":
        if not user_id or not warn_id:
            return back(member_url, "Ungültige Anfrage.", False)
        team_db = load_json(DATA_FILE, {})
        entry = user_entry(team_db, str(user_id))
        m = guild.get_member(user_id)
        if not m:
            return back(member_url, "Mitglied nicht gefunden.", False)

        target = next((w for w in entry["warns_list"] if str(w.get("id")) == str(warn_id)), None)
        if target is None or not target.get("active", True) or target.get("revoked_at"):
            return back(member_url, "Diese Verwarnung wurde bereits zurückgezogen oder ist nicht mehr vorhanden. Bitte die Seite aktualisieren.", False)

        revoke_reason = (warn_revoke_reason or "").strip()[:500]
        if not revoke_reason:
            return back(member_url, "Bitte einen Grund für die Rücknahme der Verwarnung angeben.", False)
        target["active"] = False
        target["revoked_at"] = now_de().strftime("%d.%m.%Y %H:%M")
        target["revoked_by"] = actor
        target["revoked_reason"] = revoke_reason
        count = len(active_warns(entry))
        save_json(DATA_FILE, team_db)

        original_reason = str(target.get("reason") or "Kein Grund")
        original_by = str(target.get("by") or "System")
        original_date = str(target.get("date") or "N/A")
        # Temporäre Folgen dieser konkreten Warnung beim Zurückziehen sofort entfernen.
        temp_roles = (target.get("consequences") or {}).get("temporary_roles", [])
        temp_remove_failed = False
        for restriction in temp_roles:
            role = guild.get_role(int(restriction.get("role_id", 0) or 0))
            if not role:
                continue
            try:
                if role in m.roles:
                    if guild.me and (not guild.me.guild_permissions.manage_roles or role.position >= guild.me.top_role.position):
                        temp_remove_failed = True
                        continue
                    await m.remove_roles(role, reason=f"Warn zurückgezogen · {revoke_reason}")
            except discord.Forbidden:
                temp_remove_failed = True
            except Exception:
                temp_remove_failed = True
        target.setdefault("consequences", {})["temporary_roles"] = [] if not temp_remove_failed else temp_roles
        save_json(DATA_FILE, team_db)
        role_report = await sync_warn_roles(guild, m, count, config)

        await send_team_update_embed(
            guild,
            "✅ Team-Update: Verwarnung zurückgezogen",
            f"Die Verwarnung von {m.mention} wurde durch das Team-Dashboard zurückgezogen.",
            discord.Color.green(),
            target=f"{m.mention}\nAktive Warnungen: {count}/5",
            action="Warn zurückgezogen",
            actor=actor,
            fields=[
                ("Ursprünglicher Grund", original_reason, False),
                ("Ausgestellt von", original_by, True),
                ("Ausgestellt am", original_date, True),
                ("Rücknahmegrund", revoke_reason, False),
                ("Warn-ID", warn_id, True),
                ("Discord-Warnrolle", "✅ Synchronisiert" if role_report.get("ok") else f"❌ {role_report.get('message')}"),
            ],
            thumbnail=m.display_avatar.url,
        )
        log_audit(actor, actor_id, "Warn Zurückgezogen",
                  f"User-ID {user_id}, Warn-ID {warn_id}, Grund: {original_reason}, Rücknahme: {revoke_reason}")
        if role_report.get("ok"):
            return back(member_url, f"Verwarnung zurückgezogen ({count}/5).")
        return back(member_url, f"Verwarnung zurückgezogen ({count}/5), aber Discord-Warnrolle konnte nicht synchronisiert werden: {role_report.get('message')}", False)


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
        conn.execute("INSERT OR REPLACE INTO abmeldungen VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (target, display_n, (loa_reason or "Kein Grund").strip()[:300], loa_start, loa_end, display_n, GUILD_ID))
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
        if not item or item.get("status") != "pending" or decision not in ("accept", "reject"):
            return back("/applications", "Bewerbung nicht mehr offen.", False)

        applicant = None
        try:
            applicant = guild.get_member(int(item.get("user_id")))
        except Exception:
            applicant = None

        action_reason = (action_reason or "").strip()[:500]
        if not action_reason:
            return back("/applications", "Bitte einen Grund für die Bewerbungsentscheidung angeben.", False)

        if decision == "accept":
            if not applicant:
                return back("/applications", "Der Bewerber ist nicht mehr auf dem Discord-Server.", False)
            configured_roles = [guild.get_role(rid) for rid in team_role_ids]
            configured_roles = [r for r in configured_roles if r and not r.managed]
            role = guild.get_role(hire_role_id) if hire_role_id else (min(configured_roles, key=lambda r: r.position) if configured_roles else None)
            if role is None or role.id not in team_role_ids:
                return back("/applications", "Bitte eine gültige Team-Einstiegsrolle konfigurieren/auswählen.", False)
            if guild.me and guild.me.top_role.position <= role.position:
                return back("/applications", "Der Bot steht nicht über der Einstiegsrolle.", False)
            old_team_roles = [r for r in applicant.roles if r.id in team_role_ids and r.id != role.id]
            try:
                if old_team_roles:
                    await applicant.remove_roles(*old_team_roles, reason=f"Einstellung durch {actor}: {action_reason}")
                await applicant.add_roles(role, reason=f"Einstellung durch {actor}: {action_reason}")
            except discord.Forbidden:
                return back("/applications", "Discord hat die Rollenänderung verweigert. Bot-Rolle höher setzen.", False)

            item["status"] = "accepted"
            item["hired_role_id"] = role.id
            item["hired_role"] = role.name
            item["decided_by"] = actor
            item["decided_at"] = now_de().strftime("%d.%m.%Y %H:%M")
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
            await send_dm_notification(applicant, f"🎉 Deine Bewerbung bei **{guild.name}** wurde angenommen. Du wurdest als **{role.name}** in das Team aufgenommen.")
            await send_team_update_embed(
                guild,
                "🎉 Team-Update: Einstellung",
                f"{applicant.mention} wurde erfolgreich in das Team aufgenommen.",
                discord.Color.green(),
                target=applicant.mention,
                action="Einstellung",
                actor=actor,
                fields=[("Einstiegsrolle", role.mention, True), ("Bewerbung", app_id or "—", True), ("Grund", action_reason, False), ("Zeitpunkt", now_de().strftime("%d.%m.%Y %H:%M"), True)],
                thumbnail=applicant.display_avatar.url,
            )
            log_audit(actor, actor_id, "Einstellung", f"{applicant.display_name} -> {role.name} · Grund: {action_reason}")
            return back("/applications", f"{applicant.display_name} wurde als {role.name} eingestellt.")

        item["status"] = "rejected"
        item["decided_by"] = actor
        item["decided_at"] = now_de().strftime("%d.%m.%Y %H:%M")
        save_json(APPS_FILE, apps)
        if applicant:
            await send_dm_notification(applicant, f"Deine Bewerbung bei **{guild.name}** wurde leider abgelehnt. Danke für dein Interesse!")
        await send_team_update_embed(
            guild,
            "📄 Team-Update: Bewerbung abgelehnt",
            f"Die Bewerbung von **{item.get('name','Unbekannt')}** wurde abgelehnt.",
            discord.Color.red(),
            target=item.get("name","Unbekannt"),
            action="Bewerbung abgelehnt",
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
                f"**{title}**\n📅 {when}\n{meetings['description']}\n\nZu-/Absagen im Dashboard unter *Teambesprechung*.", discord.Color.blurple())
        return back("/meetings", "Besprechung gespeichert.")

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
