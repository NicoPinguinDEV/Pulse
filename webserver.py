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
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:25095/callback")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
APPLICATION_REDIRECT_URI = os.getenv("DISCORD_APPLICATION_REDIRECT_URI", "")
if not APPLICATION_REDIRECT_URI:
    APPLICATION_REDIRECT_URI = (REDIRECT_URI.rsplit("/", 1)[0] + "/apply/callback") if "/" in REDIRECT_URI else REDIRECT_URI.rstrip("/") + "/apply/callback"
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "1474514929351524616"))
TEAM_UPDATE_CHANNEL_NAME = os.getenv("TEAM_UPDATE_CHANNEL_NAME", "╚『⚡』𝐓𝐞𝐚𝐦-𝐔𝐩𝐝𝐚𝐭𝐞𝐬")
TEAM_UPDATE_CHANNEL_ID = int(os.getenv("TEAM_UPDATE_CHANNEL_ID", "0") or 0)  # optional, robuster als der Name

WARN_ROLE_IDS = {
    1: int(os.getenv("WARN_ROLE_1", "1489221948348043395")),
    2: int(os.getenv("WARN_ROLE_2", "1489222076370780232")),
    3: int(os.getenv("WARN_ROLE_3", "1531760107971416135"))
}
SYNC_WARN_ROLES = os.getenv("SYNC_WARN_ROLES", "1") == "1"      # Warn-Rollen automatisch vergeben
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "7"))              # Login-Dauer
MAX_SHIFT_HOURS = float(os.getenv("MAX_SHIFT_HOURS", "12"))     # vergessene Schichten werden danach beendet
AUTO_BACKUP_HOURS = float(os.getenv("AUTO_BACKUP_HOURS", "24")) # 0 = aus
MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", "30"))
COOKIE_SECURE = (PUBLIC_BASE_URL or REDIRECT_URI).startswith("https://")

DATA_FILE = "team_data.json"
CONFIG_FILE = "config.json"
APPS_FILE = "applications.json"
SHIFTS_FILE = "shifts.json"
LOGS_FILE = "logs.json"
AUDIT_FILE = "audit_logs.json"
MEETINGS_FILE = "meetings.json"
DB_ABMELDUNGEN = "abmeldungen.db"
BACKUP_DIR = "backups"
SECRET_FILE = ".session_secret"

VALID_LOG_TYPES = ("Warn", "Kick", "Ban", "Notiz")
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

app = FastAPI()

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
    now = time.time()
    hits = [t for t in _rate_hits.get(key, []) if now - t < window]
    if len(hits) >= limit:
        _rate_hits[key] = hits
        return True
    hits.append(now)
    _rate_hits[key] = hits
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
    """Lädt Schichten und beendet vergessene Schichten automatisch."""
    db = load_json(SHIFTS_FILE, {})
    db.setdefault("active_shifts", {})
    db.setdefault("history", [])
    changed = False
    limit = int(MAX_SHIFT_HOURS * 3600)
    for uid, s in list(db["active_shifts"].items()):
        try:
            age = (now_de() - parse_dt(s.get("started_at_iso"))).total_seconds()
        except Exception:
            continue
        if age > limit:
            db["history"].append({
                "mod_id": uid, "date": s.get("date"),
                "duration_seconds": min(shift_elapsed(s), limit), "auto_closed": True,
            })
            del db["active_shifts"][uid]
            changed = True
    if changed:
        save_json(SHIFTS_FILE, db)
    return db


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


async def send_team_update_embed(guild, title, description, color=None):
    if not guild:
        return
    color = color or discord.Color.blue()
    channel = guild.get_channel(TEAM_UPDATE_CHANNEL_ID) if TEAM_UPDATE_CHANNEL_ID else None
    if not channel:
        channel = discord.utils.get(guild.text_channels, name=TEAM_UPDATE_CHANNEL_NAME)
    if channel:
        try:
            embed = discord.Embed(title=title, description=description, color=color)
            embed.set_footer(text=f"{guild.name} • Team-Updates System")
            embed.timestamp = datetime.now()
            await channel.send(embed=embed)
        except Exception as e:
            print(f"Fehler beim Senden des Team-Updates in Discord: {e}")


async def sync_warn_roles(guild, member, count: int):
    """Vergibt Warn-Rolle 1/2/3 passend zur Anzahl der Verwarnungen (die Rollen-IDs waren vorher ungenutzt)."""
    if not (SYNC_WARN_ROLES and guild and member):
        return
    target = WARN_ROLE_IDS.get(min(count, 3)) if count > 0 else None
    for rid in WARN_ROLE_IDS.values():
        role = guild.get_role(rid)
        if not role:
            continue
        try:
            if rid == target and role not in member.roles:
                await member.add_roles(role, reason="Warn-System (Dashboard)")
            elif rid != target and role in member.roles:
                await member.remove_roles(role, reason="Warn-System (Dashboard)")
        except Exception as e:
            print(f"Warn-Rolle konnte nicht angepasst werden: {e}")


def team_rank(member, team_role_ids) -> int:
    idx = [team_role_ids.index(r.id) for r in member.roles if r.id in team_role_ids]
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
        ("dashboard", "/dashboard", "⚡", "Moderatoren-Panel"),
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
                    <span class="text-[10px] text-slate-500 dark:text-slate-400 font-mono">Pulse v4 TeamOS</span>
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
_roblox_cache = {}


@app.get("/api/roblox-user")
async def roblox_user_lookup(username: str, user_session: str = Cookie(None)):
    get_current_user(user_session)
    name = (username or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", name):
        return JSONResponse({"success": False, "message": "Ungültiger Roblox-Name (3-20 Zeichen, A-Z, 0-9, _)"})

    cached = _roblox_cache.get(name.lower())
    if cached and time.time() - cached[0] < 300:
        info = dict(cached[1])
    else:
        async with httpx.AsyncClient() as client:
            try:
                res = await client.post(
                    "https://users.roblox.com/v1/usernames/users",
                    json={"usernames": [name], "excludeBannedUsers": False}, timeout=5.0)
                data = res.json()
                if not data.get("data"):
                    return JSONResponse({"success": False, "message": "Nutzer auf Roblox nicht gefunden"})
                u = data["data"][0]
                avatar_url = "https://www.roblox.com/headshot-thumbnail/image"
                try:
                    thumb = await client.get(
                        f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={u['id']}&size=150x150&format=Png&isCircular=true",
                        timeout=5.0)
                    td = thumb.json()
                    if td.get("data"):
                        avatar_url = td["data"][0].get("imageUrl", avatar_url)
                except Exception:
                    pass
                info = {"success": True, "id": str(u["id"]), "username": u["name"],
                        "displayName": u.get("displayName", u["name"]), "avatarUrl": avatar_url}
                _roblox_cache[name.lower()] = (time.time(), info)
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
# ROUTEN: LOGIN, LOGOUT & OAUTH CALLBACK
# =============================================================
@app.get("/", response_class=HTMLResponse)
async def home(user_session: str = Cookie(None)):
    if user_session and verify_payload(user_session):
        return RedirectResponse(url="/dashboard", status_code=303)
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
async def login():
    state = secrets.token_urlsafe(24)  # CSRF-Schutz für den OAuth-Ablauf
    url = (f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}"
           f"&redirect_uri={quote(REDIRECT_URI, safe='')}&response_type=code&scope=identify&state={state}")
    response = RedirectResponse(url=url, status_code=303)
    response.set_cookie("oauth_state", state, max_age=600, httponly=True, samesite="lax", secure=COOKIE_SECURE)
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key="user_session")
    response.delete_cookie(key="apply_session")
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
        token_res = await client.post(
            "https://discord.com/api/v10/oauth2/token",
            data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "grant_type": "authorization_code",
                  "code": code, "redirect_uri": REDIRECT_URI},
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
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie("user_session", sign_payload(session), httponly=True, samesite="lax",
                        secure=COOKIE_SECURE, max_age=SESSION_DAYS * 86400)
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
}

DASHBOARD_HEAD = """
<style>
    .type-chip { cursor:pointer; transition:.15s; }
    .type-chip.active { background:#4f46e5 !important; color:#fff !important; border-color:#4f46e5 !important; }
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
        if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="k"){e.preventDefault();const q=prompt("Pulse Suche – Name, Ticket, Aufgabe oder Wiki:");if(q) window.location.href="/search?q="+encodeURIComponent(q);}
    });
    // PWA
    if("serviceWorker" in navigator){ navigator.serviceWorker.register("/sw.js").catch(()=>{}); }

    let lookupTimeout = null;
    function lookupRobloxUser(val) {
        clearTimeout(lookupTimeout);
        const infoDiv = document.getElementById("robloxUserPreview");
        const idInput = document.getElementById("robloxIdInput");
        if (!val || val.trim().length < 3) { infoDiv.classList.add("hidden"); return; }
        lookupTimeout = setTimeout(() => {
            fetch("/api/roblox-user?username=" + encodeURIComponent(val.trim()))
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        if (idInput) idInput.value = data.id;
                        const prev = data.previous_total > 0
                            ? `<div class="text-[10px] text-amber-600 dark:text-amber-400 font-semibold mt-0.5">⚠️ ${data.previous_total} frühere Logs (${escapeHtml(data.previous_summary)})</div>`
                            : `<div class="text-[10px] text-emerald-600 dark:text-emerald-400 mt-0.5">✔ Keine früheren Logs</div>`;
                        infoDiv.innerHTML = `
                            <div class="flex items-center gap-3 p-2.5 bg-indigo-50/50 dark:bg-indigo-950/20 border border-indigo-200 dark:border-indigo-800/50 rounded-xl">
                                <img src="${escapeHtml(data.avatarUrl)}" class="w-9 h-9 rounded-full border border-indigo-300 dark:border-indigo-700">
                                <div class="truncate">
                                    <div class="font-bold text-slate-900 dark:text-white text-xs">${escapeHtml(data.displayName)} <span class="text-slate-400 text-[10px]">(@${escapeHtml(data.username)})</span></div>
                                    <div class="text-[10px] text-indigo-600 dark:text-indigo-400 font-mono font-semibold">Roblox ID: ${escapeHtml(data.id)}</div>
                                    ${prev}
                                </div>
                            </div>`;
                    } else {
                        infoDiv.innerHTML = `<span class="text-rose-500 text-[11px] block px-1">⚠️ ${escapeHtml(data.message)}</span>`;
                    }
                    infoDiv.classList.remove("hidden");
                })
                .catch(() => infoDiv.classList.add("hidden"));
        }, 400);
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

    # Logs
    logs_html = ""
    is_manager = ctx.perms["can_promote"] or ctx.perms["is_admin"]
    for log in reversed(logs_db[-50:]):
        ltype = log.get("type", "Log")
        badge = LOG_TYPE_STYLE.get(ltype, "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300")
        search = f"{log.get('target_user','')} {log.get('roblox_id','')} {log.get('moderator','')} {ltype} {log.get('reason','')}".lower()
        can_delete = is_manager or (log.get("moderator_id") and log.get("moderator_id") == mod_id)
        delete_form = f"""
                <form action="/log/delete" method="post" onsubmit="return confirm('Diesen Log-Eintrag wirklich löschen?');">
                    <input type="hidden" name="log_id" value="{esc(log.get('id'))}">
                    <button class="text-rose-500 hover:underline font-semibold">🗑️ Löschen</button>
                </form>""" if can_delete else ""
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
            <div class="text-[10px] text-slate-400 pt-1 border-t border-slate-100 dark:border-slate-800/40 flex justify-between items-center">
                <span>Moderator: {esc(log.get('moderator'))}</span>{delete_form}
            </div>
        </div>"""

    chips = "".join(
        f'<button type="button" data-type="{t}" onclick="setTypeFilter(\'{t}\')" class="type-chip {"active" if t == "all" else ""} px-2.5 py-1 rounded-lg text-[11px] font-semibold border border-slate-200 dark:border-slate-700 bg-white dark:bg-[#141824] text-slate-600 dark:text-slate-300">{label}</button>'
        for t, label in [("all", "Alle"), ("Warn", "⚠️ Warn"), ("Kick", "🚪 Kick"), ("Ban", "🚫 Ban"), ("Notiz", "📝 Notiz")])

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
            <div class="{CARD} p-6 space-y-4">
                <div>
                    <h2 class="text-lg font-bold text-slate-900 dark:text-white">Neuen Log eintragen</h2>
                    <p class="text-xs text-slate-500 dark:text-slate-400">Automatische Roblox-Abfrage inkl. Vorstrafen-Check</p>
                </div>
                <form action="/log/create" method="post" class="space-y-4 text-xs">
                    <div>
                        <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Roblox Username *</label>
                        <input type="text" name="target_user" maxlength="50" oninput="lookupRobloxUser(this.value)" placeholder="z. B. Spieler123" required class="{INPUT}">
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
        if len(team_db.get(mod_id, {}).get("warns_list", [])) >= 3:
            return back("/dashboard", "Schicht-Start gesperrt: Du hast 3 oder mehr aktive Verwarnungen!", False)
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
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Log Erstellt", f"Spieler: {target_user} ({log_type})")
    return back("/dashboard", f"{log_type}-Log für {target_user} gespeichert.")


@app.post("/log/delete")
async def delete_log(request: Request, log_id: str = Form(...), user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    logs_db = load_json(LOGS_FILE, [])
    entry = next((l for l in logs_db if l.get("id") == log_id), None)
    if not entry:
        return back("/dashboard", "Log nicht gefunden.", False)
    own = entry.get("moderator_id") and entry.get("moderator_id") == ctx.user["id"]
    if not (own or ctx.perms["can_promote"] or ctx.perms["is_admin"]):
        raise HTTPException(status_code=403, detail="Du darfst nur eigene Logs löschen.")
    save_json(LOGS_FILE, [l for l in logs_db if l.get("id") != log_id])
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Log Gelöscht",
              f"{entry.get('target_user')} ({entry.get('type')}) – ID {log_id}")
    return back("/dashboard", "Log gelöscht.")


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
# ROUTE 2: TEAMLISTE (Wochenziel mit Fortschrittsbalken)
# =============================================================
@app.get("/team", response_class=HTMLResponse)
async def team_list_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    guild = ctx.guild
    weekly_goal = float(ctx.config.get("weekly_goal_hours", 3.0))
    team_role_ids = ctx.config.get("team_role_ids", [])
    shifts_db = load_shifts()
    active = shifts_db["active_shifts"]
    loas = get_loas()

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
            "avatar": member.display_avatar.url, "role": top.name, "pos": top.position,
            "color": role_hex(top),
            "hrs": hrs, "reached": hrs >= weekly_goal, "on_loa": on_loa,
            "loa_until": fmt_date(loa["bis"]) if on_loa else "",
            "duty": active.get(str(member.id), {}).get("status"),
        })
    members.sort(key=lambda m: (-m["pos"], m["name"].lower()))

    below = len([m for m in members if not m["reached"] and not m["on_loa"]])
    summary = (f"{len(members)} Mitglieder · ✅ {len([m for m in members if m['reached']])} Ziel erreicht · "
               f"⚠️ {below} unter Ziel · 🟢 {len([m for m in members if m['duty']])} im Dienst · 🌴 {len([m for m in members if m['on_loa']])} abgemeldet")

    rows_html = ""
    for m in members:
        style = BADGE_WARN if m["on_loa"] else (BADGE_OK if m["reached"] else BADGE_BAD)
        bar_color = "bg-amber-500" if m["on_loa"] else ("bg-emerald-500" if m["reached"] else "bg-rose-500")
        pct = min(100, int(m["hrs"] / weekly_goal * 100)) if weekly_goal > 0 else 100
        loa_badge = (f'<span class="border {BADGE_WARN} text-[10px] px-2.5 py-0.5 rounded-full font-semibold">Abgemeldet bis {esc(m["loa_until"])}</span>'
                     if m["on_loa"] else "")
        duty_badge = ""
        if m["duty"]:
            duty_badge = (f'<span class="border {BADGE_WARN if m["duty"] == "break" else BADGE_OK} text-[10px] px-2.5 py-0.5 rounded-full font-semibold">'
                          f'{"☕ Pause" if m["duty"] == "break" else "🟢 Im Dienst"}</span>')
        rows_html += f"""
        <div class="team-row {CARD} hover:bg-slate-50 dark:hover:bg-[#1a2030] transition px-5 py-4 flex flex-col md:flex-row md:items-center justify-between gap-3"
             data-search="{esc((m['name'] + ' ' + m['username'] + ' ' + m['role']).lower())}" data-below="{1 if (not m['reached'] and not m['on_loa']) else 0}">
            <div class="flex items-center gap-3.5 md:w-1/3 min-w-0">
                <img src="{esc(m['avatar'])}" alt="" class="w-11 h-11 rounded-full border border-slate-200 dark:border-slate-700 shadow-sm">
                <div class="truncate">
                    <div class="font-semibold text-sm text-slate-900 dark:text-white flex items-center gap-2 flex-wrap"><span>{esc(m['name'])}</span>{loa_badge}{duty_badge}</div>
                    <div class="text-xs text-slate-400 font-mono">@{esc(m['username'])}</div>
                </div>
            </div>
            <div class="md:w-1/3 space-y-1.5">
                <div class="flex items-center gap-3 flex-wrap">
                    <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border" style="background-color:{m['color']}15;color:{m['color']};border-color:{m['color']}40;">
                        <span class="w-1.5 h-1.5 rounded-full" style="background-color:{m['color']}"></span>{esc(m['role'])}
                    </span>
                    <span class="text-xs font-mono font-bold px-2.5 py-1 rounded-lg border {style}" title="Soll-Ziel: {weekly_goal}h/Woche">⏱️ {m['hrs']:.1f}h / {weekly_goal:g}h</span>
                </div>
                <div class="h-1.5 rounded-full bg-slate-200 dark:bg-slate-800 overflow-hidden"><div class="h-full {bar_color}" style="width:{pct}%"></div></div>
            </div>
            <div class="md:w-1/6 flex md:justify-end">
                <a href="/member/{m['id']}" class="px-3 py-1.5 bg-slate-100 dark:bg-slate-800 hover:bg-indigo-600 hover:text-white rounded-xl text-slate-600 dark:text-slate-300 text-xs transition font-medium shadow-sm">👁️ Details</a>
            </div>
        </div>"""

    head = """<script>
        function filterTeam() {
            const q = document.getElementById('searchInput').value.toLowerCase();
            const onlyBelow = document.getElementById('onlyBelow').checked;
            document.querySelectorAll('.team-row').forEach(r => {
                const ok = r.dataset.search.includes(q) && (!onlyBelow || r.dataset.below === '1');
                r.style.display = ok ? '' : 'none';
            });
        }
    </script>"""
    body = f"""
    <div class="flex flex-col md:flex-row md:justify-between md:items-center gap-4 mb-6">
        <div>
            <div class="text-xs text-slate-400 mb-1">Team / <span class="text-indigo-600 dark:text-indigo-400 font-medium">Teamliste</span></div>
            <h1 class="text-2xl font-bold text-slate-900 dark:text-white">Teamliste & Wochenziel ({weekly_goal:g}h)</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">{summary}</p>
        </div>
        <div class="flex items-center gap-3">
            <label class="text-xs flex items-center gap-1.5 cursor-pointer text-slate-600 dark:text-slate-300"><input type="checkbox" id="onlyBelow" onchange="filterTeam()" class="rounded"> Nur unter Ziel</label>
            <input type="text" id="searchInput" oninput="filterTeam()" placeholder="Teammitglied suchen..." class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-700 text-xs text-slate-900 dark:text-white rounded-xl px-4 py-2.5 w-56 focus:outline-none focus:border-indigo-500 shadow-sm">
        </div>
    </div>
    <div class="space-y-3">{rows_html or f"<div class='text-center py-12 text-slate-400 text-sm {CARD}'>Keine Teammitglieder gefunden. Team-Rollen kannst du unter Einstellungen festlegen.</div>"}</div>"""
    return render_page("Teamliste", ctx, "team", body, head)


# =============================================================
# ROUTE: MITGLIEDER-DETAILSEITE
# =============================================================
@app.get("/member/{user_id}", response_class=HTMLResponse)
async def member_detail(request: Request, user_id: int, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    guild = ctx.guild
    member = guild.get_member(user_id)
    if not member:
        raise HTTPException(status_code=404, detail=f"Mitglied mit ID {user_id} wurde nicht gefunden.")

    team_role_ids = ctx.config.get("team_role_ids", [])
    team_db = load_json(DATA_FILE, {})
    shifts_db = load_shifts()
    info = team_db.get(str(user_id), {"warns_list": [], "notes": [], "ticket_cases": 0, "support_cases": 0})
    warns = info.get("warns_list", [])
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
        actions_html = f"""
        <h3 class="text-sm font-bold text-slate-900 dark:text-white">Team-Aktionen</h3>
        <form action="/action" method="post" class="flex flex-wrap gap-2">
            <input type="hidden" name="user_id" value="{member.id}"><input type="hidden" name="redirect_to_member" value="1">
            <button name="action" value="promote" onclick="return confirm('Wirklich befördern?')" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">⬆️️ Befördern</button>
            <button name="action" value="demote" onclick="return confirm('Wirklich degradieren?')" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">⬇️ Degradieren</button>
            <button name="action" value="kick" onclick="return confirm('Dieses Mitglied wirklich vom gesamten Discord-Server kicken?')" class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">🚪 Vom Server kicken</button>
        </form>
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

    warn_color = "text-rose-500" if len(warns) >= 3 else "text-amber-500"
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
                <div class="{CARD} p-5"><div class="text-xs text-slate-400 font-semibold mb-1">Verwarnungen</div><div class="text-2xl font-bold {warn_color}">{len(warns)}/3</div></div>
            </div>
            <div class="{CARD} p-6 space-y-4">
                {actions_html}{warn_form}
                <h3 class="text-sm font-bold text-slate-900 dark:text-white">Notizen</h3>
                <div class="space-y-2 max-h-36 overflow-y-auto">{notes_html or "<p class='text-xs text-slate-400 italic'>Keine Notizen hinterlegt.</p>"}</div>
                {note_form}
            </div>
            <div class="{CARD} p-6 space-y-3">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white">Verwarnungs-Historie ({len(warns)})</h3>
                <div class="space-y-2 max-h-64 overflow-y-auto">{warns_html or "<p class='text-xs text-slate-400 italic'>Keine Verwarnungen vorhanden.</p>"}</div>
            </div>
        </div>
        <div class="space-y-4">
            <div class="text-right text-xl font-extrabold uppercase tracking-widest opacity-90 break-words" style="color:{top_color};">» {esc(ctx.guild_name)} ✕ {esc(top.name)}</div>
            <div class="{CARD} p-6 space-y-4 text-xs">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white border-b border-slate-100 dark:border-slate-800 pb-2">Information</h3>
                <div><div class="text-slate-400 mb-0.5">Nutzername</div><div class="font-medium">[{esc(top.name)}] {esc(member.display_name)}</div></div>
                <div><div class="text-slate-400 mb-0.5">ID</div><div class="text-slate-600 dark:text-slate-300 font-mono">{member.id}</div></div>
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
# ROUTE: ÖFFENTLICHES BEWERBUNGSFORMULAR & BEWERBUNGS-SYSTEM
# =============================================================
@app.get("/apply", response_class=HTMLResponse)
async def public_apply_page(apply_session: str = Cookie(None)):
    applicant = verify_payload(apply_session) if apply_session else None
    if applicant and applicant.get("apply_exp", 0) < time.time():
        applicant = None

    if not applicant:
        return HTMLResponse(f"""<!DOCTYPE html>
        <html lang="de">
        <head>{get_head_html("Bochum RP - Team Bewerbung")}</head>
        <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-900 dark:text-white min-h-screen flex items-center justify-center p-4 font-sans">
            <div class="{CARD} p-8 shadow-xl w-full max-w-md text-center space-y-6">
                <div class="w-12 h-12 mx-auto rounded-2xl bg-indigo-600 flex items-center justify-center text-2xl shadow-lg shadow-indigo-600/30">📝</div>
                <div>
                    <h1 class="text-2xl font-bold">Team-Bewerbung</h1>
                    <p class="text-xs text-slate-500 dark:text-slate-400 mt-2">Melde dich mit deinem Discord-Account an, um eine Bewerbung einzureichen.</p>
                </div>
                <a href="/apply/login" class="inline-flex items-center justify-center gap-2 w-full bg-[#5865F2] hover:bg-[#4752C4] text-white font-semibold py-3 px-4 rounded-xl transition shadow-lg shadow-[#5865F2]/20 text-sm">
                    Mit Discord anmelden & bewerben
                </a>
                <a href="/" class="block text-xs text-slate-400 hover:underline">Zurück zur Startseite</a>
            </div>
        </body>
        </html>""")

    return HTMLResponse(f"""<!DOCTYPE html>
    <html lang="de">
    <head>{get_head_html("Bochum RP - Bewerbungsformular")}</head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-900 dark:text-white min-h-screen p-4 md:p-8 font-sans flex items-center justify-center">
        <div class="{CARD} p-8 shadow-xl w-full max-w-2xl space-y-6">
            <div class="flex items-center justify-between border-b border-slate-200 dark:border-slate-800 pb-4">
                <div>
                    <h1 class="text-xl font-bold">Bewerbung als Teammitglied</h1>
                    <p class="text-xs text-slate-400">Eingeloggt als @{esc(applicant.get('username'))}</p>
                </div>
                <a href="/logout" class="text-xs text-rose-500 hover:underline">Abmelden</a>
            </div>
            <form action="/apply/submit" method="post" class="space-y-4 text-xs">
                <div>
                    <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Alter *</label>
                    <input type="number" name="age" min="12" max="99" required class="{INPUT}">
                </div>
                <div>
                    <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Roblox Username *</label>
                    <input type="text" name="roblox_name" maxlength="50" required class="{INPUT}">
                </div>
                <div>
                    <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Erfahrung im Bereich Moderation / RP *</label>
                    <textarea name="experience" maxlength="2000" placeholder="Erzähle uns von deinen Erfahrungen..." required class="{INPUT} h-28"></textarea>
                </div>
                <div>
                    <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Warum möchtest du ins Team? *</label>
                    <textarea name="motivation" maxlength="2000" placeholder="Deine Motivation..." required class="{INPUT} h-28"></textarea>
                </div>
                <div>
                    <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Wöchentliche Aktivität (Stunden) *</label>
                    <input type="number" name="weekly_time" min="1" max="100" required class="{INPUT}">
                </div>
                <button class="w-full {BTN} py-3">Bewerbung absenden</button>
            </form>
        </div>
    </body>
    </html>""")


@app.get("/apply/login")
async def apply_login():
    state = secrets.token_urlsafe(24)
    url = (f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}"
           f"&redirect_uri={quote(APPLICATION_REDIRECT_URI, safe='')}&response_type=code&scope=identify&state={state}")
    response = RedirectResponse(url=url, status_code=303)
    response.set_cookie("oauth_state_apply", state, max_age=600, httponly=True, samesite="lax", secure=COOKIE_SECURE)
    return response


@app.get("/apply/callback")
async def apply_callback(request: Request, code: str = None, state: str = None, error: str = None, oauth_state_apply: str = Cookie(None)):
    retry = '<a href="/apply" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Erneut versuchen</a>'
    if error or not code or not state or not oauth_state_apply or not hmac.compare_digest(state, oauth_state_apply):
        return HTMLResponse(simple_page("❌", "Login fehlgeschlagen", "Sicherheits-Token ungültig oder Login abgebrochen.", retry), status_code=400)

    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://discord.com/api/v10/oauth2/token",
            data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "grant_type": "authorization_code",
                  "code": code, "redirect_uri": APPLICATION_REDIRECT_URI},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        access_token = token_res.json().get("access_token")
        if not access_token:
            return HTMLResponse(simple_page("❌", "Login fehlgeschlagen", "Discord OAuth Fehler.", retry))
        user_data = (await client.get("https://discord.com/api/v10/users/@me",
                                      headers={"Authorization": f"Bearer {access_token}"})).json()

    session = {
        "id": str(user_data.get("id")),
        "username": user_data.get("username"),
        "global_name": user_data.get("global_name") or user_data.get("username"),
        "apply_exp": int(time.time() + 3600),
    }
    response = RedirectResponse(url="/apply", status_code=303)
    response.set_cookie("apply_session", sign_payload(session), httponly=True, samesite="lax", secure=COOKIE_SECURE, max_age=3600)
    response.delete_cookie("oauth_state_apply")
    return response


@app.post("/apply/submit")
async def apply_submit(request: Request, age: int = Form(...), roblox_name: str = Form(...),
                       experience: str = Form(...), motivation: str = Form(...), weekly_time: int = Form(...),
                       apply_session: str = Cookie(None)):
    applicant = verify_payload(apply_session) if apply_session else None
    if not applicant:
        return RedirectResponse(url="/apply", status_code=303)

    apps_db = load_json(APPS_FILE, {})
    app_id = f"app_{uuid.uuid4().hex[:6]}"
    apps_db[app_id] = {
        "id": app_id,
        "user_id": applicant["id"],
        "username": applicant["username"],
        "global_name": applicant.get("global_name", applicant["username"]),
        "age": age,
        "roblox_name": roblox_name.strip(),
        "experience": experience.strip(),
        "motivation": motivation.strip(),
        "weekly_time": weekly_time,
        "status": "pending",
        "created_at": now_de().strftime("%d.%m.%Y %H:%M"),
    }
    save_json(APPS_FILE, apps_db)

    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    if guild:
        await send_team_update_embed(guild, "📝 Neue Team-Bewerbung",
                                     f"Eine neue Bewerbung von **{applicant.get('global_name')}** (@{applicant['username']}) ist eingegangen!",
                                     discord.Color.purple())

    return HTMLResponse(simple_page("✅", "Bewerbung eingereicht!",
                                    "Vielen Dank für deine Bewerbung. Unser Team wird sie in Kürze prüfen.",
                                    '<a href="/" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Zur Startseite</a>'))


@app.get("/applications", response_class=HTMLResponse)
async def applications_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm="can_manage_applications")
    apps_db = load_json(APPS_FILE, {})

    items_html = ""
    for app_id, a in reversed(list(apps_db.items())):
        status = a.get("status", "pending")
        st_badge = BADGE_WARN if status == "pending" else (BADGE_OK if status == "accepted" else BADGE_BAD)
        st_text = "Offen" if status == "pending" else ("Angenommen" if status == "accepted" else "Abgelehnt")

        actions = ""
        if status == "pending":
            actions = f"""
            <form action="/action" method="post" class="flex gap-2 mt-3">
                <input type="hidden" name="action" value="review_application">
                <input type="hidden" name="app_id" value="{esc(app_id)}">
                <button name="app_status" value="accepted" onclick="return confirm('Bewerbung annehmen?')" class="bg-emerald-600 hover:bg-emerald-500 text-white px-3 py-1.5 rounded-xl font-semibold text-xs">Annehmen</button>
                <button name="app_status" value="rejected" onclick="return confirm('Bewerbung ablehnen?')" class="bg-rose-600 hover:bg-rose-500 text-white px-3 py-1.5 rounded-xl font-semibold text-xs">Ablehnen</button>
            </form>"""

        items_html += f"""
        <div class="{CARD} p-5 space-y-3">
            <div class="flex justify-between items-center border-b border-slate-100 dark:border-slate-800 pb-3">
                <div>
                    <h3 class="font-bold text-slate-900 dark:text-white text-sm">{esc(a.get('global_name'))} (@{esc(a.get('username'))})</h3>
                    <div class="text-[10px] text-slate-400">Roblox: {esc(a.get('roblox_name'))} | Alter: {a.get('age')} | Zeit/Woche: {a.get('weekly_time')}h | {esc(a.get('created_at'))}</div>
                </div>
                <span class="px-2.5 py-1 rounded-full text-xs font-semibold border {st_badge}">{st_text}</span>
            </div>
            <div class="text-xs space-y-2 text-slate-700 dark:text-slate-300">
                <div><strong>Erfahrung:</strong> <p class="whitespace-pre-line mt-0.5">{esc(a.get('experience'))}</p></div>
                <div><strong>Motivation:</strong> <p class="whitespace-pre-line mt-0.5">{esc(a.get('motivation'))}</p></div>
            </div>
            {actions}
        </div>"""

    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">📝 Bewerbungsverwaltung</h1>
    <div class="space-y-4 max-w-4xl">
        {items_html or f"<div class='text-xs text-slate-400 italic {CARD} p-6'>Keine Bewerbungen vorhanden.</div>"}
    </div>"""
    return render_page("Bewerbungen", ctx, "apps", body)


# =============================================================
# ZENTRALER HANDLER FÜR AKTIONEN (/action)
# =============================================================
@app.post("/action")
async def handle_general_action(
    request: Request,
    action: str = Form(...),
    user_id: str = Form(None),
    redirect_to_member: str = Form(None),
    warn_reason: str = Form(None),
    warn_proof: str = Form(None),
    warn_id: str = Form(None),
    note_text: str = Form(None),
    meeting_title: str = Form(None),
    meeting_datetime: str = Form(None),
    meeting_desc: str = Form(None),
    reset_rsvps: str = Form(None),
    clear_topics: str = Form(None),
    announce: str = Form(None),
    rsvp_status: str = Form(None),
    topic_title: str = Form(None),
    topic_details: str = Form(None),
    topic_id: str = Form(None),
    loa_start: str = Form(None),
    loa_end: str = Form(None),
    loa_reason: str = Form(None),
    target_user_id: str = Form(None),
    app_id: str = Form(None),
    app_status: str = Form(None),
    user_session: str = Cookie(None)
):
    ctx = auth(request, user_session)
    guild = ctx.guild
    mod_id = ctx.user["id"]
    mod_name = ctx.user.get("global_name", "Dashboard Admin")
    redir_url = f"/member/{user_id}" if (redirect_to_member and user_id) else "/team"

    team_db = load_json(DATA_FILE, {})

    if action == "warn_with_proof":
        if not ctx.perms["can_warn"]:
            raise HTTPException(status_code=403, detail="Fehlende Berechtigung für Verwarnungen.")
        if not user_id or not warn_reason:
            return back(redir_url, "Grund erforderlich.", False)
        target_info = team_db.setdefault(str(user_id), {"warns_list": [], "notes": [], "ticket_cases": 0, "support_cases": 0})
        w_item = {
            "id": f"warn_{uuid.uuid4().hex[:6]}",
            "reason": warn_reason.strip()[:500],
            "proof": safe_url(warn_proof),
            "by": mod_name,
            "by_id": mod_id,
            "date": now_de().strftime("%d.%m.%Y %H:%M")
        }
        target_info["warns_list"].append(w_item)
        save_json(DATA_FILE, team_db)
        log_audit(mod_name, mod_id, "Verwarnung Ausgestellt", f"User {user_id}: {warn_reason}")

        member = guild.get_member(int(user_id))
        if member:
            await sync_warn_roles(guild, member, len(target_info["warns_list"]))
            await send_dm_notification(member, f"⚠️ **Verwarnung erhalten** auf {guild.name}\nGrund: {warn_reason}")

        return back(redir_url, "Verwarnung erfolgreich eingetragen.")

    elif action == "remove_warn":
        if not ctx.perms["can_warn"]:
            raise HTTPException(status_code=403, detail="Fehlende Berechtigung.")
        target_info = team_db.get(str(user_id), {})
        warns = target_info.get("warns_list", [])
        target_info["warns_list"] = [w for w in warns if w.get("id") != warn_id]
        save_json(DATA_FILE, team_db)
        log_audit(mod_name, mod_id, "Verwarnung Zurückgezogen", f"User {user_id}, Warn {warn_id}")

        member = guild.get_member(int(user_id))
        if member:
            await sync_warn_roles(guild, member, len(target_info["warns_list"]))

        return back(redir_url, "Verwarnung zurückgezogen.")

    elif action == "add_note":
        if not ctx.perms["can_add_notes"]:
            raise HTTPException(status_code=403, detail="Fehlende Berechtigung.")
        if not user_id or not note_text:
            return back(redir_url, "Notiztext erforderlich.", False)
        target_info = team_db.setdefault(str(user_id), {"warns_list": [], "notes": [], "ticket_cases": 0, "support_cases": 0})
        target_info.setdefault("notes", []).append(f"[{now_de().strftime('%d.%m.%Y')}] {mod_name}: {note_text.strip()[:500]}")
        save_json(DATA_FILE, team_db)
        return back(redir_url, "Notiz gespeichert.")

    elif action in ("promote", "demote"):
        if not ctx.perms["can_promote"]:
            raise HTTPException(status_code=403, detail="Fehlende Berechtigung.")
        member = guild.get_member(int(user_id))
        if not member:
            return back(redir_url, "Mitglied nicht auf dem Server.", False)
        team_role_ids = ctx.config.get("team_role_ids", [])
        member_roles = [r for r in member.roles if r.id in team_role_ids]
        if not member_roles:
            return back(redir_url, "Mitglied hat keine verwaltete Teamrolle.", False)

        current_top = max(member_roles, key=lambda r: r.position)
        curr_idx = team_role_ids.index(current_top.id) if current_top.id in team_role_ids else -1
        if curr_idx == -1:
            return back(redir_url, "Rolle ist nicht in der Rangfolge konfiguriert.", False)

        new_idx = curr_idx + 1 if action == "promote" else curr_idx - 1
        if new_idx < 0 or new_idx >= len(team_role_ids):
            return back(redir_url, f"Kann nicht weiter {'befördert' if action == 'promote' else 'degradiert'} werden.", False)

        old_role = guild.get_role(team_role_ids[curr_idx])
        new_role = guild.get_role(team_role_ids[new_idx])
        if old_role:
            await member.remove_roles(old_role, reason=f"Team-Management ({action})")
        if new_role:
            await member.add_roles(new_role, reason=f"Team-Management ({action})")

        lbl = "befördert" if action == "promote" else "degradiert"
        log_audit(mod_name, mod_id, f"Team Rolle Geändert ({action})", f"{member.display_name} -> {new_role.name if new_role else new_idx}")
        await send_team_update_embed(guild, f"⚡ Team-Rang Änderung", f"**{member.display_name}** wurde zu **{new_role.name if new_role else 'Neuer Rang'}** {lbl}!", discord.Color.gold())
        return back(redir_url, f"{member.display_name} wurde {lbl}.")

    elif action == "kick":
        if not ctx.perms["can_promote"]:
            raise HTTPException(status_code=403, detail="Fehlende Berechtigung.")
        member = guild.get_member(int(user_id))
        if member:
            await member.kick(reason=f"Gekickt durch Dashboard ({mod_name})")
            log_audit(mod_name, mod_id, "Mitglied Gekickt", f"User {user_id}")
            return back("/team", f"{member.display_name} wurde vom Server gekickt.")
        return back("/team", "Mitglied nicht gefunden.", False)

    elif action == "set_meeting_info":
        if not (ctx.perms["can_promote"] or ctx.perms["is_admin"]):
            raise HTTPException(status_code=403, detail="Fehlende Berechtigung.")
        m = load_meetings()
        m["title"] = meeting_title.strip()[:100]
        m["date_time"] = meeting_datetime.strip()[:100]
        m["description"] = (meeting_desc or "").strip()[:500]
        if reset_rsvps == "on":
            m["rsvps"] = {}
        if clear_topics == "on":
            m["topics"] = []
        save_json(MEETINGS_FILE, m)
        if announce == "on":
            await send_team_update_embed(guild, f"🎙️ {m['title']}", f"📅 **Termin:** {m['date_time']}\n\n{m['description']}\n\nBitte Rückmeldung im Dashboard geben!", discord.Color.blue())
        return back("/meetings", "Teambesprechung aktualisiert.")

    elif action == "meeting_rsvp":
        m = load_meetings()
        if rsvp_status in ("accepted", "declined"):
            m["rsvps"][mod_id] = {
                "name": mod_name,
                "status": rsvp_status,
                "time": now_de().strftime("%d.%m.%Y %H:%M")
            }
            save_json(MEETINGS_FILE, m)
        return back("/meetings", "Status gespeichert.")

    elif action == "add_meeting_topic":
        m = load_meetings()
        if topic_title and topic_details:
            m["topics"].append({
                "id": f"topic_{uuid.uuid4().hex[:6]}",
                "title": topic_title.strip()[:100],
                "details": topic_details.strip()[:600],
                "by": mod_name,
                "by_id": mod_id
            })
            save_json(MEETINGS_FILE, m)
        return back("/meetings", "Thema eingereicht.")

    elif action == "delete_meeting_topic":
        m = load_meetings()
        m["topics"] = [t for t in m["topics"] if t.get("id") != topic_id]
        save_json(MEETINGS_FILE, m)
        return back("/meetings", "Thema entfernt.")

    elif action == "submit_loa":
        uid = target_user_id or mod_id
        if not (ctx.perms["can_promote"] or ctx.perms["is_admin"] or str(uid) == mod_id):
            raise HTTPException(status_code=403, detail="Nur für dich selbst oder als Manager erlaubt.")
        conn = sqlite3.connect(DB_ABMELDUNGEN)
        target_member = guild.get_member(int(uid))
        name = target_member.display_name if target_member else f"User {uid}"
        conn.execute("""
            INSERT OR REPLACE INTO abmeldungen (user_id, user_name, grund, von, bis, original_nick, guild_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (int(uid), name, loa_reason.strip()[:300], loa_start, loa_end, name, guild.id))
        conn.commit()
        conn.close()
        log_audit(mod_name, mod_id, "Abmeldung Eingetragen", f"User {uid} ({loa_start} bis {loa_end})")
        return back("/loa", "Abmeldung erfolgreich gespeichert.")

    elif action == "cancel_loa":
        uid = target_user_id or mod_id
        if not (ctx.perms["can_promote"] or ctx.perms["is_admin"] or str(uid) == mod_id):
            raise HTTPException(status_code=403, detail="Fehlende Berechtigung.")
        conn = sqlite3.connect(DB_ABMELDUNGEN)
        conn.execute("DELETE FROM abmeldungen WHERE user_id = ?", (int(uid),))
        conn.commit()
        conn.close()
        log_audit(mod_name, mod_id, "Abmeldung Beendet", f"User {uid}")
        return back("/loa", "Abmeldung entfernt.")

    elif action == "review_application":
        if not ctx.perms["can_manage_applications"]:
            raise HTTPException(status_code=403, detail="Fehlende Berechtigung.")
        apps_db = load_json(APPS_FILE, {})
        if app_id in apps_db and app_status in ("accepted", "rejected"):
            apps_db[app_id]["status"] = app_status
            apps_db[app_id]["reviewed_by"] = mod_name
            save_json(APPS_FILE, apps_db)
            st_text = "angenommen" if app_status == "accepted" else "abgelehnt"
            log_audit(mod_name, mod_id, f"Bewerbung {st_text.capitalize()}", f"App ID {app_id}")
            return back("/applications", f"Bewerbung wurde {st_text}.")

    return back("/dashboard", "Aktion ausgeführt.")


# =============================================================
# WEITERE ROUTEN: INBOX, AUFGABEN, TICKETS, WIKI, KALENDER, ETC.
# =============================================================
@app.get("/pulse-inbox", response_class=HTMLResponse)
async def pulse_inbox_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    unread = 0
    try:
        unread = pulse_db.unread_count(ctx.user["id"])
    except Exception:
        pass
    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">📥 Pulse Inbox</h1>
    <div class="{CARD} p-6 space-y-4 max-w-3xl">
        <div class="flex items-center justify-between border-b border-slate-100 dark:border-slate-800 pb-3">
            <span class="text-xs font-semibold text-slate-500">Ungelesene Nachrichten: {unread}</span>
        </div>
        <div class="text-xs text-slate-500 dark:text-slate-400 py-8 text-center">
            Keine neuen Inbox-Benachrichtigungen vorhanden.
        </div>
    </div>"""
    return render_page("Pulse Inbox", ctx, "inbox", body)


@app.get("/tasks", response_class=HTMLResponse)
async def tasks_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">📋 Team-Aufgaben</h1>
    <div class="{CARD} p-6 max-w-3xl space-y-4">
        <p class="text-xs text-slate-500 dark:text-slate-400">Aufgabenverwaltung für das Server-Team.</p>
        <div class="text-xs text-slate-400 italic py-6 text-center border border-dashed border-slate-200 dark:border-slate-800 rounded-xl">
            Derzeit sind keine offenen Aufgaben zugewiesen.
        </div>
    </div>"""
    return render_page("Aufgaben", ctx, "tasks", body)


@app.get("/tickets", response_class=HTMLResponse)
async def tickets_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">🎫 Ticket-Verwaltung</h1>
    <div class="{CARD} p-6 max-w-4xl space-y-4">
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
            {stat_card("🎫", "Offene Tickets", 0, "indigo")}
            {stat_card("✅", "Bearbeitet heute", 0, "emerald")}
            {stat_card("⏱️", "Ø Antwortzeit", "0m", "amber")}
        </div>
        <div class="text-xs text-slate-400 italic py-8 text-center">
            Keine aktiven Tickets im Support-System.
        </div>
    </div>"""
    return render_page("Tickets", ctx, "tickets", body)


@app.get("/meetings-history", response_class=HTMLResponse)
async def meetings_history_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    meetings = load_meetings()
    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">🗂️ Meeting-Historie</h1>
    <div class="{CARD} p-6 max-w-3xl space-y-4">
        <h2 class="text-sm font-bold text-slate-900 dark:text-white">{esc(meetings.get('title'))}</h2>
        <p class="text-xs text-slate-500 font-mono">📅 {esc(meetings.get('date_time'))}</p>
        <p class="text-xs text-slate-600 dark:text-slate-300">{esc(meetings.get('description'))}</p>
    </div>"""
    return render_page("Meeting-Historie", ctx, "meeting_history", body)


@app.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">🗓️ Team-Kalender</h1>
    <div class="{CARD} p-6 max-w-4xl">
        <p class="text-xs text-slate-500 dark:text-slate-400 mb-4">Übersicht aller anstehenden Server-Events und Teambesprechungen.</p>
        <div class="bg-slate-50 dark:bg-[#0b0e14] p-4 rounded-xl border border-slate-200 dark:border-slate-800 text-xs text-center text-slate-400">
            Aktuell sind keine weiteren Events eingetragen.
        </div>
    </div>"""
    return render_page("Team-Kalender", ctx, "calendar", body)


@app.get("/wiki", response_class=HTMLResponse)
async def wiki_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">📚 Team-Wiki & Dokumentation</h1>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div class="{CARD} p-6 space-y-2">
            <h2 class="font-bold text-slate-900 dark:text-white text-sm">📖 Richtlinien für Moderatoren</h2>
            <p class="text-xs text-slate-500">Regeln für Support, Kicks, Bans und Verwarnungen auf dem Server.</p>
        </div>
        <div class="{CARD} p-6 space-y-2">
            <h2 class="font-bold text-slate-900 dark:text-white text-sm">🚨 Notfall-Prozeduren</h2>
            <p class="text-xs text-slate-500">Verhalten bei Server-Abstürzen oder Trolling-Angriffen.</p>
        </div>
    </div>"""
    return render_page("Team-Wiki", ctx, "wiki", body)


@app.get("/training", response_class=HTMLResponse)
async def training_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">🎓 Schulungen & Testphase</h1>
    <div class="{CARD} p-6 max-w-3xl space-y-3">
        <h2 class="text-sm font-bold">Team-Einarbeitung</h2>
        <p class="text-xs text-slate-500 dark:text-slate-400">Hier finden Test-Moderatoren alle wichtigen Informationen für die Einarbeitungsphase.</p>
    </div>"""
    return render_page("Schulungen", ctx, "training", body)


@app.get("/achievements", response_class=HTMLResponse)
async def achievements_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">🏅 Achievements & Auszeichnungen</h1>
    <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
        <div class="{CARD} p-5 space-y-2">
            <div class="text-3xl">🚀</div>
            <div class="font-bold text-sm">Erste Schicht</div>
            <div class="text-xs text-slate-400">Absolviere deine erste Mod-Schicht im Panel.</div>
        </div>
        <div class="{CARD} p-5 space-y-2">
            <div class="text-3xl">🏆</div>
            <div class="font-bold text-sm">Top Performer</div>
            <div class="text-xs text-slate-400">Erreiche 10+ Stunden in einer Woche.</div>
        </div>
        <div class="{CARD} p-5 space-y-2">
            <div class="text-3xl">🛡️</div>
            <div class="font-bold text-sm">Protokoll-Meister</div>
            <div class="text-xs text-slate-400">Erstelle mehr als 25 Logs.</div>
        </div>
    </div>"""
    return render_page("Achievements", ctx, "achievements", body)


@app.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    logs_db = load_json(LOGS_FILE, [])
    shifts_db = load_shifts()
    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">📊 Statistiken & Analysen</h1>
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        {stat_card("📜", "Gesamte Logs", len(logs_db), "indigo")}
        {stat_card("⏱️", "Schichten Historie", len(shifts_db.get("history", [])), "emerald")}
        {stat_card("👥", "Aktive im Dienst", len(shifts_db.get("active_shifts", {})), "amber")}
    </div>"""
    return render_page("Statistiken", ctx, "stats", body)


@app.get("/backups", response_class=HTMLResponse)
async def backups_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, admin=True)
    files = os.listdir(BACKUP_DIR) if os.path.exists(BACKUP_DIR) else []
    items_html = "".join(f"<div class='p-3 bg-slate-50 dark:bg-[#0b0e14] rounded-xl border border-slate-200 dark:border-slate-800 text-xs font-mono'>{esc(f)}</div>" for f in files)
    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">💾 Server Backups</h1>
    <div class="{CARD} p-6 max-w-3xl space-y-4">
        <h2 class="text-sm font-bold">Vorhandene Sicherungen ({len(files)})</h2>
        <div class="space-y-2">{items_html or "<div class='text-xs text-slate-400 italic'>Noch keine Backups im Ordner.</div>"}</div>
    </div>"""
    return render_page("Server Backups", ctx, "backups", body)


@app.get("/system", response_class=HTMLResponse)
async def system_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, admin=True)
    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">🩺 Systemstatus</h1>
    <div class="{CARD} p-6 max-w-3xl space-y-4">
        <div class="flex items-center justify-between text-xs border-b border-slate-100 dark:border-slate-800 pb-2">
            <span>FastAPI Status</span><span class="text-emerald-500 font-bold">🟢 Operational</span>
        </div>
        <div class="flex items-center justify-between text-xs border-b border-slate-100 dark:border-slate-800 pb-2">
            <span>Discord Bot Status</span><span class="text-emerald-500 font-bold">🟢 Verbunden</span>
        </div>
        <div class="flex items-center justify-between text-xs">
            <span>Datenbank (SQLite)</span><span class="text-emerald-500 font-bold">🟢 OK</span>
        </div>
    </div>"""
    return render_page("Systemstatus", ctx, "system", body)


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = "", user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    q_clean = q.strip().lower()
    logs_db = load_json(LOGS_FILE, [])
    results = [l for l in logs_db if q_clean and (q_clean in str(l.get("target_user","")).lower() or q_clean in str(l.get("reason","")).lower() or q_clean in str(l.get("roblox_id","")).lower())]

    res_html = "".join(f"<div class='p-3 bg-slate-50 dark:bg-[#0b0e14] rounded-xl border border-slate-200 dark:border-slate-800 text-xs'><strong>{esc(l.get('target_user'))}</strong> ({esc(l.get('type'))}): {esc(l.get('reason'))}</div>" for l in results)
    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">🔎 Globale Suche</h1>
    <div class="{CARD} p-6 max-w-3xl space-y-4">
        <form action="/search" method="get" class="flex gap-2">
            <input type="text" name="q" value="{esc(q)}" placeholder="Suchbegriff eingeben..." class="{INPUT}">
            <button class="{BTN} px-5">Suchen</button>
        </form>
        <div class="space-y-2 pt-2">{res_html or ("<div class='text-xs text-slate-400 italic'>Keine Ergebnisse gefunden.</div>" if q_clean else "<div class='text-xs text-slate-400'>Gib oben einen Suchbegriff ein.</div>")}</div>
    </div>"""
    return render_page("Globale Suche", ctx, "search", body)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, admin=True)
    audit_data = load_json(AUDIT_FILE, [])
    audit_html = "".join(f"""
        <div class="text-xs bg-slate-50 dark:bg-[#0b0e14] p-3 rounded-xl border border-slate-200 dark:border-slate-800 flex justify-between items-center">
            <div><span class="font-bold">{esc(a.get('actor'))}:</span> {esc(a.get('action'))} - {esc(a.get('details'))}</div>
            <span class="text-[10px] text-slate-400 font-mono">{esc(a.get('timestamp'))}</span>
        </div>""" for a in reversed(audit_data[-30:]))
    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">⚙️ Einstellungen & Audit-Log</h1>
    <div class="{CARD} p-6 space-y-4 max-w-4xl">
        <h2 class="text-sm font-bold text-slate-900 dark:text-white">📜 Audit-Log (Letzte Aktionen)</h2>
        <div class="space-y-2 max-h-96 overflow-y-auto">{audit_html or "<div class='text-xs text-slate-400 italic'>Keine Log-Einträge vorhanden.</div>"}</div>
    </div>"""
    return render_page("Einstellungen", ctx, "settings", body)


# =============================================================
# START-EINSTIEGSPUNKT
# =============================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "25095"))
    uvicorn.run(app, host="0.0.0.0", port=port)
