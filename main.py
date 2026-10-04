import asyncio
import os
from pathlib import Path
import discord
from discord.ext import commands
from dotenv import load_dotenv
import uvicorn

from webserver import app

BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)
load_dotenv(BASE_DIR / ".env")
TOKEN = os.getenv("DISCORD_TOKEN")
PORT = int(os.getenv("SERVER_PORT", 25095))


class CustomBot(commands.Bot):

    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True  # Rollen & Teamler erkennen
        intents.presences = True
        intents.message_content = True

        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # WICHTIG: Bot-Instanz an die Webserver-App übergeben
        app.state.bot = self

        # Cogs aus dem Ordner /cogs laden
        if os.path.exists("./cogs"):
            for filename in os.listdir("./cogs"):
                if filename.endswith(".py") and not filename.startswith("__"):
                    try:
                        await self.load_extension(f"cogs.{filename[:-3]}")
                        print(f"📦 Cog geladen: {filename[:-3]}")
                    except Exception as e:
                        print(f"❌ Fehler beim Laden von {filename}: {e}")

        # Slash Commands synchronisieren
        try:
            synced = await self.tree.sync()
            print(f"🔄 {len(synced)} Slash Commands synchronisiert!")
        except Exception as e:
            print(f"❌ Fehler beim Sync: {e}")

        # Webserver im Hintergrund starten
        config = uvicorn.Config(
            app=app, host="0.0.0.0", port=PORT, log_level="info"
        )
        server = uvicorn.Server(config)
        asyncio.create_task(server.serve())
        print(f"🌐 Webserver gestartet auf Port {PORT}")

    async def on_ready(self):
        print(f"✅ Bot ist online als {self.user} (ID: {self.user.id})")
        try:
            from webserver import load_config, reconcile_warning_roles
            guild = self.get_guild(int(os.getenv("DISCORD_GUILD_ID", "1474514929351524616")))
            report = await reconcile_warning_roles(guild, load_config())
            print(
                f"🛡️ Warnrollen abgeglichen: {report['checked']} geprüft, "
                f"{report['updated']} synchron, {report['failed']} Fehler."
            )
            if report["errors"]:
                for err in report["errors"]:
                    print(f"   ⚠️ {err}")
        except Exception as exc:
            print(f"⚠️ Warnrollen-Startup-Abgleich fehlgeschlagen: {exc}")


bot = CustomBot()

if __name__ == "__main__":
    if not TOKEN:
        print("❌ FEHLER: Kein DISCORD_TOKEN in der .env-Datei gefunden!")
    else:
        bot.run(TOKEN)
