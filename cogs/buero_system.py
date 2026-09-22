import sqlite3
import discord
from discord import app_commands
from discord.ext import commands

DB_NAME = "buero_system.db"

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


# --- BUTTON FÜR BÜRO-PING (ÜBERNEHMEN) ---
class BueroUebernehmenView(discord.ui.View):
    def __init__(self, waiting_user_id: int, target_voice_id: int):
        super().__init__(timeout=None)
        self.waiting_user_id = waiting_user_id
        self.target_voice_id = target_voice_id

    @discord.ui.button(
        label="Übernehmen",
        style=discord.ButtonStyle.green,
        emoji="📥",
        custom_id="buero_uebernehmen_btn"
    )
    async def uebernehmen(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        waiting_member = guild.get_member(self.waiting_user_id)
        team_member = interaction.user

        # Prüfen, ob der Teamler in einem Sprachkanal ist
        if not team_member.voice or not team_member.voice.channel:
            await interaction.response.send_message(
                "❌ Du musst dich in einem Sprachkanal befinden, um den User zu verschieben!",
                ephemeral=True
            )
            return

        # Prüfen, ob der wartende User noch auf dem Server/im Warteraum ist
        if not waiting_member or not waiting_member.voice:
            await interaction.response.send_message(
                "❌ Der User befindet sich nicht mehr im Warteraum!",
                ephemeral=True
            )
            return

        try:
            # User zum Teamler in den Sprachkanal verschieben
            target_channel = team_member.voice.channel
            await waiting_member.move_to(target_channel)

            # Embed im Büro-Ping als "Übernommen" aktualisieren
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green()
            embed.set_field_at(
                index=1,
                name="Status",
                value=f"✅ Übernommen von {team_member.mention} in **{target_channel.name}**",
                inline=False
            )

            # Button deaktivieren
            button.disabled = True
            button.label = "Übernommen"
            button.style = discord.ButtonStyle.secondary

            await interaction.message.edit(embed=embed, view=self)
            await interaction.response.send_message(
                f"✅ Du hast {waiting_member.mention} erfolgreich in deinen Kanal geholt!",
                ephemeral=True
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Mir fehlen die Rechte (`Mitglieder verschieben`), um diesen User zu verschieben!",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Fehler: `{e}`", ephemeral=True)


# --- DROPDOWN MENÜ IN DER DM DES USERS ---
class BueroSelect(discord.ui.Select):
    def __init__(self, voice_channels: list, guild_id: int):
        options = []
        for vc in voice_channels[:25]:  # Discord erlaubt max 25 Optionen
            options.append(
                discord.SelectOption(
                    label=vc.name,
                    value=str(vc.id),
                    emoji="💼"
                )
            )

        super().__init__(
            placeholder="Wähle das Büro aus, in das du möchtest...",
            min_values=1,
            max_values=1,
            options=options
        )
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        target_vc_id = int(self.values[0])
        guild = interaction.client.get_guild(self.guild_id)
        target_vc = guild.get_channel(target_vc_id) if guild else None

        ping_channel_id = get_config("ping_channel_id")
        ping_channel = guild.get_channel(ping_channel_id) if (guild and ping_channel_id) else None

        if not ping_channel:
            await interaction.response.send_message(
                "❌ Das Büro-Ping-System ist auf dem Server noch nicht vollständig eingerichtet.",
                ephemeral=True
            )
            return

        # Embed für den #Büro-Ping Kanal erstellen
        embed = discord.Embed(
            title="🛎️ Neue Warteraum-Anfrage",
            color=discord.Color.gold()
        )
        embed.add_field(name="Nutzer", value=interaction.user.mention, inline=True)
        embed.add_field(name="Status", value="⏳ Wartet auf Übernahme...", inline=False)
        if target_vc:
            embed.add_field(name="Gewünschtes Büro", value=f"📌 **{target_vc.name}**", inline=True)

        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        # Nachricht in Büro-Ping senden mit Button zum Übernehmen
        view = BueroUebernehmenView(waiting_user_id=interaction.user.id, target_voice_id=target_vc_id)
        await ping_channel.send(content=f"🔔 Anfrage für **{target_vc.name if target_vc else 'Büro'}**:", embed=embed, view=view)

        await interaction.response.send_message(
            f"✅ Deine Anfrage für **{target_vc.name if target_vc else 'das Büro'}** wurde weitergeleitet! Bitte bleibe im Warteraum.",
            ephemeral=True
        )


class DMSelectView(discord.ui.View):
    def __init__(self, voice_channels: list, guild_id: int):
        super().__init__(timeout=300)
        self.add_item(BueroSelect(voice_channels, guild_id))


# --- COG ---
class BueroSystemCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()

    # EVENT: ERKENNT WENN JEMAND DEN WARTERAUM BETRITT
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return

        warteraum_id = get_config("warteraum_channel_id")
        if not warteraum_id:
            return

        # Wenn der User den Warteraum neu betritt
        if after.channel and after.channel.id == warteraum_id and (before.channel != after.channel):
            category = after.channel.category
            
            # Alle Sprachkanäle in der gleichen Kategorie holen (außer Warteraum)
            if category:
                office_channels = [vc for vc in category.voice_channels if vc.id != warteraum_id]
            else:
                office_channels = [vc for vc in member.guild.voice_channels if vc.id != warteraum_id]

            if not office_channels:
                return

            try:
                # DM an den Nutzer senden
                embed = discord.Embed(
                    title="🏢 Büro-Warteraum",
                    description=f"Willkommen im Warteraum auf **{member.guild.name}**!\nBitte wähle unten aus, in welches Büro du möchtest.",
                    color=discord.Color.blue()
                )
                await member.send(embed=embed, view=DMSelectView(office_channels, member.guild.id))
            except discord.Forbidden:
                print(f"⚠️ Konnte {member.display_name} keine DM senden (DMs geschlossen).")

    # ADMIN COMMAND: SETUP
    @app_commands.command(name="setup_buero", description="[Admin] Richtet das Büro-Warteraum System ein")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        warteraum="Der Sprachkanal 'Büro-Warteraum'",
        ping_kanal="Der Textkanal 'Büro-Ping' für Benachrichtigungen"
    )
    async def setup_buero(self, interaction: discord.Interaction, warteraum: discord.VoiceChannel, ping_kanal: discord.TextChannel):
        set_config("warteraum_channel_id", warteraum.id)
        set_config("ping_channel_id", ping_kanal.id)

        await interaction.response.send_message(
            f"✅ **Büro-System eingerichtet!**\n"
            f"• **Warteraum:** {warteraum.mention}\n"
            f"• **Ping-Kanal:** {ping_kanal.mention}",
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(BueroSystemCog(bot))
