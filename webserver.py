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


# Helper: Notizen & Verwarnungen
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


# Helper: Dynamische Server-Konfiguration (Team-Rollen)
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
        <meta charset="UTF-8"><title>Team Dashboard - Login</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-white min-h-screen flex items-center justify-center p-4 font-sans">
        <div class="bg-slate-800 p-8 rounded-2xl shadow-2xl w-full max-w-md text-center border border-slate-700">
            <h1 class="text-2xl font-bold mb-2">Team-Verwaltung</h1>
            <p class="text-slate-400 text-sm mb-6">Logge dich ein, um das Team-Dashboard aufzurufen.</p>
            <a href="{DISCORD_AUTH_URL}" class="inline-flex items-center justify-center gap-3 w-full bg-[#5865F2] hover:bg-[#4752C4] text-white font-semibold py-3 px-4 rounded-xl transition shadow-md">
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

    # Teammitglieder ermitteln
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
                "avatar": member.display_avatar.url,
                "top_role": (
                    highest_team_role.name
                    if highest_team_role
                    else member.top_role.name
                ),
                "warns": user_info.get("warns", 0),
                "notes": user_info.get("notes", []),
            })

    # UI-Karten für Mitglieder
    cards_html = ""
    for m in team_members:
        notes_list = "".join([
            f"<li class='text-xs text-slate-300 bg-slate-900/40 p-1.5 rounded border border-slate-700/40'>• {n}</li>"
            for n in m["notes"]
        ])

        cards_html += f"""
        <div class="bg-slate-800 border border-slate-700 rounded-2xl p-5 flex flex-col justify-between shadow-xl">
            <div>
                <div class="flex items-center gap-4 mb-4">
                    <img src="{m['avatar']}" class="w-14 h-14 rounded-full border-2 border-indigo-500 shadow-md">
                    <div>
                        <h3 class="font-bold text-lg text-white">{m['name']}</h3>
                        <span class="text-xs bg-indigo-500/20 text-indigo-400 px-2.5 py-1 rounded-full font-semibold">{m['top_role']}</span>
                    </div>
                </div>

                <div class="bg-slate-900/60 p-3.5 rounded-xl mb-4 text-sm space-y-2 border border-slate-700/50">
                    <div class="flex justify-between items-center">
                        <span class="text-slate-400 text-xs">Verwarnungen:</span>
                        <span class="font-bold text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded text-xs">{m['warns']}</span>
                    </div>
                    <div>
                        <span class="text-slate-400 text-xs block mb-1">Notizen:</span>
                        <ul class="space-y-1 max-h-24 overflow-y-auto">{notes_list or "<span class='text-xs text-slate-500 italic'>Keine Notizen</span>"}</ul>
                    </div>
                </div>
            </div>

            <div class="space-y-2 pt-3 border-t border-slate-700/60">
                <div class="grid grid-cols-2 gap-2">
                    <form action="/action" method="post">
                        <input type="hidden" name="user_id" value="{m['id']}">
                        <input type="hidden" name="action" value="promote">
                        <button class="w-full bg-emerald-600/20 hover:bg-emerald-600 text-emerald-300 hover:text-white py-1.5 rounded-lg text-xs font-semibold transition">⬆️ Befördern</button>
                    </form>
                    <form action="/action" method="post">
                        <input type="hidden" name="user_id" value="{m['id']}">
                        <input type="hidden" name="action" value="demote">
                        <button class="w-full bg-orange-600/20 hover:bg-orange-600 text-orange-300 hover:text-white py-1.5 rounded-lg text-xs font-semibold transition">⬇️ Degradieren</button>
                    </form>
                </div>

                <div class="grid grid-cols-2 gap-2">
                    <form action="/action" method="post">
                        <input type="hidden" name="user_id" value="{m['id']}">
                        <input type="hidden" name="action" value="warn">
                        <button class="w-full bg-amber-600/20 hover:bg-amber-600 text-amber-300 hover:text-white py-1.5 rounded-lg text-xs font-semibold transition">⚠️ Verwarnen</button>
                    </form>
                    <form action="/action" method="post">
                        <input type="hidden" name="user_id" value="{m['id']}">
                        <input type="hidden" name="action" value="kick">
                        <button class="w-full bg-rose-600/20 hover:bg-rose-600 text-rose-300 hover:text-white py-1.5 rounded-lg text-xs font-semibold transition">🚪 Kicken</button>
                    </form>
                </div>

                <form action="/action" method="post" class="flex gap-2 pt-1">
                    <input type="hidden" name="user_id" value="{m['id']}">
                    <input type="hidden" name="action" value="add_note">
                    <input type="text" name="note_text" placeholder="Notiz schreiben..." required class="bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1 text-xs w-full focus:outline-none focus:border-indigo-500 text-white">
                    <button class="bg-indigo-600 hover:bg-indigo-500 px-3 py-1 rounded-lg text-xs font-semibold transition">+</button>
                </form>
            </div>
        </div>
        """

    # UI für Rollen-Verwaltung
    roles_list_html = ""
    for idx, rid in enumerate(team_role_ids, 1):
        role_obj = guild.get_role(rid)
        role_name = role_obj.name if role_obj else f"Gelöschte Rolle ({rid})"
        roles_list_html += f"""
        <div class="flex justify-between items-center bg-slate-900/60 border border-slate-700 px-3 py-2 rounded-xl text-sm">
            <span class="text-slate-300"><strong class="text-indigo-400">Rang {idx}:</strong> {role_name}</span>
            <form action="/action" method="post" class="inline">
                <input type="hidden" name="action" value="remove_role">
                <input type="hidden" name="role_id" value="{rid}">
                <button class="text-rose-400 hover:text-rose-300 text-xs font-semibold px-2 py-1 rounded bg-rose-500/10 hover:bg-rose-500/20 transition">Entfernen</button>
            </form>
        </div>
        """

    # Dropdown mit allen verfügbaren Serverrollen (außer @everyone)
    server_roles_options = ""
    for role in guild.roles:
        if role.is_default():
            continue
        if role.id not in team_role_ids:
            server_roles_options += f'<option value="{role.id}">{role.name}</option>'

    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Team Dashboard</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-white min-h-screen p-6 font-sans">
        <div class="max-w-7xl mx-auto space-y-8">
            <div class="flex justify-between items-center border-b border-slate-800 pb-5">
                <div>
                    <h1 class="text-3xl font-bold">Team Dashboard</h1>
                    <p class="text-slate-400 text-sm">Übersicht aller Teammitglieder und Verwaltungs-Tools</p>
                </div>
                <a href="/" class="bg-slate-800 hover:bg-slate-700 text-slate-300 px-4 py-2 rounded-xl text-sm font-semibold transition">Abmelden</a>
            </div>

            <!-- Rollen-Verwaltung Section -->
            <div class="bg-slate-800 border border-slate-700 rounded-2xl p-6 shadow-xl">
                <h2 class="text-xl font-bold mb-1 text-white">⚙️ Team-Rollen verwalten</h2>
                <p class="text-xs text-slate-400 mb-4">Füge Rollen von unten nach oben hinzu (Rang 1 = Niedrigste Rolle, Rang 2 = Höhere Rolle, etc.).</p>
                
                <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <!-- Aktuelle Rollen -->
                    <div class="space-y-2">
                        <h3 class="text-sm font-semibold text-slate-300 mb-2">Aktive Team-Rollen:</h3>
                        {roles_list_html or "<p class='text-xs text-slate-500 italic'>Noch keine Team-Rollen hinzugefügt.</p>"}
                    </div>

                    <!-- Rolle Hinzufügen Formular -->
                    <div class="bg-slate-900/40 p-4 rounded-xl border border-slate-700/50 h-fit">
                        <h3 class="text-sm font-semibold text-slate-300 mb-3">Neue Rolle hinzufügen</h3>
                        <form action="/action" method="post" class="space-y-3">
                            <input type="hidden" name="action" value="add_role">
                            <select name="role_id" required class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-indigo-500">
                                <option value="" disabled selected>Server-Rolle auswählen...</option>
                                {server_roles_options or "<option disabled>Alle Rollen bereits hinzugefügt</option>"}
                            </select>
                            <button class="w-full bg-indigo-600 hover:bg-indigo-500 text-white py-2 rounded-lg text-sm font-semibold transition">Rolle hinzufügen</button>
                        </form>
                    </div>
                </div>
            </div>

            <!-- Teammitglieder Liste -->
            <div>
                <h2 class="text-2xl font-bold mb-4">👥 Teammitglieder</h2>
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {cards_html or "<p class='text-slate-500 col-span-3 text-center py-10'>Keine Teammitglieder gefunden.</p>"}
                </div>
            </div>
        </div>
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

    # 1. Rolle hinzufügen
    if action == "add_role" and role_id:
        config = load_config()
        if role_id not in config["team_role_ids"]:
            config["team_role_ids"].append(role_id)
            save_config(config)

    # 2. Rolle entfernen
    elif action == "remove_role" and role_id:
        config = load_config()
        if role_id in config["team_role_ids"]:
            config["team_role_ids"].remove(role_id)
            save_config(config)

    # Ab hier Aktionen, die einen Nutzer benötigen
    if user_id:
        member = guild.get_member(user_id) if guild else None
        team_db = load_data()
        user_key = str(user_id)

        if user_key not in team_db:
            team_db[user_key] = {"warns": 0, "notes": []}

        config = load_config()
        team_role_ids = config.get("team_role_ids", [])

        # 3. Notiz hinzufügen
        if action == "add_note" and note_text:
            team_db[user_key]["notes"].append(note_text)
            save_data(team_db)

        # 4. Verwarnen
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

        # 5. Kicken
        elif action == "kick" and member:
            try:
                await member.kick(reason="Gekickt über Team Dashboard")
            except Exception as e:
                print(f"Fehler beim Kicken: {e}")

        # 6. Befördern (Promote)
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

        # 7. Degradieren (Demote)
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
