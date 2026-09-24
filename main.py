import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

class CustomBot(commands.Bot):
    def __init__(self):
        # Intents definieren und Privileged Intents aktivieren
        intents = discord.Intents.default()
        intents.members = True          # Für Rollenänderungen & Teamler-Erkennung
        intents.presences = True        # Für den Online/Offline/Abwesend Status
        intents.message_content = True  # NÖTIG: Für das Flaggen-Quiz zum Lesen der Chatnachrichten!

        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Alle Cogs aus dem Ordner /cogs laden
        for filename in os.listdir("./cogs"):
            # Ignoriert temporäre Dateien/Ordner wie __init__.py oder __pycache__
            if filename.endswith(".py") and not filename.startswith("__"):
                try:
                    await self.load_extension(f"cogs.{filename[:-3]}")
                    print(f"📦 Cog geladen: {filename[:-3]}")
                except Exception as e:
                    print(f"❌ Fehler beim Laden von {filename}: {e}")

        # Slash Commands mit Discord synchronisieren
        synced = await self.tree.sync()
        print(f"🔄 {len(synced)} Slash Commands synchronisiert!")

    async def on_ready(self):
        print(f"✅ Bot ist online als {self.user} (ID: {self.user.id})")

bot = CustomBot()

if __name__ == "__main__":
    if not TOKEN:
        print("❌ FEHLER: Kein DISCORD_TOKEN in der .env-Datei gefunden!")
    else:
        bot.run(TOKEN)
