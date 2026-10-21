import re
import sqlite3
import time

import discord
from pulse_embed_design import color as _pulse_color
from discord import app_commands
from discord.ext import commands

import pulse_db

DB_NAME = "rp_verwaltung.db"
# K-02: who may use the panel besides admins and leaders (can_promote) – role IDs set in the dashboard.
ROLE_SETTING_KEY = "rp_panel_role_ids"
ANNOUNCE_COOLDOWN_SECONDS = 60
_LAST_ANNOUNCEMENT: dict[tuple[int, str], float] = {}  # cooldown per guild and action (start/stop)

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


# --- RECHTE & EINGABEN (K-02) ---
def _member_perms(guild, member) -> dict:
    try:
        import webserver
        perms, _ = webserver.compute_perms(guild, member.id, webserver.load_config())
        return perms
    except Exception:
        return {}


def allowed_role_ids() -> set[int]:
    try:
        raw = pulse_db.get_setting(ROLE_SETTING_KEY, []) or []
    except Exception:
        raw = []
    return {int(v) for v in raw if str(v).isdigit()}


def can_use_rp_panel(guild, member) -> bool:
    if getattr(getattr(member, "guild_permissions", None), "administrator", False):
        return True
    perms = _member_perms(guild, member)
    if perms.get("is_admin") or perms.get("can_promote"):
        return True
    extra = allowed_role_ids()
    return bool(extra and any(getattr(role, "id", 0) in extra for role in getattr(member, "roles", [])))


def normalize_rp_time(value: str) -> str | None:
    """'14:30' / '9.05 Uhr' → 'HH:MM Uhr', 'offen' → 'Offen'; anything else (incl. mentions) → None."""
    text = str(value or "").strip()
    if text.casefold() == "offen":
        return "Offen"
    match = re.fullmatch(r"(\d{1,2})[:.](\d{2})(\s*uhr)?", text, re.IGNORECASE)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d} Uhr"


def _audit(member, action: str, details: str) -> None:
    try:
        import webserver
        webserver.log_audit(getattr(member, "display_name", "?"), str(getattr(member, "id", "")), action, details)
    except Exception as exc:
        print(f"⚠️ RP-Verwaltung: Audit-Eintrag fehlgeschlagen ({type(exc).__name__}).")


async def _reply(interaction: discord.Interaction, text: str):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


# --- HELFER ZUM SENDEN DER ANKÜNDIGUNG ---
async def send_rp_announcement(interaction: discord.Interaction, content: str, action: str = "RP-Ankündigung"):
    guild = interaction.guild
    if guild is None or not can_use_rp_panel(guild, interaction.user):
        await _reply(interaction, "❌ Du bist nicht berechtigt, RP-Ankündigungen zu senden.")
        return
    now = time.monotonic()
    cooldown_key = (guild.id, action)  # "RP Stop" right after "RP Start" stays possible
    last = _LAST_ANNOUNCEMENT.get(cooldown_key)
    if last is not None and now - last < ANNOUNCE_COOLDOWN_SECONDS:
        await _reply(interaction, f"⏳ Bitte warte noch {int(ANNOUNCE_COOLDOWN_SECONDS - (now - last)) + 1} s – gerade wurde schon angekündigt.")
        return

    # Sofort als verarbeitet markieren, um den 3-Sekunden-Timeout zu verhindern
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    channel_id = get_config("announcement_channel_id")
    ch = guild.get_channel(int(channel_id)) if channel_id else None
    if ch is None:
        # No fallback to the current channel: an @everyone must only go where it was configured.
        await interaction.followup.send("❌ Kein Ankündigungskanal eingerichtet oder Kanal nicht gefunden. "
                                        "Bitte /setup_rp erneut ausführen.", ephemeral=True)
        return
    _LAST_ANNOUNCEMENT[cooldown_key] = now  # reserved now (no double announcement), released again on failure

    # Only Pulse's own previous announcement is removed – never messages of other people.
    previous_id = get_config("last_announcement_message_id")
    if previous_id:
        try:
            old = await ch.fetch_message(int(previous_id))
            await old.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    try:
        message = await ch.send(
            content=content,
            allowed_mentions=discord.AllowedMentions(everyone=True, users=False, roles=False),
        )
    except (discord.Forbidden, discord.HTTPException) as exc:
        if last is None:
            _LAST_ANNOUNCEMENT.pop(cooldown_key, None)
        else:
            _LAST_ANNOUNCEMENT[cooldown_key] = last
        await interaction.followup.send(f"❌ Pulse konnte in {ch.mention} nicht senden ({type(exc).__name__}). "
                                        "Bitte Kanalrechte prüfen.", ephemeral=True)
        return
    set_config("last_announcement_message_id", int(message.id))
    _audit(interaction.user, action, f"Kanal {ch.id}")
    await interaction.followup.send(f"✅ RP-Ankündigung wurde erfolgreich in {ch.mention} gesendet!", ephemeral=True)

# --- POP-UP FENSTER FÜR RP STOP (Uhrzeit abfragen) ---
class RPStopModal(discord.ui.Modal, title="RP Stop - Nächster RP Start"):
    uhrzeit = discord.ui.TextInput(
        label="Geplanter RP-Start für morgen:",
        placeholder="z.B. 14:30 Uhr oder Offen",
        default="14:30 Uhr",
        max_length=30
    )

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or not can_use_rp_panel(interaction.guild, interaction.user):
            await _reply(interaction, "❌ Du bist nicht berechtigt, RP-Ankündigungen zu senden.")
            return
        start = normalize_rp_time(self.uhrzeit.value)
        if start is None:
            await _reply(interaction, "❌ Bitte eine Uhrzeit wie **14:30** oder **Offen** eingeben.")
            return
        msg_content = (
            "#  ⛔  RP Stop  ⛔  \n\n"
            "> **Das RP wird hiermit offiziell gestoppt!**\n"
            "> ***Informationen zu dem folgenden Tag***\n"
            f"> **⏰ Geplanter RP-Start: {start}** \n"
            "> # Kommt gerne morgen wieder auf den Server!\n"
            "@everyone"
        )
        await send_rp_announcement(interaction, msg_content, "RP Stop angekündigt")

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
        if interaction.guild is None or not can_use_rp_panel(interaction.guild, interaction.user):
            await _reply(interaction, "❌ Du bist nicht berechtigt, RP-Ankündigungen zu senden.")
            return
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
            await send_rp_announcement(interaction, msg_content, "RP Start angekündigt")

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

    @staticmethod
    def _panel_embed(guild: discord.Guild) -> discord.Embed:
        embed = discord.Embed(
            title="💻 Roleplay Verwaltung",
            description="Nutze das Menü unten, um das RP offiziell zu starten oder zu beenden.",
            color=_pulse_color("danger"),
        )
        if getattr(guild, "icon", None):
            embed.set_thumbnail(url=guild.icon.url)
        return embed

    async def publish_panel(self, guild: discord.Guild, target_channel: discord.TextChannel | None = None) -> dict:
        """Repair or publish the one tracked RP-control panel without creating duplicates."""
        channel_id = int(target_channel.id if target_channel else (get_config("panel_channel_id") or 0))
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            raise ValueError("RP-Panel-Kanal ist nicht gespeichert oder nicht mehr erreichbar. Bitte /setup_rp im Zielkanal ausführen.")
        if not hasattr(channel, "send") or getattr(channel, "guild", None) != guild:
            raise ValueError("Der konfigurierte RP-Panel-Kanal gehört nicht zu diesem Server.")

        old_channel_id = int(get_config("panel_channel_id") or 0)
        old_message_id = int(get_config("panel_message_id") or 0)
        message = None
        if old_channel_id == channel.id and old_message_id:
            try:
                message = await channel.fetch_message(old_message_id)
            except discord.NotFound:
                message = None
            except (discord.Forbidden, discord.HTTPException) as exc:
                raise ValueError(f"Die gespeicherte RP-Panel-Nachricht konnte nicht geprüft werden ({type(exc).__name__}); es wurde keine neue erstellt.") from exc

        if message is not None:
            await message.edit(embed=self._panel_embed(guild), view=RPView())
            action = "aktualisiert"
        else:
            message = await channel.send(embed=self._panel_embed(guild), view=RPView(), allowed_mentions=discord.AllowedMentions.none())
            action = "veröffentlicht"

        # If an admin explicitly re-runs /setup_rp in another channel, first publish
        # the replacement successfully, then best-effort remove the old tracked message.
        if old_channel_id and old_message_id and old_channel_id != channel.id:
            old_channel = guild.get_channel(old_channel_id)
            if old_channel is not None:
                try:
                    old_message = await old_channel.fetch_message(old_message_id)
                    await old_message.delete(reason="Pulse-RP-Panel wurde durch /setup_rp in einen anderen Kanal verschoben")
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

        set_config("panel_channel_id", channel.id)
        set_config("panel_message_id", message.id)
        self.bot.add_view(RPView())
        return {"action": action, "channel": channel, "message_id": message.id}

    @app_commands.command(name="setup_rp", description="[Admin] Erstellt das RP-Verwaltungs-Dashboard")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        ankündigungs_kanal="[Optional] Kanal, in den die Start/Stop Ankündigungen gesendet werden sollen"
    )
    async def setup_rp(self, interaction: discord.Interaction, ankündigungs_kanal: discord.TextChannel = None):
        # Ankündigungskanal speichern (falls angegeben)
        target_ch = ankündigungs_kanal or interaction.channel
        set_config("announcement_channel_id", target_ch.id)

        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.publish_panel(interaction.guild, interaction.channel)
        except (ValueError, discord.Forbidden, discord.HTTPException) as exc:
            return await interaction.followup.send(
                f"❌ RP-Panel konnte nicht erstellt/aktualisiert werden ({type(exc).__name__}).",
                ephemeral=True,
            )
        await interaction.followup.send(
            f"✅ RP-Panel {result['action']} in {result['channel'].mention}.\n"
            f"📢 Ankündigungen werden in {target_ch.mention} gesendet.\n"
            "🔐 Bedienen dürfen Admins, Leitung (Beförderungsrecht) und die in der Verwaltung freigegebenen Rollen.",
            ephemeral=True,
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(RPVerwaltungCog(bot))
