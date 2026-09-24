import sqlite3
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands

DB_NAME = "team_warns.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS warns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            mod_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


class TeamWarnsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()

    @app_commands.command(name="warn", description="[Admin/Führung] Verwarnt ein Teammitglied")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def warn_member(self, interaction: discord.Interaction, mitglied: discord.Member, grund: str):
        date_str = datetime.now().strftime("%d.%m.%Y %H:%M")
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO warns (user_id, mod_id, reason, timestamp) VALUES (?, ?, ?, ?)",
            (mitglied.id, interaction.user.id, grund, date_str)
        )
        warn_id = cursor.lastrowid
        cursor.execute("SELECT COUNT(*) FROM warns WHERE user_id = ?", (mitglied.id,))
        total_warns = cursor.fetchone()[0]
        conn.commit()
        conn.close()

        # PN an Mitglied
        try:
            embed_pm = discord.Embed(
                title="⚠️ Du hast eine Team-Verwarnung erhalten",
                description=f"**Grund:** {grund}\n**Moderator:** {interaction.user.mention}\n**Gesamt-Verwarnungen:** {total_warns}",
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )
            await mitglied.send(embed=embed_pm)
        except discord.Forbidden:
            pass

        embed_log = discord.Embed(
            title="⚠️ Team-Verwarnung erteilt",
            description=f"**Mitglied:** {mitglied.mention}\n**Grund:** {grund}\n**Warn-ID:** `{warn_id}`\n**Warns gesamt:** `{total_warns}`",
            color=discord.Color.orange()
        )
        await interaction.response.send_message(embed=embed_log)

    @app_commands.command(name="warn_liste", description="Zeigt alle Verwarnungen eines Teammitglieds an")
    async def warn_list(self, interaction: discord.Interaction, mitglied: discord.Member):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, mod_id, reason, timestamp FROM warns WHERE user_id = ?", (mitglied.id,))
        rows = cursor.fetchall()
        conn.close()

        embed = discord.Embed(
            title=f"📋 Verwarnungen von {mitglied.display_name}",
            color=discord.Color.blue()
        )

        if not rows:
            embed.description = "Keine Verwarnungen vorhanden."
        else:
            for warn_id, mod_id, reason, timestamp in rows:
                mod = interaction.guild.get_member(mod_id)
                mod_str = mod.mention if mod else f"ID: {mod_id}"
                embed.add_field(
                    name=f"Warn #{warn_id} ({timestamp})",
                    value=f"**Grund:** {reason}\n**Von:** {mod_str}",
                    inline=False
                )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="warn_loeschen", description="[Admin] Löscht eine bestimmte Verwarnung per ID")
    @app_commands.checks.has_permissions(administrator=True)
    async def warn_remove(self, interaction: discord.Interaction, warn_id: int):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM warns WHERE id = ?", (warn_id,))
        changes = conn.total_changes
        conn.commit()
        conn.close()

        if changes > 0:
            await interaction.response.send_message(f"✅ Verwarnung `# {warn_id}` erfolgreich gelöscht.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Verwarnung `# {warn_id}` wurde nicht gefunden.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TeamWarnsCog(bot))
