"""Welcome/leave messages: settings model, template validation and message building.

Pure logic (no Discord connection) so it is unit-testable; cogs/welcome.py only
collects member data, calls build_member_message() and sends the result.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from pulse_embed_design import (
    build_embed_spec, discord_timestamp, neutralize_mentions, render_template,
    safe_image_url, truncate, validate_template,
)

SETTING_KEY = "welcome_config"
BANNER_DIR = os.path.join("uploads", "welcome_banners")
MAX_BANNER_BYTES = 8 * 1024 * 1024
KINDS = ("welcome", "leave")
PLACEHOLDERS = {
    "user": "Erwähnung (nur Willkommen, wenn Erwähnung aktiv – sonst Anzeigename)",
    "display_name": "Anzeigename",
    "username": "Discord-Benutzername",
    "user_id": "Discord-ID",
    "server_name": "Servername",
    "member_count": "Mitgliederzahl (nach Beitritt bzw. Austritt)",
    "account_created": "Konto erstellt am",
    "joined_at": "Beitrittszeitpunkt",
}

DEFAULTS = {
    "include_bots": False,
    "welcome": {
        "enabled": False,
        "channel_id": "",
        "title": "👋 Willkommen auf {server_name}!",
        "message": ("Hey {user}, schön, dass du da bist!\n\n"
                    "Mach es dir gemütlich und schau dich in Ruhe um. "
                    "Bei Fragen hilft dir unser Team jederzeit weiter.\n\n"
                    "Du bist unser **{member_count}.** Mitglied."),
        "mention": True,
        "show_avatar": True,
        "banner_url": "",
        "banner_file": "",
        "rules_channel_id": "",
        "support_channel_id": "",
    },
    "leave": {
        "enabled": False,
        "channel_id": "",
        "title": "👋 Auf Wiedersehen, {display_name}",
        "message": ("**{display_name}** hat {server_name} verlassen.\n"
                    "Danke für die gemeinsame Zeit – du bist jederzeit wieder willkommen.\n\n"
                    "Wir sind jetzt **{member_count}** Mitglieder."),
        "show_avatar": True,
        "banner_url": "",
        "banner_file": "",
    },
}

_BANNER_NAME_RE = re.compile(r"(welcome|leave)_banner_[0-9a-f]{8}\.(png|jpg|gif|webp)")


def _snowflake(value) -> str:
    text = str(value or "").strip().strip("<#>")
    return text if text.isdigit() and 15 <= len(text) <= 22 else ""


def _text(value, default: str, limit: int) -> str:
    text = str(value if value is not None else "").replace("\r\n", "\n").strip()
    return truncate(text, limit) if text else default


def normalize_settings(raw) -> dict:
    """Validated settings with backwards-compatible defaults for every key."""
    raw = raw if isinstance(raw, dict) else {}
    result = {"include_bots": bool(raw.get("include_bots", DEFAULTS["include_bots"]))}
    for kind in KINDS:
        source = raw.get(kind) if isinstance(raw.get(kind), dict) else {}
        default = DEFAULTS[kind]
        entry = {
            "enabled": bool(source.get("enabled", default["enabled"])),
            "channel_id": _snowflake(source.get("channel_id")),
            "title": _text(source.get("title"), default["title"], 256),
            "message": _text(source.get("message"), default["message"], 3500),
            "show_avatar": bool(source.get("show_avatar", default["show_avatar"])),
            "banner_url": safe_image_url(source.get("banner_url")) if str(source.get("banner_url") or "").startswith("https://") else "",
            "banner_file": str(source.get("banner_file") or "") if _BANNER_NAME_RE.fullmatch(str(source.get("banner_file") or "")) else "",
        }
        if kind == "welcome":
            entry["mention"] = bool(source.get("mention", default["mention"]))
            entry["rules_channel_id"] = _snowflake(source.get("rules_channel_id"))
            entry["support_channel_id"] = _snowflake(source.get("support_channel_id"))
        result[kind] = entry
    return result


def validate_settings(settings: dict) -> list[str]:
    """Problems that must block saving (unknown placeholders, missing channel)."""
    problems = []
    labels = {"welcome": "Willkommen", "leave": "Abschied"}
    for kind in KINDS:
        entry = settings[kind]
        for field in ("title", "message"):
            for problem in validate_template(entry[field], PLACEHOLDERS):
                problems.append(f"{labels[kind]} – {'Titel' if field == 'title' else 'Text'}: {problem}")
        if entry["enabled"] and not entry["channel_id"]:
            problems.append(f"{labels[kind]}: Bitte einen Zielkanal wählen, bevor du die Nachricht aktivierst.")
    return problems


def banner_path(filename: str, base_dir: str = "") -> str | None:
    name = os.path.basename(str(filename or ""))
    if not _BANNER_NAME_RE.fullmatch(name):
        return None
    path = os.path.join(base_dir, BANNER_DIR, name) if base_dir else os.path.join(BANNER_DIR, name)
    return path if os.path.isfile(path) else None


def placeholder_values(kind: str, member: dict, mention: bool) -> dict:
    """Values for the templates. Missing data stays None (rendered as '–'), never invented."""
    display = neutralize_mentions(member.get("display_name") or member.get("username") or "Mitglied")
    created = member.get("created_at") if isinstance(member.get("created_at"), datetime) else None
    joined = member.get("joined_at") if isinstance(member.get("joined_at"), datetime) else None
    count = member.get("member_count")
    use_mention = kind == "welcome" and mention and member.get("mention")
    return {
        "user": member["mention"] if use_mention else f"**{display}**",
        "display_name": display,
        "username": neutralize_mentions(member.get("username") or ""),
        "user_id": str(member.get("id") or ""),
        "server_name": neutralize_mentions(member.get("guild_name") or ""),
        "member_count": f"{int(count):,}".replace(",", ".") if isinstance(count, int) and count > 0 else None,
        "account_created": discord_timestamp(created, "D") if created else None,
        "joined_at": discord_timestamp(joined, "f") if joined else None,
    }


def build_member_message(kind: str, settings: dict, member: dict, *, now: datetime | None = None,
                         base_dir: str = "", force: bool = False) -> dict | None:
    """Everything needed to send one welcome/leave message, or None if it must not be sent.

    member: id, mention, display_name, username, bot, avatar_url, created_at, joined_at,
    guild_name, member_count. force=True ignores enabled/bot checks (dashboard preview/test).
    """
    if kind not in KINDS:
        raise ValueError("Unbekannter Nachrichtentyp.")
    entry = settings[kind]
    if not force:
        if not entry["enabled"] or not entry["channel_id"]:
            return None
        if member.get("bot") and not settings.get("include_bots"):
            return None
    mention = bool(entry.get("mention"))
    values = placeholder_values(kind, member, mention)
    # The template is written by an administrator; still never allow mass pings from it.
    # Embed titles render neither mentions nor markdown: plain name there.
    title_values = dict(values, user=values["display_name"])
    title = neutralize_mentions(render_template(entry["title"], title_values))
    description = neutralize_mentions(render_template(entry["message"], values))
    fields = []
    if kind == "welcome":
        steps = []
        if entry.get("rules_channel_id"):
            steps.append(f"📜 Regeln: <#{entry['rules_channel_id']}>")
        if entry.get("support_channel_id"):
            steps.append(f"🎫 Hilfe & Support: <#{entry['support_channel_id']}>")
        if steps:
            fields.append(("Erste Schritte", "\n".join(steps), False))

    banner_file = banner_path(entry.get("banner_file"), base_dir)
    image_url = ""
    attachment_name = ""
    if banner_file:
        attachment_name = os.path.basename(banner_file)
        image_url = f"attachment://{attachment_name}"
    elif entry.get("banner_url"):
        image_url = entry["banner_url"]

    spec = build_embed_spec(
        "success" if kind == "welcome" else "neutral",
        title=title, description=description, fields=fields,
        thumbnail_url=member.get("avatar_url") if entry.get("show_avatar") else "",
        image_url=image_url, timestamp=now,
    )
    content = member["mention"] if kind == "welcome" and mention and member.get("mention") else ""
    return {
        "channel_id": entry["channel_id"],
        "content": content,
        "spec": spec,
        "banner_file": banner_file,
        "attachment_name": attachment_name,
        "mention_user_id": member.get("id") if content else None,
    }


def sample_member(guild_name: str = "© BOCHUM RP | V1", member_count: int | None = 250) -> dict:
    """Clearly fictional example member for the dashboard preview (never sent as real data)."""
    return {
        "id": 0, "mention": "@Beispielmitglied", "display_name": "Beispielmitglied", "username": "beispiel",
        "bot": False, "avatar_url": "https://cdn.discordapp.com/embed/avatars/0.png",
        "created_at": None, "joined_at": None, "guild_name": guild_name, "member_count": member_count,
    }
