import sqlite3
import discord
from discord import app_commands
from discord.ext import commands

DB_NAME = "welcome.db"

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


class WelcomeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        welcome_channel_id = get_config("welcome_channel_id")
        rules_channel_id = get_config("rules_channel_id")

        if not welcome_channel_id:
            return

        channel = member.guild.get_channel(welcome_channel_id)
        rules_channel = member.guild.get_channel(rules_channel_id) if rules_channel_id else None

        if channel:
            rules_mention = rules_channel.mention if rules_channel else "#regeln"

            embed = discord.Embed(
                title=f"Willkommen auf {member.guild.name}! 👋",
                description=(
                    f"Hallo {member.mention}, schön dass du da bist!\n\n"
                    f"📌 Bitte lies dir zuerst die {rules_mention} durch.\n"
                    f"👥 Wir sind nun **{member.guild.member_count}** Mitglieder."
                ),
                color=discord.Color.red()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=f"User ID: {member.id}")

            await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        leave_channel_id = get_config("welcome_channel_id")
        if not leave_channel_id:
            return

        channel = member.guild.get_channel(leave_channel_id)
        if channel:
            embed = discord.Embed(
                title="Mitglied hat den Server verlassen 🚪",
                description=f"**{member.display_name}** hat uns verlassen. Wir wünschen weiterhin alles Gute!",
                color=discord.Color.dark_gray()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)

    @app_commands.command(name="setup_willkommen", description="[Admin] Richtet den Willkommenskanal und den Regelkanal ein")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_welcome(
        self, 
        interaction: discord.Interaction, 
        willkommens_kanal: discord.TextChannel,
        regel_kanal: discord.TextChannel = None
    ):
        set_config("welcome_channel_id", willkommens_kanal.id)
        if regel_kanal:
            set_config("rules_channel_id", regel_kanal.id)

        await interaction.response.send_message(
            f"✅ Willkommenskanal auf {willkommens_kanal.mention} gesetzt!",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeCog(bot))
