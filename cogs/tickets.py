from __future__ import annotations

import asyncio
import os
import re

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, Modal, Select, TextInput, View

from pulse_db import (
    claim_ticket,
    close_ticket,
    create_ticket,
    get_ticket,
    list_tickets,
    record_event,
    unclaim_ticket,
    update_ticket_priority,
    get_setting,
    set_setting,
)

os.makedirs("transcripts", exist_ok=True)

TICKET_PARENT_NAME = "🎫・Tickets"
TEAM_UPDATE_CHANNEL_ID = 1531132354272170115

# Native Pulse ticket panel sources and lifecycle logs (no external ticket bot).
TICKET_PANEL_CHANNEL_IDS = (
    1511390378752741386,
    1494338404224340179,
    1494397389635719269,
    1488972966774706186,
    1559242374658662602,
    1488699195157254436,
)
DEFAULT_TICKET_OPEN_LOG_CHANNEL_ID = 1543914075308818503
DEFAULT_TICKET_CLAIM_LOG_CHANNEL_ID = 1535025111722098778


def configured_panel_channel_ids() -> tuple[int, ...]:
    raw = ticket_config().get("panel_channel_ids", list(TICKET_PANEL_CHANNEL_IDS))
    if not isinstance(raw, list):
        raw = list(TICKET_PANEL_CHANNEL_IDS)
    values = []
    for item in raw[:25]:
        try:
            channel_id = int(item)
        except (TypeError, ValueError):
            continue
        if channel_id > 0 and channel_id not in values:
            values.append(channel_id)
    return tuple(values) or TICKET_PANEL_CHANNEL_IDS


def configured_ticket_log_channel_id(kind: str) -> int:
    settings = ticket_config()
    key = "ticket_open_log_channel_id" if kind == "opened" else "ticket_claim_log_channel_id"
    fallback = DEFAULT_TICKET_OPEN_LOG_CHANNEL_ID if kind == "opened" else DEFAULT_TICKET_CLAIM_LOG_CHANNEL_ID
    try:
        return int(settings.get(key) or fallback)
    except (TypeError, ValueError):
        return fallback

CATEGORY_META = {
    "support": ("Allgemeiner Support", "Fragen, Hilfe und allgemeine Anliegen", "🎫", 10, "team"),
    "bug": ("Bug melden", "Technische Fehler oder Server-Bugs melden", "⚙️", 5, "team"),
    "unban": ("Entbannungsantrag", "Antrag auf Überprüfung einer Sanktion", "📩", 5, "team"),
    "highteam": ("Highteam Ticket", "Vertrauliche Angelegenheiten für das Highteam", "💢", 3, "highteam"),
    "leadership": ("Führungsebene Ticket", "Vertrauliche Angelegenheiten für die Führungsebene", "🐬", 3, "leadership"),
}

PRIORITIES = ["low", "normal", "high", "urgent"]
PRIORITY_LABELS = {"low": "⚪ Niedrig", "normal": "🟢 Normal", "high": "🟠 Hoch", "urgent": "🚨 Dringend"}


def web_cfg():
    import webserver
    return webserver.load_config()


def team_roles(guild):
    ids = set(web_cfg().get("team_role_ids", []))
    return [r for r in guild.roles if r.id in ids]


def ticket_config() -> dict:
    return get_setting("ticket_system_config", {}) or {}


def save_ticket_config(config: dict):
    set_setting("ticket_system_config", config)


def configured_role_ids(guild, group: str):
    config = ticket_config()
    key = f"{group}_role_ids"
    values = config.get(key, [])
    roles = []
    for raw in values if isinstance(values, list) else []:
        try:
            role = guild.get_role(int(raw))
        except (TypeError, ValueError):
            role = None
        if role and not role.managed:
            roles.append(role)
    if roles:
        return roles
    return team_roles(guild)


def category_open_count(key: str) -> int:
    return sum(
        1 for ticket in list_tickets(limit=2000)
        if ticket.get("category") == key and ticket.get("status") != "closed"
    )


def ticket_category_definitions() -> dict:
    """Merge Pulse's built-in categories with admin-configured categories."""
    definitions = {
        key: {
            "id": key, "name": meta[0], "description": meta[1], "emoji": meta[2],
            "max_open": meta[3], "group": meta[4], "prefix": key,
            "role_ids": [], "parent_category_id": 0, "opening_message": "", "active": True,
            "builtin": True,
        }
        for key, meta in CATEGORY_META.items()
    }
    saved = ticket_config().get("ticket_categories", [])
    for item in saved if isinstance(saved, list) else []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or "").strip()
        if not key or not re.fullmatch(r"[a-z0-9_-]{1,80}", key):
            continue
        if key not in definitions and not key.startswith("custom_"):
            continue
        base = definitions.get(key, {
            "id": key, "name": "", "description": "", "emoji": "🎫", "max_open": 0,
            "group": "team", "prefix": key, "role_ids": [], "parent_category_id": 0,
            "opening_message": "", "active": True, "builtin": False,
        })
        base.update(item)
        base["id"] = key
        base["builtin"] = key in CATEGORY_META
        definitions[key] = base
    return definitions


def category_meta(key: str):
    definition = ticket_category_definitions().get(str(key))
    if not definition:
        return ("Ticket", "Allgemeines Anliegen", "🎫", 10, "team")
    try:
        maximum = max(0, int(definition.get("max_open") or 0))
    except (TypeError, ValueError):
        maximum = 0
    return (
        str(definition.get("name") or "Ticket")[:100],
        str(definition.get("description") or "Allgemeines Anliegen")[:100],
        str(definition.get("emoji") or "🎫")[:100],
        maximum,
        str(definition.get("group") or "team"),
    )


def category_roles(guild, key: str):
    definition = ticket_category_definitions().get(str(key), {})
    raw_ids = definition.get("role_ids", [])
    selected = []
    for raw in raw_ids if isinstance(raw_ids, list) else []:
        try:
            role = guild.get_role(int(raw))
        except (TypeError, ValueError):
            role = None
        if role and not role.managed and not role.is_default():
            selected.append(role)
    if selected:
        return selected
    return configured_role_ids(guild, category_meta(key)[4])


def can_manage_ticket(member, ticket) -> bool:
    if member.guild_permissions.administrator:
        return True
    key = str(ticket.get("category", "support"))
    return any(role.id in {r.id for r in category_roles(member.guild, key)} for role in member.roles)


def build_panel_embed(guild, panel_config: dict | None = None) -> discord.Embed:
    if isinstance(panel_config, dict):
        panel_title = str(panel_config.get("title") or panel_config.get("name") or "Ticket-Support").strip()[:256]
        panel_description = str(panel_config.get("description") or "Wähle unten eine passende Kategorie, um ein Ticket zu eröffnen.").strip()[:4000]
        raw_color = str(panel_config.get("color") or "#5865F2").strip().lstrip("#")
        try:
            embed_color = discord.Color(int(raw_color[:6], 16))
        except (TypeError, ValueError):
            embed_color = discord.Color.blurple()
        embed = discord.Embed(title=panel_title, description=panel_description, color=embed_color)
        definitions = ticket_category_definitions()
        keys = panel_config.get("category_keys", [])
        if not isinstance(keys, list):
            keys = []
        lines = []
        for key in keys[:25]:
            definition = definitions.get(str(key))
            if not definition or not definition.get("active", True):
                continue
            name, _, emoji, _, _ = category_meta(str(key))
            lines.append(f"{emoji} **{name}**")
        if lines:
            embed.add_field(name="📂 Verfügbare Kategorien", value="\n".join(lines)[:1024], inline=False)
        embed.set_footer(text=str(panel_config.get("footer") or "Pulse Ticket-System")[:2048])
        embed.timestamp = discord.utils.utcnow()
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        return embed

    lines = [
        "<:ticket:1360768000235409559> **Ticket-Support**",
        "",
        "Brauchst du Hilfe oder möchtest etwas melden? Unser Team hilft dir schnell und zuverlässig weiter.",
        "",
        "## 📂 Verfügbare Tickets",
        "",
        "🎫 **Allgemeiner Support**",
        "⚙️ **Bug melden**",
        "📩 **Entbannungsantrag**",
        "💢 **Highteam Ticket**",
        "🐬 **Führungsebene Ticket**",
        "",
        "«Wähle unten einfach die passende Kategorie aus und erstelle dein Ticket.»",
        "",
        "💙 Dein ©𝑩𝑶𝑪𝑯𝑼𝑴 𝑹𝑷 | 𝑽𝟏 Team",
        "",
        "### <:chartsimple:1492541035497128132> Ticket Auslastung",
        "",
        "Hier siehst du die aktuelle Auslastung unserer Tickets. Bei einer höheren Auslastung kann es zu längeren Bearbeitungszeiten kommen. Ein Ticket kannst du jedoch jederzeit eröffnen, unabhängig von der Auslastung.",
        "",
    ]
    for key, meta in CATEGORY_META.items():
        current = category_open_count(key)
        maximum = meta[3]
        percent = int((current / maximum) * 100) if maximum else 0
        lines.append(f"- <:ut_low:1360768000239769621> **{meta[0]}:** Verfügbar `{current}/{maximum}` ({percent}%)")
    embed = discord.Embed(title="<:ticket:1360768000235409559> Ticket-Support", description="\n".join(lines), color=discord.Color.blurple())
    embed.add_field(name="🔐 Privat & sicher", value="Tickets sind nur für den Ersteller und die zuständigen Bearbeiter sichtbar.", inline=True)
    embed.add_field(name="⚡ Schnelle Bearbeitung", value="Tickets können übernommen, priorisiert und strukturiert abgeschlossen werden.", inline=True)
    embed.set_footer(text="Pulse Ticket-System • Bochum RP")
    embed.timestamp = discord.utils.utcnow()
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


def is_team(member) -> bool:
    if member.guild_permissions.administrator:
        return True
    ids = set(web_cfg().get("team_role_ids", []))
    return any(r.id in ids for r in getattr(member, "roles", []))


def safe_channel_name(name: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.lower()).strip("-")
    return value[:28] or "user"


def priority_embed_value(priority: str) -> str:
    return PRIORITY_LABELS.get(priority, "🟢 Normal")


async def _resolve_text_channel(bot, guild: discord.Guild, channel_id: int):
    channel = guild.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
    if not isinstance(channel, discord.TextChannel):
        return None
    if channel.guild.id != guild.id:
        return None
    return channel


async def send_ticket_log(
    bot: commands.Bot,
    guild: discord.Guild,
    destination_id: int,
    *,
    title: str,
    description: str,
    ticket: dict,
    actor: discord.abc.User | None = None,
    color: discord.Color | None = None,
):
    """Send a compact lifecycle log for Pulse-owned tickets."""
    destination = await _resolve_text_channel(bot, guild, destination_id)
    if destination is None:
        print(f"⚠️ Pulse-Tickets: Protokollkanal {destination_id} fehlt oder ist nicht erreichbar.")
        return False
    channel_id = str(ticket.get("channel_id") or "")
    embed = discord.Embed(
        title=title,
        description=description,
        color=color or discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Ticket-ID", value=f"`{ticket.get('id', 'unbekannt')}`", inline=True)
    embed.add_field(name="Kategorie", value=category_meta(ticket.get("category"))[0], inline=True)
    embed.add_field(name="Status", value="🔵 In Bearbeitung" if ticket.get("claimed_by_id") else "🟢 Offen", inline=True)
    creator_name = str(ticket.get("user_name") or "Unbekannt")[:200]
    creator_id = str(ticket.get("user_id") or "")
    embed.add_field(name="Ersteller", value=f"{creator_name}" + (f" (`{creator_id}`)" if creator_id else ""), inline=True)
    if actor:
        embed.add_field(name="Bearbeiter", value=f"{actor} (`{actor.id}`)", inline=True)
    if channel_id.isdigit():
        embed.add_field(name="Ticket-Kanal", value=f"<#{channel_id}>", inline=True)
        embed.url = f"https://discord.com/channels/{guild.id}/{channel_id}"
    embed.set_footer(text="Pulse Ticket-System • Ereignisprotokoll")
    try:
        await destination.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return True
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"⚠️ Pulse-Tickets: Protokoll in {destination_id} fehlgeschlagen ({type(exc).__name__}).")
        return False


async def transcript(channel):
    rows = []
    async for m in channel.history(limit=None, oldest_first=True):
        stamp = m.created_at.strftime("%d.%m.%Y %H:%M:%S")
        text = m.content or "[kein Text]"
        if m.attachments:
            text += " | Anhänge: " + ", ".join(a.url for a in m.attachments)
        if m.embeds:
            text += " | Embeds: " + ", ".join((e.title or "Embed") for e in m.embeds)
        rows.append(f"[{stamp}] {m.author} ({m.author.id}): {text}")
    return "\n".join(rows)


async def refresh_ticket_embed(channel, ticket: dict, control_view=None):
    label, _, emoji, _, _ = category_meta(ticket.get("category"))
    embed = discord.Embed(
        title=f"{emoji} {label} – Ticket",
        description=f"Ticket-ID: `{ticket['id']}`\nAnliegen: **{ticket['user_name']}**",
        color=discord.Color.red() if ticket.get("priority") == "urgent" else (
            CATEGORY_META.get(ticket.get("category"), CATEGORY_META["support"])[3] and discord.Color.blurple()
        ),
    )
    status = "✅ Geschlossen" if ticket.get("status") == "closed" else "🔵 In Bearbeitung" if ticket.get("claimed_by_id") else "🟢 Offen"
    claimed = ticket.get("claimed_by_name") or "Niemand"
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Priorität", value=priority_embed_value(ticket.get("priority", "normal")), inline=True)
    embed.add_field(name="Bearbeiter", value=claimed, inline=True)
    embed.set_footer(text="Pulse Ticket-System · Nutze die Buttons für die Bearbeitung")
    # The setup message is the first bot message in a new ticket.
    try:
        async for m in channel.history(limit=20, oldest_first=True):
            if m.author.id == channel.guild.me.id and m.embeds:
                await m.edit(embed=embed, view=control_view or TicketControlView())
                return
    except Exception:
        pass


class TicketCloseModal(Modal, title="Ticket schließen"):
    reason = TextInput(label="Schließgrund", placeholder="Warum wird das Ticket geschlossen?", required=True, max_length=500, style=discord.TextStyle.paragraph)

    def __init__(self, ticket_id: str, actor: discord.Member):
        super().__init__(timeout=300)
        self.ticket_id = ticket_id
        self.actor = actor

    async def on_submit(self, interaction: discord.Interaction):
        ticket = get_ticket(ticket_id=self.ticket_id)
        if not ticket or ticket.get("status") == "closed":
            await interaction.response.send_message("Das Ticket ist bereits geschlossen.", ephemeral=True)
            return
        channel = interaction.channel
        await interaction.response.send_message("🔒 Ticket wird archiviert …", ephemeral=True)
        path = ""
        try:
            text = await transcript(channel)
            local_name = f"{ticket['id']}.txt"
            local_path = os.path.join("transcripts", local_name)
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(text)
            path = local_name
            # Keep the transcript in the channel shortly so the team can verify it exists.
            try:
                await channel.send(file=discord.File(local_path, filename=local_name))
            except Exception:
                pass
        except Exception as exc:
            print(f"Transcript konnte nicht gespeichert werden: {exc}")
        reason = str(self.reason.value).strip()[:500]
        if not close_ticket(ticket["id"], reason, path, self.actor.id, self.actor.display_name):
            return
        record_event("ticket_closed", "ticket", ticket["id"], self.actor.id, self.actor.display_name, {"reason": reason})
        try:
            member = channel.guild.get_member(int(ticket["user_id"]))
            if member:
                dm = discord.Embed(title="🎫 Dein Ticket wurde geschlossen", color=discord.Color.blurple())
                dm.description = f"Ticket `{ticket['id']}` wurde von **{self.actor.display_name}** geschlossen.\n\n**Grund:** {reason}"
                await member.send(embed=dm)
        except Exception:
            pass
        await asyncio.sleep(1)
        try:
            await channel.delete(reason=f"Pulse Ticket geschlossen von {self.actor}")
        except Exception:
            pass


class TicketSelect(Select):
    def __init__(self, panel_id: str | None = None, category_keys=None):
        definitions = ticket_category_definitions()
        keys = category_keys if isinstance(category_keys, list) else list(definitions)
        options = []
        for key in keys:
            definition = definitions.get(str(key))
            if not definition or not definition.get("active", True):
                continue
            name, description, emoji, maximum, _ = category_meta(str(key))
            count = category_open_count(str(key))
            suffix = f" · {count}/{maximum} offen" if maximum else f" · {count} offen"
            options.append(discord.SelectOption(
                label=name[:100],
                description=(description + suffix)[:100],
                emoji=emoji[:100] if emoji else "🎫",
                value=str(key)[:100],
            ))
        options = options[:25]
        if not options:
            options = [discord.SelectOption(label="Noch nicht eingerichtet", value="__not_configured__", emoji="⚠️")]
        custom_id = "pulse_ticket_category_v3"
        if panel_id:
            safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(panel_id))[:60] or "custom"
            custom_id = f"pulse_ticket_category_panel_{safe_id}"[:100]
        super().__init__(
            placeholder="Wähle die passende Ticket-Kategorie …",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=custom_id,
        )
        self.panel_id = panel_id

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("❌ Nur auf einem Server verfügbar.", ephemeral=True)
            return

        key = self.values[0]
        if key == "__not_configured__":
            await interaction.response.send_message("Dieses Panel ist noch nicht eingerichtet. Bitte melde dich beim Serverteam.", ephemeral=True)
            return
        label, desc, emoji, maximum, group = category_meta(key)
        category_definition = ticket_category_definitions().get(key, {})

        existing = None
        for ticket in list_tickets(limit=2000):
            if (
                str(ticket.get("guild_id")) == str(guild.id)
                and str(ticket.get("user_id")) == str(interaction.user.id)
                and ticket.get("status") != "closed"
            ):
                existing = ticket
                break

        if existing:
            channel = guild.get_channel(int(existing["channel_id"])) if existing.get("channel_id") else None
            if channel:
                await interaction.response.send_message(
                    f"🎫 Du hast bereits ein offenes Ticket: {channel.mention}",
                    ephemeral=True,
                )
                return
            close_ticket(existing["id"], "Verwaistes Ticket automatisch archiviert.", "", interaction.user.id, interaction.user.display_name)

        roles = category_roles(guild, key)
        if key in ("highteam", "leadership") and not (ticket_config().get(f"{group}_role_ids") or category_definition.get("role_ids")):
            await interaction.response.send_message(
                f"⚠️ **{label}** ist noch nicht vollständig eingerichtet. Eine zuständige Rolle muss zuerst durch einen Administrator konfiguriert werden.",
                ephemeral=True,
            )
            return

        # Discord requires a response within a few seconds; channel creation and log delivery
        # may take longer, so defer only after all immediate validation errors are handled.
        await interaction.response.defer(ephemeral=True, thinking=True)
        parent_id = category_definition.get("parent_category_id")
        parent = None
        try:
            candidate = guild.get_channel(int(parent_id)) if parent_id else None
            if isinstance(candidate, discord.CategoryChannel):
                parent = candidate
        except (TypeError, ValueError):
            parent = None
        if parent is None:
            parent = discord.utils.get(guild.categories, name=TICKET_PARENT_NAME)
        if not parent:
            parent = await guild.create_category(TICKET_PARENT_NAME, reason="Pulse Ticket-System")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
                manage_permissions=True,
                attach_files=True,
                embed_links=True,
            ),
        }

        for role in roles:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True,
            )

        channel_prefix = str(category_definition.get("prefix") or key)
        channel = await guild.create_text_channel(
            f"ticket-{safe_channel_name(channel_prefix)}-{safe_channel_name(interaction.user.display_name)}",
            category=parent,
            overwrites=overwrites,
            topic=f"Pulse Ticket | user:{interaction.user.id} | category:{key}",
            reason=f"Pulse Ticket erstellt von {interaction.user}",
        )
        tid = create_ticket(channel.id, guild.id, interaction.user.id, interaction.user.display_name, key)

        opening_text = str(category_definition.get("opening_message") or "").strip()[:1800]
        if not opening_text:
            opening_text = "Beschreibe dein Anliegen bitte möglichst vollständig. Ein zuständiges Teammitglied wird sich um dein Anliegen kümmern."
        embed = discord.Embed(
            title=f"{emoji} {label}",
            description=(
                f"Willkommen {interaction.user.mention}!\n\n"
                f"{opening_text}\n\n"
                f"**Ticket-ID:** `{tid}`\n"
                f"**Kategorie:** {label}\n"
                f"**Status:** 🟢 Offen\n"
                f"**Priorität:** 🟢 Normal\n"
                f"**Bearbeiter:** Noch nicht übernommen"
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="📋 Vorgehen",
            value="1. Anliegen erklären\n2. Rückfragen beantworten\n3. Lösung bestätigen\n4. Ticket über **Schließen** beenden",
            inline=False,
        )
        embed.add_field(
            name="🛡️ Hinweise",
            value="Keine unnötigen Pings. Sensible Daten nur an zuständige Teammitglieder weitergeben.",
            inline=False,
        )
        embed.set_footer(text="Pulse Ticket-System • Ticket-Steuerung")
        embed.timestamp = discord.utils.utcnow()
        await channel.send(embed=embed, view=TicketControlView())

        mentions = " ".join(role.mention for role in roles[:10])
        if mentions:
            await channel.send(
                f"📣 {mentions}\nNeues **{label}** von {interaction.user.mention}.",
                allowed_mentions=discord.AllowedMentions(roles=True, users=True),
            )

        record_event(
            "ticket_created",
            "ticket",
            tid,
            interaction.user.id,
            interaction.user.display_name,
            {"category": key, "channel_id": channel.id, "panel_channel_id": interaction.channel.id},
        )
        created_ticket = get_ticket(ticket_id=tid)
        if created_ticket:
            await send_ticket_log(
                interaction.client,
                guild,
                configured_ticket_log_channel_id("opened"),
                title="🎫 Neues Ticket geöffnet",
                description="Ein neues Ticket wurde über das Pulse-Ticketpanel erstellt.",
                ticket=created_ticket,
                actor=interaction.user,
                color=discord.Color.green(),
            )
        await interaction.followup.send(f"✅ Dein Ticket wurde erstellt: {channel.mention}", ephemeral=True)


class TicketView(View):
    def __init__(self, panel_id: str | None = None, category_keys=None):
        super().__init__(timeout=None)
        self.add_item(TicketSelect(panel_id=panel_id, category_keys=category_keys))


class TicketControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Übernehmen", style=discord.ButtonStyle.success, emoji="📥", custom_id="pulse_ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: Button):
        t = get_ticket(channel_id=interaction.channel.id)
        if not t:
            await interaction.response.send_message("Ticket nicht gefunden.", ephemeral=True)
            return
        if not is_team(interaction.user):
            await interaction.response.send_message("❌ Nur Teammitglieder dürfen Tickets übernehmen.", ephemeral=True)
            return
        ok = claim_ticket(t["id"], interaction.user.id, interaction.user.display_name)
        if not ok:
            latest = get_ticket(ticket_id=t["id"])
            who = latest.get("claimed_by_name") if latest else None
            await interaction.response.send_message(f"Dieses Ticket wurde bereits von **{who or 'jemand anderem'}** übernommen.", ephemeral=True)
            return
        # Follow-up logging and message updates can exceed Discord's initial response window.
        await interaction.response.defer(ephemeral=True, thinking=True)
        latest = get_ticket(ticket_id=t["id"])
        button.disabled = True
        button.label = f"Übernommen · {interaction.user.display_name}"[:80]
        try:
            if latest:
                await refresh_ticket_embed(interaction.channel, latest, self)
        except Exception:
            pass
        if latest:
            await send_ticket_log(
                interaction.client,
                interaction.guild,
                configured_ticket_log_channel_id("claimed"),
                title="📥 Ticket übernommen",
                description="Ein Teammitglied hat ein Pulse-Ticket beansprucht.",
                ticket=latest,
                actor=interaction.user,
                color=discord.Color.blue(),
            )
            try:
                await interaction.channel.send(
                    f"📥 {interaction.user.mention} hat dieses Ticket übernommen.",
                    allowed_mentions=discord.AllowedMentions(users=[interaction.user], roles=False, everyone=False),
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
        await interaction.followup.send(f"✅ Du bearbeitest Ticket `{t['id']}` jetzt.", ephemeral=True)

    @discord.ui.button(label="Freigeben", style=discord.ButtonStyle.secondary, emoji="↩️", custom_id="pulse_ticket_unclaim")
    async def unclaim(self, interaction: discord.Interaction, button: Button):
        t = get_ticket(channel_id=interaction.channel.id)
        if not t:
            await interaction.response.send_message("Ticket nicht gefunden.", ephemeral=True)
            return
        if not is_team(interaction.user):
            await interaction.response.send_message("❌ Nur Teammitglieder dürfen Tickets freigeben.", ephemeral=True)
            return
        if not t.get("claimed_by_id"):
            await interaction.response.send_message("Das Ticket ist bereits frei.", ephemeral=True)
            return
        if str(t.get("claimed_by_id")) != str(interaction.user.id) and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Du kannst nur dein eigenes Ticket freigeben (Admins dürfen übernehmen).", ephemeral=True)
            return
        if unclaim_ticket(t["id"], interaction.user.id, interaction.user.display_name):
            await refresh_ticket_embed(interaction.channel, get_ticket(ticket_id=t["id"]), TicketControlView())
            await interaction.response.send_message("↩️ Ticket wurde freigegeben.", ephemeral=True)
        else:
            await interaction.response.send_message("Ticket konnte nicht freigegeben werden.", ephemeral=True)

    @discord.ui.button(label="Priorität", style=discord.ButtonStyle.secondary, emoji="🚦", custom_id="pulse_ticket_priority")
    async def priority(self, interaction: discord.Interaction, button: Button):
        t = get_ticket(channel_id=interaction.channel.id)
        if not t:
            await interaction.response.send_message("Ticket nicht gefunden.", ephemeral=True)
            return
        if not is_team(interaction.user):
            await interaction.response.send_message("❌ Dafür fehlt dir die Berechtigung.", ephemeral=True)
            return
        current = t.get("priority", "normal")
        next_priority = PRIORITIES[(PRIORITIES.index(current) + 1) % len(PRIORITIES)] if current in PRIORITIES else "normal"
        update_ticket_priority(t["id"], next_priority, interaction.user.id, interaction.user.display_name)
        await refresh_ticket_embed(interaction.channel, get_ticket(ticket_id=t["id"]), TicketControlView())
        await interaction.response.send_message(f"🚦 Priorität: {priority_embed_value(next_priority)}", ephemeral=True)

    @discord.ui.button(label="Schließen", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="pulse_ticket_close")
    async def close(self, interaction: discord.Interaction, button: Button):
        t = get_ticket(channel_id=interaction.channel.id)
        if not t:
            await interaction.response.send_message("Ticket nicht gefunden.", ephemeral=True)
            return
        if not is_team(interaction.user) and str(t.get("user_id")) != str(interaction.user.id):
            await interaction.response.send_message("❌ Dafür fehlt dir die Berechtigung.", ephemeral=True)
            return
        await interaction.response.send_modal(TicketCloseModal(t["id"], interaction.user))


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.panel_refresh.start()

    def cog_unload(self):
        self.panel_refresh.cancel()

    async def cog_load(self):
        self.bot.add_view(TicketView())
        self.bot.add_view(TicketControlView())
        for panel in ticket_config().get("ticket_panels", []):
            if not isinstance(panel, dict) or not panel.get("id"):
                continue
            self.bot.add_view(TicketView(str(panel["id"]), panel.get("category_keys", [])))

    @tasks.loop(minutes=5)
    async def panel_refresh(self):
        await self.bot.wait_until_ready()
        settings = ticket_config()
        panels = settings.get("panel_messages", {})
        if not isinstance(panels, dict):
            panels = {}
        legacy_channel_id = settings.get("panel_channel_id")
        legacy_message_id = settings.get("panel_message_id")
        if legacy_channel_id and legacy_message_id:
            panels = {**panels, str(legacy_channel_id): int(legacy_message_id)}
        if not isinstance(panels, dict) or not panels:
            return
        custom_panels = settings.get("ticket_panels", [])
        if not isinstance(custom_panels, list):
            custom_panels = []
        for guild in self.bot.guilds:
            for raw_channel_id, raw_message_id in list(panels.items()):
                try:
                    channel = guild.get_channel(int(raw_channel_id))
                    if not isinstance(channel, discord.TextChannel):
                        continue
                    message = await channel.fetch_message(int(raw_message_id))
                    await message.edit(embed=build_panel_embed(guild), view=TicketView())
                except (TypeError, ValueError, discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                    print(f"Ticket-Panel {raw_channel_id} konnte nicht aktualisiert werden ({type(exc).__name__}).")
            for panel in custom_panels:
                if not isinstance(panel, dict) or not panel.get("id"):
                    continue
                try:
                    channel = await _resolve_text_channel(self.bot, guild, int(panel.get("channel_id") or 0))
                    message_id = int(panel.get("message_id") or 0)
                    if channel is None or not message_id:
                        continue
                    message = await channel.fetch_message(message_id)
                    keys = panel.get("category_keys", [])
                    await message.edit(embed=build_panel_embed(guild, panel), view=TicketView(str(panel["id"]), keys))
                except (TypeError, ValueError, discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                    print(f"Benutzerdefiniertes Ticketpanel {panel.get('id')} konnte nicht aktualisiert werden ({type(exc).__name__}).")

    @panel_refresh.before_loop
    async def before_panel_refresh(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="ticketpanel", description="Richtet das professionelle Ticket-Panel ein.")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketpanel(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.channel:
            await interaction.response.send_message("❌ Nur auf einem Server verfügbar.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        message = await interaction.channel.send(embed=build_panel_embed(interaction.guild), view=TicketView())
        settings = ticket_config()
        settings.update({"panel_channel_id": interaction.channel.id, "panel_message_id": message.id})
        save_ticket_config(settings)
        await interaction.followup.send("✅ Professionelles Ticket-Panel eingerichtet. Die Auslastung wird automatisch aktualisiert.", ephemeral=True)

    async def publish_panel_for_guild(self, guild: discord.Guild, panel_id: str) -> dict:
        """Send or edit one configured custom Pulse panel in its selected channel."""
        settings = ticket_config()
        panels = settings.get("ticket_panels", [])
        if not isinstance(panels, list):
            panels = []
        panel = next((p for p in panels if isinstance(p, dict) and str(p.get("id")) == str(panel_id)), None)
        if panel is None:
            raise ValueError("Panel wurde nicht gefunden.")
        definitions = ticket_category_definitions()
        keys = panel.get("category_keys", [])
        keys = [str(key) for key in keys if str(key) in definitions and definitions[str(key)].get("active", True)][:25]
        if not keys:
            raise ValueError("Das Panel muss mindestens eine aktive Kategorie enthalten.")
        panel["category_keys"] = keys
        channel = await _resolve_text_channel(self.bot, guild, int(panel.get("channel_id") or 0))
        if channel is None:
            raise ValueError("Der ausgewählte Zielkanal ist nicht erreichbar.")
        message = None
        old_channel_id = int(panel.get("published_channel_id") or panel.get("channel_id") or 0)
        old_message_id = int(panel.get("message_id") or 0)
        if old_message_id and old_channel_id == channel.id:
            try:
                message = await channel.fetch_message(old_message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                message = None
        elif old_message_id and old_channel_id != channel.id:
            old_channel = await _resolve_text_channel(self.bot, guild, old_channel_id)
            if old_channel:
                try:
                    old_message = await old_channel.fetch_message(old_message_id)
                    await old_message.delete(reason="Pulse-Ticketpanel im Dashboard in einen anderen Kanal verschoben")
                except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                    pass
        view = TicketView(str(panel["id"]), keys)
        if message is None:
            message = await channel.send(embed=build_panel_embed(guild, panel), view=view)
        else:
            await message.edit(embed=build_panel_embed(guild, panel), view=view)
        panel["message_id"] = message.id
        panel["published_channel_id"] = channel.id
        settings["ticket_panels"] = panels
        save_ticket_config(settings)
        self.bot.add_view(TicketView(str(panel["id"]), keys))
        return {"channel": channel.mention, "message_id": message.id, "panel_name": str(panel.get("name") or panel.get("title") or "Panel")}

    async def setup_panels_for_guild(self, guild: discord.Guild) -> dict:
        """Create or update the native Pulse panel in every configured source channel."""
        settings = ticket_config()
        panels = settings.get("panel_messages", {})
        if not isinstance(panels, dict):
            panels = {}
        legacy_channel_id = settings.get("panel_channel_id")
        legacy_message_id = settings.get("panel_message_id")
        if legacy_channel_id and legacy_message_id:
            panels.setdefault(str(legacy_channel_id), int(legacy_message_id))
        panel_channel_ids = configured_panel_channel_ids()
        active_panel_ids = set(panel_channel_ids)
        successes = []
        failures = []

        # Remove only the messages Pulse previously registered as ticket panels
        # when an administrator removes their channel from the website settings.
        for old_channel_key, old_message_id in list(panels.items()):
            try:
                old_channel_id = int(old_channel_key)
                old_message_id = int(old_message_id)
            except (TypeError, ValueError):
                panels.pop(old_channel_key, None)
                continue
            if old_channel_id in active_panel_ids:
                continue
            old_channel = await _resolve_text_channel(self.bot, guild, old_channel_id)
            if old_channel is not None:
                try:
                    old_message = await old_channel.fetch_message(old_message_id)
                    await old_message.delete(reason="Pulse-Ticketpanel aus Website-Einstellungen entfernt")
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            panels.pop(old_channel_key, None)

        settings.pop("panel_channel_id", None)
        settings.pop("panel_message_id", None)
        for channel_id in panel_channel_ids:
            channel = await _resolve_text_channel(self.bot, guild, channel_id)
            if channel is None:
                failures.append(f"{channel_id}: Kanal fehlt oder ist kein Textkanal")
                continue
            try:
                old_message_id = panels.get(str(channel_id))
                message = None
                if old_message_id:
                    try:
                        message = await channel.fetch_message(int(old_message_id))
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                        message = None
                if message is None:
                    message = await channel.send(embed=build_panel_embed(guild), view=TicketView())
                else:
                    await message.edit(embed=build_panel_embed(guild), view=TicketView())
                panels[str(channel_id)] = message.id
                successes.append(channel.mention)
            except (discord.Forbidden, discord.HTTPException) as exc:
                failures.append(f"{channel_id}: Discord-Fehler ({type(exc).__name__})")
        settings["panel_messages"] = panels
        save_ticket_config(settings)
        return {"successes": successes, "failures": failures, "expected": len(panel_channel_ids)}

    @app_commands.command(
        name="ticketpanels_setup",
        description="[Admin] Richtet Pulse-Ticketpanels in allen sechs konfigurierten Kanälen ein.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketpanels_setup(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Nur auf einem Server verfügbar.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.setup_panels_for_guild(interaction.guild)
        details = [
            f"✅ **Pulse-Ticketpanels eingerichtet:** {len(result['successes'])}/{result['expected']}",
            ", ".join(result["successes"]) if result["successes"] else "Kein Panel konnte eingerichtet werden.",
        ]
        if result["failures"]:
            details.append("\n**Nicht eingerichtet:**\n" + "\n".join(result["failures"]))
        await interaction.followup.send("\n".join(details), ephemeral=True)

    @app_commands.command(name="ticketrole", description="Setzt die zuständige Rolle für Highteam oder Führungsebene.")
    @app_commands.describe(bereich="Geschützter Ticketbereich", rolle="Rolle, die Zugriff erhält")
    @app_commands.choices(bereich=[
        app_commands.Choice(name="Highteam Ticket", value="highteam"),
        app_commands.Choice(name="Führungsebene Ticket", value="leadership"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketrole(self, interaction: discord.Interaction, bereich: app_commands.Choice[str], rolle: discord.Role):
        settings = ticket_config()
        settings[f"{bereich.value}_role_ids"] = [rolle.id]
        save_ticket_config(settings)
        await interaction.response.send_message(
            f"✅ {rolle.mention} ist jetzt für **{CATEGORY_META[bereich.value][0]}** zuständig.",
            ephemeral=True,
        )

    @app_commands.command(name="ticketlog", description="Setzt den Kanal für Ticket-Protokolle.")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketlog(self, interaction: discord.Interaction, kanal: discord.TextChannel):
        settings = ticket_config()
        settings["log_channel_id"] = kanal.id
        save_ticket_config(settings)
        await interaction.response.send_message(f"✅ Ticket-Protokolle gehen ab jetzt in {kanal.mention}.", ephemeral=True)

    @app_commands.command(name="ticketstats", description="Zeigt Ticket-Auslastung und Bearbeitungsstatistik.")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketstats_slash(self, interaction: discord.Interaction):
        items = list_tickets(limit=2000)
        open_items = [ticket for ticket in items if ticket.get("status") != "closed"]
        closed_items = [ticket for ticket in items if ticket.get("status") == "closed"]
        ratings = [int(ticket["rating"]) for ticket in closed_items if ticket.get("rating")]
        average = sum(ratings) / len(ratings) if ratings else 0

        lines = []
        for key, meta in CATEGORY_META.items():
            current = sum(1 for ticket in open_items if ticket.get("category") == key)
            percent = int(current / meta[3] * 100) if meta[3] else 0
            lines.append(f"{meta[2]} **{meta[0]}** · `{current}/{meta[3]}` · {percent}%")

        embed = discord.Embed(
            title="📊 Pulse Ticket-Auslastung",
            description="\n".join(lines),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="🎫 Gesamt", value=str(len(items)), inline=True)
        embed.add_field(name="🟢 Offen", value=str(len(open_items)), inline=True)
        embed.add_field(name="✅ Geschlossen", value=str(len(closed_items)), inline=True)
        embed.add_field(name="⭐ Durchschnitt", value=f"{average:.1f}/5" if ratings else "Noch keine Bewertungen", inline=True)
        embed.set_footer(text="Pulse Ticket-System • Statistik")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="ticketsetup", description="Legacy-Setup: erstellt das Ticket-Panel im aktuellen Kanal.")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketsetup_slash(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.channel:
            await interaction.response.send_message("❌ Nur auf einem Server verfügbar.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        message = await interaction.channel.send(embed=build_panel_embed(interaction.guild), view=TicketView())
        settings = ticket_config()
        settings.update({"panel_channel_id": interaction.channel.id, "panel_message_id": message.id})
        save_ticket_config(settings)
        await interaction.followup.send("✅ Ticket-Panel eingerichtet und mit dem Auto-Refresh verbunden.", ephemeral=True)

    @commands.command(name="ticketsetup")
    @commands.has_permissions(administrator=True)
    async def legacy_ticketsetup(self, ctx):
        message = await ctx.send(embed=build_panel_embed(ctx.guild), view=TicketView())
        settings = ticket_config()
        settings.update({"panel_channel_id": ctx.channel.id, "panel_message_id": message.id})
        save_ticket_config(settings)

    @commands.command(name="ticketstats")
    @commands.has_permissions(administrator=True)
    async def legacy_ticketstats(self, ctx):
        items = list_tickets(limit=2000)
        closed = sum(1 for ticket in items if ticket.get("status") == "closed")
        open_count = len(items) - closed
        await ctx.send(
            f"📊 **Pulse Ticket-System** · Gesamt `{len(items)}` · Offen `{open_count}` · Geschlossen `{closed}`"
        )


async def setup(bot):
    await bot.add_cog(Tickets(bot))
