import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

class CustomBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Alle Cogs aus dem Ordner /cogs laden
        for filename in os.listdir("./cogs"):
            if filename.endswith(".py"):
                await self.load_extension(f"cogs.{filename[:-3]}")
                print(f"📦 Cog geladen: {filename[:-3]}")

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
