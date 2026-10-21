from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from pulse_embed_design import color as _pulse_color, is_modern
from pulse_discord_style import edit_styled, embed_to_spec, is_v2, message_texts, send_styled, view_rows
from pulse_embed_design import layout_from_spec
import pulse_db as _pulse_db
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
    set_ticket_automation_state,
    set_ticket_panel,
    touch_ticket_activity,
    transfer_ticket,
)
from pulse_ticket_design import category_block, load_line, ticket_channel_name
from pulse_ticket_locks import create_lock, get_lock, release_lock
from pulse_transcripts import TranscriptError, archive_transcript, keep_channel_after_failure
import pulse_ids as _pulse_ids  # A-09: fixed Discord IDs live in one place
from pulse_ticket_design import (
    PANEL_IMAGE_DIR,
    automation_actions,
    automation_settings,
    clean_questions,
    load_state,
    normalize_panel,
    panel_signature,
    render_placeholders,
    welcome_texts,
)

os.makedirs("transcripts", exist_ok=True)

TICKET_PARENT_NAME = "🎫・Tickets"
TEAM_UPDATE_CHANNEL_ID = _pulse_ids.TEAM_UPDATE_CHANNEL_ID

# Native Pulse ticket panel sources and lifecycle logs (no external ticket bot).
TICKET_PANEL_CHANNEL_IDS = _pulse_ids.TICKET_PANEL_CHANNEL_IDS
DEFAULT_TICKET_OPEN_LOG_CHANNEL_ID = _pulse_ids.TICKET_OPEN_LOG_CHANNEL_ID
DEFAULT_TICKET_CLAIM_LOG_CHANNEL_ID = _pulse_ids.TICKET_CLAIM_LOG_CHANNEL_ID


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
            "role_ids": [], "parent_category_id": 0, "claimed_category_id": 0, "opening_message": "", "active": True,
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
            "group": "team", "prefix": key, "role_ids": [], "parent_category_id": 0, "claimed_category_id": 0,
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
    group = category_meta(key)[4]
    # Confidential categories must never silently fall back to every team role.
    if group in {"highteam", "leadership"}:
        raw_group_ids = ticket_config().get(f"{group}_role_ids", [])
        roles = []
        for raw in raw_group_ids if isinstance(raw_group_ids, list) else []:
            try:
                role = guild.get_role(int(raw))
            except (TypeError, ValueError):
                role = None
            if role and not role.managed and not role.is_default():
                roles.append(role)
        return roles
    return configured_role_ids(guild, group)


def category_ping_roles(guild, key: str):
    """Roles mentioned when a ticket opens or waits too long.

    Explicitly chosen per category (any number, empty = no ping). Categories saved
    before this option existed keep the former behaviour: their responsible roles.
    """
    definition = ticket_category_definitions().get(str(key), {})
    if "ping_role_ids" not in definition:
        return category_roles(guild, key)
    roles = []
    for raw in definition.get("ping_role_ids") or []:
        try:
            role = guild.get_role(int(raw))
        except (TypeError, ValueError):
            role = None
        if role and not role.managed and not role.is_default() and role not in roles:
            roles.append(role)
    return roles


def can_manage_ticket(member, ticket) -> bool:
    if member.guild_permissions.administrator:
        return True
    key = str(ticket.get("category", "support"))
    return any(role.id in {r.id for r in category_roles(member.guild, key)} for role in member.roles)


def panel_image_path(filename: str) -> str | None:
    """Absolute path of an uploaded panel image, or None if missing/unsafe."""
    name = os.path.basename(str(filename or ""))
    if not name or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}\.(png|jpg|gif|webp)", name):
        return None
    path = os.path.join(PANEL_IMAGE_DIR, name)
    return path if os.path.isfile(path) else None


def panel_files(panel: dict) -> list[discord.File]:
    """Uploaded thumbnail/banner as message attachments (Discord needs no public URL then)."""
    files = []
    for key in ("thumbnail_file", "banner_file"):
        path = panel_image_path(panel.get(key))
        if path:
            files.append(discord.File(path, filename=os.path.basename(path)))
    return files


def panel_entries(panel: dict) -> list[tuple]:
    """(name, emoji, open tickets, maximum) for every active category of a panel."""
    definitions = ticket_category_definitions()
    entries = []
    for key in panel.get("category_keys", [])[:25]:
        definition = definitions.get(str(key))
        if not definition or not definition.get("active", True):
            continue
        name, _, emoji, maximum, _ = category_meta(str(key))
        entries.append((name, emoji, category_open_count(str(key)) if panel.get("show_load") else 0, maximum))
    return entries


def build_panel_layout(guild, panel_config: dict, view, image_urls: dict | None = None):
    """Modern ticket panel like GalaxyBot: big title, text, load section, banner, footer, menu below."""
    panel = normalize_panel(panel_config)
    embed = build_panel_embed(guild, panel_config, image_urls)
    spec = embed_to_spec(embed)
    spec["fields"] = []
    spec["timestamp"] = None  # a pinned panel needs no "today at …" footer
    block = category_block(panel_entries(panel), show_load=bool(panel.get("show_load")),
                           list_categories=bool(panel_config.get("list_categories", True)))
    return layout_from_spec(spec, heading="##", extra_blocks=[block] if block else [],
                            action_rows=view_rows(view), rows_outside=True)


def build_panel_embed(guild, panel_config: dict | None = None, image_urls: dict | None = None) -> discord.Embed:
    if isinstance(panel_config, dict):
        panel = normalize_panel(panel_config)
        panel_title = str(panel.get("title") or panel.get("name") or "Ticket-Support").strip()[:256]
        panel_description = str(panel.get("description") or "Wähle unten eine passende Kategorie, um ein Ticket zu eröffnen.").strip()[:4000]
        try:
            embed_color = discord.Color(int(panel["color"].lstrip("#"), 16))
        except (TypeError, ValueError):
            embed_color = _pulse_color("info")
        embed = discord.Embed(title=panel_title, description=panel_description, color=embed_color)
        entries = panel_entries(panel)
        if panel.get("show_load"):
            lines = [load_line(*entry) for entry in entries]
        elif panel_config.get("list_categories", True):
            lines = [f"{emoji} **{name}**" for name, emoji, _, _ in entries]
        else:
            lines = []
        if lines:
            title = "📊 Ticket-Auslastung" if panel.get("show_load") else "📂 Verfügbare Kategorien"
            embed.add_field(name=title, value="\n".join(lines)[:1024], inline=False)
        embed.set_footer(text=str(panel.get("footer") or "Pulse Ticket-System")[:2048])
        embed.timestamp = discord.utils.utcnow()
        # Images: freshly fetched message URLs (refresh) > uploaded file > external URL > server icon.
        image_urls = image_urls or {}
        thumb_file = panel_image_path(panel.get("thumbnail_file"))
        banner_file = panel_image_path(panel.get("banner_file"))
        thumbnail = image_urls.get("thumbnail") or (
            f"attachment://{os.path.basename(thumb_file)}" if thumb_file else panel.get("thumbnail_url")
        )
        if not thumbnail and panel.get("use_server_icon") and guild.icon:
            thumbnail = guild.icon.url
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        banner = image_urls.get("image") or (
            f"attachment://{os.path.basename(banner_file)}" if banner_file else panel.get("banner_url")
        )
        if banner:
            embed.set_image(url=banner)
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
    embed = discord.Embed(title="<:ticket:1360768000235409559> Ticket-Support", description="\n".join(lines), color=_pulse_color("info"))
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


def category_channel_name(definition: dict, username: str, suffix: str = "") -> str:
    """'『🤝』𝐏𝐚𝐫𝐭𝐧𝐞𝐫𝐬𝐜𝐡𝐚𝐟𝐭-𝐓𝐢𝐜𝐤𝐞𝐭-maxim' – own short label per category, else its name."""
    definition = definition or {}
    label = str(definition.get("channel_label") or definition.get("name") or "Support")
    return ticket_channel_name(str(definition.get("emoji") or "🎫"), label, username, suffix)


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
        color=color or _pulse_color("info"),
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
        await send_styled(destination, embed=embed, allowed_mentions=discord.AllowedMentions.none())
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
        elif is_v2(m):
            text += " | Inhalt: " + " / ".join(t.replace("\n", " ") for t in message_texts(m))[:2000]
        rows.append(f"[{stamp}] {m.author} ({m.author.id}): {text}")
    return "\n".join(rows)


STATUS_FIELDS = ("Status", "Priorität", "Bearbeiter")
TICKET_CARD_PREFIX = "ticket_card:"


def save_ticket_card(ticket_id: str, embed: discord.Embed, files: list[str], meta: dict | None = None) -> None:
    """Remember the welcome card of a container ticket so status updates can rebuild it."""
    try:
        previous = _pulse_db.get_setting(TICKET_CARD_PREFIX + str(ticket_id), None)
        if meta is None and isinstance(previous, dict):
            meta = previous.get("meta")
        _pulse_db.set_setting(TICKET_CARD_PREFIX + str(ticket_id),
                              {"embed": embed.to_dict(), "files": files[:4], "meta": meta or {}})
    except Exception as exc:
        print(f"⚠️ Pulse-Tickets: Ticketkarte {ticket_id} nicht gespeichert ({type(exc).__name__}).")


def load_ticket_card(ticket_id: str, *, with_meta: bool = False):
    try:
        card = _pulse_db.get_setting(TICKET_CARD_PREFIX + str(ticket_id), None)
    except Exception:
        card = None
    if not isinstance(card, dict) or not isinstance(card.get("embed"), dict):
        return (None, [], {}) if with_meta else (None, [])
    files = []
    for name in card.get("files", []):
        path = panel_image_path(name)
        if path:
            files.append(discord.File(path, filename=os.path.basename(path)))
    embed = discord.Embed.from_dict(card["embed"])
    meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
    return (embed, files, meta) if with_meta else (embed, files)


def welcome_banner_choice(category: dict, panel: dict) -> tuple[str | None, str]:
    """Banner of the ticket welcome: category upload > category URL > panel upload > panel URL."""
    path = panel_image_path((category or {}).get("welcome_banner_file"))
    if path:
        return path, ""
    url = str((category or {}).get("welcome_banner_url") or "")
    if url.startswith("https://"):
        return None, url
    path = panel_image_path((panel or {}).get("welcome_banner_file"))
    if path:
        return path, ""
    url = str((panel or {}).get("welcome_banner_url") or "")
    return None, url if url.startswith("https://") else ""


def control_view_for(ticket: dict) -> "TicketControlView":
    """A fresh control view showing the claim state. Never hand the persistent, registered
    instance to a layout conversion: its buttons would be re-parented and later clicks in
    other tickets could no longer be dispatched."""
    view = TicketControlView()
    claimed = bool(ticket.get("claimed_by_id"))
    for item in view.children:
        if getattr(item, "custom_id", None) == "pulse_ticket_claim":
            item.disabled = claimed
            item.label = (f"Übernommen · {ticket.get('claimed_by_name') or 'Teammitglied'}"[:80] if claimed else "Übernehmen")
    return view


def build_ticket_welcome_layout(embed: discord.Embed, view, meta: dict):
    """Ticket welcome like GalaxyBot: title, text, form answers, overview with status, banner, buttons."""
    spec = embed_to_spec(embed)
    welcome = (embed.description or "").split("\n\n**Ticket-ID:**", 1)[0]
    fields = {f.name: f.value for f in embed.fields}
    answers = [(f.name[1:].strip(), f.value) for f in embed.fields if f.name.startswith("📝")]
    blocks = []
    if answers:
        blocks.append("### 📝 Deine Angaben\n" + "\n".join(
            f"**{question}**\n> " + str(answer).replace("\n", "\n> ") for question, answer in answers))
    elif fields.get("📋 Vorgehen"):
        blocks.append("### 📋 So geht es weiter\n" + fields["📋 Vorgehen"])
    overview = [f"**Status:** {fields.get('Status', '🟢 Offen')}"]
    if meta.get("ticket_id"):
        overview.append(f"- 🆔 **Ticket-ID:** `{meta['ticket_id']}`")
    if meta.get("category"):
        overview.append(f"- 🏷️ **Kategorie:** {meta.get('emoji', '')} {meta['category']}".replace("  ", " "))
    if str(meta.get("creator_id") or "").isdigit():
        overview.append(f"- 👤 **Ersteller:** <@{meta['creator_id']}>")
    overview.append(f"- 🚦 **Priorität:** {fields.get('Priorität', '🟢 Normal')}")
    overview.append(f"- 🧑‍💼 **Bearbeiter:** {fields.get('Bearbeiter', 'Noch nicht übernommen')}")
    blocks.append("### 📌 Ticket-Übersicht\n" + "\n".join(overview))
    spec.update(description=welcome, fields=[])
    return layout_from_spec(spec, heading="##", extra_blocks=blocks, action_rows=view_rows(view))


async def move_ticket_channel(channel, ticket: dict, *, claimed: bool) -> str:
    """Move the ticket into the Discord category configured for its state.

    claimed=True → "Kategorie nach Übernahme", claimed=False → creation category.
    Permissions are kept (sync_permissions=False), so the ticket stays private.
    Returns a short status for logs/tests; never raises.
    """
    definition = ticket_category_definitions().get(str(ticket.get("category")), {})
    raw = definition.get("claimed_category_id" if claimed else "parent_category_id")
    try:
        target = channel.guild.get_channel(int(raw)) if raw else None
    except (TypeError, ValueError):
        target = None
    if not isinstance(target, discord.CategoryChannel):
        return "not_configured"
    if getattr(channel, "category_id", None) == target.id:
        return "unchanged"
    if len(target.channels) >= 50:
        print(f"⚠️ Pulse-Tickets: Kategorie {target.id} ist voll (50 Kanäle); Ticket {ticket.get('id')} bleibt.")
        return "full"
    try:
        await channel.edit(category=target, sync_permissions=False,
                           reason=f"Pulse: Ticket {'übernommen' if claimed else 'freigegeben'}")
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"⚠️ Pulse-Tickets: Ticket {ticket.get('id')} konnte nicht verschoben werden ({type(exc).__name__}).")
        return "failed"
    return "moved"


def drop_ticket_card(ticket_id: str) -> None:
    try:
        _pulse_db.delete_setting(TICKET_CARD_PREFIX + str(ticket_id))
    except Exception:
        pass


async def refresh_ticket_embed(channel, ticket: dict, control_view=None):
    # control_view is accepted for compatibility; a fresh copy with the current state is used.
    control_view = control_view_for(ticket)
    status = "✅ Geschlossen" if ticket.get("status") == "closed" else "🔵 In Bearbeitung" if ticket.get("claimed_by_id") else "🟢 Offen"
    values = {
        "Status": status,
        "Priorität": priority_embed_value(ticket.get("priority", "normal")),
        "Bearbeiter": ticket.get("claimed_by_name") or "Noch nicht übernommen",
    }
    # Update only the status fields of the welcome message so its text, form answers
    # and images stay intact (older tickets without these fields use the classic embed).
    try:
        async for m in channel.history(limit=20, oldest_first=True):
            if m.author.id != channel.guild.me.id or not (m.embeds or is_v2(m)):
                continue
            files, meta = [], {}
            if m.embeds:
                embed = m.embeds[0].copy()
            else:
                embed, files, meta = load_ticket_card(ticket.get("id"), with_meta=True)
                if embed is None:
                    break
            names = [field.name for field in embed.fields]
            if all(name in names for name in STATUS_FIELDS):
                for index, field in enumerate(embed.fields):
                    if field.name in values:
                        embed.set_field_at(index, name=field.name, value=values[field.name], inline=True)
                if ticket.get("priority") == "urgent":
                    embed.color = _pulse_color("danger")
                extra = {"attachments": files} if files else {}
                if is_v2(m) and meta:
                    extra["layout"] = build_ticket_welcome_layout(embed, control_view_for(ticket), meta)
                await edit_styled(m, embed=embed, view=control_view or TicketControlView(), **extra)
                if is_v2(m):
                    save_ticket_card(ticket.get("id"), embed, [f.filename for f in files])
                return
            break
    except Exception as exc:
        print(f"⚠️ Pulse-Tickets: Ticketstatus konnte nicht aktualisiert werden ({type(exc).__name__}).")
    await _refresh_classic_ticket_embed(channel, ticket, control_view)


async def _refresh_classic_ticket_embed(channel, ticket: dict, control_view=None):
    label, _, emoji, _, _ = category_meta(ticket.get("category"))
    embed = discord.Embed(
        title=f"{emoji} {label} – Ticket",
        description=f"Ticket-ID: `{ticket['id']}`\nAnliegen: **{ticket['user_name']}**",
        color=_pulse_color("danger") if ticket.get("priority") == "urgent" else (
            CATEGORY_META.get(ticket.get("category"), CATEGORY_META["support"])[3] and _pulse_color("info")
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
            if m.author.id == channel.guild.me.id and (m.embeds or is_v2(m)):
                await edit_styled(m, embed=embed, view=control_view or TicketControlView())
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
        await interaction.response.send_message("🔒 Ticket wird archiviert …", ephemeral=True)
        reason = str(self.reason.value).strip()[:500]
        if not await archive_ticket(interaction.channel, ticket, self.actor.id, self.actor.display_name, reason,
                                    bot=interaction.client):
            await interaction.followup.send("⚠️ Archivierung fehlgeschlagen – der Kanal bleibt erhalten. Details stehen im Ticket.",
                                            ephemeral=True)


TICKET_REMINDER_QUEUE_KEY = "ticket_reminder_dm_queue"
TICKET_REMINDER_QUEUE_MAX = 500
TICKET_REMINDER_DM_LIMIT = 1800
TICKET_REMINDER_DEFAULT_QUIET_FROM = 23
TICKET_REMINDER_DEFAULT_QUIET_TO = 8


def ticket_reminder_quiet_hours(now: datetime | None = None) -> bool:
    """Global citizen ticket-reminder quiet window, in the configured local timezone."""
    def read_hour(key: str, default: int) -> int:
        try:
            hour = int(os.getenv(key, str(default)))
        except (TypeError, ValueError):
            return default
        return hour if 0 <= hour <= 23 else default

    global_start = read_hour("PULSE_DM_QUIET_FROM", TICKET_REMINDER_DEFAULT_QUIET_FROM)
    global_end = read_hour("PULSE_DM_QUIET_TO", TICKET_REMINDER_DEFAULT_QUIET_TO)
    start = read_hour("PULSE_TICKET_REMINDER_QUIET_FROM", global_start)
    end = read_hour("PULSE_TICKET_REMINDER_QUIET_TO", global_end)
    if start == end:
        return False  # Equal hours explicitly disable the window.

    try:
        zone = ZoneInfo(os.getenv("PANEL_TIMEZONE", "Europe/Berlin"))
    except (ZoneInfoNotFoundError, ValueError):
        zone = datetime.now().astimezone().tzinfo or timezone.utc

    if now is None:
        current = datetime.now(zone)
    elif now.tzinfo is None:
        # Naive datetimes passed by tests/callers are interpreted as local wall time.
        current = now
    else:
        current = now.astimezone(zone)
    hour = current.hour
    return start <= hour < end if start < end else hour >= start or hour < end


def _ticket_reminder_record_key(item: dict) -> tuple[str, str, str]:
    return (str(item.get("user_id", "")), str(item.get("ticket_id", "")), str(item.get("channel_id", "")))


def _ticket_reminder_queue() -> list[dict]:
    queued = get_setting(TICKET_REMINDER_QUEUE_KEY, [])
    if not isinstance(queued, list):
        return []
    return [item for item in queued if isinstance(item, dict) and item.get("user_id")
            and item.get("ticket_id") and str(item.get("channel_id", "")).isdigit()]


def _queue_ticket_reminder_dm(channel, ticket: dict, member, auto_close_hours: int = 0) -> None:
    """Persist a citizen reminder while quiet hours apply; dedupe a pending ticket/user pair."""
    try:
        hours = max(0, min(720, int(auto_close_hours or 0)))
    except (TypeError, ValueError):
        hours = 0
    item = {
        "user_id": str(member.id),
        "guild_id": str(channel.guild.id),
        "guild_name": str(channel.guild.name)[:100],
        "channel_id": str(channel.id),
        "ticket_id": str(ticket.get("id", ""))[:60],
        "auto_close_hours": hours,
        "queued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if not item["ticket_id"]:
        return
    queue = _ticket_reminder_queue()
    key = _ticket_reminder_record_key(item)
    if any(_ticket_reminder_record_key(existing) == key for existing in queue):
        return
    queue.append(item)
    queue = queue[-TICKET_REMINDER_QUEUE_MAX:]
    set_setting(TICKET_REMINDER_QUEUE_KEY, queue)


def _ticket_reminder_dm_text(item: dict) -> str:
    ticket_id = str(item.get("ticket_id", ""))[:60]
    guild_name = str(item.get("guild_name") or "Discord-Server")[:100]
    channel_id = str(item.get("channel_id", ""))
    channel_link = f"<#{channel_id}>" if channel_id.isdigit() else "deinem Ticketkanal"
    try:
        hours = max(0, int(item.get("auto_close_hours", 0) or 0))
    except (TypeError, ValueError):
        hours = 0
    note = f"\nOhne Rückmeldung wird das Ticket in **{hours} h** automatisch geschlossen." if hours else ""
    return (f"🔔 Erinnerung: In deinem Ticket `{ticket_id}` auf **{guild_name}** wartet "
            f"das Team auf deine Antwort: {channel_link}{note}")


def _ticket_reminder_chunks(items: list[dict]) -> list[tuple[list[dict], str]]:
    """Build Discord-safe messages without silently truncating queued reminders."""
    chunks: list[tuple[list[dict], str]] = []
    current_items: list[dict] = []
    current_text = ""
    for item in items:
        line = _ticket_reminder_dm_text(item)
        candidate = f"{current_text}\n\n{line}" if current_text else line
        if current_items and len(candidate) > TICKET_REMINDER_DM_LIMIT:
            chunks.append((current_items, current_text))
            current_items, current_text = [item], line
        else:
            current_items.append(item)
            current_text = candidate
    if current_items:
        chunks.append((current_items, current_text))
    return chunks


async def drain_ticket_reminder_dms(bot) -> dict:
    """Deliver queued ticket DMs outside quiet hours, bundling several tickets per user.

    The queue is persisted in Pulse's existing SQLite settings table. The current
    queue is reconciled after Discord awaits, so reminders queued concurrently are not
    overwritten. This runs from the already-existing ticket automation loop.
    """
    if ticket_reminder_quiet_hours():
        return {"sent": 0, "skipped": 0, "pending": len(_ticket_reminder_queue())}

    snapshot = _ticket_reminder_queue()
    if not snapshot:
        return {"sent": 0, "skipped": 0, "pending": 0}

    grouped: dict[str, list[dict]] = {}
    for item in snapshot:
        grouped.setdefault(str(item["user_id"]), []).append(item)

    processed: set[tuple[str, str, str]] = set()
    sent = skipped = 0
    rate_limited = False
    groups = list(grouped.items())

    for group_index, (user_id, items) in enumerate(groups):
        try:
            user_id_int = int(user_id)
            user = bot.get_user(user_id_int)
            if user is None:
                fetch_user = getattr(bot, "fetch_user", None)
                if not callable(fetch_user):
                    raise TypeError("Discord-Benutzer nicht im Cache und fetch_user nicht verfügbar")
                user = await fetch_user(user_id_int)
        except discord.Forbidden:
            processed.update(_ticket_reminder_record_key(item) for item in items)
            skipped += len(items)
            continue
        except discord.HTTPException as exc:
            if getattr(exc, "status", None) == 429:
                rate_limited = True
                break
            processed.update(_ticket_reminder_record_key(item) for item in items)
            skipped += len(items)
            continue
        except (ValueError, TypeError):
            processed.update(_ticket_reminder_record_key(item) for item in items)
            skipped += len(items)
            continue
        except Exception as exc:
            print(f"⚠️ Pulse-Tickets: vorgemerkte DM konnte nicht geladen werden ({type(exc).__name__}).")
            rate_limited = True
            break

        for chunk_index, (chunk_items, message) in enumerate(_ticket_reminder_chunks(items)):
            try:
                # Preferences are not required for ticket creators; these messages used to
                # be sent unconditionally. Only the global quiet window delays them.
                await user.send(message)
                sent += len(chunk_items)
                processed.update(_ticket_reminder_record_key(item) for item in chunk_items)
            except discord.Forbidden:
                processed.update(_ticket_reminder_record_key(item) for item in chunk_items)
                skipped += len(chunk_items)
                # Closed DMs are not retried; keep reminders in the actual ticket channel.
                processed.update(_ticket_reminder_record_key(item) for item in items)
                skipped += sum(len(queued) for queued, _text in _ticket_reminder_chunks(items)[chunk_index + 1:])
                break
            except discord.HTTPException as exc:
                if getattr(exc, "status", None) == 429:
                    rate_limited = True
                    break
                processed.update(_ticket_reminder_record_key(item) for item in chunk_items)
                skipped += len(chunk_items)
            except Exception as exc:
                print(f"⚠️ Pulse-Tickets: vorgemerkte DM fehlgeschlagen ({type(exc).__name__}).")
                rate_limited = True
                break

        if rate_limited:
            break

    # Only delete snapshot items we actually handled. A fresh read preserves reminders
    # that another coroutine added while a Discord send was in flight.
    current = _ticket_reminder_queue()
    remaining = [item for item in current if _ticket_reminder_record_key(item) not in processed]
    set_setting(TICKET_REMINDER_QUEUE_KEY, remaining)
    return {"sent": sent, "skipped": skipped, "pending": len(remaining)}


async def send_user_reminder(channel, ticket: dict, member, by: str | None = None, auto_close_hours: int = 0):
    """Remind the ticket creator in-channel immediately, deferring only the DM at night."""
    try:
        hours = max(0, min(720, int(auto_close_hours or 0)))
    except (TypeError, ValueError):
        hours = 0
    note = f"\nOhne Rückmeldung wird das Ticket in **{hours} h** automatisch geschlossen." if hours else ""
    source = f" ({by})" if by else ""
    try:
        await channel.send(
            f"🔔 {member.mention}, das Team wartet auf deine Rückmeldung{source}.{note}",
            allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
        )
    except (discord.Forbidden, discord.HTTPException):
        pass

    if ticket_reminder_quiet_hours():
        _queue_ticket_reminder_dm(channel, ticket, member, hours)
        return True

    try:
        await member.send(_ticket_reminder_dm_text({
            "ticket_id": ticket.get("id", ""),
            "guild_name": channel.guild.name,
            "channel_id": str(channel.id),
            "auto_close_hours": hours,
        }))
        return False
    except (discord.Forbidden, discord.HTTPException):
        return False


async def archive_ticket(channel, ticket: dict, actor_id, actor_name: str, reason: str, bot=None) -> bool:
    """Save the transcript, close the ticket record, inform the creator and delete the channel.

    K-06: without a stored transcript the channel is kept (renamed, locked) and the ticket stays open.
    """
    log_channel = None
    if bot is not None:
        log_channel = await _resolve_text_channel(bot, channel.guild, configured_ticket_log_channel_id("claimed"))
    try:
        path = await archive_transcript(channel, ticket["id"], log_channel=log_channel)
    except TranscriptError as exc:
        print(f"⚠️ Pulse-Tickets: Ticket {ticket['id']} nicht archiviert – {exc}. Kanal bleibt erhalten.")
        record_event("ticket_archive_failed", "ticket", ticket["id"], actor_id, actor_name, {"error": str(exc)})
        await keep_channel_after_failure(channel, ticket, str(exc))
        return False
    if not close_ticket(ticket["id"], reason, path, actor_id, actor_name):
        return False
    drop_ticket_card(ticket["id"])
    record_event("ticket_closed", "ticket", ticket["id"], actor_id, actor_name, {"reason": reason})
    try:
        member = channel.guild.get_member(int(ticket["user_id"]))
        if member:
            dm = discord.Embed(title="🎫 Dein Ticket wurde geschlossen", color=_pulse_color("info"))
            dm.description = f"Ticket `{ticket['id']}` wurde von **{actor_name}** geschlossen.\n\n**Grund:** {reason}"
            await send_styled(member, embed=dm)
    except Exception:
        pass
    await asyncio.sleep(1)
    try:
        await channel.delete(reason=f"Pulse Ticket geschlossen von {actor_name}")
    except Exception:
        pass
    return True


def datetime_marker(value) -> str:
    """Normalise a stored timestamp the same way automation_actions compares it."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(value or "")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def find_panel(panel_id) -> dict | None:
    if not panel_id:
        return None
    panels = ticket_config().get("ticket_panels", [])
    for panel in panels if isinstance(panels, list) else []:
        if isinstance(panel, dict) and str(panel.get("id")) == str(panel_id):
            return normalize_panel(panel)
    return None


def _safe_component_id(value) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "", str(value))[:40] or "custom"


async def start_ticket_flow(interaction: discord.Interaction, key: str, panel_id: str | None = None):
    """Validate the request, show the category form if configured, then open the ticket."""
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ Nur auf einem Server verfügbar.", ephemeral=True)
        return
    if key == "__not_configured__":
        await interaction.response.send_message("Dieses Panel ist noch nicht eingerichtet. Bitte melde dich beim Serverteam.", ephemeral=True)
        return
    definitions = ticket_category_definitions()
    category_definition = definitions.get(key)
    if not category_definition or not category_definition.get("active", True):
        await interaction.response.send_message("Diese Kategorie ist nicht mehr verfügbar. Bitte wähle eine andere.", ephemeral=True)
        return
    label = category_meta(key)[0]

    # Temporary abuse lock: it only blocks new ticket creation; historical/open cases remain intact.
    lock = get_lock(guild.id, interaction.user.id)
    if lock:
        reason = str(lock.get("reason") or "Kein Grund hinterlegt.")[:500]
        expires_at = str(lock.get("expires_at") or "")
        expiry_hint = f"\n**Gesperrt bis:** {expires_at}" if expires_at else "\n**Ende:** nach manueller Freigabe"
        await interaction.response.send_message(
            f"🔒 Neue Ticket-Erstellungen sind für dich vorübergehend gesperrt.\n"
            f"**Grund:** {reason}{expiry_hint}\n"
            "Deine bestehenden Tickets bleiben erhalten. Bei Rückfragen wende dich bitte an das Serverteam.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    existing = _pulse_db.open_ticket_for_user(guild.id, interaction.user.id)  # P-13: indexed lookup
    if (guild.id, interaction.user.id) in _OPENING_TICKETS:
        await interaction.response.send_message("⏳ Dein Ticket wird gerade erstellt – einen Moment bitte.", ephemeral=True)
        return
    if existing:
        channel = guild.get_channel(int(existing["channel_id"])) if existing.get("channel_id") else None
        if channel:
            await interaction.response.send_message(f"🎫 Du hast bereits ein offenes Ticket: {channel.mention}", ephemeral=True)
            return
        close_ticket(existing["id"], "Verwaistes Ticket automatisch archiviert.", "", interaction.user.id, interaction.user.display_name)

    if not category_roles(guild, key):
        await interaction.response.send_message(
            f"⚠️ **{label}** ist noch nicht vollständig eingerichtet. Weise dieser Kategorie zuerst mindestens eine gültige zuständige Rolle zu.",
            ephemeral=True,
        )
        return

    questions = clean_questions(category_definition.get("questions"))
    if questions:
        # A modal must be the first response, so it is shown before any defer.
        await interaction.response.send_modal(TicketQuestionModal(key, panel_id, questions, label))
        return
    # Channel creation and log delivery may exceed Discord's initial response window.
    await interaction.response.defer(ephemeral=True, thinking=True)
    await create_ticket_channel(interaction, key, panel_id, [])


# P-13: one ticket creation per user at a time (two fast clicks must not create two channels).
_OPENING_TICKETS: set[tuple[int, int]] = set()


async def create_ticket_channel(interaction: discord.Interaction, key: str, panel_id: str | None, answers: list):
    guild = interaction.guild
    lock_key = (guild.id, interaction.user.id)
    if lock_key in _OPENING_TICKETS:
        await interaction.followup.send("⏳ Dein Ticket wird gerade erstellt – einen Moment bitte.", ephemeral=True)
        return
    existing = _pulse_db.open_ticket_for_user(guild.id, interaction.user.id)
    channel = guild.get_channel(int(existing["channel_id"])) if existing and str(existing.get("channel_id") or "").isdigit() else None
    if channel is not None:
        await interaction.followup.send(f"🎫 Du hast bereits ein offenes Ticket: {channel.mention}", ephemeral=True)
        return
    _OPENING_TICKETS.add(lock_key)
    try:
        await _create_ticket_channel(interaction, key, panel_id, answers)
    finally:
        _OPENING_TICKETS.discard(lock_key)


async def _create_ticket_channel(interaction: discord.Interaction, key: str, panel_id: str | None, answers: list):
    guild = interaction.guild
    label, desc, emoji, maximum, group = category_meta(key)
    category_definition = ticket_category_definitions().get(key, {})
    panel = find_panel(panel_id) or {}
    roles = category_roles(guild, key)
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

    channel = await guild.create_text_channel(
        category_channel_name(category_definition, interaction.user.name),
        category=parent,
        overwrites=overwrites,
        topic=f"Pulse Ticket | user:{interaction.user.id} | category:{key}",
        reason=f"Pulse Ticket erstellt von {interaction.user}",
    )
    tid = create_ticket(channel.id, guild.id, interaction.user.id, interaction.user.display_name, key)
    if panel_id:
        set_ticket_panel(tid, panel_id)
    cog = interaction.client.get_cog("Tickets")
    if cog is not None:
        cog.ticket_channel_ids.add(channel.id)

    # Welcome: category text > panel default > Pulse default, with placeholders.
    welcome_title, welcome_message = welcome_texts(panel, category_definition)
    variables = dict(
        user_mention=interaction.user.mention, user_name=interaction.user.display_name,
        server=guild.name, category=label, ticket_id=tid,
    )
    try:
        color = discord.Color(int(str(panel.get("color") or "#5865F2").lstrip("#"), 16))
    except (TypeError, ValueError):
        color = _pulse_color("info")
    embed = discord.Embed(
        title=render_placeholders(welcome_title, **variables)[:256],
        description=(
            render_placeholders(welcome_message, **variables)[:3500]
            + f"\n\n**Ticket-ID:** `{tid}` · **Kategorie:** {emoji} {label}"
        ),
        color=color,
    )
    # Status fields are updated in place on claim/priority changes (see refresh_ticket_embed).
    embed.add_field(name="Status", value="🟢 Offen", inline=True)
    embed.add_field(name="Priorität", value="🟢 Normal", inline=True)
    embed.add_field(name="Bearbeiter", value="Noch nicht übernommen", inline=True)
    for question, answer in answers[:5]:
        embed.add_field(name=f"📝 {question}"[:256], value=(str(answer).strip() or "—")[:1024], inline=False)
    if not answers:
        embed.add_field(
            name="📋 Vorgehen",
            value="1. Anliegen erklären\n2. Rückfragen beantworten\n3. Lösung bestätigen\n4. Ticket über **Schließen** beenden",
            inline=False,
        )
    # Welcome images (same options as the panel): uploaded file > URL > creator avatar.
    welcome_files = []
    thumb_path = panel_image_path(panel.get("welcome_thumbnail_file"))
    banner_path, banner_url = welcome_banner_choice(category_definition, panel)
    if thumb_path:
        welcome_files.append(discord.File(thumb_path, filename=os.path.basename(thumb_path)))
        embed.set_thumbnail(url=f"attachment://{os.path.basename(thumb_path)}")
    elif panel.get("welcome_thumbnail_url"):
        embed.set_thumbnail(url=panel["welcome_thumbnail_url"])
    elif panel.get("welcome_use_avatar", True):
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
    if banner_path:
        welcome_files.append(discord.File(banner_path, filename=os.path.basename(banner_path)))
        embed.set_image(url=f"attachment://{os.path.basename(banner_path)}")
    elif banner_url:
        embed.set_image(url=banner_url)
    embed.set_footer(text="Pulse Ticket-System • Ticket-Steuerung")
    embed.timestamp = discord.utils.utcnow()
    card_meta = {"ticket_id": tid, "category": label, "emoji": emoji, "creator_id": str(interaction.user.id)}
    welcome_layout = build_ticket_welcome_layout(embed, TicketControlView(), card_meta) if is_modern() else None
    welcome_message = await send_styled(channel, embed=embed, view=TicketControlView(), files=welcome_files,
                                        allowed_mentions=discord.AllowedMentions.none(), layout=welcome_layout)
    if is_v2(welcome_message):
        save_ticket_card(tid, embed, [f.filename for f in welcome_files], card_meta)

    ping_roles = category_ping_roles(guild, key)[:10]
    mentions = " ".join(role.mention for role in ping_roles)
    if mentions and panel.get("ping_roles", True):
        await channel.send(
            f"📣 {mentions}\nNeues **{label}** von {interaction.user.mention}.",
            allowed_mentions=discord.AllowedMentions(roles=ping_roles, users=[interaction.user], everyone=False),
        )

    record_event(
        "ticket_created",
        "ticket",
        tid,
        interaction.user.id,
        interaction.user.display_name,
        {"category": key, "channel_id": channel.id, "panel_channel_id": interaction.channel.id, "panel_id": panel_id or ""},
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
            color=_pulse_color("success"),
        )
    await interaction.followup.send(f"✅ Dein Ticket wurde erstellt: {channel.mention}", ephemeral=True)


class TicketQuestionModal(Modal):
    """Category form (up to five questions) answered before the ticket channel is created."""

    def __init__(self, key: str, panel_id: str | None, questions: list, label: str):
        super().__init__(title=f"{label}"[:45] or "Ticket", timeout=600)
        self.key = key
        self.panel_id = panel_id
        self.inputs = []
        for question in questions[:5]:
            field = TextInput(
                label=question["label"][:45],
                placeholder=question.get("placeholder") or None,
                required=question.get("required", True),
                style=discord.TextStyle.paragraph if question.get("long") else discord.TextStyle.short,
                max_length=1000 if question.get("long") else 200,
            )
            self.add_item(field)
            self.inputs.append((question["label"], field))

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        answers = [(label, str(field.value or "")) for label, field in self.inputs]
        try:
            await create_ticket_channel(interaction, self.key, self.panel_id, answers)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Pulse-Tickets: Ticket konnte nicht erstellt werden ({type(exc).__name__}).")
            await interaction.followup.send("❌ Das Ticket konnte nicht erstellt werden. Bitte melde dich beim Team.", ephemeral=True)


class TicketSelect(Select):
    def __init__(self, panel_id: str | None = None, category_keys=None, placeholder: str | None = None):
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
            placeholder=(placeholder or "Wähle die passende Ticket-Kategorie …")[:150],
            min_values=1,
            max_values=1,
            options=options,
            custom_id=custom_id,
        )
        self.panel_id = panel_id

    async def callback(self, interaction: discord.Interaction):
        await start_ticket_flow(interaction, self.values[0], self.panel_id)


class TicketCategoryButton(Button):
    BUTTON_STYLES = {
        "primary": discord.ButtonStyle.primary, "secondary": discord.ButtonStyle.secondary,
        "success": discord.ButtonStyle.success, "danger": discord.ButtonStyle.danger,
    }

    def __init__(self, panel_id: str, key: str, definition: dict):
        name, _, emoji, _, _ = category_meta(key)
        super().__init__(
            label=name[:80],
            emoji=emoji or "🎫",
            style=self.BUTTON_STYLES.get(str(definition.get("button_style") or "primary"), discord.ButtonStyle.primary),
            custom_id=f"pulse_ticket_btn_{_safe_component_id(panel_id)}_{_safe_component_id(key)}"[:100],
        )
        self.panel_id = panel_id
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        await start_ticket_flow(interaction, self.key, self.panel_id)


class TicketView(View):
    def __init__(self, panel_id: str | None = None, category_keys=None, style: str = "select", placeholder: str | None = None):
        super().__init__(timeout=None)
        if style == "buttons" and panel_id:
            definitions = ticket_category_definitions()
            keys = [str(k) for k in (category_keys or []) if definitions.get(str(k), {}).get("active", True) and str(k) in definitions]
            for key in keys[:25]:
                self.add_item(TicketCategoryButton(str(panel_id), key, definitions[key]))
            if keys:
                return
        self.add_item(TicketSelect(panel_id=panel_id, category_keys=category_keys, placeholder=placeholder))


def panel_view(panel: dict) -> TicketView:
    panel = normalize_panel(panel)
    return TicketView(str(panel.get("id")), panel.get("category_keys", []), panel.get("component_style"), panel.get("placeholder"))


class TicketHandoverModal(Modal, title="Ticket an Teammitglied übergeben"):
    """Required-note handover modal; the database CAS protects against concurrent transfers."""

    def __init__(self, ticket_id: str):
        super().__init__(timeout=300)
        self.ticket_id = str(ticket_id)
        self.assignee_id = TextInput(
            label="Discord-ID des neuen Bearbeiters",
            placeholder="Nur die numerische Discord-ID",
            min_length=17,
            max_length=20,
            required=True,
        )
        self.note = TextInput(
            label="Übergabenotiz (Pflicht)",
            placeholder="Bisheriger Stand, bereits geprüfte Punkte, nächster Schritt …",
            style=discord.TextStyle.paragraph,
            min_length=10,
            max_length=1000,
            required=True,
        )
        self.add_item(self.assignee_id)
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        note = str(self.note.value or "").strip()
        raw_assignee = str(self.assignee_id.value or "").strip()
        if not raw_assignee.isdigit() or not 17 <= len(raw_assignee) <= 20:
            return await interaction.response.send_message(
                "❌ Bitte gib eine gültige numerische Discord-ID an.", ephemeral=True,
            )
        if len(note) < 10:
            return await interaction.response.send_message(
                "❌ Die Übergabenotiz muss mindestens 10 Zeichen enthalten.", ephemeral=True,
            )

        ticket = get_ticket(ticket_id=self.ticket_id)
        if not ticket or ticket.get("status") == "closed":
            return await interaction.response.send_message(
                "❌ Das Ticket existiert nicht mehr oder wurde bereits geschlossen.", ephemeral=True,
            )
        if not is_team(interaction.user):
            return await interaction.response.send_message(
                "❌ Nur Teammitglieder dürfen Tickets übergeben.", ephemeral=True,
            )
        current_assignee = str(ticket.get("claimed_by_id") or "")
        if not current_assignee:
            return await interaction.response.send_message(
                "❌ Dieses Ticket ist nicht übernommen. Übernimm es zuerst.", ephemeral=True,
            )
        if current_assignee != str(interaction.user.id) and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ Du darfst nur deine eigenen Tickets übergeben (Admins dürfen übernehmen).", ephemeral=True,
            )
        if raw_assignee == current_assignee:
            return await interaction.response.send_message(
                "❌ Das Ticket ist bereits dieser Person zugewiesen.", ephemeral=True,
            )

        recipient = interaction.guild.get_member(int(raw_assignee))
        if recipient is None:
            try:
                recipient = await interaction.guild.fetch_member(int(raw_assignee))
            except (discord.NotFound, discord.HTTPException, discord.Forbidden):
                recipient = None
        if recipient is None or recipient.bot or not is_team(recipient):
            return await interaction.response.send_message(
                "❌ Das Ziel muss ein erreichbares Teammitglied dieses Servers sein.", ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        transferred = transfer_ticket(
            self.ticket_id,
            expected_assignee_id=current_assignee,
            new_assignee_id=recipient.id,
            new_assignee_name=recipient.display_name,
            actor_id=interaction.user.id,
            actor_name=interaction.user.display_name,
            note=note,
        )
        if not transferred:
            return await interaction.followup.send(
                "❌ Die Übergabe wurde abgebrochen: Das Ticket wurde zwischenzeitlich geändert. Bitte lade es neu.",
                ephemeral=True,
            )

        latest = get_ticket(ticket_id=self.ticket_id)
        try:
            if latest:
                await refresh_ticket_embed(interaction.channel, latest)
                await move_ticket_channel(interaction.channel, latest, claimed=True)
        except Exception as exc:
            print(f"⚠️ Pulse-Tickets: Übergabe-Anzeige nicht aktualisiert ({type(exc).__name__}).")

        await send_ticket_log(
            interaction.client,
            interaction.guild,
            configured_ticket_log_channel_id("claimed"),
            title="🔁 Ticket übergeben",
            description=f"Ticket wurde an {recipient.mention} übergeben.\n**Übergabenotiz:** {note}",
            ticket=latest or ticket,
            actor=interaction.user,
            color=_pulse_color("info"),
        )
        try:
            await interaction.channel.send(
                f"🔁 Ticket an {recipient.mention} übergeben. Die Übergabenotiz wurde im Ticket-Protokoll gespeichert.",
                allowed_mentions=discord.AllowedMentions(
                    users=[recipient], roles=False, everyone=False, replied_user=False,
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass
        await interaction.followup.send(
            f"✅ Ticket {self.ticket_id} wurde an {recipient.display_name} übergeben.",
            ephemeral=True,
        )


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
        try:
            if latest:
                await refresh_ticket_embed(interaction.channel, latest)
                await move_ticket_channel(interaction.channel, latest, claimed=True)
        except Exception as exc:
            print(f"⚠️ Pulse-Tickets: Übernahme-Anzeige nicht aktualisiert ({type(exc).__name__}).")
        if latest:
            await send_ticket_log(
                interaction.client,
                interaction.guild,
                configured_ticket_log_channel_id("claimed"),
                title="📥 Ticket übernommen",
                description="Ein Teammitglied hat ein Pulse-Ticket beansprucht.",
                ticket=latest,
                actor=interaction.user,
                color=_pulse_color("info"),
            )
            try:
                await interaction.channel.send(
                    f"📥 {interaction.user.mention} hat dieses Ticket übernommen.",
                    allowed_mentions=discord.AllowedMentions(users=[interaction.user], roles=False, everyone=False),
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
        await interaction.followup.send(f"✅ Du bearbeitest Ticket `{t['id']}` jetzt.", ephemeral=True)

    @discord.ui.button(label="Übergeben", style=discord.ButtonStyle.primary, emoji="🔁", custom_id="pulse_ticket_transfer")
    async def transfer(self, interaction: discord.Interaction, button: Button):
        t = get_ticket(channel_id=interaction.channel.id)
        if not t or t.get("status") == "closed":
            return await interaction.response.send_message("Ticket nicht gefunden oder bereits geschlossen.", ephemeral=True)
        if not is_team(interaction.user):
            return await interaction.response.send_message("❌ Nur Teammitglieder dürfen Tickets übergeben.", ephemeral=True)
        claimed_by = str(t.get("claimed_by_id") or "")
        if not claimed_by:
            return await interaction.response.send_message("❌ Übernimm das Ticket zuerst, bevor du es übergibst.", ephemeral=True)
        if claimed_by != str(interaction.user.id) and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Du kannst nur deine eigenen Tickets übergeben (Admins dürfen übernehmen).", ephemeral=True)
        await interaction.response.send_modal(TicketHandoverModal(str(t["id"])))

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
            await interaction.response.send_message("↩️ Ticket wurde freigegeben.", ephemeral=True)
            latest = get_ticket(ticket_id=t["id"])
            await refresh_ticket_embed(interaction.channel, latest)
            await move_ticket_channel(interaction.channel, latest, claimed=False)
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

    @discord.ui.button(label="Erinnern", style=discord.ButtonStyle.secondary, emoji="🔔", custom_id="pulse_ticket_remind")
    async def remind(self, interaction: discord.Interaction, button: Button):
        """Team nudges the creator in the ticket and via DM (e.g. while waiting for an answer)."""
        t = get_ticket(channel_id=interaction.channel.id)
        if not t:
            await interaction.response.send_message("Ticket nicht gefunden.", ephemeral=True)
            return
        if not is_team(interaction.user):
            await interaction.response.send_message("❌ Nur Teammitglieder können erinnern.", ephemeral=True)
            return
        member = interaction.guild.get_member(int(t["user_id"])) if str(t.get("user_id") or "").isdigit() else None
        if member is None:
            await interaction.response.send_message("Der Ticket-Ersteller ist nicht mehr auf dem Server.", ephemeral=True)
            return
        await interaction.response.send_message("🔔 Erinnerung wurde gesendet.", ephemeral=True)
        await send_user_reminder(interaction.channel, t, member, by=interaction.user.display_name)

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
        # Open ticket channels, used to record activity without a DB lookup per message.
        self.ticket_channel_ids: set[int] = set()
        # Last rendered state per panel message; unchanged panels are not edited (avoids 429s).
        self._panel_signatures: dict[int, str] = {}
        self.panel_refresh.start()

    def cog_unload(self):
        self.panel_refresh.cancel()

    async def cog_load(self):
        self.bot.add_view(TicketView())
        self.bot.add_view(TicketControlView())
        for panel in ticket_config().get("ticket_panels", []):
            if not isinstance(panel, dict) or not panel.get("id"):
                continue
            self.bot.add_view(panel_view(panel))
        self.reload_ticket_channels()

    def reload_ticket_channels(self):
        self.ticket_channel_ids = {
            int(t["channel_id"]) for t in list_tickets(limit=2000)
            if t.get("status") != "closed" and str(t.get("channel_id") or "").isdigit()
        }

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or message.channel.id not in self.ticket_channel_ids:
            return
        ticket = get_ticket(channel_id=message.channel.id)
        if not ticket or ticket.get("status") == "closed":
            self.ticket_channel_ids.discard(message.channel.id)
            return
        kind = "user" if str(message.author.id) == str(ticket.get("user_id")) else ("staff" if is_team(message.author) else None)
        if kind:
            await asyncio.to_thread(touch_ticket_activity, message.channel.id, kind)

    async def _edit_if_changed(self, message: discord.Message, embed: discord.Embed, view: View, files=None, layout=None):
        """Edit a panel message only when its visible content changed."""
        data = embed.to_dict()
        data.pop("timestamp", None)
        options = [
            [getattr(o, "label", ""), getattr(o, "description", "")]
            for item in view.children for o in getattr(item, "options", [])
        ] + [getattr(item, "label", "") for item in view.children if isinstance(item, Button)]
        signature = panel_signature({"embed": data, "options": options})
        if self._panel_signatures.get(message.id) == signature:
            return False
        extra = {"attachments": files} if (files and is_v2(message)) else {}
        await edit_styled(message, embed=embed, view=view, layout=layout if is_v2(message) else None, **extra)
        self._panel_signatures[message.id] = signature
        return True

    @tasks.loop(minutes=5)
    async def panel_refresh(self):
        await self.bot.wait_until_ready()
        try:
            await self.refresh_panels()
        except Exception as exc:
            print(f"⚠️ Pulse-Tickets: Panel-Aktualisierung fehlgeschlagen ({type(exc).__name__}: {exc}).")
        try:
            await self.run_automation()
        except Exception as exc:
            print(f"⚠️ Pulse-Tickets: Ticket-Automatik fehlgeschlagen ({type(exc).__name__}: {exc}).")

    async def run_automation(self):
        """Reminders and optional auto-close, reusing the existing 5-minute loop (no extra task)."""
        try:
            await drain_ticket_reminder_dms(self.bot)
        except Exception as exc:
            print(f"⚠️ Pulse-Tickets: vorgemerkte DMs konnten nicht verarbeitet werden ({type(exc).__name__}).")
        settings = automation_settings(ticket_config().get("automation"))
        if not any(settings.values()):
            return
        now = discord.utils.utcnow()
        tickets = [t for t in list_tickets(limit=2000) if t.get("status") != "closed"]
        self.ticket_channel_ids = {int(t["channel_id"]) for t in tickets if str(t.get("channel_id") or "").isdigit()}
        for ticket in tickets:
            actions = automation_actions(ticket, settings, now)
            if not actions:
                continue
            guild = self.bot.get_guild(int(ticket.get("guild_id") or 0))
            channel = guild.get_channel(int(ticket["channel_id"])) if guild and str(ticket.get("channel_id") or "").isdigit() else None
            if not isinstance(channel, discord.TextChannel):
                continue
            state = load_state(ticket.get("automation_state"))
            label = category_meta(ticket.get("category"))[0]
            try:
                if "remind_unclaimed" in actions:
                    roles = category_ping_roles(guild, str(ticket.get("category")))[:10]
                    mentions = " ".join(r.mention for r in roles)
                    await channel.send(
                        f"⏰ {mentions}\nDieses **{label}** wartet seit über {settings['unclaimed_ping_minutes']} Minuten auf eine Übernahme.".replace("⏰ \n", "⏰ "),
                        allowed_mentions=discord.AllowedMentions(roles=roles, users=False, everyone=False),
                    )
                    state["unclaimed_ping"] = now.isoformat()
                if "remind_staff" in actions:
                    claimer = guild.get_member(int(ticket["claimed_by_id"])) if str(ticket.get("claimed_by_id") or "").isdigit() else None
                    if claimer:
                        await channel.send(
                            f"⏳ {claimer.mention}, der Ersteller wartet seit über {settings['staff_reply_hours']} h auf eine Antwort.",
                            allowed_mentions=discord.AllowedMentions(users=[claimer], roles=False, everyone=False),
                        )
                    state["staff_reminder_for"] = datetime_marker(ticket["last_user_message_at"])
                if "remind_user" in actions:
                    member = guild.get_member(int(ticket["user_id"])) if str(ticket.get("user_id") or "").isdigit() else None
                    if member:
                        await send_user_reminder(channel, ticket, member, auto_close_hours=settings["auto_close_hours"])
                    state["user_reminder_for"] = datetime_marker(ticket["last_staff_message_at"])
                    state["user_reminded_at"] = now.isoformat()
                if "auto_close" in actions:
                    set_ticket_automation_state(ticket["id"], state)
                    await archive_ticket(channel, ticket, self.bot.user.id, "Pulse Automatik",
                                         f"Automatisch geschlossen: keine Rückmeldung {settings['auto_close_hours']} h nach der Erinnerung.",
                                         bot=self.bot)
                    self.ticket_channel_ids.discard(channel.id)
                    continue
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"⚠️ Pulse-Tickets: Erinnerung für {ticket.get('id')} fehlgeschlagen ({type(exc).__name__}).")
            set_ticket_automation_state(ticket["id"], state)

    async def refresh_panels(self):
        settings = ticket_config()
        panels = settings.get("panel_messages", {})
        if not isinstance(panels, dict):
            panels = {}
        legacy_channel_id = settings.get("panel_channel_id")
        legacy_message_id = settings.get("panel_message_id")
        if legacy_channel_id and legacy_message_id:
            panels = {**panels, str(legacy_channel_id): int(legacy_message_id)}
        if not isinstance(panels, dict):
            panels = {}
        custom_panels = settings.get("ticket_panels", [])
        if not isinstance(custom_panels, list):
            custom_panels = []
        if not panels and not custom_panels:
            return
        for guild in self.bot.guilds:
            for raw_channel_id, raw_message_id in list(panels.items()):
                try:
                    channel = guild.get_channel(int(raw_channel_id))
                    if not isinstance(channel, discord.TextChannel):
                        continue
                    message = await channel.fetch_message(int(raw_message_id))
                    await self._edit_if_changed(message, build_panel_embed(guild), TicketView())
                except discord.NotFound:
                    # A deleted panel must not stay broken forever. Re-create it in
                    # the same channel and persist the new message ID immediately.
                    channel = guild.get_channel(int(raw_channel_id))
                    if not isinstance(channel, discord.TextChannel):
                        print(f"Ticket-Panel {raw_channel_id} fehlt; der Kanal ist nicht erreichbar.")
                        continue
                    try:
                        replacement = await send_styled(
                            channel,
                            embed=build_panel_embed(guild),
                            view=TicketView(),
                        )
                        current_settings = ticket_config()
                        current_panels = current_settings.get("panel_messages", {})
                        if not isinstance(current_panels, dict):
                            current_panels = {}
                        current_panels[str(channel.id)] = replacement.id
                        current_settings["panel_messages"] = current_panels
                        current_settings["panel_channel_id"] = channel.id
                        current_settings["panel_message_id"] = replacement.id
                        save_ticket_config(current_settings)
                        panels[str(channel.id)] = replacement.id
                        print(
                            f"✅ Ticket-Panel in Kanal {channel.id} fehlte und wurde neu erstellt "
                            f"(Nachricht {replacement.id})."
                        )
                    except (discord.Forbidden, discord.HTTPException, TypeError, ValueError) as repair_exc:
                        print(
                            f"❌ Ticket-Panel in Kanal {raw_channel_id} fehlt und konnte nicht "
                            f"neu erstellt werden ({type(repair_exc).__name__})."
                        )
                except (TypeError, ValueError, discord.Forbidden, discord.HTTPException) as exc:
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
                    # Uploaded images live as attachments of this message: reuse their fresh URLs
                    # so the edit keeps them without re-uploading.
                    existing = message.embeds[0] if message.embeds else None
                    image_urls = {
                        "thumbnail": existing.thumbnail.url if existing and existing.thumbnail and panel_image_path(panel.get("thumbnail_file")) else None,
                        "image": existing.image.url if existing and existing.image and panel_image_path(panel.get("banner_file")) else None,
                    }
                    await self._edit_if_changed(message, build_panel_embed(guild, panel, image_urls), panel_view(panel),
                                                files=panel_files(panel) if is_v2(message) else None,
                                                layout=build_panel_layout(guild, panel, panel_view(panel)) if is_v2(message) else None)
                except discord.NotFound:
                    # The dashboard publishing helper creates a replacement
                    # message and writes its new ID back into the settings.
                    try:
                        replacement = await self.publish_panel_for_guild(guild, str(panel["id"]))
                        print(
                            f"✅ Benutzerdefiniertes Ticketpanel {panel.get('id')} fehlte und wurde "
                            f"in {replacement.get('channel', 'dem Zielkanal')} neu veröffentlicht."
                        )
                    except (TypeError, ValueError, discord.Forbidden, discord.HTTPException) as repair_exc:
                        print(
                            f"❌ Benutzerdefiniertes Ticketpanel {panel.get('id')} fehlt und konnte "
                            f"nicht neu veröffentlicht werden ({type(repair_exc).__name__})."
                        )
                except (TypeError, ValueError, discord.Forbidden, discord.HTTPException) as exc:
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
        message = await send_styled(interaction.channel, embed=build_panel_embed(interaction.guild), view=TicketView())
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
        view = panel_view(panel)
        # Explicit publish always re-uploads the images so a changed thumbnail/banner appears.
        files = panel_files(panel)
        if message is not None and is_v2(message) != is_modern():
            # Discord cannot convert embed <-> container: replace the panel exactly once.
            try:
                await message.delete()
            except discord.NotFound:
                pass
            message = None
        layout = build_panel_layout(guild, panel, panel_view(panel)) if is_modern() else None
        if message is None:
            message = await send_styled(channel, embed=build_panel_embed(guild, panel), view=view, files=files, layout=layout)
        else:
            if is_v2(message) and layout is None:
                layout = build_panel_layout(guild, panel, panel_view(panel))
            await edit_styled(message, embed=build_panel_embed(guild, panel), view=view, attachments=files, layout=layout)
        self._panel_signatures.pop(message.id, None)
        panel["message_id"] = message.id
        panel["published_channel_id"] = channel.id
        settings["ticket_panels"] = panels
        save_ticket_config(settings)
        self.bot.add_view(panel_view(panel))
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
                if message is not None and is_v2(message) != is_modern():
                    try:
                        await message.delete()
                    except discord.NotFound:
                        pass
                    message = None
                if message is None:
                    message = await send_styled(channel, embed=build_panel_embed(guild), view=TicketView())
                else:
                    await edit_styled(message, embed=build_panel_embed(guild), view=TicketView())
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

    @app_commands.command(
        name="ticketlock",
        description="Sperrt neue Ticket-Erstellungen für ein Mitglied befristet.",
    )
    @app_commands.describe(
        mitglied="Mitglied, für das neue Ticket-Erstellungen gesperrt werden",
        dauer_stunden="Dauer in Stunden (1 bis 168)",
        grund="Nachvollziehbarer Grund für die Sperre",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketlock(
        self,
        interaction: discord.Interaction,
        mitglied: discord.Member,
        dauer_stunden: app_commands.Range[int, 1, 168],
        grund: app_commands.Range[str, 5, 500],
    ):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur auf einem Server verfügbar.", ephemeral=True)
        if mitglied.bot or mitglied.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ Bots und Serveradministratoren können nicht über diese Funktion gesperrt werden.",
                ephemeral=True,
            )
        try:
            record = create_lock(
                interaction.guild.id, mitglied.id, int(dauer_stunden), grund,
                actor_id=interaction.user.id, actor_name=interaction.user.display_name,
            )
        except ValueError as exc:
            return await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
        import datetime as _datetime
        expires = _datetime.datetime.fromisoformat(record["expires_at"])
        expiry = int(expires.timestamp())
        await interaction.response.send_message(
            f"🔒 Neue Ticket-Erstellungen für {mitglied.mention} wurden für **{dauer_stunden} h** gesperrt.\n"
            f"**Grund:** {record['reason']}\n**Endet:** <t:{expiry}:F> (<t:{expiry}:R>)",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="ticketunlock",
        description="Hebt eine befristete Ticket-Sperre auf.",
    )
    @app_commands.describe(mitglied="Mitglied, dessen Ticket-Sperre aufgehoben werden soll")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketunlock(self, interaction: discord.Interaction, mitglied: discord.Member):
        if not interaction.guild:
            return await interaction.response.send_message("❌ Nur auf einem Server verfügbar.", ephemeral=True)
        record = release_lock(
            interaction.guild.id, mitglied.id,
            actor_id=interaction.user.id, actor_name=interaction.user.display_name,
        )
        if not record:
            return await interaction.response.send_message(
                f"ℹ️ Für {mitglied.mention} ist keine aktive Ticket-Sperre hinterlegt.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        pulse_db.record_event(
            "ticket_member_lock_removed", "member", str(mitglied.id),
            interaction.user.id, interaction.user.display_name,
            {"reason": str(record.get("reason") or ""), "original_expires_at": str(record.get("expires_at") or "")},
        )
        await interaction.response.send_message(
            f"🔓 Die Ticket-Sperre für {mitglied.mention} wurde aufgehoben. Bestehende Tickets waren davon nicht betroffen.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
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
        # Aggregations run in SQLite and are restricted to this server.
        rating_breakdown = _pulse_db.ticket_rating_stats(interaction.guild_id, limit=8)

        lines = []
        for key, meta in CATEGORY_META.items():
            current = sum(1 for ticket in open_items if ticket.get("category") == key)
            percent = int(current / meta[3] * 100) if meta[3] else 0
            lines.append(f"{meta[2]} **{meta[0]}** · `{current}/{meta[3]}` · {percent}%")

        embed = discord.Embed(
            title="📊 Pulse Ticket-Auslastung",
            description="\n".join(lines),
            color=_pulse_color("info"),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="🎫 Gesamt", value=str(len(items)), inline=True)
        embed.add_field(name="🟢 Offen", value=str(len(open_items)), inline=True)
        embed.add_field(name="✅ Geschlossen", value=str(len(closed_items)), inline=True)
        embed.add_field(name="⭐ Durchschnitt", value=f"{average:.1f}/5" if ratings else "Noch keine Bewertungen", inline=True)
        category_ratings = rating_breakdown["categories"]
        category_lines = [
            f"**{str(row.get('category') or 'Unbekannt')[:50]}** · {row['average_rating']:.2f}/5 ({row['rated_count']})"
            for row in category_ratings
        ]
        staff_ratings = rating_breakdown["staff"]
        staff_lines = [
            f"**{str(row.get('user_name') or 'Teammitglied')[:50]}** · {row['average_rating']:.2f}/5 ({row['rated_count']})"
            for row in staff_ratings
        ]
        embed.add_field(
            name="🏷️ Bewertungen nach Kategorie",
            value="\\n".join(category_lines)[:1024] or "Noch keine Bewertungen",
            inline=False,
        )
        embed.add_field(
            name="🧑‍💼 Bewertungen nach Teammitglied",
            value="\\n".join(staff_lines)[:1024] or "Noch keine Bewertungen",
            inline=False,
        )
        embed.set_footer(text="Pulse Ticket-System • Statistik")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="ticketsetup", description="Legacy-Setup: erstellt das Ticket-Panel im aktuellen Kanal.")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticketsetup_slash(self, interaction: discord.Interaction):
        if not interaction.guild or not interaction.channel:
            await interaction.response.send_message("❌ Nur auf einem Server verfügbar.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        message = await send_styled(interaction.channel, embed=build_panel_embed(interaction.guild), view=TicketView())
        settings = ticket_config()
        settings.update({"panel_channel_id": interaction.channel.id, "panel_message_id": message.id})
        save_ticket_config(settings)
        await interaction.followup.send("✅ Ticket-Panel eingerichtet und mit dem Auto-Refresh verbunden.", ephemeral=True)

    @commands.command(name="ticketsetup")
    @commands.has_permissions(administrator=True)
    async def legacy_ticketsetup(self, ctx):
        message = await send_styled(ctx, embed=build_panel_embed(ctx.guild), view=TicketView())
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
