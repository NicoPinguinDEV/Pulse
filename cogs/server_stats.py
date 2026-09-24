import sqlite3
import discord
from discord import app_commands
from discord.ext import commands, tasks

DB_NAME = "stats.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value INTEGER
        )
    """)
    conn.commit()
    conn.close()

def set_config(key: str, value: int):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def get_config(key: str):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM config WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


class ServerStatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()
        self.update_stats_loop.start()

    def cog_unload(self):
        self.update_stats_loop.cancel()

    @tasks.loop(minutes=10)
    async def update_stats_loop(self):
        await self.bot.wait_until_ready()
        
        member_vc_id = get_config("stats_member_vc")
        team_online_vc_id = get_config("stats_team_vc")
        team_role_id = get_config("stats_team_role")

        for guild in self.bot.guilds:
            # 1. Mitglieder-Anzahl (ohne Bots)
            if member_vc_id:
                member_vc = guild.get_channel(member_vc_id)
                if member_vc:
                    human_count = sum(1 for m in guild.members if not m.bot)
                    new_name = f"👥 Mitglieder: {human_count}"
                    if member_vc.name != new_name:
                        await member_vc.edit(name=new_name)

            # 2. Teammitglieder Online
            if team_online_vc_id and team_role_id:
                team_vc = guild.get_channel(team_online_vc_id)
                team_role = guild.get_role(team_role_id)
                if team_vc and team_role:
                    online_team = sum(
                        1 for m in team_role.members 
                        if m.status != discord.Status.offline
                    )
                    new_name = f"🛡️ Team Online: {online_team}"
                    if team_vc.name != new_name:
                        await team_vc.edit(name=new_name)

    @app_commands.command(name="setup_stats", description="[Admin] Richtet Sprachkanäle für Live-Statistiken ein")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_stats(
        self, 
        interaction: discord.Interaction, 
        mitglied_kanal: discord.VoiceChannel, 
        team_online_kanal: discord.VoiceChannel = None,
        team_rolle: discord.Role = None
    ):
        set_config("stats_member_vc", mitglied_kanal.id)
        if team_online_kanal and team_rolle:
            set_config("stats_team_vc", team_online_kanal.id)
            set_config("stats_team_role", team_rolle.id)

        await interaction.response.send_message("✅ Statistik-Kanäle eingerichtet! Die Aktualisierung erfolgt alle 10 Minuten.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerStatsCog(bot))
