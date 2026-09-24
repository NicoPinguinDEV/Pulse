import json
import os
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx

load_dotenv()

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = "http://fi4.bot-hosting.cloud:25095/callback"

# =============================================================
# EINSTELLUNGEN
# =============================================================
GUILD_ID = 1474514929351524616  # DEINE DISCORD SERVER-ID

DATA_FILE = "team_data.json"
CONFIG_FILE = "config.json"

app = FastAPI()

DISCORD_AUTH_URL = (
    f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds"
)


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"team_role_ids": []}


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


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
            <p class="text-slate-400 text-sm mb-6">Bitte melde dich an, um auf die Teamliste zuzugreifen.</p>
            <a href="{DISCORD_AUTH_URL}" class="inline-flex items-center justify-center gap-3 w-full bg-[#5865F2] hover:bg-[#4752C4] text-white font-semibold py-3 px-4 rounded-xl transition shadow-lg">
                Mit Discord anmelden
            </a>
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


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    bot = getattr(request.app.state, "bot", None)
    if not bot:
        return "<h3>Bot-Instanz noch nicht bereit!</h3>"

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return f"<h3>Fehler: Server mit ID {GUILD_ID} wurde nicht gefunden.</h3>"

    config = load_config()
    team_role_ids = config.get("team_role_ids", [])
    team_db = load_data()

    team_members = []
    for member in guild.members:
        member_role_ids = [r.id for r in member.roles]

        if any(rid in member_role_ids for rid in team_role_ids):
            user_id_str = str(member.id)
            user_info = team_db.get(user_id_str, {"warns": 0, "notes": []})

            highest_team_role = None
            for rid in reversed(team_role_ids):
                if rid in member_role_ids:
                    highest_team_role = guild.get_role(rid)
                    break

            team_members.append({
                "id": member.id,
                "name": member.display_name,
                "username": member.name,
                "avatar": member.display_avatar.url,
                "top_role": (
                    highest_team_role.name
                    if highest_team_role
                    else member.top_role.name
                ),
                "top_role_color": (
                    f"#{highest_team_role.color.value:06x}"
                    if highest_team_role and highest_team_role.color.value
                    else "#6366f1"
                ),
                "warns": user_info.get("warns", 0),
                "notes": user_info.get("notes", []),
            })

    # Erzeugen der Zeilen für die Tabellenansicht
    rows_html = ""
    for m in team_members:
        notes_html = "".join([
            f"<div class='text-[11px] bg-[#0b0e14] px-2 py-0.5 rounded border border-slate-800 text-slate-300'>• {n}</div>"
            for n in m["notes"]
        ])

        rows_html += f"""
        <div class="bg-[#141824] hover:bg-[#1a2030] transition border border-slate-800/80 rounded-xl px-5 py-3.5 flex items-center justify-between shadow-md group">
            <!-- Nutzer Info -->
            <div class="flex items-center gap-3.5 w-1/3">
                <img src="{m['avatar']}" class="w-10 h-10 rounded-full border border-slate-700">
                <div class="truncate">
                    <div class="font-semibold text-sm text-white flex items-center gap-1.5">
                        {m['name']}
                    </div>
                    <div class="text-xs text-slate-400 font-mono">@{m['username']}</div>
                </div>
            </div>

            <!-- Rolle Badge -->
            <div class="w-1/3 flex justify-start">
                <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border shadow-sm" style="background-color: {m['top_role_color']}15; color: {m['top_role_color']}; border-color: {m['top_role_color']}40;">
                    <span class="w-1.5 h-1.5 rounded-full" style="background-color: {m['top_role_color']}"></span>
                    {m['top_role']}
                </span>
            </div>

            <!-- Aktionen / Details -->
            <div class="w-1/3 flex items-center justify-end gap-2">
                <!-- Schnellaktionen & Notizen Formular -->
                <form action="/action" method="post" class="flex items-center gap-1">
                    <input type="hidden" name="user_id" value="{m['id']}">
                    
                    <button name="action" value="promote" title="Befördern" class="p-1.5 hover:bg-emerald-500/20 text-emerald-400 rounded-lg text-xs transition">⬆️</button>
                    <button name="action" value="demote" title="Degradieren" class="p-1.5 hover:bg-orange-500/20 text-orange-400 rounded-lg text-xs transition">⬇️</button>
                    <button name="action" value="warn" title="Verwarnen ({m['warns']})" class="p-1.5 hover:bg-amber-500/20 text-amber-400 rounded-lg text-xs transition flex items-center gap-1">
                        ⚠️ <span class="text-[10px] bg-amber-500/20 px-1 rounded">{m['warns']}</span>
                    </button>
                    <button name="action" value="kick" title="Kicken" class="p-1.5 hover:bg-rose-500/20 text-rose-400 rounded-lg text-xs transition">🚪</button>
                </form>

                <details class="relative">
                    <summary class="list-none p-1.5 hover:bg-slate-800 rounded-lg text-slate-400 hover:text-white cursor-pointer transition">
                        👁️
                    </summary>
                    <div class="absolute right-0 top-8 z-50 w-64 bg-[#1a2030] border border-slate-700 p-3 rounded-xl shadow-2xl space-y-2">
                        <div class="text-xs font-bold text-slate-300">Notizen:</div>
                        <div class="space-y-1 max-h-32 overflow-y-auto">
                            {notes_html or "<p class='text-[11px] text-slate-500 italic'>Keine Notizen vorhanden</p>"}
                        </div>
                        <form action="/action" method="post" class="flex gap-1 pt-1">
                            <input type="hidden" name="user_id" value="{m['id']}">
                            <input type="hidden" name="action" value="add_note">
                            <input type="text" name="note_text" placeholder="Neue Notiz..." required class="bg-[#0b0e14] border border-slate-700 rounded px-2 py-1 text-xs w-full text-white">
                            <button class="bg-indigo-600 hover:bg-indigo-500 px-2 py-1 rounded text-xs font-semibold text-white">+</button>
                        </form>
                    </div>
                </details>
            </div>
        </div>
        """

    # Rollen-Verwaltung Dropdown
    server_roles_options = ""
    for role in guild.roles:
        if not role.is_default() and role.id not in team_role_ids:
            server_roles_options += f'<option value="{role.id}">{role.name}</option>'

    roles_badge_html = ""
    for idx, rid in enumerate(team_role_ids, 1):
        role_obj = guild.get_role(rid)
        r_name = role_obj.name if role_obj else f"ID: {rid}"
        roles_badge_html += f"""
        <div class="flex items-center justify-between bg-[#0b0e14] border border-slate-800 px-3 py-1.5 rounded-lg text-xs">
            <span class="text-slate-300"><strong class="text-indigo-400">Rang {idx}:</strong> {r_name}</span>
            <form action="/action" method="post" class="inline">
                <input type="hidden" name="action" value="remove_role">
                <input type="hidden" name="role_id" value="{rid}">
                <button class="text-rose-400 hover:text-rose-300 ml-2 font-bold">✕</button>
            </form>
        </div>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Teams - {guild.name}</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-[#0b0e14] text-slate-200 font-sans min-h-screen flex">

        <!-- Sidebar Navigation -->
        <aside class="w-64 bg-[#141824] border-r border-slate-800/80 flex flex-col justify-between p-4 min-h-screen shrink-0">
            <div class="space-y-6">
                <!-- Branding -->
                <div class="flex items-center gap-3 px-2">
                    <div class="w-8 h-8 rounded-xl bg-indigo-600 flex items-center justify-center font-bold text-white shadow-lg">T</div>
                    <div>
                        <h2 class="font-bold text-white leading-none">Teams</h2>
                        <span class="text-[10px] text-slate-500 font-mono">v1.0.0</span>
                    </div>
                </div>

                <!-- Server Selector -->
                <div class="bg-[#0b0e14] border border-slate-800 rounded-xl p-2.5 flex items-center justify-between cursor-pointer">
                    <div class="flex items-center gap-2 truncate">
                        <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
                        <span class="text-xs font-semibold text-slate-200 truncate">{guild.name}</span>
                    </div>
                    <span class="text-xs text-slate-500">▾</span>
                </div>

                <!-- Nav Menu -->
                <nav class="space-y-1 text-xs">
                    <div class="text-[10px] font-semibold text-slate-500 uppercase tracking-wider px-2 mb-2">Übersicht</div>
                    <a href="#" class="flex items-center gap-2.5 px-3 py-2 rounded-lg text-slate-400 hover:bg-slate-800/50 hover:text-slate-200 transition">
                        📊 <span>Dashboard</span>
                    </a>
                    
                    <div class="text-[10px] font-semibold text-slate-500 uppercase tracking-wider px-2 mt-4 mb-2">Team</div>
                    <a href="/dashboard" class="flex items-center gap-2.5 px-3 py-2 rounded-lg bg-indigo-600/10 text-indigo-400 font-semibold border border-indigo-500/20">
                        👥 <span>Teamliste</span>
                    </a>
                </nav>
            </div>

            <!-- Footer User Info -->
            <div class="border-t border-slate-800/80 pt-3 px-1 flex items-center justify-between">
                <div class="flex items-center gap-2.5">
                    <div class="w-7 h-7 rounded-full bg-slate-700 flex items-center justify-center text-xs font-bold text-white">B</div>
                    <span class="text-xs font-medium text-slate-300 truncate">Bot Host</span>
                </div>
                <a href="/" class="text-xs text-slate-500 hover:text-rose-400 transition">↤ Abmelden</a>
            </div>
        </aside>

        <!-- Main Content Area -->
        <main class="flex-1 p-8 overflow-y-auto">
            <!-- Header -->
            <div class="flex justify-between items-center mb-6">
                <div>
                    <div class="text-xs text-slate-500 flex items-center gap-1.5 mb-1">
                        <span>Team</span>
                        <span>/</span>
                        <span class="text-indigo-400 font-medium">Teamliste</span>
                    </div>
                    <h1 class="text-2xl font-bold text-white">Teamliste</h1>
                </div>

                <details class="relative">
                    <summary class="bg-[#141824] border border-slate-800 hover:border-slate-700 text-xs px-3 py-2 rounded-xl text-slate-300 cursor-pointer transition flex items-center gap-2">
                        <span>⚙️ Team-Rollen verwalten</span>
                    </summary>
                    <div class="absolute right-0 top-10 w-80 bg-[#141824] border border-slate-700 p-4 rounded-xl shadow-2xl z-50 space-y-4">
                        <h3 class="text-xs font-bold text-white">Aktivierte Team-Rollen:</h3>
                        <div class="space-y-1.5 max-h-40 overflow-y-auto">
                            {roles_badge_html or "<p class='text-xs text-slate-500 italic'>Keine Rollen hinterlegt.</p>"}
                        </div>
                        <form action="/action" method="post" class="space-y-2 pt-2 border-t border-slate-800">
                            <input type="hidden" name="action" value="add_role">
                            <select name="role_id" required class="w-full bg-[#0b0e14] border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white">
                                <option value="" disabled selected>Rolle auswählen...</option>
                                {server_roles_options or "<option disabled>Alle hinzugefügt</option>"}
                            </select>
                            <button class="w-full bg-indigo-600 hover:bg-indigo-500 text-white py-1.5 rounded-lg text-xs font-semibold">Rolle hinzufügen</button>
                        </form>
                    </div>
                </details>
            </div>

            <!-- Table Header -->
            <div class="px-5 py-2.5 text-xs font-semibold text-slate-500 flex items-center justify-between mb-2">
                <div class="w-1/3 flex items-center gap-1">Nutzer ⇂⇞</div>
                <div class="w-1/3 flex items-center gap-1">Rolle ⇂⇞</div>
                <div class="w-1/3 text-right">Aktionen</div>
            </div>

            <!-- Table Rows -->
            <div class="space-y-2.5">
                {rows_html or "<div class='text-center py-12 text-slate-500 text-sm bg-[#141824] border border-slate-800 rounded-2xl'>Keine Teammitglieder gefunden. Füge oben unter '⚙️ Team-Rollen verwalten' deine Server-Rollen hinzu.</div>"}
            </div>
        </main>

    </body>
    </html>
    """


@app.post("/action")
async def handle_action(
    request: Request,
    user_id: int = Form(None),
    action: str = Form(...),
    note_text: str = Form(None),
    role_id: int = Form(None),
):
    bot = getattr(request.app.state, "bot", None)
    if not bot:
        return RedirectResponse(url="/dashboard", status_code=303)

    guild = bot.get_guild(GUILD_ID)

    if action == "add_role" and role_id:
        config = load_config()
        if role_id not in config["team_role_ids"]:
            config["team_role_ids"].append(role_id)
            save_config(config)

    elif action == "remove_role" and role_id:
        config = load_config()
        if role_id in config["team_role_ids"]:
            config["team_role_ids"].remove(role_id)
            save_config(config)

    if user_id:
        member = guild.get_member(user_id) if guild else None
        team_db = load_data()
        user_key = str(user_id)

        if user_key not in team_db:
            team_db[user_key] = {"warns": 0, "notes": []}

        config = load_config()
        team_role_ids = config.get("team_role_ids", [])

        if action == "add_note" and note_text:
            team_db[user_key]["notes"].append(note_text)
            save_data(team_db)

        elif action == "warn":
            team_db[user_key]["warns"] += 1
            save_data(team_db)
            if member:
                try:
                    await member.send(
                        f"⚠️ Du wurdest auf **{guild.name}** über das Team-Dashboard verwarnt!"
                    )
                except Exception:
                    pass

        elif action == "kick" and member:
            try:
                await member.kick(reason="Gekickt über Team Dashboard")
            except Exception as e:
                print(f"Fehler beim Kicken: {e}")

        elif action == "promote" and member:
            member_role_ids = [r.id for r in member.roles]
            current_idx = -1
            for idx, rid in enumerate(team_role_ids):
                if rid in member_role_ids:
                    current_idx = idx

            if current_idx + 1 < len(team_role_ids):
                next_role_id = team_role_ids[current_idx + 1]
                next_role = guild.get_role(next_role_id)
                if next_role:
                    if current_idx >= 0:
                        old_role = guild.get_role(team_role_ids[current_idx])
                        if old_role:
                            await member.remove_roles(old_role)
                    await member.add_roles(next_role)

        elif action == "demote" and member:
            member_role_ids = [r.id for r in member.roles]
            current_idx = -1
            for idx, rid in enumerate(team_role_ids):
                if rid in member_role_ids:
                    current_idx = idx

            if current_idx > 0:
                prev_role_id = team_role_ids[current_idx - 1]
                prev_role = guild.get_role(prev_role_id)
                old_role = guild.get_role(team_role_ids[current_idx])

                if prev_role and old_role:
                    await member.remove_roles(old_role)
                    await member.add_roles(prev_role)

    return RedirectResponse(url="/dashboard", status_code=303)
