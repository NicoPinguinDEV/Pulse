import csv
import io
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, Cookie, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse
import httpx
import discord

load_dotenv()

# =============================================================
# KONFIGURATION & UMGEBUNGSVARIABELN
# =============================================================
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "http://fi4.bot-hosting.cloud:25095/callback")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "1474514929351524616"))
TEAM_UPDATE_CHANNEL_NAME = os.getenv("TEAM_UPDATE_CHANNEL_NAME", "╚『⚡』𝐓𝐞𝐚𝐦-𝐔𝐩𝐝𝐚𝐭𝐞𝐬")

WARN_ROLE_IDS = {
    1: int(os.getenv("WARN_ROLE_1", "1489221948348043395")),
    2: int(os.getenv("WARN_ROLE_2", "1489222076370780232")),
    3: int(os.getenv("WARN_ROLE_3", "1531760107971416135"))
}

DATA_FILE = "team_data.json"
CONFIG_FILE = "config.json"
APPS_FILE = "applications.json"
SHIFTS_FILE = "shifts.json"
LOGS_FILE = "logs.json"
AUDIT_FILE = "audit_logs.json"
MEETINGS_FILE = "meetings.json"
DB_ABMELDUNGEN = "abmeldungen.db"
BACKUP_DIR = "backups"

if not os.path.exists(BACKUP_DIR):
    os.makedirs(BACKUP_DIR)

app = FastAPI()

DISCORD_AUTH_URL = (
    f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds"
)

# =============================================================
# DATENBANK-INITIALISIERUNG
# =============================================================
def init_db():
    conn = sqlite3.connect(DB_ABMELDUNGEN)
    cursor = conn.cursor()
    cursor.execute("""
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
# HELFER-FUNKTIONEN
# =============================================================
def load_json(filepath, default):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def log_audit(actor_name: str, actor_id: str, action: str, details: str):
    """Protokolliert Aktionen im Panel-Audit-Log."""
    audit_data = load_json(AUDIT_FILE, [])
    audit_data.append({
        "id": f"audit_{uuid.uuid4().hex[:6]}",
        "actor": actor_name,
        "actor_id": str(actor_id),
        "action": action,
        "details": details,
        "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    })
    save_json(AUDIT_FILE, audit_data)


def get_current_user(user_session: str = Cookie(None)) -> dict:
    """Liest die Session-Daten des angemeldeten Discord-Nutzers aus."""
    if not user_session:
        raise HTTPException(status_code=303, headers={"Location": "/"})
    try:
        return json.loads(user_session)
    except Exception:
        raise HTTPException(status_code=303, headers={"Location": "/"})


def calculate_weekly_hours(mod_id_str, shifts_history):
    """Berechnet die Arbeitsstunden der aktuellen Woche aus der Shift-Historie."""
    total_seconds = 0
    now = datetime.now()
    start_of_week = now - timedelta(days=now.weekday())
    start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)

    for entry in shifts_history:
        if str(entry.get("mod_id")) == str(mod_id_str):
            try:
                entry_date = datetime.strptime(entry.get("date"), "%Y-%m-%d")
                if entry_date >= start_of_week:
                    total_seconds += entry.get("duration_seconds", 0)
            except Exception:
                pass
    
    hours = total_seconds / 3600
    return f"{hours:.1f}h"


async def send_dm_notification(user_or_member, message: str, embed: discord.Embed = None):
    """Versendet eine automatische Direktnachricht per Discord Bot."""
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


async def send_team_update_embed(guild, title, description, color=discord.Color.blue()):
    """Sendet ein Embed in den eingestellten Team-Updates Kanal."""
    if not guild:
        return
    channel = discord.utils.get(guild.text_channels, name=TEAM_UPDATE_CHANNEL_NAME)
    if channel:
        try:
            embed = discord.Embed(title=title, description=description, color=color)
            embed.set_footer(text=f"{guild.name} • Team-Updates System")
            embed.timestamp = datetime.now()
            await channel.send(embed=embed)
        except Exception as e:
            print(f"Fehler beim Senden des Team-Updates in Discord: {e}")


def get_head_html(title: str):
    return f"""
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{ darkMode: 'class' }}
        if (localStorage.theme === 'dark' || (!('theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches)) {{
            document.documentElement.classList.add('dark');
        }} else {{
            document.documentElement.classList.remove('dark');
        }}
        function toggleTheme() {{
            if (document.documentElement.classList.contains('dark')) {{
                document.documentElement.classList.remove('dark');
                localStorage.theme = 'light';
            }} else {{
                document.documentElement.classList.add('dark');
                localStorage.theme = 'dark';
            }}
        }}
    </script>
    """


def get_sidebar_html(guild_name, current_page="dashboard", current_user=None):
    user_name = current_user.get("global_name", "Team Mitglied") if current_user else "Gast"
    avatar_id = current_user.get("avatar") if current_user else None
    user_id = current_user.get("id") if current_user else None
    
    if avatar_id and user_id:
        avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_id}.png"
    else:
        avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"

    return f"""
    <aside class="w-64 bg-white dark:bg-[#141824] border-r border-slate-200 dark:border-slate-800/80 flex flex-col justify-between p-4 min-h-screen shrink-0 transition-colors duration-200">
        <div class="space-y-6">
            <div class="flex items-center gap-3 px-2">
                <div class="w-9 h-9 rounded-xl bg-indigo-600 flex items-center justify-center font-bold text-white shadow-md shadow-indigo-600/20">B</div>
                <div>
                    <h2 class="font-bold text-slate-900 dark:text-white leading-none">{guild_name}</h2>
                    <span class="text-[10px] text-slate-500 dark:text-slate-400 font-mono">v2.4.0 Pro</span>
                </div>
            </div>

            <div class="bg-slate-100 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-800 rounded-xl p-2.5 flex items-center justify-between shadow-inner">
                <div class="flex items-center gap-2 truncate">
                    <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                    <span class="text-xs font-semibold text-slate-700 dark:text-slate-200 truncate">{guild_name}</span>
                </div>
                <span class="text-xs text-slate-400">▾</span>
            </div>

            <nav class="space-y-1 text-xs">
                <div class="text-[10px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider px-2 mb-2">Hauptmenü</div>
                <a href="/dashboard" class="flex items-center gap-2.5 px-3 py-2.5 rounded-xl {'bg-indigo-600/10 text-indigo-600 dark:text-indigo-400 font-semibold border border-indigo-500/20 shadow-sm' if current_page == 'dashboard' else 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800/50 hover:text-slate-900 dark:hover:text-slate-200'} transition">
                    ⚡ <span>Moderatoren-Panel</span>
                </a>
                <a href="/team" class="flex items-center gap-2.5 px-3 py-2.5 rounded-xl {'bg-indigo-600/10 text-indigo-600 dark:text-indigo-400 font-semibold border border-indigo-500/20 shadow-sm' if current_page == 'team' else 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800/50 hover:text-slate-900 dark:hover:text-slate-200'} transition">
                    👥 <span>Teamliste</span>
                </a>
                <a href="/meetings" class="flex items-center gap-2.5 px-3 py-2.5 rounded-xl {'bg-indigo-600/10 text-indigo-600 dark:text-indigo-400 font-semibold border border-indigo-500/20 shadow-sm' if current_page == 'meetings' else 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800/50 hover:text-slate-900 dark:hover:text-slate-200'} transition">
                    🎙️ <span>Teambesprechung</span>
                </a>
                <a href="/loa" class="flex items-center gap-2.5 px-3 py-2.5 rounded-xl {'bg-indigo-600/10 text-indigo-600 dark:text-indigo-400 font-semibold border border-indigo-500/20 shadow-sm' if current_page == 'loa' else 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800/50 hover:text-slate-900 dark:hover:text-slate-200'} transition">
                    🌴 <span>Abmeldungen (LOA)</span>
                </a>
                <a href="/applications" class="flex items-center gap-2.5 px-3 py-2.5 rounded-xl {'bg-indigo-600/10 text-indigo-600 dark:text-indigo-400 font-semibold border border-indigo-500/20 shadow-sm' if current_page == 'apps' else 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800/50 hover:text-slate-900 dark:hover:text-slate-200'} transition">
                    📋 <span>Bewerbungen</span>
                </a>
                
                <div class="text-[10px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider px-2 mt-5 mb-2">Verwaltung</div>
                <a href="/backups" class="flex items-center gap-2.5 px-3 py-2.5 rounded-xl {'bg-indigo-600/10 text-indigo-600 dark:text-indigo-400 font-semibold border border-indigo-500/20 shadow-sm' if current_page == 'backups' else 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800/50 hover:text-slate-900 dark:hover:text-slate-200'} transition">
                    💾 <span>Server Backups</span>
                </a>
                <a href="/settings" class="flex items-center gap-2.5 px-3 py-2.5 rounded-xl {'bg-indigo-600/10 text-indigo-600 dark:text-indigo-400 font-semibold border border-indigo-500/20 shadow-sm' if current_page == 'settings' else 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800/50 hover:text-slate-900 dark:hover:text-slate-200'} transition">
                    ⚙️ <span>Rechte & Audit-Log</span>
                </a>
            </nav>
        </div>

        <div class="border-t border-slate-200 dark:border-slate-800/80 pt-4 px-1 space-y-3">
            <button onclick="toggleTheme()" class="w-full flex items-center justify-between px-3 py-2.5 rounded-xl bg-slate-100 dark:bg-slate-800/60 hover:bg-slate-200 dark:hover:bg-slate-800 text-slate-700 dark:text-slate-300 text-xs font-medium transition shadow-sm">
                <span class="flex items-center gap-2">
                    <span class="dark:hidden">🌙 Dark Mode</span>
                    <span class="hidden dark:inline">☀️ Light Mode</span>
                </span>
                <span class="text-[10px] px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-700 font-mono text-slate-600 dark:text-slate-300">Umschalten</span>
            </button>

            <div class="flex items-center justify-between pt-1">
                <div class="flex items-center gap-2.5 truncate">
                    <img src="{avatar_url}" class="w-7 h-7 rounded-full border border-slate-200 dark:border-slate-700 object-cover">
                    <span class="text-xs font-medium text-slate-700 dark:text-slate-300 truncate">{user_name}</span>
                </div>
                <a href="/logout" class="text-xs text-slate-400 hover:text-rose-500 dark:hover:text-rose-400 transition font-medium">↤ Abmelden</a>
            </div>
        </div>
    </aside>
    """

# =============================================================
# ROUTEN: LOGIN, LOGOUT & OAUTH CALLBACK
# =============================================================
@app.get("/", response_class=HTMLResponse)
async def home():
    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        {get_head_html("Bochum RP Panel Login")}
    </head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-900 dark:text-white min-h-screen flex items-center justify-center p-4 font-sans transition-colors duration-200">
        <div class="absolute top-6 right-6">
            <button onclick="toggleTheme()" class="px-3 py-2 rounded-xl bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 text-xs shadow-sm font-medium">
                <span class="dark:hidden">🌙 Dark</span>
                <span class="hidden dark:inline">☀️ Light</span>
            </button>
        </div>

        <div class="bg-white dark:bg-[#141824] p-8 rounded-2xl shadow-xl w-full max-w-md text-center border border-slate-200 dark:border-slate-800">
            <div class="flex justify-center items-center gap-3 mb-4">
                <div class="w-12 h-12 rounded-2xl bg-indigo-600 flex items-center justify-center text-2xl shadow-lg shadow-indigo-600/30">🛡️</div>
            </div>
            <h1 class="text-2xl font-bold tracking-tight mb-2">Team Management Panel</h1>
            <p class="text-slate-500 dark:text-slate-400 text-xs mb-8">Melde dich mit deinem Discord-Account an, um Zugriff zu erhalten.</p>
            
            <a href="{DISCORD_AUTH_URL}" class="inline-flex items-center justify-center gap-3 w-full bg-[#5865F2] hover:bg-[#4752C4] text-white font-semibold py-3 px-4 rounded-xl transition shadow-lg shadow-[#5865F2]/20 text-sm">
                Mit Discord anmelden
            </a>

            <div class="mt-6 pt-6 border-t border-slate-100 dark:border-slate-800">
                <a href="/apply" class="text-xs text-indigo-600 dark:text-indigo-400 hover:underline font-medium">Du möchtest dich ins Team bewerben? Hier klicken!</a>
            </div>
        </div>
    </body>
    </html>
    """

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key="user_session")
    return response


@app.get("/callback")
async def callback(code: str):
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://discord.com/api/v10/oauth2/token",
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_data = token_res.json()
        access_token = token_data.get("access_token")

        if not access_token:
            return HTMLResponse("<h2>Login fehlgeschlagen.</h2><a href='/'>Erneut versuchen</a>")

        user_res = await client.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        user_data = user_res.json()

    user_session = {
        "id": str(user_data.get("id")),
        "username": user_data.get("username"),
        "global_name": user_data.get("global_name") or user_data.get("username"),
        "avatar": user_data.get("avatar")
    }

    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="user_session", value=json.dumps(user_session), httponly=True)
    return response

# =============================================================
# ROUTE 1: HAUPT-DASHBOARD (Inkl. Live Logs-Filter)
# =============================================================
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_main(request: Request, user_session: str = Cookie(None)):
    current_user = get_current_user(user_session)
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    guild_name = guild.name if guild else "Bochum RP"

    shifts_db = load_json(SHIFTS_FILE, {"active_shifts": {}, "history": []})
    logs_db = load_json(LOGS_FILE, [])

    active_shifts = shifts_db.get("active_shifts", {})
    active_staff_count = len([s for s in active_shifts.values() if s.get("status") in ["online", "break"]])

    mod_id = current_user["id"]
    current_shift = active_shifts.get(mod_id)
    started_at_iso = current_shift.get("started_at_iso") if current_shift else ""
    shift_status = current_shift.get("status") if current_shift else "offline"

    config = load_json(CONFIG_FILE, {"team_role_ids": [], "permissions": {}})
    team_role_ids = config.get("team_role_ids", [])
    
    leaderboard_data = []
    if guild:
        for member in guild.members:
            if any(r.id in team_role_ids for r in member.roles):
                hrs = calculate_weekly_hours(str(member.id), shifts_db.get("history", []))
                try:
                    float_hrs = float(hrs.replace("h", ""))
                except Exception:
                    float_hrs = 0.0
                leaderboard_data.append({"name": member.display_name, "hours": hrs, "val": float_hrs})
        leaderboard_data.sort(key=lambda x: x["val"], reverse=True)

    leaderboard_html = ""
    for idx, user in enumerate(leaderboard_data[:3], 1):
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        leaderboard_html += f"""
        <div class="flex items-center justify-between bg-slate-50 dark:bg-[#0b0e14] px-3.5 py-2.5 rounded-xl border border-slate-200 dark:border-slate-800 text-xs">
            <div class="flex items-center gap-2">
                <span class="font-bold text-sm">{medals.get(idx, '•')}</span>
                <span class="text-slate-700 dark:text-slate-200 font-medium truncate max-w-[120px]">{user['name']}</span>
            </div>
            <span class="text-indigo-600 dark:text-indigo-400 font-mono font-bold">{user['hours']}</span>
        </div>
        """

    logs_html = ""
    for log in reversed(logs_db[-30:]):
        type_colors = {
            "Ban": "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30",
            "Kick": "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30",
            "Warn": "bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 border-yellow-500/30",
            "Notiz": "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/30",
        }
        badge_style = type_colors.get(log.get("type"), "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300")
        search_data = f"{log.get('target_user','')} {log.get('roblox_id','')} {log.get('moderator','')} {log.get('type','')}".lower()

        logs_html += f"""
        <div class="log-card bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800/80 rounded-2xl p-4 space-y-2 shadow-sm transition-all hover:shadow" data-search="{search_data}">
            <div class="flex items-center justify-between border-b border-slate-100 dark:border-slate-800/60 pb-2">
                <div class="flex items-center gap-2">
                    <span class="px-2.5 py-0.5 rounded-full text-[10px] font-bold border {badge_style}">
                        {log.get('type', 'Log')}
                    </span>
                    <span class="text-xs font-semibold text-slate-900 dark:text-white">{log.get('target_user')}</span>
                </div>
                <span class="text-[10px] text-slate-400 font-mono">{log.get('created_at')}</span>
            </div>
            <div class="text-xs text-slate-600 dark:text-slate-300 space-y-1">
                <div><span class="text-slate-400">Roblox ID:</span> <span class="font-mono text-slate-800 dark:text-slate-200">{log.get('roblox_id', 'N/A')}</span></div>
                <div><span class="text-slate-400">Grund:</span> <span class="text-slate-700 dark:text-slate-200">{log.get('reason')}</span></div>
            </div>
            <div class="text-[10px] text-slate-400 pt-1 border-t border-slate-100 dark:border-slate-800/40 flex justify-between">
                <span>Moderator: {log.get('moderator')}</span>
            </div>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        {get_head_html(f"Moderatoren-Panel - {guild_name}")}
        <script>
            document.addEventListener("DOMContentLoaded", function() {{
                const shiftStartTime = "{started_at_iso}";
                const shiftStatus = "{shift_status}";
                
                if (shiftStartTime && shiftStatus !== "offline") {{
                    const startDate = new Date(shiftStartTime);
                    setInterval(() => {{
                        const now = new Date();
                        const diff = Math.floor((now - startDate) / 1000);
                        if (diff >= 0) {{
                            const hours = Math.floor(diff / 3600);
                            const minutes = Math.floor((diff % 3600) / 60);
                            const seconds = diff % 60;
                            const timerEl = document.getElementById("liveShiftTimer");
                            if (timerEl) {{
                                timerEl.innerText = `${{hours}}h ${{minutes}}m ${{seconds}}s`;
                            }}
                        }}
                    }}, 1000);
                }}
            }});

            function filterLogs() {{
                let input = document.getElementById('logSearch').value.toLowerCase();
                let cards = document.getElementsByClassName('log-card');
                for (let card of cards) {{
                    let search = card.getAttribute('data-search');
                    if (search.includes(input)) {{
                        card.style.display = "";
                    }} else {{
                        card.style.display = "none";
                    }}
                }}
            }}
        </script>
    </head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-800 dark:text-slate-200 font-sans min-h-screen flex transition-colors duration-200">
        {get_sidebar_html(guild_name, 'dashboard', current_user)}

        <main class="flex-1 p-8 overflow-y-auto">
            <div class="grid grid-cols-1 lg:grid-cols-12 gap-8">
                
                <div class="lg:col-span-4 space-y-6">
                    <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl p-6 shadow-sm space-y-5">
                        <div class="flex items-center justify-between">
                            <div>
                                <h2 class="text-lg font-bold text-slate-900 dark:text-white">Schicht-Steuerung</h2>
                                <p class="text-xs text-slate-500 dark:text-slate-400">Erfasse deine Arbeitszeit live</p>
                            </div>
                            <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
                                <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                                {active_staff_count} im Dienst
                            </span>
                        </div>

                        <div class="bg-slate-50 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-800 rounded-xl p-3 flex items-center justify-between text-xs">
                            <span class="flex items-center gap-2 font-medium">
                                <span class="w-2 h-2 rounded-full {'bg-emerald-500 animate-pulse' if shift_status != 'offline' else 'bg-slate-400'}"></span>
                                {shift_status.upper() if shift_status != 'offline' else 'OFFLINE'}
                            </span>
                            <span id="liveShiftTimer" class="font-mono font-bold text-indigo-600 dark:text-indigo-400">
                                {('0h 0m 0s' if shift_status == 'offline' else 'Läuft...')}
                            </span>
                        </div>

                        <form action="/shift/action" method="post" class="grid grid-cols-2 gap-3">
                            <button name="shift_action" value="start" class="col-span-2 bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-3 px-4 rounded-xl transition shadow-md shadow-emerald-900/10 text-xs">
                                ▶️ Schicht Starten
                            </button>
                            <button name="shift_action" value="break" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 font-semibold py-2.5 px-3 rounded-xl transition text-xs">
                                ⏸️ Pause
                            </button>
                            <button name="shift_action" value="end" class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 font-semibold py-2.5 px-3 rounded-xl transition text-xs">
                                ⏹️ Beenden
                            </button>
                        </form>
                    </div>

                    <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl p-6 shadow-sm space-y-3">
                        <h3 class="text-sm font-bold text-slate-900 dark:text-white mb-2">🏆 Wochen-Aktivität (Top 3)</h3>
                        <div class="space-y-2">
                            {leaderboard_html or "<div class='text-xs text-slate-400 italic'>Noch keine Daten vorhanden.</div>"}
                        </div>
                    </div>
                </div>

                <div class="lg:col-span-4 space-y-6">
                    <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl p-6 shadow-sm space-y-4">
                        <div>
                            <h2 class="text-lg font-bold text-slate-900 dark:text-white">Neuen Log eintragen</h2>
                            <p class="text-xs text-slate-500 dark:text-slate-400">Strafe oder Notiz für Roblox-Spieler festhalten</p>
                        </div>

                        <form action="/log/create" method="post" class="space-y-4 text-xs">
                            <div>
                                <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Roblox Username *</label>
                                <input type="text" name="target_user" placeholder="z. B. Spieler123" required class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-3 text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500">
                            </div>

                            <div>
                                <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Roblox Player ID (Optional)</label>
                                <input type="text" name="roblox_id" placeholder="z. B. 12345678" class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-3 text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500">
                            </div>

                            <div>
                                <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Typ der Strafe *</label>
                                <select name="log_type" required class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-3 text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500">
                                    <option value="Warn">⚠️ Verwarnung (Warn)</option>
                                    <option value="Kick">🚪 Kick</option>
                                    <option value="Ban">🚫 Ban</option>
                                    <option value="Notiz">📝 Notiz / Hinweis</option>
                                </select>
                            </div>

                            <div>
                                <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Begründung *</label>
                                <textarea name="reason" placeholder="Grund hier eingeben..." required class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-3 text-slate-900 dark:text-white h-24 focus:outline-none focus:border-indigo-500"></textarea>
                            </div>

                            <button class="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-bold py-3 rounded-xl transition shadow-md shadow-indigo-600/20">
                                Log Speichern
                            </button>
                        </form>
                    </div>
                </div>

                <div class="lg:col-span-4 space-y-4">
                    <div class="flex items-center justify-between">
                        <h2 class="text-lg font-bold text-slate-900 dark:text-white">Protokoll (Punishment Logs)</h2>
                        <div class="flex items-center gap-2">
                            <a href="/export/logs" class="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-700 text-xs px-2.5 py-1.5 rounded-lg text-slate-700 dark:text-slate-300 transition shadow-sm">📊 CSV Export</a>
                            <span class="text-xs text-slate-400 font-mono">{len(logs_db)} Gesamt</span>
                        </div>
                    </div>

                    <input type="text" id="logSearch" onkeyup="filterLogs()" placeholder="🔎 Logs filtern nach Name, ID oder Mod..." class="w-full bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-xl px-3.5 py-2 text-xs text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500 shadow-sm mb-2">

                    <div class="space-y-3 max-h-[calc(100vh-220px)] overflow-y-auto pr-1">
                        {logs_html or "<div class='text-xs text-slate-400 italic bg-white dark:bg-[#141824] p-6 rounded-2xl border border-slate-200 dark:border-slate-800 text-center shadow-sm'>Noch keine Logs eingetragen.</div>"}
                    </div>
                </div>

            </div>
        </main>
    </body>
    </html>
    """

# =============================================================
# ROUTE 2: TEAMLISTE (Inkl. Wochenziel-Aktivität Farbkennzeichnung)
# =============================================================
@app.get("/team", response_class=HTMLResponse)
async def team_list_page(request: Request, user_session: str = Cookie(None)):
    current_user = get_current_user(user_session)
    bot = getattr(request.app.state, "bot", None)
    if not bot:
        return "<h3>Bot-Instanz noch nicht bereit!</h3>"

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return f"<h3>Fehler: Server {GUILD_ID} nicht gefunden.</h3>"
    
    guild_name = guild.name

    config = load_json(CONFIG_FILE, {"team_role_ids": [], "weekly_goal_hours": 3.0, "permissions": {}})
    weekly_goal = float(config.get("weekly_goal_hours", 3.0))

    shifts_db = load_json(SHIFTS_FILE, {"active_shifts": {}, "history": []})
    team_role_ids = config.get("team_role_ids", [])

    active_loas = {}
    if os.path.exists(DB_ABMELDUNGEN):
        conn = sqlite3.connect(DB_ABMELDUNGEN)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, bis FROM abmeldungen")
        for row in cursor.fetchall():
            active_loas[str(row[0])] = row[1]
        conn.close()

    team_members = []
    for member in guild.members:
        member_team_roles = [r for r in member.roles if r.id in team_role_ids]

        if member_team_roles:
            highest_role = max(member_team_roles, key=lambda r: r.position)

            loa_badge = ""
            user_id_str = str(member.id)
            if user_id_str in active_loas:
                end_date_str = active_loas[user_id_str]
                loa_badge = f'<span class="bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/30 text-[10px] px-2.5 py-0.5 rounded-full font-semibold">Abgemeldet bis {end_date_str}</span>'

            weekly_hours_str = calculate_weekly_hours(user_id_str, shifts_db.get("history", []))
            try:
                hrs_val = float(weekly_hours_str.replace("h", ""))
            except Exception:
                hrs_val = 0.0

            reached_goal = hrs_val >= weekly_goal
            hours_badge_style = "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30" if reached_goal else "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30"

            team_members.append({
                "id": member.id,
                "name": member.display_name,
                "username": member.name,
                "avatar": member.display_avatar.url,
                "top_role": highest_role.name,
                "top_role_color": f"#{highest_role.color.value:06x}" if highest_role.color.value else "#6366f1",
                "role_position": highest_role.position,
                "loa_badge": loa_badge,
                "weekly_hours": weekly_hours_str,
                "hours_badge_style": hours_badge_style,
                "reached_goal": reached_goal
            })

    team_members.sort(key=lambda m: m["role_position"], reverse=True)

    rows_html = ""
    for m in team_members:
        rows_html += f"""
        <div class="team-row bg-white dark:bg-[#141824] hover:bg-slate-50 dark:hover:bg-[#1a2030] transition border border-slate-200 dark:border-slate-800/80 rounded-2xl px-5 py-4 flex items-center justify-between shadow-sm" data-name="{m['name'].lower()}" data-username="{m['username'].lower()}" data-role="{m['top_role'].lower()}">
            <div class="flex items-center gap-3.5 w-1/3">
                <img src="{m['avatar']}" class="w-11 h-11 rounded-full border border-slate-200 dark:border-slate-700 shadow-sm">
                <div class="truncate">
                    <div class="font-semibold text-sm text-slate-900 dark:text-white flex items-center gap-2 flex-wrap">
                        <span>{m['name']}</span>
                        {m['loa_badge']}
                    </div>
                    <div class="text-xs text-slate-400 font-mono">@{m['username']}</div>
                </div>
            </div>

            <div class="w-1/3 flex items-center gap-3">
                <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border shadow-sm" style="background-color: {m['top_role_color']}15; color: {m['top_role_color']}; border-color: {m['top_role_color']}40;">
                    <span class="w-1.5 h-1.5 rounded-full" style="background-color: {m['top_role_color']}"></span>
                    {m['top_role']}
                </span>
                <span class="text-xs font-mono font-bold px-2.5 py-1 rounded-lg border {m['hours_badge_style']}" title="Soll-Ziel: {weekly_goal}h/Woche">
                    ⏱️ {m['weekly_hours']} / {weekly_goal}h
                </span>
            </div>

            <div class="w-1/6 flex items-center justify-end">
                <a href="/member/{m['id']}" title="Profil ansehen" class="px-3 py-1.5 bg-slate-100 dark:bg-slate-800 hover:bg-indigo-600 hover:text-white rounded-xl text-slate-600 dark:text-slate-300 text-xs transition font-medium flex items-center gap-1.5 shadow-sm">
                    👁️ Details
                </a>
            </div>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        {get_head_html(f"Teamliste - {guild_name}")}
        <script>
            function filterTeam() {{
                let input = document.getElementById('searchInput').value.toLowerCase();
                let rows = document.getElementsByClassName('team-row');
                for (let row of rows) {{
                    let name = row.getAttribute('data-name');
                    let uname = row.getAttribute('data-username');
                    let role = row.getAttribute('data-role');
                    if (name.includes(input) || uname.includes(input) || role.includes(input)) {{
                        row.style.display = "";
                    }} else {{
                        row.style.display = "none";
                    }}
                }}
            }}
        </script>
    </head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-800 dark:text-slate-200 font-sans min-h-screen flex transition-colors duration-200">
        {get_sidebar_html(guild_name, 'team', current_user)}
        <main class="flex-1 p-8 overflow-y-auto">
            <div class="flex justify-between items-center mb-6">
                <div>
                    <div class="text-xs text-slate-400 flex items-center gap-1.5 mb-1">
                        <span>Team</span> / <span class="text-indigo-600 dark:text-indigo-400 font-medium">Teamliste</span>
                    </div>
                    <h1 class="text-2xl font-bold text-slate-900 dark:text-white">Teamliste & Wochenziel ({weekly_goal}h)</h1>
                </div>
                <div>
                    <input type="text" id="searchInput" onkeyup="filterTeam()" placeholder="Teammitglied suchen..." class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-700 text-xs text-slate-900 dark:text-white rounded-xl px-4 py-2.5 w-64 focus:outline-none focus:border-indigo-500 shadow-sm">
                </div>
            </div>

            <div class="space-y-3" id="teamContainer">
                {rows_html or "<div class='text-center py-12 text-slate-400 text-sm bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl shadow-sm'>Keine Teammitglieder gefunden.</div>"}
            </div>
        </main>
    </body>
    </html>
    """

# =============================================================
# ROUTE: TEAM-BESPRECHUNGS-TOOL
# =============================================================
@app.get("/meetings", response_class=HTMLResponse)
async def meetings_page(request: Request, user_session: str = Cookie(None)):
    current_user = get_current_user(user_session)
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    guild_name = guild.name if guild else "Bochum RP"

    meetings_data = load_json(MEETINGS_FILE, {
        "title": "Nächste Teambesprechung",
        "date_time": "Noch nicht angesetzt",
        "description": "Hier können wichtige Punkte für die kommende Besprechung gesammelt werden.",
        "rsvps": {},
        "topics": []
    })

    user_id = current_user["id"]
    user_status = meetings_data["rsvps"].get(user_id, {}).get("status", "none")

    accepted_list = [v.get("name") for k, v in meetings_data["rsvps"].items() if v.get("status") == "accepted"]
    declined_list = [v.get("name") for k, v in meetings_data["rsvps"].items() if v.get("status") == "declined"]

    accepted_html = "".join([f"<li class='text-emerald-600 dark:text-emerald-400'>• {name}</li>" for name in accepted_list])
    declined_html = "".join([f"<li class='text-rose-600 dark:text-rose-400'>• {name}</li>" for name in declined_list])

    topics_html = ""
    for topic in meetings_data.get("topics", []):
        topics_html += f"""
        <div class="bg-slate-50 dark:bg-[#0b0e14] p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 text-xs space-y-1">
            <div class="flex justify-between font-semibold text-slate-900 dark:text-white">
                <span>📌 {topic.get('title')}</span>
                <span class="text-[10px] text-slate-400 font-mono">Von: {topic.get('by')}</span>
            </div>
            <p class="text-slate-600 dark:text-slate-300">{topic.get('details')}</p>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        {get_head_html("Teambesprechung")}
    </head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-800 dark:text-slate-200 font-sans min-h-screen flex transition-colors duration-200">
        {get_sidebar_html(guild_name, 'meetings', current_user)}
        <main class="flex-1 p-8 overflow-y-auto">
            <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">🎙️ Team-Besprechungs-Tool</h1>

            <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                
                <div class="lg:col-span-2 space-y-6">
                    <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl p-6 shadow-sm space-y-4">
                        <div class="flex justify-between items-start border-b border-slate-100 dark:border-slate-800 pb-4">
                            <div>
                                <h2 class="text-lg font-bold text-slate-900 dark:text-white">{meetings_data.get('title')}</h2>
                                <p class="text-xs text-indigo-600 dark:text-indigo-400 font-mono mt-1">📅 Datum & Uhrzeit: {meetings_data.get('date_time')}</p>
                            </div>
                            <span class="bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-500/20 text-xs px-3 py-1 rounded-full font-semibold">Anstehend</span>
                        </div>
                        <p class="text-xs text-slate-600 dark:text-slate-300">{meetings_data.get('description')}</p>

                        <div class="pt-2">
                            <label class="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-2">Dein Status für die Besprechung:</label>
                            <form action="/action" method="post" class="flex gap-3">
                                <input type="hidden" name="action" value="meeting_rsvp">
                                <button name="rsvp_status" value="accepted" class="{'bg-emerald-600 text-white font-bold' if user_status == 'accepted' else 'bg-emerald-500/10 text-emerald-600 border border-emerald-500/30'} px-4 py-2 rounded-xl text-xs transition">✅ Zusage</button>
                                <button name="rsvp_status" value="declined" class="{'bg-rose-600 text-white font-bold' if user_status == 'declined' else 'bg-rose-500/10 text-rose-600 border border-rose-500/30'} px-4 py-2 rounded-xl text-xs transition">❌ Absage</button>
                            </form>
                        </div>
                    </div>

                    <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl p-6 shadow-sm space-y-4">
                        <h3 class="text-sm font-bold text-slate-900 dark:text-white">💡 Themenvorschläge einreichen</h3>
                        <form action="/action" method="post" class="space-y-3 text-xs">
                            <input type="hidden" name="action" value="add_meeting_topic">
                            <input type="text" name="topic_title" placeholder="Thema / Titel..." required class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-3 text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500">
                            <textarea name="topic_details" placeholder="Beschreibung / Details zum Thema..." required class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-3 text-slate-900 dark:text-white h-20 focus:outline-none focus:border-indigo-500"></textarea>
                            <button class="bg-indigo-600 hover:bg-indigo-500 text-white font-semibold px-4 py-2.5 rounded-xl shadow-sm transition">Thema auf Tagesordnung setzen</button>
                        </form>

                        <div class="pt-4 border-t border-slate-100 dark:border-slate-800 space-y-2">
                            <h4 class="text-xs font-bold text-slate-900 dark:text-white">Eingereichte Themenvorschläge ({len(meetings_data.get('topics', []))})</h4>
                            <div class="space-y-2 max-h-56 overflow-y-auto">
                                {topics_html or "<p class='text-xs text-slate-400 italic'>Noch keine Themenvorschläge eingereicht.</p>"}
                            </div>
                        </div>
                    </div>
                </div>

                <div class="space-y-6">
                    <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl p-6 shadow-sm space-y-4 text-xs">
                        <h3 class="text-sm font-bold text-slate-900 dark:text-white border-b border-slate-100 dark:border-slate-800 pb-2">Teilnehmer-Übersicht</h3>
                        
                        <div>
                            <div class="font-bold text-emerald-600 dark:text-emerald-400 mb-1">Zugesagt ({len(accepted_list)})</div>
                            <ul class="space-y-1">
                                {accepted_html or "<li class='text-slate-400 italic'>Niemand</li>"}
                            </ul>
                        </div>

                        <hr class="border-slate-100 dark:border-slate-800">

                        <div>
                            <div class="font-bold text-rose-600 dark:text-rose-400 mb-1">Abgesagt ({len(declined_list)})</div>
                            <ul class="space-y-1">
                                {declined_html or "<li class='text-slate-400 italic'>Niemand</li>"}
                            </ul>
                        </div>
                    </div>

                    <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl p-6 shadow-sm space-y-3 text-xs">
                        <h3 class="text-sm font-bold text-slate-900 dark:text-white">⚙️ Besprechung ansetzen (Admin)</h3>
                        <form action="/action" method="post" class="space-y-3">
                            <input type="hidden" name="action" value="set_meeting_info">
                            <input type="text" name="meeting_title" placeholder="Titel..." required class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-2.5 text-slate-900 dark:text-white">
                            <input type="text" name="meeting_datetime" placeholder="z. B. Sonntag, 18:00 Uhr" required class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-2.5 text-slate-900 dark:text-white">
                            <textarea name="meeting_desc" placeholder="Kurze Beschreibung..." class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-2.5 text-slate-900 dark:text-white h-16"></textarea>
                            <button class="w-full bg-indigo-600 hover:bg-indigo-500 font-semibold py-2 rounded-xl text-white shadow-sm transition">Besprechung Aktualisieren</button>
                        </form>
                    </div>
                </div>

            </div>
        </main>
    </body>
    </html>
    """

# =============================================================
# ROUTE: DISCORD BACKUP SYSTEM
# =============================================================
@app.get("/backups", response_class=HTMLResponse)
async def backups_page(request: Request, user_session: str = Cookie(None)):
    current_user = get_current_user(user_session)
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    guild_name = guild.name if guild else "Bochum RP"

    backup_files = []
    if os.path.exists(BACKUP_DIR):
        backup_files = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith(".json")], reverse=True)

    backups_html = ""
    for filename in backup_files:
        filepath = os.path.join(BACKUP_DIR, filename)
        file_size = round(os.path.getsize(filepath) / 1024, 1)
        backups_html += f"""
        <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl p-4.5 flex items-center justify-between shadow-sm">
            <div>
                <div class="font-bold text-slate-900 dark:text-white text-sm font-mono">{filename}</div>
                <div class="text-xs text-slate-400 mt-0.5">Größe: {file_size} KB</div>
            </div>
            <div class="flex items-center gap-2">
                <a href="/backup/download/{filename}" class="bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-xs px-3.5 py-2 rounded-xl text-slate-700 dark:text-slate-300 font-medium transition shadow-sm">📥 Herunterladen</a>
                <form action="/backup/delete" method="post" onsubmit="return confirm('Backup wirklich löschen?');">
                    <input type="hidden" name="filename" value="{filename}">
                    <button class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-xs px-3.5 py-2 rounded-xl font-medium transition">🗑️ Löschen</button>
                </form>
            </div>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        {get_head_html(f"Server Backups - {guild_name}")}
    </head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-800 dark:text-slate-200 font-sans min-h-screen flex transition-colors duration-200">
        {get_sidebar_html(guild_name, 'backups', current_user)}
        <main class="flex-1 p-8 overflow-y-auto">
            <div class="flex justify-between items-center mb-6">
                <div>
                    <h1 class="text-2xl font-bold text-slate-900 dark:text-white">Discord Server Backups</h1>
                    <p class="text-xs text-slate-500 dark:text-slate-400">Erstelle Sicherheitskopien von Kanälen, Rollen und Einstellungen.</p>
                </div>
                <form action="/backup/create" method="post">
                    <button class="bg-indigo-600 hover:bg-indigo-500 text-white font-bold text-xs py-3 px-5 rounded-xl transition shadow-md shadow-indigo-600/20">
                        💾 Neues Backup erstellen
                    </button>
                </form>
            </div>

            <div class="space-y-3 max-w-3xl">
                {backups_html or "<div class='text-xs text-slate-400 italic bg-white dark:bg-[#141824] p-6 rounded-2xl border border-slate-200 dark:border-slate-800 text-center shadow-sm'>Noch keine Backups vorhanden. Erstelle jetzt dein erstes Backup!</div>"}
            </div>
        </main>
    </body>
    </html>
    """


@app.post("/backup/create")
async def create_backup(user_session: str = Cookie(None)):
    current_user = get_current_user(user_session)
    bot = getattr(app.state, "bot", None)
    if not bot:
        return RedirectResponse(url="/backups", status_code=303)
    
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return RedirectResponse(url="/backups", status_code=303)

    backup_data = {
        "guild_name": guild.name,
        "guild_id": guild.id,
        "created_at": datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
        "roles": [],
        "categories": [],
        "channels": []
    }

    for role in guild.roles:
        if role.is_default():
            continue
        backup_data["roles"].append({
            "name": role.name,
            "color": role.color.value,
            "permissions": role.permissions.value,
            "hoist": role.hoist,
            "position": role.position
        })

    for category in guild.categories:
        cat_channels = []
        for ch in category.channels:
            cat_channels.append({
                "name": ch.name,
                "type": str(ch.type),
                "topic": getattr(ch, "topic", None)
            })
        backup_data["categories"].append({
            "name": category.name,
            "channels": cat_channels
        })

    filename = f"backup_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json"
    filepath = os.path.join(BACKUP_DIR, filename)
    save_json(filepath, backup_data)

    log_audit(current_user.get("global_name"), current_user.get("id"), "Backup Erstellt", f"Filename: {filename}")
    return RedirectResponse(url="/backups", status_code=303)


@app.get("/backup/download/{filename}")
async def download_backup(filename: str, user_session: str = Cookie(None)):
    get_current_user(user_session)
    filepath = os.path.join(BACKUP_DIR, filename)
    if os.path.exists(filepath):
        return FileResponse(filepath, media_type='application/json', filename=filename)
    raise HTTPException(status_code=404, detail="Backup nicht gefunden.")


@app.post("/backup/delete")
async def delete_backup(filename: str = Form(...), user_session: str = Cookie(None)):
    current_user = get_current_user(user_session)
    filepath = os.path.join(BACKUP_DIR, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        log_audit(current_user.get("global_name"), current_user.get("id"), "Backup Gelöscht", f"Filename: {filename}")
    return RedirectResponse(url="/backups", status_code=303)

# =============================================================
# ROUTE: MITGLIEDER-DETAILSEITE
# =============================================================
@app.get("/member/{user_id}", response_class=HTMLResponse)
async def member_detail(request: Request, user_id: int, user_session: str = Cookie(None)):
    current_user = get_current_user(user_session)
    bot = getattr(request.app.state, "bot", None)
    if not bot:
        return "<h3>Bot-Instanz noch nicht bereit!</h3>"

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return f"<h3>Fehler: Server {GUILD_ID} nicht gefunden.</h3>"

    guild_name = guild.name
    member = guild.get_member(user_id)
    if not member:
        return f"<h3>Mitglied mit ID {user_id} wurde nicht gefunden.</h3>"

    config = load_json(CONFIG_FILE, {"team_role_ids": [], "permissions": {}})
    team_db = load_json(DATA_FILE, {})
    shifts_db = load_json(SHIFTS_FILE, {"active_shifts": {}, "history": []})
    team_role_ids = config.get("team_role_ids", [])

    user_info = team_db.get(str(user_id), {
        "warns_list": [],
        "notes": [],
        "ticket_cases": 0,
        "support_cases": 0
    })

    weekly_hours = calculate_weekly_hours(str(user_id), shifts_db.get("history", []))

    member_team_roles = [r for r in member.roles if r.id in team_role_ids]
    highest_role = max(member_team_roles, key=lambda r: r.position) if member_team_roles else member.top_role

    top_role_name = highest_role.name
    top_role_color = f"#{highest_role.color.value:06x}" if highest_role.color.value else "#6366f1"

    warns_html = ""
    for w in user_info.get("warns_list", []):
        proof_btn = f'<a href="{w["proof"]}" target="_blank" class="text-indigo-600 dark:text-indigo-400 hover:underline ml-2 font-medium">🔗 Beweis</a>' if w.get("proof") else ""
        warn_id = w.get("id", "")
        warns_html += f"""
        <div class="bg-slate-50 dark:bg-[#0b0e14] p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 text-xs space-y-2">
            <div class="flex justify-between items-center text-slate-400 font-mono text-[10px]">
                <span>Datum: {w.get('date', 'N/A')} | Von: {w.get('by', 'System')}</span>
                <form action="/action" method="post" onsubmit="return confirm('Diesen Warn wirklich löschen/zurückziehen?');">
                    <input type="hidden" name="action" value="remove_warn">
                    <input type="hidden" name="user_id" value="{user_id}">
                    <input type="hidden" name="warn_id" value="{warn_id}">
                    <input type="hidden" name="redirect_to_member" value="1">
                    <button class="text-rose-500 hover:underline font-semibold">🗑️ Zurückziehen</button>
                </form>
            </div>
            <div class="text-slate-700 dark:text-slate-200"><strong>Grund:</strong> {w.get('reason', 'Kein Grund')} {proof_btn}</div>
        </div>
        """

    notes_html = "".join([
        f"<div class='text-xs bg-slate-50 dark:bg-[#0b0e14] p-3 rounded-xl border border-slate-200 dark:border-slate-800 text-slate-700 dark:text-slate-300'>• {n}</div>"
        for n in user_info.get("notes", [])
    ])

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        {get_head_html(f"{member.display_name} - Details")}
    </head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-800 dark:text-slate-200 font-sans min-h-screen flex transition-colors duration-200">
        {get_sidebar_html(guild_name, 'team', current_user)}
        <main class="flex-1 p-8 overflow-y-auto">
            <div class="flex items-center gap-4 mb-8">
                <a href="/team" class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-700 dark:text-slate-300 p-2.5 rounded-xl transition shadow-sm">←</a>
                <img src="{member.display_avatar.url}" class="w-12 h-12 rounded-full border border-slate-200 dark:border-slate-700 shadow-sm">
                <div>
                    <h1 class="text-xl font-bold text-slate-900 dark:text-white leading-tight">{member.display_name}</h1>
                    <p class="text-xs text-slate-400 font-mono">@{member.name}</p>
                </div>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div class="lg:col-span-2 space-y-6">
                    
                    <div class="grid grid-cols-3 gap-4">
                        <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800/80 rounded-2xl p-5 shadow-sm">
                            <div class="text-xs text-slate-400 font-semibold mb-1">Ticket-Cases</div>
                            <div class="text-2xl font-bold text-slate-900 dark:text-white">{user_info.get('ticket_cases', 0)}</div>
                        </div>
                        <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800/80 rounded-2xl p-5 shadow-sm">
                            <div class="text-xs text-slate-400 font-semibold mb-1">Support-Cases</div>
                            <div class="text-2xl font-bold text-slate-900 dark:text-white">{user_info.get('support_cases', 0)}</div>
                        </div>
                        <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800/80 rounded-2xl p-5 shadow-sm">
                            <div class="text-xs text-slate-400 font-semibold mb-1">Wochenstunden</div>
                            <div class="text-2xl font-bold text-indigo-600 dark:text-indigo-400">{weekly_hours}</div>
                        </div>
                    </div>

                    <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800/80 rounded-2xl p-6 space-y-4 shadow-sm">
                        <h3 class="text-sm font-bold text-slate-900 dark:text-white">Team-Aktionen</h3>
                        <form action="/action" method="post" class="flex flex-wrap gap-2">
                            <input type="hidden" name="user_id" value="{member.id}">
                            <input type="hidden" name="redirect_to_member" value="1">
                            <button name="action" value="promote" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">⬆️ Befördern</button>
                            <button name="action" value="demote" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">⬇️ Degradieren</button>
                            <button name="action" value="kick" class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">🚪 Kicken</button>
                        </form>

                        <hr class="border-slate-100 dark:border-slate-800 my-4">

                        <h3 class="text-sm font-bold text-slate-900 dark:text-white">Verwarnung ausstellen</h3>
                        <form action="/action" method="post" class="space-y-2.5 text-xs">
                            <input type="hidden" name="user_id" value="{member.id}">
                            <input type="hidden" name="action" value="warn_with_proof">
                            <input type="hidden" name="redirect_to_member" value="1">
                            <input type="text" name="warn_reason" placeholder="Grund für die Verwarnung..." required class="bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl px-3.5 py-2.5 w-full text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500">
                            <input type="url" name="warn_proof" placeholder="Beweis-Link (Screenshot / Video URL)..." class="bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl px-3.5 py-2.5 w-full text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500">
                            <button class="bg-amber-500 hover:bg-amber-600 px-4 py-2 rounded-xl font-semibold text-white shadow-sm transition">⚠️ Verwarnung eintragen</button>
                        </form>

                        <hr class="border-slate-100 dark:border-slate-800 my-4">

                        <h3 class="text-sm font-bold text-slate-900 dark:text-white">Notizen</h3>
                        <div class="space-y-2 max-h-36 overflow-y-auto">
                            {notes_html or "<p class='text-xs text-slate-400 italic'>Keine Notizen hinterlegt.</p>"}
                        </div>
                        <form action="/action" method="post" class="flex gap-2 pt-1 text-xs">
                            <input type="hidden" name="user_id" value="{member.id}">
                            <input type="hidden" name="action" value="add_note">
                            <input type="hidden" name="redirect_to_member" value="1">
                            <input type="text" name="note_text" placeholder="Neue Notiz..." required class="bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl px-3.5 py-2.5 w-full text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500">
                            <button class="bg-indigo-600 hover:bg-indigo-500 px-4 py-2.5 rounded-xl font-semibold text-white shadow-sm transition">Hinzufügen</button>
                        </form>
                    </div>

                    <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800/80 rounded-2xl p-6 space-y-3 shadow-sm">
                        <h3 class="text-sm font-bold text-slate-900 dark:text-white">Verwarnungs-Historie ({len(user_info.get('warns_list', []))})</h3>
                        <div class="space-y-2 max-h-48 overflow-y-auto">
                            {warns_html or "<p class='text-xs text-slate-400 italic'>Keine Verwarnungen vorhanden.</p>"}
                        </div>
                    </div>

                </div>

                <div class="space-y-4">
                    <div class="text-right text-xl font-extrabold uppercase tracking-widest opacity-90" style="color: {top_role_color};">
                        » {guild_name} ✕ {top_role_name}
                    </div>

                    <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800/80 rounded-2xl p-6 space-y-4 shadow-sm text-xs">
                        <h3 class="text-sm font-bold text-slate-900 dark:text-white border-b border-slate-100 dark:border-slate-800 pb-2">Information</h3>
                        <div>
                            <div class="text-slate-400 mb-0.5">Nutzername</div>
                            <div class="text-slate-800 dark:text-slate-200 font-medium">[{top_role_name}] {member.display_name}</div>
                        </div>
                        <div>
                            <div class="text-slate-400 mb-0.5">ID</div>
                            <div class="text-slate-600 dark:text-slate-300 font-mono">{member.id}</div>
                        </div>
                        <div>
                            <div class="text-slate-400 mb-0.5">Verwarnungen insgesamt</div>
                            <div class="text-amber-500 font-bold">{len(user_info.get('warns_list', []))}</div>
                        </div>
                    </div>
                </div>

            </div>
        </main>
    </body>
    </html>
    """

# =============================================================
# ROUTE: ABWESENHEITEN (LOA) ÜBER SQLITE DATENBANK
# =============================================================
@app.get("/loa", response_class=HTMLResponse)
async def loa_page(request: Request, user_session: str = Cookie(None)):
    current_user = get_current_user(user_session)
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    guild_name = guild.name if guild else "Bochum RP"

    loa_entries_html = ""
    if os.path.exists(DB_ABMELDUNGEN):
        conn = sqlite3.connect(DB_ABMELDUNGEN)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, user_name, grund, von, bis FROM abmeldungen")
        rows = cursor.fetchall()
        conn.close()

        for user_id, user_name, grund, von, bis in rows:
            member = guild.get_member(user_id) if guild else None
            display_name = member.display_name if member else user_name
            loa_entries_html += f"""
            <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl p-4.5 flex justify-between items-center shadow-sm">
                <div>
                    <div class="font-bold text-slate-900 dark:text-white text-sm">{display_name}</div>
                    <div class="text-xs text-slate-400 mt-0.5">📅 {von} bis {bis}</div>
                    <div class="text-xs text-slate-600 dark:text-slate-300 mt-1.5"><strong>Grund:</strong> {grund}</div>
                </div>
                <form action="/action" method="post">
                    <input type="hidden" name="action" value="cancel_loa">
                    <input type="hidden" name="target_user_id" value="{user_id}">
                    <button class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-xs px-3.5 py-2 rounded-xl font-medium transition">Beenden</button>
                </form>
            </div>
            """

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        {get_head_html("Abmeldungen (LOA)")}
    </head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-800 dark:text-slate-200 font-sans min-h-screen flex transition-colors duration-200">
        {get_sidebar_html(guild_name, 'loa', current_user)}
        <main class="flex-1 p-8 overflow-y-auto">
            <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">Abwesenheiten (LOA)</h1>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
                <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl p-6 space-y-4 shadow-sm">
                    <h2 class="text-base font-bold text-slate-900 dark:text-white">Neue Abmeldung eintragen</h2>
                    <form action="/action" method="post" class="space-y-3.5 text-xs">
                        <input type="hidden" name="action" value="submit_loa">
                        <div>
                            <label class="block text-slate-500 dark:text-slate-400 mb-1 font-medium">Mitglied ID</label>
                            <input type="number" name="user_id" placeholder="Discord ID eintragen..." required class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-3 text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500">
                        </div>
                        <div class="grid grid-cols-2 gap-3">
                            <div>
                                <label class="block text-slate-500 dark:text-slate-400 mb-1 font-medium">Startdatum</label>
                                <input type="date" name="loa_start" required class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-3 text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500">
                            </div>
                            <div>
                                <label class="block text-slate-500 dark:text-slate-400 mb-1 font-medium">Enddatum</label>
                                <input type="date" name="loa_end" required class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-3 text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500">
                            </div>
                        </div>
                        <div>
                            <label class="block text-slate-500 dark:text-slate-400 mb-1 font-medium">Grund</label>
                            <textarea name="loa_reason" placeholder="Grund für die Abmeldung..." required class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-3 text-slate-900 dark:text-white h-24 focus:outline-none focus:border-indigo-500"></textarea>
                        </div>
                        <button class="w-full bg-indigo-600 hover:bg-indigo-500 font-semibold py-3 rounded-xl text-white shadow-md shadow-indigo-600/20 transition">Abmeldung speichern</button>
                    </form>
                </div>

                <div class="space-y-4">
                    <h2 class="text-base font-bold text-slate-900 dark:text-white">Aktuell Abgemeldet</h2>
                    <div class="space-y-3">
                        {loa_entries_html or "<div class='text-xs text-slate-400 italic bg-white dark:bg-[#141824] p-6 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-sm'>Keine aktiven Abmeldungen.</div>"}
                    </div>
                </div>
            </div>
        </main>
    </body>
    </html>
    """

# =============================================================
# ROUTE: EINSTELLUNGEN & AUDIT-LOG
# =============================================================
@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user_session: str = Cookie(None)):
    current_user = get_current_user(user_session)
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    guild_name = guild.name if guild else "Bochum RP"

    config = load_json(CONFIG_FILE, {"team_role_ids": [], "weekly_goal_hours": 3.0, "permissions": {}})
    team_role_ids = config.get("team_role_ids", [])
    perms = config.get("permissions", {})
    audit_data = load_json(AUDIT_FILE, [])

    roles_settings_html = ""
    if guild:
        for rid in team_role_ids:
            role = guild.get_role(rid)
            if not role:
                continue
            r_perm = perms.get(str(rid), {})

            roles_settings_html += f"""
            <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl p-5 space-y-4 shadow-sm">
                <div class="flex justify-between items-center border-b border-slate-100 dark:border-slate-800 pb-2.5">
                    <span class="font-bold text-sm" style="color: #{role.color.value:06x};">{role.name}</span>
                    <span class="text-[10px] text-slate-400 font-mono">ID: {role.id}</span>
                </div>
                <form action="/action" method="post" class="grid grid-cols-2 gap-3 text-xs">
                    <input type="hidden" name="action" value="save_role_permissions">
                    <input type="hidden" name="role_id" value="{role.id}">

                    <label class="flex items-center gap-2 cursor-pointer text-slate-700 dark:text-slate-300">
                        <input type="checkbox" name="can_view_dashboard" {'checked' if r_perm.get('can_view_dashboard') else ''} class="rounded bg-slate-100 dark:bg-slate-900 border-slate-300 dark:border-slate-700 text-indigo-600 focus:ring-indigo-500">
                        <span>Dashboard sehen</span>
                    </label>
                    <label class="flex items-center gap-2 cursor-pointer text-slate-700 dark:text-slate-300">
                        <input type="checkbox" name="can_warn" {'checked' if r_perm.get('can_warn') else ''} class="rounded bg-slate-100 dark:bg-slate-900 border-slate-300 dark:border-slate-700 text-indigo-600 focus:ring-indigo-500">
                        <span>Verwarnen</span>
                    </label>
                    <label class="flex items-center gap-2 cursor-pointer text-slate-700 dark:text-slate-300">
                        <input type="checkbox" name="can_promote" {'checked' if r_perm.get('can_promote') else ''} class="rounded bg-slate-100 dark:bg-slate-900 border-slate-300 dark:border-slate-700 text-indigo-600 focus:ring-indigo-500">
                        <span>Befördern/Degradieren</span>
                    </label>
                    <label class="flex items-center gap-2 cursor-pointer text-slate-700 dark:text-slate-300">
                        <input type="checkbox" name="can_add_notes" {'checked' if r_perm.get('can_add_notes') else ''} class="rounded bg-slate-100 dark:bg-slate-900 border-slate-300 dark:border-slate-700 text-indigo-600 focus:ring-indigo-500">
                        <span>Notizen erstellen</span>
                    </label>

                    <button class="col-span-2 mt-2 bg-indigo-600 hover:bg-indigo-500 py-2.5 rounded-xl text-white font-semibold shadow-sm transition">Rechte Speichern</button>
                </form>
            </div>
            """

    audit_html = ""
    for entry in reversed(audit_data[-20:]):
        audit_html += f"""
        <div class="bg-slate-50 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-800 rounded-xl p-3 text-xs flex justify-between items-center">
            <div>
                <span class="font-bold text-slate-900 dark:text-white">{entry.get('actor')}</span>
                <span class="text-indigo-600 dark:text-indigo-400 font-semibold px-2">[{entry.get('action')}]</span>
                <span class="text-slate-600 dark:text-slate-300">{entry.get('details')}</span>
            </div>
            <span class="text-[10px] text-slate-400 font-mono">{entry.get('timestamp')}</span>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        {get_head_html("Einstellungen & Rechte")}
    </head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-800 dark:text-slate-200 font-sans min-h-screen flex transition-colors duration-200">
        {get_sidebar_html(guild_name, 'settings', current_user)}
        <main class="flex-1 p-8 overflow-y-auto">
            <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-2">Einstellungen & Panel-Audit-Log</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400 mb-6">Server-ID: <code class="text-indigo-600 dark:text-indigo-400 font-mono">{GUILD_ID}</code></p>

            <div class="space-y-8">
                <div>
                    <h2 class="text-lg font-bold text-slate-900 dark:text-white mb-4">Rollen-Berechtigungen</h2>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {roles_settings_html or "<p class='text-xs text-slate-400 italic'>Keine Team-Rollen konfiguriert.</p>"}
                    </div>
                </div>

                <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl p-6 shadow-sm space-y-4">
                    <h2 class="text-lg font-bold text-slate-900 dark:text-white">📜 Panel-Audit-Log (Letzte 20 Aktionen)</h2>
                    <div class="space-y-2 max-h-72 overflow-y-auto pr-1">
                        {audit_html or "<p class='text-xs text-slate-400 italic'>Keine Audit-Einträge vorhanden.</p>"}
                    </div>
                </div>
            </div>
        </main>
    </body>
    </html>
    """

# =============================================================
# ROUTE: ÖFFENTLICHES BEWERBUNGSFORMULAR
# =============================================================
@app.get("/apply", response_class=HTMLResponse)
async def public_apply_page():
    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        {get_head_html("Team-Bewerbung")}
    </head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-900 dark:text-white min-h-screen flex items-center justify-center p-4 font-sans transition-colors duration-200">
        <div class="absolute top-6 right-6">
            <button onclick="toggleTheme()" class="px-3 py-2 rounded-xl bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 text-xs shadow-sm font-medium">
                <span class="dark:hidden">🌙 Dark</span>
                <span class="hidden dark:inline">☀️ Light</span>
            </button>
        </div>

        <div class="bg-white dark:bg-[#141824] p-8 rounded-2xl shadow-xl w-full max-w-lg border border-slate-200 dark:border-slate-800 space-y-4">
            <h1 class="text-xl font-bold text-center">Team-Bewerbung</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400 text-center">Fülle das Formular aus, um dich bei uns im Team zu bewerben.</p>

            <form action="/action" method="post" class="space-y-3.5 text-xs">
                <input type="hidden" name="action" value="submit_application">
                <div>
                    <label class="block text-slate-500 dark:text-slate-400 mb-1 font-medium">Deine Discord ID</label>
                    <input type="number" name="applicant_id" placeholder="1234567890..." required class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-3 text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500">
                </div>
                <div>
                    <label class="block text-slate-500 dark:text-slate-400 mb-1 font-medium">Nutzername</label>
                    <input type="text" name="applicant_name" placeholder="Dein Discord Name..." required class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-3 text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500">
                </div>
                <div>
                    <label class="block text-slate-500 dark:text-slate-400 mb-1 font-medium">Warum möchtest du ins Team?</label>
                    <textarea name="applicant_text" placeholder="Erzähle etwas über dich..." required class="w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl p-3 text-slate-900 dark:text-white h-28 focus:outline-none focus:border-indigo-500"></textarea>
                </div>
                <button class="w-full bg-emerald-600 hover:bg-emerald-500 font-semibold py-3 rounded-xl text-white shadow-md transition">Bewerbung Absenden</button>
            </form>
        </div>
    </body>
    </html>
    """

# =============================================================
# ROUTE: BEWERBUNGEN ÜBERSICHT
# =============================================================
@app.get("/applications", response_class=HTMLResponse)
async def applications_page(request: Request, user_session: str = Cookie(None)):
    current_user = get_current_user(user_session)
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    guild_name = guild.name if guild else "Bochum RP"

    apps = load_json(APPS_FILE, {})

    apps_html = ""
    for app_id, item in apps.items():
        if item.get("status") != "pending":
            continue

        upvotes = len(item.get("upvotes", []))
        downvotes = len(item.get("downvotes", []))

        apps_html += f"""
        <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl p-5 space-y-3.5 shadow-sm">
            <div class="flex justify-between items-center">
                <div>
                    <h3 class="font-bold text-slate-900 dark:text-white text-sm">{item.get('name')}</h3>
                    <span class="text-[10px] text-slate-400 font-mono">ID: {item.get('user_id')}</span>
                </div>
                <div class="flex items-center gap-2 text-xs font-semibold">
                    <span class="text-emerald-600 dark:text-emerald-400">👍 {upvotes}</span>
                    <span class="text-rose-600 dark:text-rose-400">👎 {downvotes}</span>
                </div>
            </div>

            <p class="text-xs text-slate-700 dark:text-slate-300 bg-slate-50 dark:bg-[#0b0e14] p-3.5 rounded-xl border border-slate-200 dark:border-slate-800/80">{item.get('text')}</p>

            <div class="flex justify-between items-center pt-2 border-t border-slate-100 dark:border-slate-800/80">
                <form action="/action" method="post" class="flex gap-2">
                    <input type="hidden" name="action" value="vote_app">
                    <input type="hidden" name="app_id" value="{app_id}">
                    <button name="vote" value="up" class="bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-xs px-3 py-1.5 rounded-xl text-slate-700 dark:text-slate-300 transition shadow-sm font-medium">👍 Dafür</button>
                    <button name="vote" value="down" class="bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-xs px-3 py-1.5 rounded-xl text-slate-700 dark:text-slate-300 transition shadow-sm font-medium">👎 Dagegen</button>
                </form>

                <form action="/action" method="post" class="flex gap-2">
                    <input type="hidden" name="action" value="decide_app">
                    <input type="hidden" name="app_id" value="{app_id}">
                    <button name="decision" value="accept" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 text-xs px-3.5 py-1.5 rounded-xl font-semibold transition">Annehmen</button>
                    <button name="decision" value="reject" class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-xs px-3.5 py-1.5 rounded-xl font-semibold transition">Ablehnen</button>
                </form>
            </div>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        {get_head_html("Bewerbungen")}
    </head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-800 dark:text-slate-200 font-sans min-h-screen flex transition-colors duration-200">
        {get_sidebar_html(guild_name, 'apps', current_user)}
        <main class="flex-1 p-8 overflow-y-auto">
            <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">Offene Bewerbungen</h1>
            <div class="space-y-4 max-w-3xl">
                {apps_html or "<p class='text-xs text-slate-400 italic bg-white dark:bg-[#141824] p-6 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-sm'>Keine offenen Bewerbungen vorhanden.</p>"}
            </div>
        </main>
    </body>
    </html>
    """

# =============================================================
# ROUTE: CSV-EXPORT FÜR LOGS
# =============================================================
@app.get("/export/logs")
async def export_logs(user_session: str = Cookie(None)):
    get_current_user(user_session)
    logs_db = load_json(LOGS_FILE, [])

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    
    writer.writerow(["ID", "Datum", "Typ", "Ziel-Nutzer", "Roblox ID", "Moderator", "Grund"])

    for log in logs_db:
        writer.writerow([
            log.get("id"),
            log.get("created_at"),
            log.get("type"),
            log.get("target_user"),
            log.get("roblox_id"),
            log.get("moderator"),
            log.get("reason")
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=punishment_logs.csv"}
    )

# =============================================================
# ACTION: ERSTELLEN VON ROBLOX LOGS
# =============================================================
@app.post("/log/create")
async def create_log(
    target_user: str = Form(...),
    roblox_id: str = Form("N/A"),
    log_type: str = Form(...),
    reason: str = Form(...),
    user_session: str = Cookie(None)
):
    current_user = get_current_user(user_session)
    logs_db = load_json(LOGS_FILE, [])

    new_entry = {
        "id": f"log_{uuid.uuid4().hex[:6]}",
        "target_user": target_user,
        "roblox_id": roblox_id if roblox_id else "N/A",
        "type": log_type,
        "reason": reason,
        "moderator": current_user.get("global_name", "Dashboard Admin"),
        "created_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
    }

    logs_db.append(new_entry)
    save_json(LOGS_FILE, logs_db)

    log_audit(current_user.get("global_name"), current_user.get("id"), "Log Erstellt", f"Spieler: {target_user} ({log_type})")
    return RedirectResponse(url="/dashboard", status_code=303)

# =============================================================
# ACTION: SCHICHT-SYSTEM STEUERUNG (Inkl. Sperre bei 3 Warns)
# =============================================================
@app.post("/shift/action")
async def handle_shift_action(shift_action: str = Form(...), user_session: str = Cookie(None)):
    current_user = get_current_user(user_session)
    shifts_db = load_json(SHIFTS_FILE, {"active_shifts": {}, "history": []})
    team_db = load_json(DATA_FILE, {})
    
    mod_id = current_user["id"]
    now_ts = datetime.now()

    if shift_action == "start":
        user_warns = team_db.get(str(mod_id), {}).get("warns_list", [])
        if len(user_warns) >= 3:
            raise HTTPException(status_code=400, detail="Schicht-Start gesperrt: Du hast 3 oder mehr aktive Verwarnungen!")

        shifts_db["active_shifts"][mod_id] = {
            "status": "online",
            "started_at_iso": now_ts.isoformat(),
            "date": now_ts.strftime("%Y-%m-%d"),
            "accumulated_seconds": 0
        }
    elif shift_action == "break":
        if mod_id in shifts_db["active_shifts"]:
            shifts_db["active_shifts"][mod_id]["status"] = "break"
    elif shift_action == "end":
        if mod_id in shifts_db["active_shifts"]:
            shift_data = shifts_db["active_shifts"][mod_id]
            start_iso = shift_data.get("started_at_iso")
            if start_iso:
                try:
                    start_dt = datetime.fromisoformat(start_iso)
                    duration = int((now_ts - start_dt).total_seconds())
                    
                    shifts_db["history"].append({
                        "mod_id": mod_id,
                        "date": shift_data.get("date"),
                        "duration_seconds": duration
                    })
                except Exception:
                    pass
            del shifts_db["active_shifts"][mod_id]

    save_json(SHIFTS_FILE, shifts_db)
    return RedirectResponse(url="/dashboard", status_code=303)

# =============================================================
# ZENTRALER ACTION-HANDLER
# =============================================================
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
    note_text: str = Form(None),
    loa_start: str = Form(None),
    loa_end: str = Form(None),
    loa_reason: str = Form(None),
    target_user_id: str = Form(None),
    applicant_id: int = Form(None),
    applicant_name: str = Form(None),
    applicant_text: str = Form(None),
    app_id: str = Form(None),
    vote: str = Form(None),
    decision: str = Form(None),
    rsvp_status: str = Form(None),
    topic_title: str = Form(None),
    topic_details: str = Form(None),
    meeting_title: str = Form(None),
    meeting_datetime: str = Form(None),
    meeting_desc: str = Form(None),
    can_view_dashboard: bool = Form(False),
    can_warn: bool = Form(False),
    can_promote: bool = Form(False),
    can_add_notes: bool = Form(False),
    user_session: str = Cookie(None)
):
    current_user = get_current_user(user_session) if action != "submit_application" else None
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None

    team_db = load_json(DATA_FILE, {})
    config = load_json(CONFIG_FILE, {"team_role_ids": [], "weekly_goal_hours": 3.0, "permissions": {}})
    apps = load_json(APPS_FILE, {})

    # ---------------------------------------------------------
    # BEFÖRDERN, DEGRADIEREN & KICKEN (Index-basiert auf team_role_ids)
    # ---------------------------------------------------------
    if action in ["promote", "demote", "kick"] and user_id and guild:
        member = guild.get_member(user_id)
        if member:
            team_role_ids = config.get("team_role_ids", [])
            member_team_role_ids = [r.id for r in member.roles if r.id in team_role_ids]

            if action == "kick":
                await send_dm_notification(member, f"❌ Du wurdest von **{guild.name}** aus dem Team entfernt. Grund: Vom Dashboard aus gekickt durch {current_user.get('global_name')}.")
                await member.kick(reason=f"Vom Dashboard aus gekickt durch {current_user.get('global_name')}.")
                await send_team_update_embed(
                    guild, 
                    "🚪 Team-Update: Kick", 
                    f"**Mitglied:** {member.mention} ({member.display_name})\n**Aktion:** Wurde aus dem Team gekickt durch {current_user.get('global_name')}.", 
                    discord.Color.red()
                )
                log_audit(current_user.get("global_name"), current_user.get("id"), "Kick", f"Mitglied {member.display_name} gekickt.")

            elif action == "promote":
                current_idx = -1
                if member_team_role_ids:
                    indices = [team_role_ids.index(rid) for rid in member_team_role_ids if rid in team_role_ids]
                    if indices:
                        current_idx = max(indices)
                
                if current_idx < len(team_role_ids) - 1:
                    new_idx = current_idx + 1
                    new_role_id = team_role_ids[new_idx]
                    new_role = guild.get_role(new_role_id)
                    
                    for rid in member_team_role_ids:
                        r_obj = guild.get_role(rid)
                        if r_obj:
                            try: await member.remove_roles(r_obj)
                            except Exception: pass
                    
                    if new_role:
                        await member.add_roles(new_role)
                        await send_dm_notification(member, f"🎉 **Herzlichen Glückwunsch!** Du wurdest auf **{guild.name}** zum **{new_role.name}** befördert!")
                        await send_team_update_embed(
                            guild,
                            "📈 Team-Update: Beförderung",
                            f"**Mitglied:** {member.mention} ({member.display_name})\n**Neuer Rang:** {new_role.name}\n**Durch:** {current_user.get('global_name')}",
                            discord.Color.green()
                        )
                        log_audit(current_user.get("global_name"), current_user.get("id"), "Beförderung", f"{member.display_name} -> {new_role.name}")

            elif action == "demote":
                current_idx = -1
                if member_team_role_ids:
                    indices = [team_role_ids.index(rid) for rid in member_team_role_ids if rid in team_role_ids]
                    if indices:
                        current_idx = max(indices)
                
                if current_idx > 0:
                    new_idx = current_idx - 1
                    new_role_id = team_role_ids[new_idx]
                    new_role = guild.get_role(new_role_id)
                    
                    for rid in member_team_role_ids:
                        r_obj = guild.get_role(rid)
                        if r_obj:
                            try: await member.remove_roles(r_obj)
                            except Exception: pass
                    
                    if new_role:
                        await member.add_roles(new_role)
                        await send_dm_notification(member, f"⚠️ Du wurdest auf **{guild.name}** zum **{new_role.name}** degradiert.")
                        await send_team_update_embed(
                            guild,
                            "📉 Team-Update: Degradierung",
                            f"**Mitglied:** {member.mention} ({member.display_name})\n**Neuer Rang:** {new_role.name}\n**Durch:** {current_user.get('global_name')}",
                            discord.Color.orange()
                        )
                        log_audit(current_user.get("global_name"), current_user.get("id"), "Degradierung", f"{member.display_name} -> {new_role.name}")

    # ---------------------------------------------------------
    # VERWARNUNGEN UND NOTIZEN
    # ---------------------------------------------------------
    elif action == "warn_with_proof" and user_id:
        user_key = str(user_id)
        if user_key not in team_db:
            team_db[user_key] = {"warns_list": [], "notes": [], "ticket_cases": 0, "support_cases": 0}
        
        warn_entry = {
            "id": f"warn_{uuid.uuid4().hex[:6]}",
            "reason": warn_reason or "Kein Grund angegeben",
            "proof": warn_proof or "",
            "by": current_user.get("global_name", "Admin"),
            "date": datetime.now().strftime("%d.%m.%Y %H:%M")
        }
        team_db[user_key]["warns_list"].append(warn_entry)
        save_json(DATA_FILE, team_db)

        warn_count = len(team_db[user_key]["warns_list"])
        if guild:
            member = guild.get_member(user_id)
            if member:
                role_id = WARN_ROLE_IDS.get(warn_count)
                if role_id:
                    role_obj = guild.get_role(role_id)
                    if role_obj:
                        try: await member.add_roles(role_obj)
                        except Exception: pass
                await send_dm_notification(member, f"⚠️ Du hast eine Verwarnung erhalten ({warn_count}. Verwarnung).\n**Grund:** {warn_reason}")
                await send_team_update_embed(
                    guild,
                    "⚠️ Team-Update: Verwarnung",
                    f"**Mitglied:** {member.mention} ({member.display_name})\n**Anzahl:** {warn_count}. Verwarnung\n**Grund:** {warn_reason}\n**Von:** {current_user.get('global_name')}",
                    discord.Color.gold()
                )

        log_audit(current_user.get("global_name"), current_user.get("id"), "Verwarnung", f"User {user_id} verwarnt: {warn_reason}")

    elif action == "remove_warn" and user_id and warn_id:
        user_key = str(user_id)
        if user_key in team_db and "warns_list" in team_db[user_key]:
            team_db[user_key]["warns_list"] = [w for w in team_db[user_key]["warns_list"] if w.get("id") != warn_id]
            save_json(DATA_FILE, team_db)
            log_audit(current_user.get("global_name"), current_user.get("id"), "Warn Zurückgezogen", f"Warn {warn_id} für User {user_id} gelöscht.")

    elif action == "add_note" and user_id and note_text:
        user_key = str(user_id)
        if user_key not in team_db:
            team_db[user_key] = {"warns_list": [], "notes": [], "ticket_cases": 0, "support_cases": 0}
        team_db[user_key]["notes"].append(f"{note_text} (von {current_user.get('global_name')})")
        save_json(DATA_FILE, team_db)
        log_audit(current_user.get("global_name"), current_user.get("id"), "Notiz Hinzugefügt", f"Notiz für User {user_id}")

    # ---------------------------------------------------------
    # ABMELDUNGEN (LOA)
    # ---------------------------------------------------------
    elif action == "submit_loa" and user_id and loa_start and loa_end and loa_reason:
        member_name = "Unbekannt"
        if guild:
            member = guild.get_member(user_id)
            if member:
                member_name = member.display_name
        
        conn = sqlite3.connect(DB_ABMELDUNGEN)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO abmeldungen (user_id, user_name, grund, von, bis, original_nick, guild_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, member_name, loa_reason, loa_start, loa_end, member_name, GUILD_ID))
        conn.commit()
        conn.close()

        if guild:
            member = guild.get_member(user_id)
            if member:
                await send_team_update_embed(
                    guild,
                    "🌴 Team-Update: Abmeldung (LOA)",
                    f"**Mitglied:** {member.mention}\n**Zeitraum:** {loa_start} bis {loa_end}\n**Grund:** {loa_reason}",
                    discord.Color.teal()
                )

        log_audit(current_user.get("global_name"), current_user.get("id"), "LOA Eingetragen", f"LOA für {user_id} ({loa_start} - {loa_end})")
        return RedirectResponse(url="/loa", status_code=303)

    elif action == "cancel_loa" and target_user_id:
        conn = sqlite3.connect(DB_ABMELDUNGEN)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM abmeldungen WHERE user_id = ?", (target_user_id,))
        conn.commit()
        conn.close()
        log_audit(current_user.get("global_name"), current_user.get("id"), "LOA Beendet", f"LOA für {target_user_id} beendet.")
        return RedirectResponse(url="/loa", status_code=303)

    # ---------------------------------------------------------
    # BEWERBUNGSSYSTEM
    # ---------------------------------------------------------
    elif action == "submit_application" and applicant_id and applicant_name and applicant_text:
        app_id_new = f"app_{uuid.uuid4().hex[:6]}"
        apps[app_id_new] = {
            "user_id": str(applicant_id),
            "name": applicant_name,
            "text": applicant_text,
            "status": "pending",
            "upvotes": [],
            "downvotes": [],
            "created_at": datetime.now().strftime("%d.%m.%Y %H:%M")
        }
        save_json(APPS_FILE, apps)
        return HTMLResponse("<h2>Bewerbung erfolgreich abgesendet!</h2><p>Vielen Dank für deine Bewerbung.</p><a href='/apply'>Zurück</a>")

    elif action == "vote_app" and app_id and vote and app_id in apps:
        voter_id = current_user.get("id")
        upvotes = set(apps[app_id].get("upvotes", []))
        downvotes = set(apps[app_id].get("downvotes", []))

        if vote == "up":
            upvotes.add(voter_id)
            downvotes.discard(voter_id)
        elif vote == "down":
            downvotes.add(voter_id)
            upvotes.discard(voter_id)

        apps[app_id]["upvotes"] = list(upvotes)
        apps[app_id]["downvotes"] = list(downvotes)
        save_json(APPS_FILE, apps)
        return RedirectResponse(url="/applications", status_code=303)

    elif action == "decide_app" and app_id and decision and app_id in apps:
        apps[app_id]["status"] = "accepted" if decision == "accept" else "rejected"
        save_json(APPS_FILE, apps)
        
        app_user_id = int(apps[app_id].get("user_id", 0))
        if guild and app_user_id:
            member = guild.get_member(app_user_id)
            if member:
                status_str = "angenommen 🎉" if decision == "accept" else "abgelehnt ❌"
                await send_dm_notification(member, f"Hallo {member.display_name}, deine Team-Bewerbung auf **{guild.name}** wurde {status_str}.")
        
        log_audit(current_user.get("global_name"), current_user.get("id"), "Bewerbung Entscheiden", f"Bewerbung {app_id}: {decision}")
        return RedirectResponse(url="/applications", status_code=303)

    # ---------------------------------------------------------
    # BESPRECHUNGEN
    # ---------------------------------------------------------
    elif action == "meeting_rsvp" and rsvp_status:
        meetings_data = load_json(MEETINGS_FILE, {"title": "Nächste Teambesprechung", "date_time": "Noch nicht angesetzt", "description": "", "rsvps": {}, "topics": []})
        user_id_str = current_user.get("id")
        meetings_data["rsvps"][user_id_str] = {
            "name": current_user.get("global_name"),
            "status": rsvp_status
        }
        save_json(MEETINGS_FILE, meetings_data)
        return RedirectResponse(url="/meetings", status_code=303)

    elif action == "add_meeting_topic" and topic_title and topic_details:
        meetings_data = load_json(MEETINGS_FILE, {"title": "Nächste Teambesprechung", "date_time": "Noch nicht angesetzt", "description": "", "rsvps": {}, "topics": []})
        meetings_data.setdefault("topics", []).append({
            "title": topic_title,
            "details": topic_details,
            "by": current_user.get("global_name")
        })
        save_json(MEETINGS_FILE, meetings_data)
        return RedirectResponse(url="/meetings", status_code=303)

    elif action == "set_meeting_info" and meeting_title and meeting_datetime:
        meetings_data = load_json(MEETINGS_FILE, {"title": "", "date_time": "", "description": "", "rsvps": {}, "topics": []})
        meetings_data["title"] = meeting_title
        meetings_data["date_time"] = meeting_datetime
        meetings_data["description"] = meeting_desc or ""
        save_json(MEETINGS_FILE, meetings_data)
        log_audit(current_user.get("global_name"), current_user.get("id"), "Meeting Angesetzt", f"Titel: {meeting_title}")
        return RedirectResponse(url="/meetings", status_code=303)

    # ---------------------------------------------------------
    # EINSTELLUNGEN
    # ---------------------------------------------------------
    elif action == "save_role_permissions" and role_id:
        perms = config.get("permissions", {})
        perms[str(role_id)] = {
            "can_view_dashboard": can_view_dashboard,
            "can_warn": can_warn,
            "can_promote": can_promote,
            "can_add_notes": can_add_notes
        }
        config["permissions"] = perms
        save_json(CONFIG_FILE, config)
        log_audit(current_user.get("global_name"), current_user.get("id"), "Rechte Gespeichert", f"Rolle {role_id}")
        return RedirectResponse(url="/settings", status_code=303)

    if redirect_to_member and user_id:
        return RedirectResponse(url=f"/member/{user_id}", status_code=303)
    return RedirectResponse(url="/dashboard", status_code=303)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=25095)
    await send_dm_notification(member, f"🎉 Herzlichen Glückwunsch! Du wurdest auf **{guild.name}** zum **{new_role.name}** befördert.")
                        await send_team_update_embed(
                            guild, 
                            "⬆️ Team-Update: Beförderung", 
                            f"**Mitglied:** {member.mention} ({member.display_name})\n**Neuer Rang:** {new_role.name}\n**Durchgeführt von:** {current_user.get('global_name')}", 
                            discord.Color.green()
                        )
                        log_audit(current_user.get("global_name"), current_user.get("id"), "Beförderung", f"{member.display_name} -> {new_role.name}")

            elif action == "demote":
                current_idx = -1
                if member_team_role_ids:
                    indices = [team_role_ids.index(rid) for rid in member_team_role_ids if rid in team_role_ids]
                    if indices:
                        current_idx = min(indices)
                
                if current_idx > 0:
                    new_idx = current_idx - 1
                    new_role_id = team_role_ids[new_idx]
                    new_role = guild.get_role(new_role_id)
                    
                    for rid in member_team_role_ids:
                        r_obj = guild.get_role(rid)
                        if r_obj:
                            try: await member.remove_roles(r_obj)
                            except Exception: pass
                    
                    if new_role:
                        await member.add_roles(new_role)
                        await send_dm_notification(member, f"⚠️ Du wurdest auf **{guild.name}** auf **{new_role.name}** degradiert.")
                        await send_team_update_embed(
                            guild, 
                            "⬇️ Team-Update: Degradierung", 
                            f"**Mitglied:** {member.mention} ({member.display_name})\n**Neuer Rang:** {new_role.name}\n**Durchgeführt von:** {current_user.get('global_name')}", 
                            discord.Color.orange()
                        )
                        log_audit(current_user.get("global_name"), current_user.get("id"), "Degradierung", f"{member.display_name} -> {new_role.name}")

    # ---------------------------------------------------------
    # VERWARNUNGEN & NOTIZEN
    # ---------------------------------------------------------
    elif action == "warn_with_proof" and user_id:
        user_id_str = str(user_id)
        if user_id_str not in team_db:
            team_db[user_id_str] = {"warns_list": [], "notes": [], "ticket_cases": 0, "support_cases": 0}
        
        warn_data = {
            "id": f"warn_{uuid.uuid4().hex[:6]}",
            "reason": warn_reason or "Kein Grund angegeben",
            "proof": warn_proof or "",
            "by": current_user.get("global_name", "System"),
            "date": datetime.now().strftime("%d.%m.%Y %H:%M")
        }
        
        team_db[user_id_str]["warns_list"].append(warn_data)
        warn_count = len(team_db[user_id_str]["warns_list"])
        save_json(DATA_FILE, team_db)

        if guild and (member := guild.get_member(user_id)):
            role_id = WARN_ROLE_IDS.get(warn_count)
            if role_id:
                warn_role = guild.get_role(role_id)
                if warn_role:
                    try: await member.add_roles(warn_role)
                    except Exception: pass

            await send_dm_notification(
                member, 
                f"⚠️ Du hast eine Verwarnung auf **{guild.name}** erhalten.\n**Grund:** {warn_reason}\n**Anzahl Verwarnungen:** {warn_count}/3"
            )
            await send_team_update_embed(
                guild, 
                "⚠️ Team-Update: Verwarnung", 
                f"**Mitglied:** {member.mention} ({member.display_name})\n**Grund:** {warn_reason}\n**Warns Gesamt:** {warn_count}\n**Ausgestellt von:** {current_user.get('global_name')}", 
                discord.Color.gold()
            )

        log_audit(current_user.get("global_name"), current_user.get("id"), "Verwarnung", f"User {user_id} verwarnt: {warn_reason}")

    elif action == "remove_warn" and user_id and warn_id:
        user_id_str = str(user_id)
        if user_id_str in team_db:
            team_db[user_id_str]["warns_list"] = [w for w in team_db[user_id_str].get("warns_list", []) if w.get("id") != warn_id]
            save_json(DATA_FILE, team_db)
            log_audit(current_user.get("global_name"), current_user.get("id"), "Warn Zurückgezogen", f"Warn {warn_id} bei User {user_id} gelöscht.")

    elif action == "add_note" and user_id and note_text:
        user_id_str = str(user_id)
        if user_id_str not in team_db:
            team_db[user_id_str] = {"warns_list": [], "notes": [], "ticket_cases": 0, "support_cases": 0}
        team_db[user_id_str]["notes"].append(note_text)
        save_json(DATA_FILE, team_db)
        log_audit(current_user.get("global_name"), current_user.get("id"), "Notiz Erstellt", f"Notiz für User {user_id} hinzugefügt.")

    # ---------------------------------------------------------
    # ABMELDUNGEN (LOA)
    # ---------------------------------------------------------
    elif action == "submit_loa" and user_id and loa_start and loa_end:
        user_name = "Unbekannt"
        if guild and (member := guild.get_member(user_id)):
            user_name = member.display_name

        conn = sqlite3.connect(DB_ABMELDUNGEN)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO abmeldungen (user_id, user_name, grund, von, bis, guild_id) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, user_name, loa_reason or "Kein Grund", loa_start, loa_end, GUILD_ID)
        )
        conn.commit()
        conn.close()

        if guild and (member := guild.get_member(user_id)):
            await send_team_update_embed(
                guild, 
                "🌴 Team-Update: Abmeldung (LOA)", 
                f"**Mitglied:** {member.mention} ({user_name})\n**Zeitraum:** {loa_start} bis {loa_end}\n**Grund:** {loa_reason}", 
                discord.Color.blue()
            )

        log_audit(current_user.get("global_name"), current_user.get("id"), "LOA Eingetragen", f"Abmeldung für {user_id} ({loa_start} - {loa_end})")

    elif action == "cancel_loa" and target_user_id:
        conn = sqlite3.connect(DB_ABMELDUNGEN)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM abmeldungen WHERE user_id = ?", (target_user_id,))
        conn.commit()
        conn.close()
        log_audit(current_user.get("global_name"), current_user.get("id"), "LOA Beendet", f"Abmeldung für ID {target_user_id} aufgehoben.")

    # ---------------------------------------------------------
    # BEWERBUNGEN
    # ---------------------------------------------------------
    elif action == "submit_application" and applicant_id and applicant_name and applicant_text:
        new_app_id = f"app_{uuid.uuid4().hex[:6]}"
        apps[new_app_id] = {
            "user_id": applicant_id,
            "name": applicant_name,
            "text": applicant_text,
            "status": "pending",
            "upvotes": [],
            "downvotes": []
        }
        save_json(APPS_FILE, apps)
        return RedirectResponse(url="/apply?success=1", status_code=303)

    elif action == "vote_app" and app_id and vote and app_id in apps:
        voter_id = current_user["id"]
        app_data = apps[app_id]
        if vote == "up":
            if voter_id not in app_data["upvotes"]:
                app_data["upvotes"].append(voter_id)
            if voter_id in app_data["downvotes"]:
                app_data["downvotes"].remove(voter_id)
        elif vote == "down":
            if voter_id not in app_data["downvotes"]:
                app_data["downvotes"].append(voter_id)
            if voter_id in app_data["upvotes"]:
                app_data["upvotes"].remove(voter_id)
        save_json(APPS_FILE, apps)

    elif action == "decide_app" and app_id and decision and app_id in apps:
        app_data = apps[app_id]
        app_data["status"] = "accepted" if decision == "accept" else "rejected"
        save_json(APPS_FILE, apps)

        if guild and (member := guild.get_member(app_data["user_id"])):
            status_txt = "angenommen 🎉" if decision == "accept" else "abgelehnt ❌"
            await send_dm_notification(member, f"Deine Team-Bewerbung bei **{guild.name}** wurde {status_txt}.")

        log_audit(current_user.get("global_name"), current_user.get("id"), "Bewerbung Entscheiden", f"Bewerbung {app_id}: {decision}")

    # ---------------------------------------------------------
    # BESPRECHUNGS-TOOL
    # ---------------------------------------------------------
    elif action == "meeting_rsvp" and rsvp_status:
        meetings_data = load_json(MEETINGS_FILE, {"title": "Nächste Teambesprechung", "date_time": "", "description": "", "rsvps": {}, "topics": []})
        user_id_str = current_user["id"]
        meetings_data["rsvps"][user_id_str] = {
            "name": current_user.get("global_name"),
            "status": rsvp_status
        }
        save_json(MEETINGS_FILE, meetings_data)

    elif action == "add_meeting_topic" and topic_title:
        meetings_data = load_json(MEETINGS_FILE, {"title": "Nächste Teambesprechung", "date_time": "", "description": "", "rsvps": {}, "topics": []})
        meetings_data["topics"].append({
            "title": topic_title,
            "details": topic_details or "",
            "by": current_user.get("global_name")
        })
        save_json(MEETINGS_FILE, meetings_data)

    elif action == "set_meeting_info" and meeting_title and meeting_datetime:
        meetings_data = load_json(MEETINGS_FILE, {"title": "Nächste Teambesprechung", "date_time": "", "description": "", "rsvps": {}, "topics": []})
        meetings_data["title"] = meeting_title
        meetings_data["date_time"] = meeting_datetime
        meetings_data["description"] = meeting_desc or ""
        meetings_data["rsvps"] = {}
        meetings_data["topics"] = []
        save_json(MEETINGS_FILE, meetings_data)
        log_audit(current_user.get("global_name"), current_user.get("id"), "Besprechung Geändert", f"Titel: {meeting_title}")

    # ---------------------------------------------------------
    # RECHTE / ROLLEN
    # ---------------------------------------------------------
    elif action == "save_role_permissions" and role_id:
        role_id_str = str(role_id)
        if "permissions" not in config:
            config["permissions"] = {}
        config["permissions"][role_id_str] = {
            "can_view_dashboard": can_view_dashboard,
            "can_warn": can_warn,
            "can_promote": can_promote,
            "can_add_notes": can_add_notes
        }
        save_json(CONFIG_FILE, config)
        log_audit(current_user.get("global_name"), current_user.get("id"), "Rechte Gespeichert", f"Rolle {role_id}")

    if redirect_to_member and user_id:
        return RedirectResponse(url=f"/member/{user_id}", status_code=303)
    
    referer = request.headers.get("referer", "/dashboard")
    return RedirectResponse(url=referer, status_code=303)
