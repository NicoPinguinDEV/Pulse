import asyncio
import os
from pathlib import Path
import discord
from discord.ext import commands
from dotenv import load_dotenv
import uvicorn

BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)

# .env muss vor dem Import von webserver/pulse_db geladen sein, da diese
# Module ihre Konfiguration beim Import lesen.
load_dotenv(BASE_DIR / ".env")

from webserver import app

TOKEN = os.getenv("DISCORD_TOKEN")
PORT = int(os.getenv("SERVER_PORT", 25095))

# discord.py caches recent messages to resolve references and certain gateway
# events. On a small hosting plan, 1,000 Message objects can needlessly consume
# memory. Keep a useful cache and make the limit configurable without disabling
# any Cog or intent.
try:
    MESSAGE_CACHE_SIZE = max(100, min(1000, int(os.getenv("DISCORD_MESSAGE_CACHE_SIZE", "250"))))
except (TypeError, ValueError):
    MESSAGE_CACHE_SIZE = 250


class CustomBot(commands.Bot):

    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True  # Rollen & Teamler erkennen
        intents.presences = True
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            max_messages=MESSAGE_CACHE_SIZE,
        )
        self.pulse_message_cache_limit = MESSAGE_CACHE_SIZE
        self._pulse_web_server = None
        self._pulse_web_server_task = None
        # Startup report data (shown by the Pulse Pro cog in the alert channel).
        self.pulse_loaded_cogs = []
        self.pulse_failed_cogs = []
        self.pulse_synced_commands = None

    async def setup_hook(self):
        # WICHTIG: Bot-Instanz an die Webserver-App übergeben
        app.state.bot = self

        # Cogs aus dem Ordner /cogs laden
        if os.path.exists("./cogs"):
            for filename in os.listdir("./cogs"):
                if filename.endswith(".py") and not filename.startswith("__"):
                    try:
                        await self.load_extension(f"cogs.{filename[:-3]}")
                        self.pulse_loaded_cogs.append(filename[:-3])
                        print(f"📦 Cog geladen: {filename[:-3]}")
                    except Exception as e:
                        self.pulse_failed_cogs.append(f"{filename[:-3]}: {type(e).__name__}")
                        print(f"❌ Fehler beim Laden von {filename}: {e}")

        # Slash Commands synchronisieren
        try:
            synced = await self.tree.sync()
            self.pulse_synced_commands = len(synced)
            print(f"🔄 {len(synced)} Slash Commands synchronisiert!")
        except Exception as e:
            print(f"❌ Fehler beim Sync: {e}")

        # Pulse Ultimate Automation Engine starten
        try:
            from pulse_ultimate import start_automation
            await start_automation(self)
            print("🤖 Pulse Ultimate Automation Engine gestartet.")
        except Exception as e:
            print(f"⚠️ Pulse Ultimate Automation Engine konnte nicht gestartet werden: {e}")

        # Webserver im Hintergrund starten
        config = uvicorn.Config(
            app=app, host="0.0.0.0", port=PORT, log_level="info"
        )
        server = uvicorn.Server(config)
        self._pulse_web_server = server
        self._pulse_web_server_task = asyncio.create_task(server.serve(), name="pulse-webserver")
        print(f"🌐 Webserver gestartet auf Port {PORT}")

    async def close(self):
        """Stop the dashboard cleanly before closing Discord's event loop."""
        server = self._pulse_web_server
        task = self._pulse_web_server_task
        if server is not None:
            server.should_exit = True

        if task is not None and task is not asyncio.current_task():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=8.0)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                print(f"⚠️ Webserver-Shutdown meldet einen Fehler: {type(exc).__name__}: {exc}")

        await super().close()

    async def on_ready(self):
        print(f"✅ Bot ist online als {self.user} (ID: {self.user.id})")
        try:
            await self.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name="Pulse TeamOS"
                )
            )
            print("🎮 Discord Presence: Pulse TeamOS")
        except Exception as exc:
            print(f"⚠️ Discord Presence konnte nicht gesetzt werden: {exc}")
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
