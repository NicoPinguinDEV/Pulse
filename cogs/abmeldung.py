import os
import asyncio
import discord
from discord.ext import commands

# --- TRAGE HIER DEINE SERVER-ID EIN ---
GUILD_ID = discord.Object(id=1541206148785373276) # Beispiel-ID durch deine ersetzen!

class DiscordBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        if os.path.exists('./cogs'):
            for filename in os.listdir('./cogs'):
                if filename.endswith('.py'):
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    print(f'[INFO] Cog geladen: {filename[:-3]}')

        # Kopiert alle Befehle direkt auf deinen Server (Sofort-Sync)
        self.tree.copy_global_to(guild=GUILD_ID)
        synced = await self.tree.sync(guild=GUILD_ID)
        print(f'[INFO] {len(synced)} Slash-Commands SOFORT auf dem Server registriert.')

    async def on_ready(self):
        print(f'[READY] Eingeloggt als {self.user} (ID: {self.user.id})')
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, 
                name="Team-Abmeldungen"
            )
        )

async def main():
    bot = DiscordBot()
    token = os.getenv('DISCORD_TOKEN')
    
    if not token:
        print("[FEHLER] Kein DISCORD_TOKEN in den Umgebungsvariablen gefunden!")
        return
        
    await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
