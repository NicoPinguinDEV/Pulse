import json
import os
import uuid
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx

load_dotenv()

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = "http://fi4.bot-hosting.cloud:25095/callback"

GUILD_ID = 1474514929351524616  # DEINE DISCORD SERVER-ID

DATA_FILE = "team_data.json"
CONFIG_FILE = "config.json"
APPS_FILE = "applications.json"

app = FastAPI()

DISCORD_AUTH_URL = (
    f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds"
)


# =============================================================
# HELFER-FUNKTIONEN DATERBANK
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


def get_sorted_team_roles(guild, config_role_ids):
    roles = [guild.get_role(rid) for rid in config_role_ids if guild.get_role(rid)]
    roles.sort(key=lambda r: r.position)
    return roles


def has_permission(member, guild, perm_key):
    """Prüft, ob ein Nutzer ein bestimmtes Recht besitzt oder Server-Owner ist."""
    if member.id == guild.owner_id:
        return True

    config = load_json(CONFIG_FILE, {"team_role_ids": [], "permissions": {}})
    perms = config.get("permissions", {})

    for role in member.roles:
        r_perm = perms.get(str(role.id), {})
        if r_perm.get("full_access", False) or r_perm.get(perm_key, False):
            return True
    return False


def get_sidebar_html(guild_name, current_page="team"):
    return f"""
    <aside class="w-64 bg-[#141824] border-r border-slate-800/80 flex flex-col justify-between p-4 min-h-screen shrink-0">
        <div class="space-y-6">
            <div class="flex items-center gap-3 px-2">
                <div class="w-8 h-8 rounded-xl bg-indigo-600 flex items-center justify-center font-bold text-white shadow-lg">T</div>
                <div>
                    <h2 class="font-bold text-white leading-none">Teams</h2>
                    <span class="text-[10px] text-slate-500 font-mono">v2.0.0</span>
                </div>
            </div>

            <div class="bg-[#0b0e14] border border-slate-800 rounded-xl p-2.5 flex items-center justify-between cursor-pointer">
                <div class="flex items-center gap-2 truncate">
                    <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
                    <span class="text-xs font-semibold text-slate-200 truncate">{guild_name}</span>
                </div>
                <span class="text-xs text-slate-500">▾</span>
            </div>

            <nav class="space-y-1 text-xs">
                <div class="text-[10px] font-semibold text-slate-500 uppercase tracking-wider px-2 mb-2">Übersicht</div>
                <a href="/dashboard" class="flex items-center gap-2.5 px-3 py-2 rounded-lg {'bg-indigo-600/10 text-indigo-400 font-semibold border border-indigo-500/20' if current_page == 'team' else 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'} transition">
                    👥 <span>Teamliste</span>
                </a>
                <a href="/loa" class="flex items-center gap-2.5 px-3 py-2 rounded-lg {'bg-indigo-600/10 text-indigo-400 font-semibold border border-indigo-500/20' if current_page == 'loa' else 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'} transition">
                    🌴 <span>Abmeldungen (LOA)</span>
                </a>
                <a href="/applications" class="flex items-center gap-2.5 px-3 py-2 rounded-lg {'bg-indigo-600/10 text-indigo-400 font-semibold border border-indigo-500/20' if current_page == 'apps' else 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'} transition">
                    📋 <span>Bewerbungen</span>
                </a>
                
                <div class="text-[10px] font-semibold text-slate-500 uppercase tracking-wider px-2 mt-4 mb-2">Verwaltung</div>
                <a href="/settings" class="flex items-center gap-2.5 px-3 py-2 rounded-lg {'bg-indigo-600/10 text-indigo-400 font-semibold border border-indigo-500/20' if current_page == 'settings' else 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'} transition">
                    ⚙️ <span>Rechte & Rollen</span>
                </a>
            </nav>
        </div>

        <div class="border-t border-slate-800/80 pt-3 px-1 flex items-center justify-between">
            <div class="flex items-center gap-2.5">
                <div class="w-7 h-7 rounded-full bg-slate-700 flex items-center justify-center text-xs font-bold text-white">B</div>
                <span class="text-xs font-medium text-slate-300 truncate">Bot Host</span>
            </div>
            <a href="/" class="text-xs text-slate-500 hover:text-rose-400 transition">↤ Abmelden</a>
        </div>
    </aside>
    """


# =============================================================
# ROUTEN: LOGIN & CALLBACK
# =============================================================
@app.get("/", response_class=HTMLResponse)
async def home():
    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8"><title>Teams Login</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-[#0b0e14] text-white min-h-screen flex items-center justify-center p-4 font-sans">
        <div class="bg-[#141824] p-8 rounded-2xl shadow-2xl w-full max-w-md text-center border border-slate-800">
            <div class="flex justify-center items-center gap-2 mb-6">
                <span class="text-3xl">🛡️</span>
                <h1 class="text-2xl font-bold text-white tracking-wide">Teams Dashboard</h1>
            </div>
            <p class="text-slate-400 text-sm mb-6">Bitte melde dich an, um auf das Team-Dashboard zuzugreifen.</p>
            <a href="{DISCORD_AUTH_URL}" class="inline-flex items-center justify-center gap-3 w-full bg-[#5865F2] hover:bg-[#4752C4] text-white font-semibold py-3 px-4 rounded-xl transition shadow-lg">
                Mit Discord anmelden
            </a>
            <div class="mt-4 pt-4 border-t border-slate-800">
                <a href="/apply" class="text-xs text-indigo-400 hover:underline">Du willst dich bewerben? Hier klicken!</a>
            </div>
        </div>
    </body>
    </html>
    """


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
    response.set_cookie(key="session", value="authenticated")
    return response


# =============================================================
# ROUTE: DASHBOARD / TEAMLISTE
# =============================================================
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    bot = getattr(request.app.state, "bot", None)
    if not bot:
        return "<h3>Bot-Instanz noch nicht bereit!</h3>"

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return f"<h3>Fehler: Server {GUILD_ID} nicht gefunden.</h3>"

    config = load_json(CONFIG_FILE, {"team_role_ids": [], "permissions": {}})
    team_db = load_json(DATA_FILE, {})
    team_role_ids = config.get("team_role_ids", [])

    today_str = datetime.now().strftime("%Y-%m-%d")

    team_members = []
    for member in guild.members:
        member_team_roles = [r for r in member.roles if r.id in team_role_ids]

        if member_team_roles:
            highest_role = max(member_team_roles, key=lambda r: r.position)

            user_data = team_db.get(str(member.id), {})
            loa = user_data.get("loa", {})

            # Prüfen, ob eine Abmeldung (LOA) aktiv ist
            loa_badge = ""
            if (
                loa.get("active")
                and loa.get("end")
                and loa.get("end") >= today_str
            ):
                end_formatted = datetime.strptime(
                    loa["end"], "%Y-%m-%d"
                ).strftime("%d.%m.")
                loa_badge = f'<span class="bg-amber-500/10 text-amber-400 border border-amber-500/30 text-[10px] px-2 py-0.5 rounded-full font-semibold">Abgemeldet bis {end_formatted}</span>'

            team_members.append({
                "id": member.id,
                "name": member.display_name,
                "username": member.name,
                "avatar": member.display_avatar.url,
                "top_role": highest_role.name,
                "top_role_color": (
                    f"#{highest_role.color.value:06x}"
                    if highest_role.color.value
                    else "#6366f1"
                ),
                "role_position": highest_role.position,
                "loa_badge": loa_badge,
            })

    team_members.sort(key=lambda m: m["role_position"], reverse=True)

    rows_html = ""
    for m in team_members:
        rows_html += f"""
        <div class="bg-[#141824] hover:bg-[#1a2030] transition border border-slate-800/80 rounded-xl px-5 py-3.5 flex items-center justify-between shadow-md">
            <div class="flex items-center gap-3.5 w-1/3">
                <img src="{m['avatar']}" class="w-10 h-10 rounded-full border border-slate-700">
                <div class="truncate">
                    <div class="font-semibold text-sm text-white flex items-center gap-2">
                        <span>{m['name']}</span>
                        {m['loa_badge']}
                    </div>
                    <div class="text-xs text-slate-400 font-mono">@{m['username']}</div>
                </div>
            </div>

            <div class="w-1/3 flex justify-start">
                <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border shadow-sm" style="background-color: {m['top_role_color']}15; color: {m['top_role_color']}; border-color: {m['top_role_color']}40;">
                    <span class="w-1.5 h-1.5 rounded-full" style="background-color: {m['top_role_color']}"></span>
                    {m['top_role']}
                </span>
            </div>

            <div class="w-1/3 flex items-center justify-end">
                <a href="/member/{m['id']}" title="Profil ansehen" class="p-2 hover:bg-slate-800 rounded-xl text-slate-400 hover:text-white transition">
                    👁️
                </a>
            </div>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8"><title>Teams - {guild.name}</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-[#0b0e14] text-slate-200 font-sans min-h-screen flex">
        {get_sidebar_html(guild.name, 'team')}
        <main class="flex-1 p-8 overflow-y-auto">
            <div class="flex justify-between items-center mb-6">
                <div>
                    <div class="text-xs text-slate-500 flex items-center gap-1.5 mb-1">
                        <span>Team</span> / <span class="text-indigo-400 font-medium">Teamliste</span>
                    </div>
                    <h1 class="text-2xl font-bold text-white">Teamliste</h1>
                </div>
            </div>

            <div class="px-5 py-2.5 text-xs font-semibold text-slate-500 flex items-center justify-between mb-2">
                <div class="w-1/3">Nutzer</div>
                <div class="w-1/3">Rolle</div>
                <div class="w-1/3 text-right">Aktionen</div>
            </div>

            <div class="space-y-2.5">
                {rows_html or "<div class='text-center py-12 text-slate-500 text-sm bg-[#141824] border border-slate-800 rounded-2xl'>Keine Teammitglieder gefunden.</div>"}
            </div>
        </main>
    </body>
    </html>
    """


# =============================================================
# ROUTE: DETAILSEITE FÜR MITGLIED
# =============================================================
@app.get("/member/{user_id}", response_class=HTMLResponse)
async def member_detail(request: Request, user_id: int):
    bot = getattr(request.app.state, "bot", None)
    if not bot:
        return "<h3>Bot-Instanz noch nicht bereit!</h3>"

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return f"<h3>Fehler: Server {GUILD_ID} nicht gefunden.</h3>"

    member = guild.get_member(user_id)
    if not member:
        return f"<h3>Mitglied mit ID {user_id} wurde nicht gefunden.</h3>"

    config = load_json(CONFIG_FILE, {"team_role_ids": [], "permissions": {}})
    team_db = load_json(DATA_FILE, {})
    team_role_ids = config.get("team_role_ids", [])

    user_info = team_db.get(
        str(user_id),
        {
            "warns": 0,
            "warns_list": [],
            "notes": [],
            "ticket_cases": 0,
            "support_cases": 0,
            "mod_cases": 0,
            "weekly_hours": "0h",
        },
    )

    member_team_roles = [r for r in member.roles if r.id in team_role_ids]
    highest_role = (
        max(member_team_roles, key=lambda r: r.position)
        if member_team_roles
        else member.top_role
    )

    top_role_name = highest_role.name
    top_role_color = (
        f"#{highest_role.color.value:06x}"
        if highest_role.color.value
        else "#6366f1"
    )

    # Verwarnungen mit Beweis-Links HTML generieren
    warns_html = ""
    for w in user_info.get("warns_list", []):
        proof_btn = (
            f'<a href="{w["proof"]}" target="_blank" class="text-indigo-400 hover:underline ml-2">🔗 Beweis anzeigen</a>'
            if w.get("proof")
            else ""
        )
        warns_html += f"""
        <div class="bg-[#0b0e14] p-3 rounded-lg border border-slate-800 text-xs space-y-1">
            <div class="flex justify-between text-slate-400 font-mono text-[10px]">
                <span>Datum: {w.get('date', 'N/A')}</span>
                <span>Von: {w.get('by', 'System')}</span>
            </div>
            <div class="text-slate-200"><strong>Grund:</strong> {w.get('reason', 'Kein Grund')} {proof_btn}</div>
        </div>
        """

    notes_html = "".join([
        f"<div class='text-xs bg-[#0b0e14] p-2 rounded-lg border border-slate-800 text-slate-300'>• {n}</div>"
        for n in user_info.get("notes", [])
    ])

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8"><title>{member.display_name} - Details</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-[#0b0e14] text-slate-200 font-sans min-h-screen flex">
        {get_sidebar_html(guild.name, 'team')}
        <main class="flex-1 p-8 overflow-y-auto">
            <div class="flex items-center gap-4 mb-8">
                <a href="/dashboard" class="bg-[#141824] border border-slate-800 hover:bg-slate-800 text-slate-300 p-2.5 rounded-xl transition">←</a>
                <img src="{member.display_avatar.url}" class="w-12 h-12 rounded-full border border-slate-700">
                <div>
                    <h1 class="text-xl font-bold text-white leading-tight">{member.display_name}</h1>
                    <p class="text-xs text-slate-400 font-mono">@{member.name}</p>
                </div>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div class="lg:col-span-2 space-y-6">
                    
                    <!-- Stats / Cases -->
                    <div class="grid grid-cols-3 gap-4">
                        <div class="bg-[#141824] border border-slate-800/80 rounded-xl p-5 shadow-md">
                            <div class="text-xs text-slate-400 font-semibold mb-1">Ticket-Cases</div>
                            <div class="text-2xl font-bold text-white">{user_info.get('ticket_cases', 0)}</div>
                        </div>
                        <div class="bg-[#141824] border border-slate-800/80 rounded-xl p-5 shadow-md">
                            <div class="text-xs text-slate-400 font-semibold mb-1">Support-Cases</div>
                            <div class="text-2xl font-bold text-white">{user_info.get('support_cases', 0)}</div>
                        </div>
                        <div class="bg-[#141824] border border-slate-800/80 rounded-xl p-5 shadow-md">
                            <div class="text-xs text-slate-400 font-semibold mb-1">Wochenstunden</div>
                            <div class="text-2xl font-bold text-indigo-400">{user_info.get('weekly_hours', '0h')}</div>
                        </div>
                    </div>

                    <!-- Team-Aktionen -->
                    <div class="bg-[#141824] border border-slate-800/80 rounded-xl p-6 space-y-4 shadow-md">
                        <h3 class="text-sm font-bold text-white">Team-Aktionen</h3>
                        <form action="/action" method="post" class="flex flex-wrap gap-2">
                            <input type="hidden" name="user_id" value="{member.id}">
                            <input type="hidden" name="redirect_to_member" value="1">
                            <button name="action" value="promote" class="bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-400 border border-emerald-500/30 px-3 py-1.5 rounded-lg text-xs font-semibold">⬆️ Befördern</button>
                            <button name="action" value="demote" class="bg-orange-600/20 hover:bg-orange-600/30 text-orange-400 border border-orange-500/30 px-3 py-1.5 rounded-lg text-xs font-semibold">⬇️ Degradieren</button>
                            <button name="action" value="kick" class="bg-rose-600/20 hover:bg-rose-600/30 text-rose-400 border border-rose-500/30 px-3 py-1.5 rounded-lg text-xs font-semibold">🚪 Vom Server kicken</button>
                        </form>

                        <hr class="border-slate-800 my-4">

                        <!-- Verwarnung mit Beweis ausstellen -->
                        <h3 class="text-sm font-bold text-white">Verwarnung ausstellen</h3>
                        <form action="/action" method="post" class="space-y-2">
                            <input type="hidden" name="user_id" value="{member.id}">
                            <input type="hidden" name="action" value="warn_with_proof">
                            <input type="hidden" name="redirect_to_member" value="1">
                            <input type="text" name="warn_reason" placeholder="Grund für die Verwarnung..." required class="bg-[#0b0e14] border border-slate-700 rounded-lg px-3 py-1.5 text-xs w-full text-white">
                            <input type="url" name="warn_proof" placeholder="Beweis-Link (z. B. Screenshot / Video URL)..." class="bg-[#0b0e14] border border-slate-700 rounded-lg px-3 py-1.5 text-xs w-full text-white">
                            <button class="bg-amber-600 hover:bg-amber-500 px-3 py-1.5 rounded-lg text-xs font-semibold text-white">⚠️ Verwarnung eintragen</button>
                        </form>

                        <hr class="border-slate-800 my-4">

                        <h3 class="text-sm font-bold text-white">Notizen</h3>
                        <div class="space-y-2 max-h-36 overflow-y-auto">
                            {notes_html or "<p class='text-xs text-slate-500 italic'>Keine Notizen hinterlegt.</p>"}
                        </div>
                        <form action="/action" method="post" class="flex gap-2 pt-1">
                            <input type="hidden" name="user_id" value="{member.id}">
                            <input type="hidden" name="action" value="add_note">
                            <input type="hidden" name="redirect_to_member" value="1">
                            <input type="text" name="note_text" placeholder="Neue Notiz..." required class="bg-[#0b0e14] border border-slate-700 rounded-lg px-3 py-1.5 text-xs w-full text-white">
                            <button class="bg-indigo-600 hover:bg-indigo-500 px-3 py-1.5 rounded-lg text-xs font-semibold text-white">Hinzufügen</button>
                        </form>
                    </div>

                    <!-- Historie der Verwarnungen & Beweise -->
                    <div class="bg-[#141824] border border-slate-800/80 rounded-xl p-6 space-y-3 shadow-md">
                        <h3 class="text-sm font-bold text-white">Verwarnungs-Historie ({len(user_info.get('warns_list', []))})</h3>
                        <div class="space-y-2 max-h-48 overflow-y-auto">
                            {warns_html or "<p class='text-xs text-slate-500 italic'>Keine Verwarnungen vorhanden.</p>"}
                        </div>
                    </div>

                </div>

                <!-- Info-Spalte rechts -->
                <div class="space-y-4">
                    <div class="text-right text-2xl font-extrabold uppercase tracking-widest opacity-90" style="color: {top_role_color};">
                        » BORP ✕ {top_role_name}
                    </div>

                    <div class="bg-[#141824] border border-slate-800/80 rounded-xl p-6 space-y-4 shadow-md text-xs">
                        <h3 class="text-sm font-bold text-white border-b border-slate-800 pb-2">Information</h3>
                        <div>
                            <div class="text-slate-500 mb-0.5">Nutzername</div>
                            <div class="text-slate-200 font-medium">[{top_role_name}] {member.display_name}</div>
                        </div>
                        <div>
                            <div class="text-slate-500 mb-0.5">ID</div>
                            <div class="text-slate-300 font-mono">{member.id}</div>
                        </div>
                        <div>
                            <div class="text-slate-500 mb-0.5">Verwarnungen insgesamt</div>
                            <div class="text-amber-400 font-bold">{len(user_info.get('warns_list', []))} / 3</div>
                        </div>
                    </div>
                </div>

            </div>
        </main>
    </body>
    </html>
    """


# =============================================================
# ROUTE: ABWESENHEITEN (LOA)
# =============================================================
@app.get("/loa", response_class=HTMLResponse)
async def loa_page(request: Request):
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    team_db = load_json(DATA_FILE, {})

    loa_entries_html = ""
    for user_id_str, udata in team_db.items():
        loa = udata.get("loa", {})
        if loa.get("active"):
            member = guild.get_member(int(user_id_str)) if guild else None
            name = member.display_name if member else f"ID: {user_id_str}"
            loa_entries_html += f"""
            <div class="bg-[#141824] border border-slate-800 rounded-xl p-4 flex justify-between items-center">
                <div>
                    <div class="font-bold text-white text-sm">{name}</div>
                    <div class="text-xs text-slate-400">📅 {loa.get('start')} bis {loa.get('end')}</div>
                    <div class="text-xs text-slate-300 mt-1"><strong>Grund:</strong> {loa.get('reason')}</div>
                </div>
                <form action="/action" method="post">
                    <input type="hidden" name="action" value="cancel_loa">
                    <input type="hidden" name="target_user_id" value="{user_id_str}">
                    <button class="bg-rose-600/20 hover:bg-rose-600/30 text-rose-400 border border-rose-500/30 text-xs px-3 py-1.5 rounded-lg">Beenden</button>
                </form>
            </div>
            """

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8"><title>Abmeldungen (LOA)</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-[#0b0e14] text-slate-200 font-sans min-h-screen flex">
        {get_sidebar_html(guild.name if guild else '', 'loa')}
        <main class="flex-1 p-8 overflow-y-auto">
            <h1 class="text-2xl font-bold text-white mb-6">Abwesenheiten (LOA)</h1>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
                <!-- LOA Einreichen -->
                <div class="bg-[#141824] border border-slate-800 rounded-xl p-6 space-y-4">
                    <h2 class="text-base font-bold text-white">Neue Abmeldung eintragen</h2>
                    <form action="/action" method="post" class="space-y-3 text-xs">
                        <input type="hidden" name="action" value="submit_loa">
                        <div>
                            <label class="block text-slate-400 mb-1">Mitglied ID</label>
                            <input type="number" name="user_id" placeholder="Discord ID eintragen..." required class="w-full bg-[#0b0e14] border border-slate-700 rounded-lg p-2.5 text-white">
                        </div>
                        <div class="grid grid-cols-2 gap-2">
                            <div>
                                <label class="block text-slate-400 mb-1">Startdatum</label>
                                <input type="date" name="loa_start" required class="w-full bg-[#0b0e14] border border-slate-700 rounded-lg p-2.5 text-white">
                            </div>
                            <div>
                                <label class="block text-slate-400 mb-1">Enddatum</label>
                                <input type="date" name="loa_end" required class="w-full bg-[#0b0e14] border border-slate-700 rounded-lg p-2.5 text-white">
                            </div>
                        </div>
                        <div>
                            <label class="block text-slate-400 mb-1">Grund</label>
                            <textarea name="loa_reason" placeholder="Grund für die Abmeldung..." required class="w-full bg-[#0b0e14] border border-slate-700 rounded-lg p-2.5 text-white h-20"></textarea>
                        </div>
                        <button class="w-full bg-indigo-600 hover:bg-indigo-500 font-semibold py-2.5 rounded-lg text-white">Abmeldung speichern</button>
                    </form>
                </div>

                <!-- Aktive LOAs -->
                <div class="space-y-4">
                    <h2 class="text-base font-bold text-white">Aktuell Abgemeldet</h2>
                    <div class="space-y-3">
                        {loa_entries_html or "<div class='text-xs text-slate-500 italic bg-[#141824] p-4 rounded-xl border border-slate-800'>Keine aktiven Abmeldungen.</div>"}
                    </div>
                </div>
            </div>
        </main>
    </body>
    </html>
    """


# =============================================================
# ROUTE: RECHTE & RECHTE-SCHLÜSSEL SETTINGS
# =============================================================
@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None

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
            <div class="bg-[#141824] border border-slate-800 rounded-xl p-5 space-y-3">
                <div class="flex justify-between items-center border-b border-slate-800 pb-2">
                    <span class="font-bold text-sm text-white" style="color: #{role.color.value:06x};">{role.name}</span>
                    <span class="text-[10px] text-slate-500 font-mono">ID: {role.id}</span>
                </div>
                <form action="/action" method="post" class="grid grid-cols-2 gap-2 text-xs">
                    <input type="hidden" name="action" value="save_role_permissions">
                    <input type="hidden" name="role_id" value="{role.id}">

                    <label class="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" name="can_view_dashboard" {'checked' if r_perm.get('can_view_dashboard') else ''} class="rounded bg-slate-900 border-slate-700">
                        <span>Dashboard sehen</span>
                    </label>
                    <label class="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" name="can_warn" {'checked' if r_perm.get('can_warn') else ''} class="rounded bg-slate-900 border-slate-700">
                        <span>Verwarnen</span>
                    </label>
                    <label class="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" name="can_promote" {'checked' if r_perm.get('can_promote') else ''} class="rounded bg-slate-900 border-slate-700">
                        <span>Befördern/Degradieren</span>
                    </label>
                    <label class="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" name="can_add_notes" {'checked' if r_perm.get('can_add_notes') else ''} class="rounded bg-slate-900 border-slate-700">
                        <span>Notizen erstellen</span>
                    </label>
                    <label class="flex items-center gap-2 cursor-pointer col-span-2 pt-2 border-t border-slate-800/60">
                        <input type="checkbox" name="can_manage_settings" {'checked' if r_perm.get('can_manage_settings') else ''} class="rounded bg-slate-900 border-slate-700">
                        <span class="text-amber-400 font-semibold">Einstellungen verwalten</span>
                    </label>

                    <button class="col-span-2 mt-2 bg-indigo-600 hover:bg-indigo-500 py-1.5 rounded-lg text-white font-semibold">Rechte Speichern</button>
                </form>
            </div>
            """

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8"><title>Einstellungen & Rechte</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-[#0b0e14] text-slate-200 font-sans min-h-screen flex">
        {get_sidebar_html(guild.name if guild else '', 'settings')}
        <main class="flex-1 p-8 overflow-y-auto">
            <h1 class="text-2xl font-bold text-white mb-2">Rollen-Berechtigungen</h1>
            <p class="text-xs text-slate-400 mb-6">Der Server-Owner hat automatisch bei allen Modulen Vollzugriff.</p>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                {roles_settings_html or "<p class='text-xs text-slate-500 italic'>Keine Team-Rollen konfiguriert.</p>"}
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
    return """
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8"><title>Bewerbung einreichen</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-[#0b0e14] text-white min-h-screen flex items-center justify-center p-4 font-sans">
        <div class="bg-[#141824] p-8 rounded-2xl shadow-2xl w-full max-w-lg border border-slate-800 space-y-4">
            <h1 class="text-xl font-bold text-center">Team-Bewerbung</h1>
            <p class="text-xs text-slate-400 text-center">Fülle das Formular aus, um dich bei uns im Team zu bewerben.</p>

            <form action="/action" method="post" class="space-y-3 text-xs">
                <input type="hidden" name="action" value="submit_application">
                <div>
                    <label class="block text-slate-400 mb-1">Deine Discord ID</label>
                    <input type="number" name="applicant_id" placeholder="1234567890..." required class="w-full bg-[#0b0e14] border border-slate-700 rounded-lg p-2.5 text-white">
                </div>
                <div>
                    <label class="block text-slate-400 mb-1">Nutzername</label>
                    <input type="text" name="applicant_name" placeholder="Dein Discord Name..." required class="w-full bg-[#0b0e14] border border-slate-700 rounded-lg p-2.5 text-white">
                </div>
                <div>
                    <label class="block text-slate-400 mb-1">Warum möchtest du ins Team?</label>
                    <textarea name="applicant_text" placeholder="Erzähle etwas über deine Erfahrung..." required class="w-full bg-[#0b0e14] border border-slate-700 rounded-lg p-2.5 text-white h-28"></textarea>
                </div>
                <button class="w-full bg-emerald-600 hover:bg-emerald-500 font-semibold py-3 rounded-xl text-white">Bewerbung Absenden</button>
            </form>
        </div>
    </body>
    </html>
    """


# =============================================================
# ROUTE: BEWERBUNGEN ÜBERSICHT (DASHBOARD)
# =============================================================
@app.get("/applications", response_class=HTMLResponse)
async def applications_page(request: Request):
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None

    apps = load_json(APPS_FILE, {})

    apps_html = ""
    for app_id, item in apps.items():
        if item.get("status") != "pending":
            continue

        upvotes = len(item.get("upvotes", []))
        downvotes = len(item.get("downvotes", []))

        apps_html += f"""
        <div class="bg-[#141824] border border-slate-800 rounded-xl p-5 space-y-3">
            <div class="flex justify-between items-center">
                <div>
                    <h3 class="font-bold text-white text-sm">{item.get('name')}</h3>
                    <span class="text-[10px] text-slate-400 font-mono">ID: {item.get('user_id')}</span>
                </div>
                <div class="flex items-center gap-2 text-xs">
                    <span class="text-emerald-400 font-bold">👍 {upvotes}</span>
                    <span class="text-rose-400 font-bold">👎 {downvotes}</span>
                </div>
            </div>

            <p class="text-xs text-slate-300 bg-[#0b0e14] p-3 rounded-lg border border-slate-800/80">{item.get('text')}</p>

            <div class="flex justify-between items-center pt-2 border-t border-slate-800/80">
                <form action="/action" method="post" class="flex gap-2">
                    <input type="hidden" name="action" value="vote_app">
                    <input type="hidden" name="app_id" value="{app_id}">
                    <button name="vote" value="up" class="bg-slate-800 hover:bg-slate-700 text-xs px-2.5 py-1 rounded-lg">👍 Dafür</button>
                    <button name="vote" value="down" class="bg-slate-800 hover:bg-slate-700 text-xs px-2.5 py-1 rounded-lg">👎 Dagegen</button>
                </form>

                <form action="/action" method="post" class="flex gap-2">
                    <input type="hidden" name="action" value="decide_app">
                    <input type="hidden" name="app_id" value="{app_id}">
                    <button name="decision" value="accept" class="bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-400 border border-emerald-500/30 text-xs px-3 py-1 rounded-lg font-semibold">Annehmen</button>
                    <button name="decision" value="reject" class="bg-rose-600/20 hover:bg-rose-600/30 text-rose-400 border border-rose-500/30 text-xs px-3 py-1 rounded-lg font-semibold">Ablehnen</button>
                </form>
            </div>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8"><title>Bewerbungen</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-[#0b0e14] text-slate-200 font-sans min-h-screen flex">
        {get_sidebar_html(guild.name if guild else '', 'apps')}
        <main class="flex-1 p-8 overflow-y-auto">
            <h1 class="text-2xl font-bold text-white mb-6">Offene Bewerbungen</h1>
            <div class="space-y-4 max-w-3xl">
                {apps_html or "<p class='text-xs text-slate-500 italic bg-[#141824] p-4 rounded-xl border border-slate-800'>Keine offenen Bewerbungen vorhanden.</p>"}
            </div>
        </main>
    </body>
    </html>
    """


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
    # Verwarnung mit Beweis
    warn_reason: str = Form(None),
    warn_proof: str = Form(None),
    # Notiz
    note_text: str = Form(None),
    # LOA
    loa_start: str = Form(None),
    loa_end: str = Form(None),
    loa_reason: str = Form(None),
    target_user_id: str = Form(None),
    # Bewerbung Form
    applicant_id: int = Form(None),
    applicant_name: str = Form(None),
    applicant_text: str = Form(None),
    app_id: str = Form(None),
    vote: str = Form(None),
    decision: str = Form(None),
    # Rechte
    can_view_dashboard: bool = Form(False),
    can_warn: bool = Form(False),
    can_promote: bool = Form(False),
    can_add_notes: bool = Form(False),
    can_manage_settings: bool = Form(False),
):
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None

    team_db = load_json(DATA_FILE, {})
    config = load_json(CONFIG_FILE, {"team_role_ids": [], "permissions": {}})
    apps = load_json(APPS_FILE, {})

    # 1. Verwarnung mit Beweis-Link
    if action == "warn_with_proof" and user_id:
        user_key = str(user_id)
        if user_key not in team_db:
            team_db[user_key] = {
                "warns": 0,
                "warns_list": [],
                "notes": [],
                "ticket_cases": 0,
                "support_cases": 0,
                "mod_cases": 0,
            }

        if "warns_list" not in team_db[user_key]:
            team_db[user_key]["warns_list"] = []

        team_db[user_key]["warns_list"].append({
            "reason": warn_reason,
            "proof": warn_proof,
            "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "by": "Dashboard Admin",
        })
        save_json(DATA_FILE, team_db)

    # 2. Notiz hinzufügen
    elif action == "add_note" and user_id and note_text:
        user_key = str(user_id)
        if user_key not in team_db:
            team_db[user_key] = {"notes": [], "warns_list": []}
        team_db[user_key]["notes"].append(note_text)
        save_json(DATA_FILE, team_db)

    # 3. Abmeldung (LOA) eintragen / beenden
    elif action == "submit_loa" and user_id:
        user_key = str(user_id)
        if user_key not in team_db:
            team_db[user_key] = {}
        team_db[user_key]["loa"] = {
            "active": True,
            "start": loa_start,
            "end": loa_end,
            "reason": loa_reason,
        }
        save_json(DATA_FILE, team_db)
        return RedirectResponse(url="/loa", status_code=303)

    elif action == "cancel_loa" and target_user_id:
        if target_user_id in team_db and "loa" in team_db[target_user_id]:
            team_db[target_user_id]["loa"]["active"] = False
            save_json(DATA_FILE, team_db)
        return RedirectResponse(url="/loa", status_code=303)

    # 4. Öffentliche Bewerbung einreichen
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

    # 5. Bewerbung abstimmen / entscheiden
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

    # 6. Rechte pro Rolle speichern
    elif action == "save_role_permissions" and role_id:
        if "permissions" not in config:
            config["permissions"] = {}
        config["permissions"][str(role_id)] = {
            "can_view_dashboard": can_view_dashboard,
            "can_warn": can_warn,
            "can_promote": can_promote,
            "can_add_notes": can_add_notes,
            "can_manage_settings": can_manage_settings,
        }
        save_json(CONFIG_FILE, config)
        return RedirectResponse(url="/settings", status_code=303)

    # Weiterleitung
    if redirect_to_member and user_id:
        return RedirectResponse(url=f"/member/{user_id}", status_code=303)

    return RedirectResponse(url="/dashboard", status_code=303)
