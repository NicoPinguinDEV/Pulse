import sqlite3
from datetime import datetime, time
import zoneinfo
import discord
from discord import app_commands
from discord.ext import commands, tasks

DB_NAME = "activity_check.db"
BERLIN_TZ = zoneinfo.ZoneInfo("Europe/Berlin")

class ActivityCheckCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.init_db()
        self.daily_activity_check.start()

    def cog_unload(self):
        self.daily_activity_check.cancel()

    # --- DATENBANK ---
    def init_db(self):
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

    def set_config(self, key: str, value: int):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()

    def get_config(self, key: str):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None

    # --- NACHRICHT ERSTELLEN & SENDEN ---
    async def send_check_message(self, channel: discord.TextChannel, role: discord.Role = None):
        msg_text = (
            "┏━━━━━━━━━━ ⟡ ━━━━━━━━━━┓\n"
            "      ⟡  A C T I V I T Y – C H E C K  ⟡\n"
            "┗━━━━━━━━━━ ⟡ ━━━━━━━━━━┛\n\n"
            "      ━━━━⟡━━━━━━◇━━━━━━⟡━━━━\n"
            "          ◇  Aktivitätskontrolle  ◇\n"
            "      ━━━━⟡━━━━━━◇━━━━━━⟡━━━━\n\n"
            "⟡ Teilnahme:\n\n"
            "┃ ⟢ Reaktion setzen (✅)\n"
            "┃ ⟢ Aktiv bleiben\n\n"
            "      ━━━━⟡━━━━━━◇━━━━━━⟡━━━━\n"
            "              ✔ Ziel: 35 Member\n"
            "      ━━━━⟡━━━━━━◇━━━━━━⟡━━━━\n\n"
            "      ⟡━━━━━━━◇━━━━━━━⟡\n"
            "        Inaktiv = Konsequenzen\n"
            "      ⟡━━━━━━━◇━━━━━━━⟡"
        )

        role_ping = role.mention if role else ""
        content = f"{role_ping}\n\n{msg_text}" if role_ping else msg_text

        # Sendet die reine Nachricht ohne Buttons
        msg = await channel.send(
            content=content,
            allowed_mentions=discord.AllowedMentions(roles=True, everyone=True)
        )
        
        # Fügt die ✅-Reaktion hinzu
        try:
            await msg.add_reaction("✅")
        except Exception:
            pass

    # --- AUTOMATISCHER TASK UM 06:00 UHR MORGENS ---
    @tasks.loop(time=time(hour=6, minute=0, tzinfo=BERLIN_TZ))
    async def daily_activity_check(self):
        await self.bot.wait_until_ready()

        channel_id = self.get_config("activity_channel_id")
        role_id = self.get_config("activity_role_id")

        if not channel_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return

        role = None
        if role_id and channel.guild:
            role = channel.guild.get_role(role_id)

        await self.send_check_message(channel, role)
        print(f"⏰ Activity Check automatisch um 06:00 Uhr gesendet in #{channel.name}")

    # --- COMMANDS ---

    @app_commands.command(name="setup_activitycheck", description="[Admin] Stellt Kanal und Team-Rolle für den täglichen 6-Uhr Check ein")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        kanal="Kanal, in den der Check gesendet werden soll",
        team_rolle="[Optional] Team-Rolle, die gepingt werden soll"
    )
    async def setup_activitycheck(self, interaction: discord.Interaction, kanal: discord.TextChannel, team_rolle: discord.Role = None):
        self.set_config("activity_channel_id", kanal.id)
        if team_rolle:
            self.set_config("activity_role_id", team_rolle.id)
            await interaction.response.send_message(
                f"✅ Activity Check wurde eingerichtet!\n• **Kanal:** {kanal.mention}\n• **Rolle:** {team_rolle.mention}\n• **Uhrzeit:** Täglich um 06:00 Uhr",
                ephemeral=True
            )
        else:
            self.set_config("activity_role_id", 0)
            await interaction.response.send_message(
                f"✅ Activity Check wurde eingerichtet!\n• **Kanal:** {kanal.mention}\n• **Rolle:** Keine\n• **Uhrzeit:** Täglich um 06:00 Uhr",
                ephemeral=True
            )

    @app_commands.command(name="test_activitycheck", description="[Admin] Sendet den Activity Check zum Testen sofort ab")
    @app_commands.checks.has_permissions(administrator=True)
    async def test_activitycheck(self, interaction: discord.Interaction):
        channel_id = self.get_config("activity_channel_id")
        role_id = self.get_config("activity_role_id")

        target_channel = interaction.guild.get_channel(channel_id) if channel_id else interaction.channel
        role = interaction.guild.get_role(role_id) if role_id else None

        await interaction.response.send_message("⌛ Test-Nachricht wird gesendet...", ephemeral=True)
        await self.send_check_message(target_channel, role)

async def setup(bot: commands.Bot):
    await bot.add_cog(ActivityCheckCog(bot))
