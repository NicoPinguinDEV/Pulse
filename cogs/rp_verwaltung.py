import sqlite3
import discord
from discord import app_commands
from discord.ext import commands

DB_NAME = "rp_verwaltung.db"

# --- DATENBANK HELFER ---
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

# --- HELFER ZUM SENDEN DER ANKÜNDIGUNG ---
async def send_rp_announcement(interaction: discord.Interaction, content: str):
    # 1. Sofort als verarbeitet markieren, um den 3-Sekunden-Timeout zu verhindern
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    channel_id = get_config("announcement_channel_id")
    target_channel = interaction.guild.get_channel(channel_id) if (channel_id and interaction.guild) else None
    ch = target_channel or interaction.channel

    if ch:
        # 2. Alte Nachrichten im Zielkanal löschen
        try:
            await ch.purge(limit=10)
        except Exception:
            pass

        # 3. Neue Ankündigung senden
        await ch.send(
            content=content,
            allowed_mentions=discord.AllowedMentions(everyone=True)
        )

        # 4. Bestätigung via followup senden
        await interaction.followup.send(
            f"✅ RP-Ankündigung wurde erfolgreich in {ch.mention} gesendet!",
            ephemeral=True
        )

# --- POP-UP FENSTER FÜR RP STOP (Uhrzeit abfragen) ---
class RPStopModal(discord.ui.Modal, title="RP Stop - Nächster RP Start"):
    uhrzeit = discord.ui.TextInput(
        label="Geplanter RP-Start für morgen:",
        placeholder="z.B. 14:30 Uhr oder Offen",
        default="14:30 Uhr",
        max_length=30
    )

    async def on_submit(self, interaction: discord.Interaction):
        msg_content = (
            "#  ⛔  RP Stop  ⛔  \n\n"
            "> **Das RP wird hiermit offiziell gestoppt!**\n"
            "> ***Informationen zu dem folgenden Tag***\n"
            f"> **⏰ Geplanter RP-Start: {self.uhrzeit.value}** \n"
            "> # Kommt gerne morgen wieder auf den Server!\n"
            "@everyone"
        )
        await send_rp_announcement(interaction, msg_content)

# --- DROPDOWN MENÜ ---
class RPSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="RP Start",
                value="rp_start",
                description="Eröffnet das heutige Roleplay",
                emoji="🔥"
            ),
            discord.SelectOption(
                label="RP Stop",
                value="rp_stop",
                description="Beendet das aktuelle Roleplay",
                emoji="🛑"
            )
        ]
        super().__init__(
            placeholder="Wähle eine RP-Aktion aus...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="rp_verwaltung_select"
        )

    async def callback(self, interaction: discord.Interaction):
        # RP START ANKÜNDIGUNG
        if self.values[0] == "rp_start":
            msg_content = (
                "#  ✅  RP Start  ✅  \n\n"
                "> **Das RP wird hiermit offiziell eröffnet!**\n"
                "> ***Informationen zu dem folgenden Tag***\n"
                "> **⏰ Geplanter RP-Stop: Offen** \n"
                "> # Kommt gerne auf den Server!\n"
                "@everyone"
            )
            await send_rp_announcement(interaction, msg_content)

        # RP STOP ANKÜNDIGUNG (Öffnet Modal für Uhrzeit)
        elif self.values[0] == "rp_stop":
            await interaction.response.send_modal(RPStopModal())

# --- VIEW ---
class RPView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RPSelect())

# --- COG ---
class RPVerwaltungCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()

    async def cog_load(self):
        self.bot.add_view(RPView())

    @app_commands.command(name="setup_rp", description="[Admin] Erstellt das RP-Verwaltungs-Dashboard")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        ankündigungs_kanal="[Optional] Kanal, in den die Start/Stop Ankündigungen gesendet werden sollen"
    )
    async def setup_rp(self, interaction: discord.Interaction, ankündigungs_kanal: discord.TextChannel = None):
        # Ankündigungskanal speichern (falls angegeben)
        target_ch = ankündigungs_kanal or interaction.channel
        set_config("announcement_channel_id", target_ch.id)

        embed = discord.Embed(
            title="💻 Roleplay Verwaltung",
            description="Nutze das Menü unten, um das RP offiziell zu starten oder zu beenden.",
            color=discord.Color.red()
        )
        
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)

        await interaction.channel.send(embed=embed, view=RPView())
        await interaction.response.send_message(
            f"✅ RP-Verwaltungs-Panel wurde erstellt!\n📢 Ankündigungen werden in {target_ch.mention} gesendet.",
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(RPVerwaltungCog(bot))
