import sqlite3
from contextlib import closing
import re
import unicodedata
import discord
from pulse_embed_design import color as _pulse_color
from discord import app_commands
from discord.ext import commands
import pulse_ids as _pulse_ids  # A-09: fixed Discord IDs live in one place

DB_NAME = "buero_system.db"

# Aktuelle Server-Zuordnung für das Büro-System. Die bestehende /setup_buero-
# Konfiguration bleibt weiterhin änderbar; die Version-Migration korrigiert einmalig
# veraltete IDs aus älteren Deployments.
DEFAULT_WAITING_ROOM_ID = _pulse_ids.OFFICE_WAITING_ROOM_ID
DEFAULT_PING_CHANNEL_ID = _pulse_ids.OFFICE_PING_CHANNEL_ID
OFFICE_CONFIG_VERSION = 3

# Explizite Rollen-Zuordnung pro Zielbüro. Mehrere Rollen pro Büro sind möglich.
# Diese IDs werden nach der Auswahl in der DM gepingt; die Warteraum-Anfrage
# selbst bleibt weiterhin bestehen und wird beim Verlassen des Warteraums gelöscht.
DEFAULT_OFFICE_ROLE_IDS: dict[int, tuple[int, ...]] = _pulse_ids.OFFICE_ROLE_IDS

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


def migrate_office_config():
    """Set the requested channel IDs once, then preserve future /setup_buero changes."""
    init_db()
    if get_config("office_config_version") == OFFICE_CONFIG_VERSION:
        return
    set_config("warteraum_channel_id", DEFAULT_WAITING_ROOM_ID)
    set_config("ping_channel_id", DEFAULT_PING_CHANNEL_ID)
    set_config("office_config_version", OFFICE_CONFIG_VERSION)


async def resolve_ping_channel(guild: discord.Guild, channel_id: int | None = None):
    """Resolve the configured ping channel from cache, then from Discord if needed."""
    channel_id = int(channel_id or get_config("ping_channel_id") or DEFAULT_PING_CHANNEL_ID)
    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
    return channel if isinstance(channel, discord.TextChannel) else None


def save_pending_request(user_id: int, guild_id: int, ping_channel_id: int,
                         message_id: int, target_voice_id: int):
    init_db()
    with closing(sqlite3.connect(DB_NAME)) as conn, conn:
        conn.execute(
            """INSERT OR REPLACE INTO pending_requests
               (user_id, guild_id, ping_channel_id, message_id, target_voice_id)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, guild_id, ping_channel_id, message_id, target_voice_id),
        )


def get_pending_request(user_id: int):
    init_db()
    with closing(sqlite3.connect(DB_NAME)) as conn, conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM pending_requests WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def get_pending_request_by_message(message_id: int):
    init_db()
    with closing(sqlite3.connect(DB_NAME)) as conn, conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM pending_requests WHERE message_id = ?", (int(message_id),)
        ).fetchone()
        return dict(row) if row else None


def clear_pending_request(user_id: int, message_id: int | None = None):
    init_db()
    with closing(sqlite3.connect(DB_NAME)) as conn, conn:
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


def office_roles_for(guild: discord.Guild, target_voice_id: int) -> list[discord.Role]:
    """Resolve all roles for an office: admin override, explicit ID mapping, then name matching."""
    override_id = get_config(f"office_role_{target_voice_id}")
    if override_id:
        role = guild.get_role(int(override_id))
        if role and not role.is_default() and not role.managed:
            return [role]

    # Explicit mapping has priority over fuzzy name matching. If a configured
    # role is missing from the server, don't accidentally ping a different role.
    if int(target_voice_id) in DEFAULT_OFFICE_ROLE_IDS:
        roles = []
        for role_id in DEFAULT_OFFICE_ROLE_IDS[int(target_voice_id)]:
            role = guild.get_role(int(role_id))
            if role and not role.is_default() and not role.managed:
                roles.append(role)
        return roles

    channel = guild.get_channel(int(target_voice_id))
    if not isinstance(channel, discord.VoiceChannel):
        return []

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
    return [max(matches, key=lambda item: (item[0], item[1]))[2]] if matches else []


def office_role_for(guild: discord.Guild, target_voice_id: int):
    """Backward-compatible singular helper used for the waiting-room arrival ping."""
    roles = office_roles_for(guild, target_voice_id)
    return roles[0] if roles else None

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

    async def _request_state(self, interaction: discord.Interaction):
        """(waiting user, target voice). K-07: the stateless view registered at startup reads it from the DB."""
        if self.waiting_user_id:
            return self.waiting_user_id, self.target_voice_id
        request = get_pending_request_by_message(interaction.message.id) if interaction.message else None
        if not request:
            await interaction.response.send_message(
                "ℹ️ Diese Büro-Anfrage ist nicht mehr aktiv. Die Person kann einfach erneut den Warteraum betreten.",
                ephemeral=True
            )
            return None
        return int(request["user_id"]), int(request["target_voice_id"] or 0)

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
        state = await self._request_state(interaction)
        if state is None:
            return
        waiting_user_id, target_voice_id = state
        waiting_member = guild.get_member(waiting_user_id)
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
                waiting_user_id,
                expected_message_id=interaction.message.id,
                fallback_message=interaction.message,
            )
            await interaction.response.send_message(
                "❌ Die Person wartet nicht mehr im Büro-Warteraum. Die Ping-Nachricht wurde entfernt.",
                ephemeral=True
            )
            return

        target_channel = guild.get_channel(target_voice_id) if target_voice_id else None
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
                waiting_user_id,
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


    @discord.ui.button(
        label="Auftrag erledigt",
        style=discord.ButtonStyle.secondary,
        emoji="✅",
        custom_id="buero_auftrag_erledigt_btn"
    )
    async def auftrag_erledigt(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "❌ Diese Aktion ist nur auf dem Server verfügbar.",
                ephemeral=True,
            )
            return

        team_member = interaction.user
        try:
            cfg = __import__("webserver").load_config()
            team_ids = {int(role_id) for role_id in cfg.get("team_role_ids", [])}
            allowed = team_member.guild_permissions.administrator or any(
                role.id in team_ids for role in getattr(team_member, "roles", [])
            )
        except Exception:
            allowed = team_member.guild_permissions.administrator
        if not allowed:
            await interaction.response.send_message(
                "❌ Nur Teammitglieder dürfen Büro-Anfragen abschließen.",
                ephemeral=True,
            )
            return

        state = await self._request_state(interaction)
        if state is None:
            return
        waiting_user_id = state[0]
        await interaction.response.defer(ephemeral=True, thinking=True)
        removed = await delete_pending_ping(
            self.bot,
            waiting_user_id,
            expected_message_id=interaction.message.id,
            fallback_message=interaction.message,
        )
        if removed:
            await interaction.followup.send(
                "✅ Büro-Auftrag als erledigt markiert. Die Ping-Nachricht wurde gelöscht.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "ℹ️ Diese Ping-Nachricht gehört nicht mehr zur aktuellen offenen Anfrage.",
                ephemeral=True,
            )


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

        if not guild or not isinstance(target_vc, discord.VoiceChannel):
            await interaction.response.send_message(
                "❌ Das ausgewählte Büro ist nicht mehr verfügbar. Bitte betrete den Warteraum erneut.",
                ephemeral=True
            )
            return

        warteraum_id = get_config("warteraum_channel_id")
        guild_member = guild.get_member(interaction.user.id)
        if (
            not guild_member
            or not guild_member.voice
            or not guild_member.voice.channel
            or guild_member.voice.channel.id != warteraum_id
        ):
            await interaction.response.send_message(
                "❌ Du bist nicht mehr im Büro-Warteraum. Bitte betrete ihn erneut, bevor du eine Anfrage sendest.",
                ephemeral=True
            )
            return

        ping_channel_id = int(get_config("ping_channel_id") or DEFAULT_PING_CHANNEL_ID)
        ping_channel = await resolve_ping_channel(guild, ping_channel_id)
        if not isinstance(ping_channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ Das Büro-Ping-System ist auf dem Server noch nicht vollständig eingerichtet.",
                ephemeral=True
            )
            return

        # Pro Person nur eine offene Anfrage. Eine ältere Ping-Nachricht wird ersetzt.
        await delete_pending_ping(interaction.client, interaction.user.id)

        embed = discord.Embed(
            title="🛎️ Neue Warteraum-Anfrage",
            color=_pulse_color("highlight")
        )
        embed.add_field(name="Nutzer", value=interaction.user.mention, inline=True)
        embed.add_field(name="Status", value="⏳ Wartet auf Übernahme...", inline=False)
        embed.add_field(name="Gewünschtes Büro", value=f"📌 **{target_vc.name}**", inline=True)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        office_roles = office_roles_for(guild, target_vc_id)
        role_mentions = " ".join(role.mention for role in office_roles)
        content = (
            (role_mentions + " ") if role_mentions else ""
        ) + f"🔔 Anfrage für **{target_vc.name}**:"
        view = BueroUebernehmenView(
            bot=interaction.client,
            waiting_user_id=interaction.user.id,
            target_voice_id=target_vc_id,
        )
        try:
            ping_message = await ping_channel.send(
                content=content,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(
                    roles=office_roles,
                    users=False,
                    everyone=False,
                    replied_user=False,
                ),
            )
            save_pending_request(
                user_id=interaction.user.id,
                guild_id=guild.id,
                ping_channel_id=ping_channel.id,
                message_id=ping_message.id,
                target_voice_id=target_vc_id,
            )
        except (discord.Forbidden, discord.HTTPException, sqlite3.Error) as exc:
            try:
                if "ping_message" in locals():
                    await ping_message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            print(f"⚠️ Büro-System: Anfrage konnte nicht angelegt werden ({type(exc).__name__}).")
            await interaction.response.send_message(
                "❌ Die Büro-Anfrage konnte nicht gespeichert oder gesendet werden. Prüfe die Bot-Rechte und versuche es erneut.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ Deine Anfrage für **{target_vc.name}** wurde weitergeleitet! Bitte bleibe im Warteraum."
            + (
                ""
                if office_roles
                else "\n⚠️ Für dieses Büro wurde keine gültige Ping-Rolle gefunden. Ein Administrator kann sie mit /buero_rolle_setzen fest zuordnen."
            ),
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
        migrate_office_config()

    async def cog_load(self):
        # K-07: stateless handler for "buero_uebernehmen_btn" on pings posted before a restart.
        self.bot.add_view(BueroUebernehmenView(self.bot, 0, 0))

    async def _send_arrival_ping(self, member: discord.Member, waiting_room: discord.VoiceChannel):
        """Publish an immediate office request on entry, even before the DM selection."""
        ping_channel_id = int(get_config("ping_channel_id") or DEFAULT_PING_CHANNEL_ID)
        ping_channel = await resolve_ping_channel(member.guild, ping_channel_id)
        if not ping_channel:
            print(
                f"⚠️ Büro-System: Ping-Kanal {ping_channel_id} fehlt oder ist nicht erreichbar. "
                "Prüfe /setup_buero sowie die Bot-Rechte."
            )
            return

        # Replace any stale open request, while keeping the database and message in sync.
        await delete_pending_ping(self.bot, member.id)
        embed = discord.Embed(
            title="🛎️ Neue Büro-Anfrage",
            description=f"**{member.display_name}** ist dem Büro-Warteraum beigetreten und wartet auf Unterstützung.",
            color=_pulse_color("highlight"),
        )
        embed.add_field(name="Nutzer", value=member.mention, inline=True)
        embed.add_field(name="Warteraum", value=waiting_room.mention, inline=True)
        embed.add_field(name="Gewünschtes Büro", value="Noch nicht ausgewählt – Auswahl per DM möglich.", inline=False)
        embed.add_field(name="Status", value="⏳ Wartet auf Übernahme …", inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)

        # target_voice_id=0 lets a team member take the visitor into their current voice channel.
        view = BueroUebernehmenView(
            bot=self.bot,
            waiting_user_id=member.id,
            target_voice_id=0,
        )
        # If the waiting room has a configured office role, notify that role.
        # Otherwise still publish the request without pinging every team rank.
        arrival_role = office_role_for(member.guild, waiting_room.id)
        content = (
            (arrival_role.mention + " ") if arrival_role else ""
        ) + "🔔 **Neue Büro-Anfrage**"
        try:
            ping_message = await ping_channel.send(
                content=content,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(
                    roles=[arrival_role] if arrival_role else [],
                    users=False,
                    everyone=False,
                    replied_user=False,
                ),
            )
            save_pending_request(
                user_id=member.id,
                guild_id=member.guild.id,
                ping_channel_id=ping_channel.id,
                message_id=ping_message.id,
                target_voice_id=0,
            )
            print(
                f"🏢 Büro-System: Warteraum-Eintritt von {member} erkannt; "
                f"Ping in Kanal {ping_channel.id} erstellt."
            )

            # A very quick exit can race with sending/saving the notification.
            current_voice = member.voice.channel if member.voice else None
            if not current_voice or current_voice.id != waiting_room.id:
                await delete_pending_ping(
                    self.bot,
                    member.id,
                    expected_message_id=ping_message.id,
                    fallback_message=ping_message,
                )
        except (discord.Forbidden, discord.HTTPException, sqlite3.Error) as exc:
            try:
                if "ping_message" in locals():
                    await ping_message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            print(f"⚠️ Büro-System: Eintritts-Ping konnte nicht erstellt werden ({type(exc).__name__}).")

    # EVENT: ERKENNT WENN JEMAND DEN WARTERAUM BETRITT
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return

        warteraum_id = get_config("warteraum_channel_id")
        if not warteraum_id:
            return

        # Jede offene Ping-Nachricht löschen, sobald die Person den Warteraum verlässt.
        # Dies greift auch beim Wechsel ins gewählte Büro nach einer Übernahme.
        if before.channel and before.channel.id == warteraum_id and (
            after.channel is None or after.channel.id != warteraum_id
        ):
            await delete_pending_ping(self.bot, member.id)

        # Wenn der User den Warteraum neu betritt
        if after.channel and after.channel.id == warteraum_id and (before.channel != after.channel):
            category = after.channel.category

            # Den Textkanal sofort benachrichtigen, wenn jemand den Warteraum betritt.
            # Die DM-Auswahl bleibt zusätzlich bestehen, damit die passende Bürorolle
            # anschließend gezielt gepingt werden kann.
            await self._send_arrival_ping(member, after.channel)

            # Alle Sprachkanäle in der gleichen Kategorie holen (außer Warteraum)
            if category:
                office_channels = [vc for vc in category.voice_channels if vc.id != warteraum_id]
            else:
                office_channels = [vc for vc in member.guild.voice_channels if vc.id != warteraum_id]

            if not office_channels:
                print("⚠️ Büro-System: Keine weiteren Büro-Sprachkanäle für die DM-Auswahl gefunden.")
                return

            try:
                # DM an den Nutzer senden
                embed = discord.Embed(
                    title="🏢 Büro-Warteraum",
                    description=f"Willkommen im Warteraum auf **{member.guild.name}**!\nBitte wähle unten aus, in welches Büro du möchtest.",
                    color=_pulse_color("info")
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
        set_config("office_config_version", OFFICE_CONFIG_VERSION)

        await interaction.response.send_message(
            f"✅ **Büro-System eingerichtet!**\n"
            f"• **Warteraum:** {warteraum.mention}\n"
            f"• **Ping-Kanal:** {ping_kanal.mention}",
            ephemeral=True
        )


    @app_commands.command(
        name="buero_rolle_setzen",
        description="[Admin] Ordnet einem Büro eine feste Ping-Rolle zu",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        buero="Sprachkanal des Büros",
        rolle="Rolle, die bei einer Anfrage für dieses Büro gepingt werden soll",
    )
    async def buero_rolle_setzen(
        self,
        interaction: discord.Interaction,
        buero: discord.VoiceChannel,
        rolle: discord.Role,
    ):
        if rolle.is_default() or rolle.managed:
            await interaction.response.send_message(
                "❌ Bitte wähle eine normale, erwähnbare Bürorolle.",
                ephemeral=True,
            )
            return
        set_config(f"office_role_{buero.id}", rolle.id)
        await interaction.response.send_message(
            f"✅ Für **{buero.name}** wird bei neuen Büro-Anfragen jetzt {rolle.mention} gepingt. "
            "Die feste Zuordnung überschreibt die automatische Namens-Erkennung.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(BueroSystemCog(bot))
