import sqlite3
import re
import unicodedata
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_requests (
            user_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            ping_channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            target_voice_id INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
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


def save_pending_request(user_id: int, guild_id: int, ping_channel_id: int,
                         message_id: int, target_voice_id: int):
    init_db()
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO pending_requests
               (user_id, guild_id, ping_channel_id, message_id, target_voice_id)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, guild_id, ping_channel_id, message_id, target_voice_id),
        )


def get_pending_request(user_id: int):
    init_db()
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM pending_requests WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def clear_pending_request(user_id: int, message_id: int | None = None):
    init_db()
    with sqlite3.connect(DB_NAME) as conn:
        if message_id is None:
            conn.execute("DELETE FROM pending_requests WHERE user_id = ?", (user_id,))
        else:
            conn.execute(
                "DELETE FROM pending_requests WHERE user_id = ? AND message_id = ?",
                (user_id, message_id),
            )


_GENERIC_OFFICE_WORDS = {
    "buero", "office", "raum", "zimmer", "warte", "warteraum",
    "fuer", "der", "die", "das", "im", "ins", "zum", "zur", "und",
}


def _normalise_label(value: str) -> str:
    value = (value or "").casefold().replace("ä", "ae").replace("ö", "oe")
    value = value.replace("ü", "ue").replace("ß", "ss")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def office_role_for(guild: discord.Guild, target_voice_id: int):
    """Resolve an office ping role: override first, then match the office and role names."""
    override_id = get_config(f"office_role_{target_voice_id}")
    if override_id:
        role = guild.get_role(int(override_id))
        if role and not role.is_default():
            return role

    channel = guild.get_channel(int(target_voice_id))
    if not isinstance(channel, discord.VoiceChannel):
        return None

    channel_label = _normalise_label(channel.name)
    channel_words = channel_label.split()
    channel_core = [word for word in channel_words if word not in _GENERIC_OFFICE_WORDS]
    matches = []
    for role in guild.roles:
        if role.is_default() or role.managed:
            continue
        role_label = _normalise_label(role.name)
        role_words = role_label.split()
        role_core = [word for word in role_words if word not in _GENERIC_OFFICE_WORDS]
        if not role_core:
            continue
        score = 0
        if role_label == channel_label:
            score = 1000 + len(role_label)
        elif role_label and role_label in channel_label:
            score = 800 + len(role_label)
        elif all(word in channel_core for word in role_core):
            score = 500 + len(" ".join(role_core))
        elif len(role_core) == 1 and role_core[0] in channel_words:
            score = 300 + len(role_core[0])
        if score:
            matches.append((score, role.position, role))
    return max(matches, key=lambda item: (item[0], item[1]))[2] if matches else None


async def delete_pending_ping(
    bot: commands.Bot,
    user_id: int,
    *,
    expected_message_id: int | None = None,
    fallback_message: discord.Message | None = None,
) -> bool:
    """Delete a pending office ping and its DB record without touching newer requests."""
    request = get_pending_request(user_id)
    if request and expected_message_id and int(request["message_id"]) != expected_message_id:
        if fallback_message:
            try:
                await fallback_message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        return False

    message = fallback_message
    if request:
        if message is None or message.id != int(request["message_id"]):
            channel = bot.get_channel(int(request["ping_channel_id"]))
            if channel is None:
                try:
                    channel = await bot.fetch_channel(int(request["ping_channel_id"]))
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    print("⚠️ Büro-System: Ping-Kanal zum Löschen nicht erreichbar.")
                    return False
            try:
                message = await channel.fetch_message(int(request["message_id"]))
            except discord.NotFound:
                clear_pending_request(user_id, int(request["message_id"]))
                return True
            except (discord.Forbidden, discord.HTTPException):
                print("⚠️ Büro-System: Ping-Nachricht konnte nicht abgerufen werden.")
                return False

    if message:
        try:
            await message.delete()
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException):
            print("⚠️ Büro-System: Ping-Nachricht konnte nicht gelöscht werden.")
            return False

    if request:
        clear_pending_request(user_id, int(request["message_id"]))
    return True


# --- BUTTON FÜR BÜRO-PING (ÜBERNEHMEN) ---
class BueroUebernehmenView(discord.ui.View):
    def __init__(self, bot: commands.Bot, waiting_user_id: int, target_voice_id: int):
        super().__init__(timeout=None)
        self.bot = bot
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
        if guild is None:
            await interaction.response.send_message("❌ Diese Aktion ist nur auf dem Server verfügbar.", ephemeral=True)
            return
        waiting_member = guild.get_member(self.waiting_user_id)
        team_member = interaction.user

        # Nur Teamler/Administratoren dürfen Anfragen übernehmen.
        try:
            cfg = __import__("webserver").load_config()
            team_ids = {int(role_id) for role_id in cfg.get("team_role_ids", [])}
            allowed = team_member.guild_permissions.administrator or any(
                role.id in team_ids for role in getattr(team_member, "roles", [])
            )
        except Exception:
            allowed = team_member.guild_permissions.administrator
        if not allowed:
            await interaction.response.send_message("❌ Nur Teammitglieder dürfen Büro-Anfragen übernehmen.", ephemeral=True)
            return

        warteraum_id = get_config("warteraum_channel_id")
        if (
            not waiting_member
            or not waiting_member.voice
            or not waiting_member.voice.channel
            or waiting_member.voice.channel.id != warteraum_id
        ):
            await delete_pending_ping(
                self.bot,
                self.waiting_user_id,
                expected_message_id=interaction.message.id,
                fallback_message=interaction.message,
            )
            await interaction.response.send_message(
                "❌ Die Person wartet nicht mehr im Büro-Warteraum. Die Ping-Nachricht wurde entfernt.",
                ephemeral=True
            )
            return

        target_channel = guild.get_channel(self.target_voice_id) if self.target_voice_id else None
        if not isinstance(target_channel, discord.VoiceChannel):
            if not getattr(team_member, "voice", None) or not team_member.voice.channel:
                await interaction.response.send_message(
                    "❌ Das gewünschte Büro existiert nicht mehr und du bist in keinem Sprachkanal.",
                    ephemeral=True
                )
                return
            target_channel = team_member.voice.channel

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await waiting_member.move_to(target_channel, reason=f"Büro-Anfrage übernommen von {team_member}")
            # The voice-state listener also deletes the ping when the user leaves
            # the waiting room. This second cleanup is safe if the listener won the race.
            await delete_pending_ping(
                self.bot,
                self.waiting_user_id,
                expected_message_id=interaction.message.id,
                fallback_message=interaction.message,
            )
            await interaction.followup.send(
                f"✅ Du hast {waiting_member.mention} erfolgreich in **{target_channel.name}** geholt. Die Ping-Nachricht wurde entfernt.",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Mir fehlen die Rechte (Mitglieder verschieben), um diesen User zu verschieben!",
                ephemeral=True
            )
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"❌ Discord konnte die Person nicht verschieben ({type(exc).__name__}).",
                ephemeral=True
            )
        except Exception as exc:
            print(f"⚠️ Büro-System: Übernahme fehlgeschlagen ({type(exc).__name__}).")
            await interaction.followup.send("❌ Die Übernahme ist fehlgeschlagen.", ephemeral=True)


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
