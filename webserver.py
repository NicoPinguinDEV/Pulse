import os
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx

load_dotenv()

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8000/callback"

app = FastAPI()

# Der OAuth2-Link für die Anmeldung
DISCORD_AUTH_URL = (
    f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds"
)


@app.get("/", response_class=HTMLResponse)
async def home():
    # Einfache Startseite mit einem Login-Button
    return f"""
    <html>
        <head><title>Dashboard Login</title></head>
        <body style="font-family: Arial; text-align: center; margin-top: 50px; background-color: #1e1e2e; color: white;">
            <h1>Willkommen auf dem Team-Dashboard</h1>
            <a href="{DISCORD_AUTH_URL}">
                <button style="padding: 12px 24px; font-size: 16px; background-color: #5865F2; color: white; border: none; border-radius: 5px; cursor: pointer;">
                    Mit Discord anmelden
                </button>
            </a>
        </body>
    </html>
    """


@app.get("/callback")
async def callback(code: str):
    async with httpx.AsyncClient() as client:
        # 1. Den empfangenen Code gegen einen Access Token eintauschen
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
            return {"error": "Login fehlgeschlagen", "details": token_data}

        # 2. Mit dem Access Token die Nutzerdaten bei Discord abfragen
        user_response = await client.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        user_data = user_response.json()

        # 3. Optional: Prüfen, auf welchen Servern der Nutzer ist
        guilds_response = await client.get(
            "https://discord.com/api/v10/users/@me/guilds",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        guilds_data = guilds_response.json()

    # Renders die Daten testweise im Browser
    return {
        "status": "Erfolgreich angemeldet!",
        "username": user_data.get("username"),
        "user_id": user_data.get("id"),
        "avatar_url": f"https://cdn.discordapp.com/avatars/{user_data.get('id')}/{user_data.get('avatar')}.png",
        "anzahl_server": len(guilds_data)
        if isinstance(guilds_data, list)
        else 0,
    }
