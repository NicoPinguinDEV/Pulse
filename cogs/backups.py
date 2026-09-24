import os
import shutil
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands, tasks

class BackupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.backup_channel_id = None
        self.daily_backup.start()

    def cog_unload(self):
        self.daily_backup.cancel()

    def create_backups(self) -> list[str]:
        backup_dir = "backups"
        os.makedirs(backup_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        saved_files = []

        for file in os.listdir("."):
            if file.endswith(".db"):
                dest_name = f"{timestamp}_{file}"
                dest_path = os.path.join(backup_dir, dest_name)
                shutil.copy2(file, dest_path)
                saved_files.append(dest_path)

        return saved_files

    @tasks.loop(hours=24)
    async def daily_backup(self):
        await self.bot.wait_until_ready()
        files = self.create_backups()

        if self.backup_channel_id:
            channel = self.bot.get_channel(self.backup_channel_id)
            if channel and files:
                discord_files = [discord.File(f) for f in files]
                embed = discord.Embed(
                    title="💾 Automatisches Datenbank-Backup",
                    description=f"Es wurden **{len(files)}** Datenbank-Dateien gesichert.",
                    color=discord.Color.green(),
                    timestamp=datetime.now()
                )
                await channel.send(embed=embed, files=discord_files)

    @app_commands.command(name="setup_backup", description="[Admin] Richtet den Kanal für automatische Datenbank-Backups ein")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_backup(self, interaction: discord.Interaction, kanal: discord.TextChannel):
        self.backup_channel_id = kanal.id
        await interaction.response.send_message(f"✅ Backup-Kanal auf {kanal.mention} gesetzt. Backups laufen täglich ab.", ephemeral=True)

    @app_commands.command(name="backup_erstellen", description="[Admin] Führt sofort ein manuelles Backup durch")
    @app_commands.checks.has_permissions(administrator=True)
    async def manual_backup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        files = self.create_backups()
        
        if files:
            discord_files = [discord.File(f) for f in files]
            await interaction.followup.send("✅ Backup erfolgreich erstellt! Hier sind die aktuellen Datenbank-Dateien:", files=discord_files, ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Keine `.db`-Dateien im Projektverzeichnis gefunden.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(BackupCog(bot))
