import os
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import httpx

load_dotenv()

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = "http://fi4.bot-hosting.cloud:25095/callback"

app = FastAPI()

DISCORD_AUTH_URL = (
    f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds"
)


@app.get("/", response_class=HTMLResponse)
async def home():
    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Team Dashboard - Login</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-white min-h-screen flex items-center justify-center p-4 font-sans">
        <div class="bg-slate-800 p-8 rounded-2xl shadow-2xl w-full max-w-md text-center border border-slate-700">
            <h1 class="text-2xl font-bold mb-2">Team-Dashboard</h1>
            <p class="text-slate-400 text-sm mb-6">Melde dich an, um auf dein Profil und die Einstellungen zuzugreifen.</p>
            <a href="{DISCORD_AUTH_URL}" class="inline-flex items-center justify-center gap-3 w-full bg-[#5865F2] hover:bg-[#4752C4] text-white font-semibold py-3 px-4 rounded-xl transition duration-200 shadow-md">
                <svg class="w-6 h-6 fill-current" viewBox="0 0 127.14 96.36">
                    <path d="M107.7,8.07A105.15,105.15,0,0,0,81.47,0a72.06,72.06,0,0,0-3.36,6.83A97.68,97.68,0,0,0,49,6.83,72.37,72.37,0,0,0,45.64,0,105.89,105.89,0,0,0,19.39,8.09C2.79,32.65-1.71,56.6.54,80.21h0A105.73,105.73,0,0,0,32.71,96.36,77.7,77.7,0,0,0,39.6,85.25a68.42,68.42,0,0,1-10.85-5.18c.91-.66,1.8-1.34,2.66-2a73.57,73.57,0,0,0,64.32,0c.87.68,1.76,1.36,2.66,2a68.68,68.68,0,0,1-10.87,5.19,77,77,0,0,0,6.89,11.1,105.25,105.25,0,0,0,32.19-16.14c2.64-27.38-4.51-51.11-21.6-72.13ZM42.45,65.69C36.18,65.69,31,60,31,53s5-12.74,11.43-12.74S54,45.91,53.87,53,48.8,65.69,42.45,65.69Zm42.24,0C78.41,65.69,73.25,60,73.25,53s5.08-12.74,11.44-12.74S96.23,45.91,96.1,53,91.08,65.69,84.69,65.69Z"/>
                </svg>
                Mit Discord anmelden
            </a>
        </div>
    </body>
    </html>
    """


@app.get("/callback", response_class=HTMLResponse)
async def callback(code: str):
    async with httpx.AsyncClient() as client:
        # 1. Access Token holen
        token_response = await client.post(
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
        token_data = token_response.json()
        access_token = token_data.get("access_token")

        if not access_token:
            return """
            <body style="background:#0f172a; color:white; text-align:center; padding-top:50px; font-family:sans-serif;">
                <h2>❌ Login fehlgeschlagen!</h2>
                <p>Der Code konnte nicht verifiziert werden.</p>
                <a href="/" style="color:#6366f1;">Zurück zur Startseite</a>
            </body>
            """

        # 2. Nutzerdaten abfragen
        user_response = await client.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user_data = user_response.json()

        # 3. Server-Liste abfragen
        guilds_response = await client.get(
            "https://discord.com/api/v10/users/@me/guilds",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        guilds_data = guilds_response.json()

    # Daten aufbereiten
    username = user_data.get("username", "Unbekannt")
    user_id = user_data.get("id", "")
    avatar = user_data.get("avatar")

    if avatar:
        avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png?size=256"
    else:
        avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"

    anzahl_server = (
        len(guilds_data) if isinstance(guilds_data, list) else 0
    )

    # Das styled Dashboard-HTML als Antwort
    return f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Dashboard - {username}</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-white min-h-screen flex items-center justify-center p-4 font-sans">
        <div class="bg-slate-800 p-8 rounded-2xl shadow-2xl w-full max-w-md text-center border border-slate-700">
            <!-- Profilbild -->
            <img src="{avatar_url}" alt="Avatar" class="w-24 h-24 rounded-full mx-auto mb-4 border-4 border-indigo-500 shadow-xl">
            
            <!-- Begrüßung -->
            <h1 class="text-2xl font-bold mb-1">Willkommen, <span class="text-indigo-400">{username}</span>! 👋</h1>
            <p class="text-slate-400 text-sm mb-6">Erfolgreich eingeloggt</p>
            
            <!-- Info Card -->
            <div class="bg-slate-900/80 p-4 rounded-xl border border-slate-700/60 mb-6 space-y-3 text-left">
                <div class="flex justify-between items-center text-sm">
                    <span class="text-slate-400">User ID:</span>
                    <span class="font-mono text-slate-200 text-xs bg-slate-800 px-2 py-1 rounded">{user_id}</span>
                </div>
                <div class="flex justify-between items-center text-sm">
                    <span class="text-slate-400">Deine Server:</span>
                    <span class="font-bold text-indigo-400">{anzahl_server}</span>
                </div>
            </div>

            <!-- Buttons -->
            <div class="space-y-3">
                <a href="/" class="block w-full bg-slate-700 hover:bg-slate-600 text-slate-200 font-semibold py-2.5 px-4 rounded-xl transition duration-200">
                    Abmelden / Zurück
                </a>
            </div>
        </div>
    </body>
    </html>
    """
