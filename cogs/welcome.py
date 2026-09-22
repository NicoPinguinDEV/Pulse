import sqlite3
import discord
from discord import app_commands
from discord.ext import commands

DB_NAME = "welcome_config.db"

# --- DATENBANK HELFER ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS welcome_config (
            guild_id INTEGER PRIMARY KEY,
            welcome_channel_id INTEGER,
            leave_channel_id INTEGER,
            welcome_banner TEXT,
            leave_banner TEXT
        )
    """)
    conn.commit()
    conn.close()

def set_welcome_channel(guild_id: int, channel_id: int, banner_url: str = None):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO welcome_config (guild_id, welcome_channel_id, welcome_banner)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET 
            welcome_channel_id = excluded.welcome_channel_id,
            welcome_banner = COALESCE(excluded.welcome_banner, welcome_config.welcome_banner)
    """, (guild_id, channel_id, banner_url))
    conn.commit()
    conn.close()

def set_leave_channel(guild_id: int, channel_id: int, banner_url: str = None):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO welcome_config (guild_id, leave_channel_id, leave_banner)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET 
            leave_channel_id = excluded.leave_channel_id,
            leave_banner = COALESCE(excluded.leave_banner, welcome_config.leave_banner)
    """, (guild_id, channel_id, banner_url))
    conn.commit()
    conn.close()

def get_welcome_config(guild_id: int):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT welcome_channel_id, leave_channel_id, welcome_banner, leave_banner FROM welcome_config WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()
    return row if row else (None, None, None, None)


class WelcomeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()

    # --- WILLKOMMENS-EVENT ---
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        welcome_channel_id, _, welcome_banner, _ = get_welcome_config(member.guild.id)
        if not welcome_channel_id:
            return

        channel = member.guild.get_channel(welcome_channel_id)
        if not channel:
            return

        embed = discord.Embed(
            title=f"Willkommen auf *{member.guild.name}*",
            description=(
                f"Willkommen {member.mention}\n"
                f"Auf *{member.guild.name}* findest du richtiges Roleplay.\n"
                f"Lies dir bitte alles gründlich durch, und halte dich immer an die Regeln! Wenn du Hilfe brauchst, ist unser Team direkt für dich da!\n\n"
                f"Mit freundlichen Grüßen\n"
                f"Dein {member.guild.name} Team."
            ),
            color=discord.Color.from_rgb(200, 20, 40)
        )

        # Server Icon oben rechts
        if member.guild.icon:
            embed.set_thumbnail(url=member.guild.icon.url)

        # Banner unter der Nachricht (falls eins angegeben wurde)
        if welcome_banner:
            embed.set_image(url=welcome_banner)

        await channel.send(embed=embed)

    # --- VERLASSEN-EVENT ---
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        _, leave_channel_id, _, leave_banner = get_welcome_config(member.guild.id)
        if not leave_channel_id:
            return

        channel = member.guild.get_channel(leave_channel_id)
        if not channel:
            return

        embed = discord.Embed(
            title=f"Auf Wiedersehen von *{member.guild.name}*",
            description=(
                f"Auf Wiedersehen {member.mention}\n"
                f"Schade, dass du uns verlässt! Wir bedanken uns für deine Zeit auf *{member.guild.name}*.\n"
                f"Falls du deine Meinung änderst, bist du jederzeit wieder herzlich willkommen!\n\n"
                f"Mit freundlichen Grüßen\n"
                f"Dein {member.guild.name} Team."
            ),
            color=discord.Color.dark_gray()
        )

        # User Avatar oder Server Icon oben rechts
        if member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)

        # Banner unter der Nachricht
        if leave_banner:
            embed.set_image(url=leave_banner)

        await channel.send(embed=embed)

    # --- SLASH COMMANDS ---
    @app_commands.command(name="setup_willkommen", description="[Admin] Richtet den Willkommenskanal und ein Banner ein")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_welcome_cmd(self, interaction: discord.Interaction, kanal: discord.TextChannel, banner_url: str = None):
        set_welcome_channel(interaction.guild.id, kanal.id, banner_url)
        await interaction.response.send_message(
            f"✅ Willkommens-Nachrichten wurden für {kanal.mention} eingerichtet!",
            ephemeral=True
        )

    @app_commands.command(name="setup_verlassen", description="[Admin] Richtet den Verlassenskanal und ein Banner ein")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_leave_cmd(self, interaction: discord.Interaction, kanal: discord.TextChannel, banner_url: str = None):
        set_leave_channel(interaction.guild.id, kanal.id, banner_url)
        await interaction.response.send_message(
            f"✅ Verlassens-Nachrichten wurden für {kanal.mention} eingerichtet!",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeCog(bot))
