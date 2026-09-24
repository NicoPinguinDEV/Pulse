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

load_dotenv()

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = "http://fi4.bot-hosting.cloud:25095/callback"

GUILD_ID = 1474514929351524616  # DEINE DISCORD SERVER-ID

DATA_FILE = "team_data.json"
CONFIG_FILE = "config.json"
APPS_FILE = "applications.json"
SHIFTS_FILE = "shifts.json"
LOGS_FILE = "logs.json"
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
# HELFER-FUNKTIONEN & DESIGN HEADER
# =============================================================
json_load = lambda filepath, default: json.load(open(filepath, "r", encoding="utf-8")) if os.path.exists(filepath) else default

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


async def verify_session(session: str = Cookie(None)):
    """Prüft, ob der Nutzer eingeloggt ist."""
    if session != "authenticated":
        raise HTTPException(status_code=303, headers={"Location": "/"})


def calculate_weekly_hours(mod_id_str, shifts_history):
    """Berechnet die Arbeitsstunden der aktuellen Woche aus der Shift-Historie."""
    total_seconds = 0
    now = datetime.now()
    start_of_week = now - timedelta(days=now.weekday())
    start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)

    for entry in shifts_history:
        if entry.get("mod_id") == mod_id_str:
            try:
                entry_date = datetime.strptime(entry.get("date"), "%Y-%m-%d")
                if entry_date >= start_of_week:
                    total_seconds += entry.get("duration_seconds", 0)
            except Exception:
                pass
    
    hours = total_seconds / 3600
    return f"{hours:.1f}h"


def get_head_html(title: str):
    return f"""
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            darkMode: 'class',
        }}
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


def get_sidebar_html(guild_name, current_page="dashboard"):
    return f"""
    <aside class="w-64 bg-white dark:bg-[#141824] border-r border-slate-200 dark:border-slate-800/80 flex flex-col justify-between p-4 min-h-screen shrink-0 transition-colors duration-200">
        <div class="space-y-6">
            <div class="flex items-center gap-3 px-2">
                <div class="w-9 h-9 rounded-xl bg-indigo-600 flex items-center justify-center font-bold text-white shadow-md shadow-indigo-600/20">B</div>
                <div>
                    <h2 class="font-bold text-slate-900 dark:text-white leading-none">Bochum RP</h2>
                    <span class="text-[10px] text-slate-500 dark:text-slate-400 font-mono">v2.3.0 Pro</span>
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
                    ⚙️ <span>Rechte & Einstellungen</span>
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
                <div class="flex items-center gap-2.5">
                    <div class="w-7 h-7 rounded-full bg-slate-200 dark:bg-slate-700 flex items-center justify-center text-xs font-bold text-slate-700 dark:text-white">B</div>
                    <span class="text-xs font-medium text-slate-700 dark:text-slate-300 truncate">Bot Host</span>
                </div>
                <a href="/logout" class="text-xs text-slate-400 hover:text-rose-500 dark:hover:text-rose-400 transition font-medium">↤ Abmelden</a>
            </div>
        </div>
    </aside>
    """


# =============================================================
# ROUTEN: LOGIN, LOGOUT & CALLBACK
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
            <h1 class="text-2xl font-bold tracking-tight mb-2">Bochum RP Panel</h1>
            <p class="text-slate-500 dark:text-slate-400 text-xs mb-8">Bitte melde dich mit deinem Discord-Account an, um auf das Moderatoren-Panel zuzugreifen.</p>
            
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
    response.delete_cookie(key="session")
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
            return HTMLResponse(
                "<h2>Login fehlgeschlagen.</h2><a href='/'>Erneut versuchen</a>"
            )

    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="session", value="authenticated", httponly=True)
    return response


# =============================================================
# ROUTE 1: HAUPT-DASHBOARD
# =============================================================
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_main(request: Request, session: str = Cookie(None)):
    await verify_session(session)
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    guild_name = guild.name if guild else "Bochum RP"

    shifts_db = load_json(SHIFTS_FILE, {"active_shifts": {}, "history": []})
    logs_db = load_json(LOGS_FILE, [])

    active_shifts = shifts_db.get("active_shifts", {})
    active_staff_count = len(
        [s for s in active_shifts.values() if s.get("status") in ["online", "break"]]
    )

    config = load_json(CONFIG_FILE, {"team_role_ids": [], "permissions": {}})
    team_role_ids = config.get("team_role_ids", [])
    
    leaderboard_data = []
    if guild:
        for member in guild.members:
            if any(r.id in team_role_ids for r in member.roles):
                hrs = calculate_weekly_hours(str(member.id), shifts_db.get("history", []))
                try:
                    float_hrs = float(hrs.replace("h", ""))
                except:
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
    for log in reversed(logs_db[-20:]):
        type_colors = {
            "Ban": "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30",
            "Kick": "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30",
            "Warn": "bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 border-yellow-500/30",
            "Notiz": "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/30",
        }
        badge_style = type_colors.get(log.get("type"), "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300")

        logs_html += f"""
        <div class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800/80 rounded-2xl p-4 space-y-2 shadow-sm transition-all hover:shadow">
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
    </head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-800 dark:text-slate-200 font-sans min-h-screen flex transition-colors duration-200">
        {get_sidebar_html(guild_name, 'dashboard')}

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

                    <div class="space-y-3 max-h-[calc(100vh-160px)] overflow-y-auto pr-1">
                        {logs_html or "<div class='text-xs text-slate-400 italic bg-white dark:bg-[#141824] p-6 rounded-2xl border border-slate-200 dark:border-slate-800 text-center shadow-sm'>Noch keine Logs eingetragen.</div>"}
                    </div>
                </div>

            </div>
        </main>
    </body>
    </html>
    """


# =============================================================
# ROUTE 2: TEAMLISTE (MIT SQLITE LOA ABGLEICH)
# =============================================================
@app.get("/team", response_class=HTMLResponse)
async def team_list_page(request: Request, session: str = Cookie(None)):
    await verify_session(session)
    bot = getattr(request.app.state, "bot", None)
    if not bot:
        return "<h3>Bot-Instanz noch nicht bereit!</h3>"

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return f"<h3>Fehler: Server {GUILD_ID} nicht gefunden.</h3>"
    
    guild_name = guild.name

    config = load_json(CONFIG_FILE, {"team_role_ids": [], "permissions": {}})
    team_db = load_json(DATA_FILE, {})
    shifts_db = load_json(SHIFTS_FILE, {"active_shifts": {}, "history": []})
    team_role_ids = config.get("team_role_ids", [])

    # Aktive Abmeldungen aus SQLite laden
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

            weekly_hours = calculate_weekly_hours(user_id_str, shifts_db.get("history", []))

            team_members.append({
                "id": member.id,
                "name": member.display_name,
                "username": member.name,
                "avatar": member.display_avatar.url,
                "top_role": highest_role.name,
                "top_role_color": f"#{highest_role.color.value:06x}" if highest_role.color.value else "#6366f1",
                "role_position": highest_role.position,
                "loa_badge": loa_badge,
                "weekly_hours": weekly_hours
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

            <div class="w-1/4 flex items-center gap-3">
                <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border shadow-sm" style="background-color: {m['top_role_color']}15; color: {m['top_role_color']}; border-color: {m['top_role_color']}40;">
                    <span class="w-1.5 h-1.5 rounded-full" style="background-color: {m['top_role_color']}"></span>
                    {m['top_role']}
                </span>
                <span class="text-xs text-indigo-600 dark:text-indigo-400 font-mono font-bold bg-indigo-500/10 px-2.5 py-1 rounded-lg border border-indigo-500/20" title="Wochenstunden">
                    ⏱️ {m['weekly_hours']}
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
        {get_sidebar_html(guild_name, 'team')}
        <main class="flex-1 p-8 overflow-y-auto">
            <div class="flex justify-between items-center mb-6">
                <div>
                    <div class="text-xs text-slate-400 flex items-center gap-1.5 mb-1">
                        <span>Team</span> / <span class="text-indigo-600 dark:text-indigo-400 font-medium">Teamliste</span>
                    </div>
                    <h1 class="text-2xl font-bold text-slate-900 dark:text-white">Teamliste & Aktivität</h1>
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
# ROUTE: DISCORD BACKUP SYSTEM
# =============================================================
@app.get("/backups", response_class=HTMLResponse)
async def backups_page(request: Request, session: str = Cookie(None)):
    await verify_session(session)
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
        {get_sidebar_html(guild_name, 'backups')}
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
async def create_backup(session: str = Cookie(None)):
    await verify_session(session)
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

    return RedirectResponse(url="/backups", status_code=303)


@app.get("/backup/download/{filename}")
async def download_backup(filename: str, session: str = Cookie(None)):
    await verify_session(session)
    filepath = os.path.join(BACKUP_DIR, filename)
    if os.path.exists(filepath):
        return FileResponse(filepath, media_type='application/json', filename=filename)
    raise HTTPException(status_code=404, detail="Backup nicht gefunden.")


@app.post("/backup/delete")
async def delete_backup(filename: str = Form(...), session: str = Cookie(None)):
    await verify_session(session)
    filepath = os.path.join(BACKUP_DIR, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    return RedirectResponse(url="/backups", status_code=303)


# =============================================================
# ROUTE: MITGLIEDER-DETAILSEITE
# =============================================================
@app.get("/member/{user_id}", response_class=HTMLResponse)
async def member_detail(request: Request, user_id: int, session: str = Cookie(None)):
    await verify_session(session)
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
        warns_html += f"""
        <div class="bg-slate-50 dark:bg-[#0b0e14] p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 text-xs space-y-1">
            <div class="flex justify-between text-slate-400 font-mono text-[10px]">
                <span>Datum: {w.get('date', 'N/A')}</span>
                <span>Von: {w.get('by', 'System')}</span>
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
        {get_sidebar_html(guild_name, 'team')}
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
                        » BORP ✕ {top_role_name}
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
async def loa_page(request: Request, session: str = Cookie(None)):
    await verify_session(session)
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
        {get_sidebar_html(guild_name, 'loa')}
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
# ROUTE: EINSTELLUNGEN & RECHTE
# =============================================================
@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session: str = Cookie(None)):
    await verify_session(session)
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    guild_name = guild.name if guild else "Bochum RP"

    config = load_json(CONFIG_FILE, {"team_role_ids": [], "permissions": {}})
    team_role_ids = config.get("team_role_ids", [])
    perms = config.get("permissions", {})

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

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        {get_head_html("Einstellungen & Rechte")}
    </head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-800 dark:text-slate-200 font-sans min-h-screen flex transition-colors duration-200">
        {get_sidebar_html(guild_name, 'settings')}
        <main class="flex-1 p-8 overflow-y-auto">
            <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-2">Rollen-Berechtigungen & System-Status</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400 mb-6">Server-ID: <code class="text-indigo-600 dark:text-indigo-400 font-mono">{GUILD_ID}</code> | Redirect-URI: <code class="text-indigo-600 dark:text-indigo-400 font-mono">{REDIRECT_URI}</code></p>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                {roles_settings_html or "<p class='text-xs text-slate-400 italic'>Keine Team-Rollen konfiguriert.</p>"}
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
# ROUTE: BEWERBUNGEN ÜBERSICHT (DASHBOARD)
# =============================================================
@app.get("/applications", response_class=HTMLResponse)
async def applications_page(request: Request, session: str = Cookie(None)):
    await verify_session(session)
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
        {get_sidebar_html(guild_name, 'apps')}
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
async def export_logs(session: str = Cookie(None)):
    await verify_session(session)
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
    session: str = Cookie(None)
):
    await verify_session(session)
    logs_db = load_json(LOGS_FILE, [])

    new_entry = {
        "id": f"log_{uuid.uuid4().hex[:6]}",
        "target_user": target_user,
        "roblox_id": roblox_id if roblox_id else "N/A",
        "type": log_type,
        "reason": reason,
        "moderator": "Dashboard Admin",
        "created_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
    }

    logs_db.append(new_entry)
    save_json(LOGS_FILE, logs_db)

    return RedirectResponse(url="/dashboard", status_code=303)


# =============================================================
# ACTION: SCHICHT-SYSTEM STEUERUNG
# =============================================================
@app.post("/shift/action")
async def handle_shift_action(shift_action: str = Form(...), session: str = Cookie(None)):
    await verify_session(session)
    shifts_db = load_json(SHIFTS_FILE, {"active_shifts": {}, "history": []})
    
    mod_id = "moderator_nico"
    now_ts = datetime.now()

    if shift_action == "start":
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
            start_iso = shift_data.get("started_id_iso") or shift_data.get("started_at_iso")
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
# ZENTRALER ACTION-HANDLER (SQLITE LOA & PROFILES & SETTINGS)
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
    can_view_dashboard: bool = Form(False),
    can_warn: bool = Form(False),
    can_promote: bool = Form(False),
    can_add_notes: bool = Form(False),
    session: str = Cookie(None)
):
    await verify_session(session)
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None

    team_db = load_json(DATA_FILE, {})
    config = load_json(CONFIG_FILE, {"team_role_ids": [], "permissions": {}})
    apps = load_json(APPS_FILE, {})

    if action in ["promote", "demote", "kick"] and user_id and guild:
        member = guild.get_member(user_id)
        if member:
            if action == "kick":
                await member.kick(reason="Vom Dashboard aus gekickt.")

    elif action == "warn_with_proof" and user_id:
        user_key = str(user_id)
        if user_key not in team_db:
            team_db[user_key] = {"warns_list": [], "notes": []}

        if "warns_list" not in team_db[user_key]:
            team_db[user_key]["warns_list"] = []

        team_db[user_key]["warns_list"].append({
            "reason": warn_reason,
            "proof": warn_proof,
            "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "by": "Dashboard Admin",
        })
        save_json(DATA_FILE, team_db)

    elif action == "add_note" and user_id and note_text:
        user_key = str(user_id)
        if user_key not in team_db:
            team_db[user_key] = {"notes": [], "warns_list": []}
        team_db[user_key]["notes"].append(note_text)
        save_json(DATA_FILE, team_db)

    elif action == "submit_loa" and user_id and guild:
        # Datum von YYYY-MM-DD zu DD.MM.YYYY konvertieren
        try:
            von_formatted = datetime.strptime(loa_start, "%Y-%m-%d").strftime("%d.%m.%Y")
            bis_formatted = datetime.strptime(loa_end, "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            von_formatted = loa_start
            bis_formatted = loa_end

        member = guild.get_member(user_id)
        user_name = member.display_name if member else f"User {user_id}"
        original_nick = member.nick if member else None

        # In SQLite-Datenbank speichern
        conn = sqlite3.connect(DB_ABMELDUNGEN)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO abmeldungen (user_id, user_name, grund, von, bis, original_nick, guild_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, user_name, loa_reason, von_formatted, bis_formatted, original_nick, GUILD_ID))
        conn.commit()
        conn.close()

        # --- NEU: Discord Benachrichtigung & Anpassung ---
        if bot:
            try:
                import discord
                # Sucht nach einem Kanal namens "abmeldungen" oder "loa"
                loa_channel = discord.utils.get(guild.text_channels, name="abmeldungen") or discord.utils.get(guild.text_channels, name="loa")
                
                if loa_channel:
                    embed = discord.Embed(
                        title="🌴 Neue Abmeldung (Web-Dashboard)",
                        description=f"**Mitglied:** {member.mention if member else user_name}\n"
                                    f"**Zeitraum:** {von_formatted} bis {bis_formatted}\n"
                                    f"**Grund:** {loa_reason}",
                        color=discord.Color.orange()
                    )
                    embed.set_footer(text="Eingetragen über das Web-Panel")
                    await loa_channel.send(embed=embed)
                
                # Optional: Nickname anpassen (falls der Bot die Rechte dazu hat)
                if member:
                    try:
                        if not member.display_name.startswith("[Abgemeldet]"):
                            await member.edit(nick=f"[Abgemeldet] {member.display_name}")
                    except Exception:
                        pass
            except Exception as e:
                print(f"Fehler beim Senden der Discord-Benachrichtigung: {e}")
        # ------------------------------------------------

        return RedirectResponse(url="/loa", status_code=303)

    elif action == "cancel_loa" and target_user_id:
        conn = sqlite3.connect(DB_ABMELDUNGEN)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM abmeldungen WHERE user_id = ?", (int(target_user_id),))
        conn.commit()
        conn.close()
        return RedirectResponse(url="/loa", status_code=303)

    elif action == "submit_application" and applicant_id:
        new_id = f"app_{uuid.uuid4().hex[:8]}"
        apps[new_id] = {
            "user_id": applicant_id,
            "name": applicant_name,
            "text": applicant_text,
            "status": "pending",
            "upvotes": [],
            "downvotes": [],
        }
        save_json(APPS_FILE, apps)
        return HTMLResponse(
            "<body style='background:#0b0e14;color:white;font-family:sans-serif;text-align:center;padding-top:50px;'><h2>Deine Bewerbung wurde erfolgreich abgesendet!</h2><a href='/apply' style='color:#6366f1;'>Zurück</a></body>"
        )

    elif action == "decide_app" and app_id and decision and guild:
        if app_id in apps:
            apps[app_id]["status"] = decision
            save_json(APPS_FILE, apps)

            if decision == "accept":
                target_member = guild.get_member(apps[app_id]["user_id"])
                team_role_ids = config.get("team_role_ids", [])
                if target_member and team_role_ids:
                    first_role = guild.get_role(team_role_ids[0])
                    if first_role:
                        await target_member.add_roles(first_role)
        return RedirectResponse(url="/applications", status_code=303)

    elif action == "vote_app" and app_id and vote:
        if app_id in apps:
            voter_id = "admin_user"
            if vote == "up":
                if voter_id not in apps[app_id]["upvotes"]:
                    apps[app_id]["upvotes"].append(voter_id)
                if voter_id in apps[app_id]["downvotes"]:
                    apps[app_id]["downvotes"].remove(voter_id)
            elif vote == "down":
                if voter_id not in apps[app_id]["downvotes"]:
                    apps[app_id]["downvotes"].append(voter_id)
                if voter_id in apps[app_id]["upvotes"]:
                    apps[app_id]["upvotes"].remove(voter_id)
            save_json(APPS_FILE, apps)
        return RedirectResponse(url="/applications", status_code=303)

    elif action == "save_role_permissions" and role_id:
        if "permissions" not in config:
            config["permissions"] = {}
        config["permissions"][str(role_id)] = {
            "can_view_dashboard": can_view_dashboard,
            "can_warn": can_warn,
            "can_promote": can_promote,
            "can_add_notes": can_add_notes,
        }
        save_json(CONFIG_FILE, config)
        return RedirectResponse(url="/settings", status_code=303)

    if redirect_to_member and user_id:
        return RedirectResponse(url=f"/member/{user_id}", status_code=303)

    return RedirectResponse(url="/dashboard", status_code=303)
