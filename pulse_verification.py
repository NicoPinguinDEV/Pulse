"""Member verification: settings, role-change planning and panel embed (pure, testable).

Flow (operator-specified role IDs):
  join   → Pulse adds the unverified role 1532762237284913363
  button → Pulse adds the citizen role 1531303733311307997 FIRST and only then
           removes the unverified role, so a failure never leaves a member without
           both protection and access.
cogs/verification.py performs the Discord calls; everything decided here is pure.
"""
from __future__ import annotations

import os
import re

from pulse_embed_design import build_embed_spec, neutralize_mentions, safe_image_url, truncate

SETTING_KEY = "verification_config"
UNVERIFIED_ROLE_ID = 1532762237284913363
CITIZEN_ROLE_ID = 1531303733311307997
BUTTON_CUSTOM_ID = "pulse:verify:v1"
MAX_ERRORS = 10
BANNER_DIR = os.path.join("uploads", "welcome_banners")
_BANNER_NAME_RE = re.compile(r"verify_banner_[0-9a-f]{8}\.(png|jpg|gif|webp)")

DEFAULTS = {
    "enabled": False,          # join role + button only act after an admin enables it
    "channel_id": "",
    "title": "✅ Willkommen – bitte verifiziere dich",
    "description": (
        "Damit du alle Bereiche von **© BOCHUM RP | V1** nutzen kannst, bestätige kurz, "
        "dass du unsere Regeln gelesen hast.\n\n"
        "**So geht's**\n"
        "1️⃣ Lies dir die Serverregeln durch.\n"
        "2️⃣ Klicke unten auf **„Jetzt verifizieren“**.\n"
        "3️⃣ Fertig – du erhältst sofort die Bürgerrolle und siehst alle freigegebenen Kanäle.\n\n"
        "Probleme? Melde dich über den Support."
    ),
    "button_label": "Jetzt verifizieren",
    "banner_url": "",
    "banner_file": "",
    "message_id": "",
    "published_channel_id": "",
    "errors": [],
    # Reminder ping for unverified members (one message at a time, old one is deleted).
    "reminder_enabled": True,
    "reminder_minutes": 60,
    "reminder_text": "👋 Du bist noch nicht verifiziert? Klicke oben im Panel auf **„Jetzt verifizieren“**, um alle Kanäle freizuschalten.",
    "reminder_message_id": "",
    "reminder_last_at": "",
}
REMINDER_MIN_MINUTES = 15
REMINDER_MAX_MINUTES = 24 * 60


def _snowflake(value) -> str:
    text = str(value or "").strip().strip("<#>")
    return text if text.isdigit() and 15 <= len(text) <= 22 else ""


def normalize_settings(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    errors = [e for e in raw.get("errors", []) if isinstance(e, dict)][-MAX_ERRORS:] if isinstance(raw.get("errors"), list) else []
    title = str(raw.get("title") or "").strip()
    description = str(raw.get("description") or "").replace("\r\n", "\n").strip()
    label = re.sub(r"\s+", " ", str(raw.get("button_label") or "")).strip()
    banner = str(raw.get("banner_url") or "").strip()
    return {
        "enabled": bool(raw.get("enabled", DEFAULTS["enabled"])),
        "channel_id": _snowflake(raw.get("channel_id")),
        "title": truncate(title, 256) if title else DEFAULTS["title"],
        "description": truncate(description, 3500) if description else DEFAULTS["description"],
        "button_label": truncate(label, 80) if label else DEFAULTS["button_label"],
        "banner_url": safe_image_url(banner) if banner.startswith("https://") else "",
        "banner_file": str(raw.get("banner_file") or "") if _BANNER_NAME_RE.fullmatch(str(raw.get("banner_file") or "")) else "",
        "message_id": _snowflake(raw.get("message_id")),
        "published_channel_id": _snowflake(raw.get("published_channel_id")),
        "errors": errors,
        "reminder_enabled": bool(raw.get("reminder_enabled", DEFAULTS["reminder_enabled"])),
        "reminder_minutes": _minutes(raw.get("reminder_minutes")),
        "reminder_text": _reminder_text(raw.get("reminder_text")),
        "reminder_message_id": _snowflake(raw.get("reminder_message_id")),
        "reminder_last_at": str(raw.get("reminder_last_at") or "")[:40],
    }


def _minutes(value) -> int:
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULTS["reminder_minutes"]
    return max(REMINDER_MIN_MINUTES, min(REMINDER_MAX_MINUTES, minutes))


def _reminder_text(value) -> str:
    text = re.sub(r"[ \t]+", " ", str(value or "").replace("\r\n", "\n")).strip()
    # The role ping is added by Pulse itself; never allow extra mass/role pings from the text.
    return truncate(neutralize_mentions(text), 300) if text else DEFAULTS["reminder_text"]


def plan_reminder(settings: dict, *, now_ts: float, last_ts: float | None, unverified_count: int) -> str:
    """'send', 'cleanup' (nobody left – remove the old reminder) or 'wait'/'off'."""
    if not (settings["enabled"] and settings["reminder_enabled"] and settings["channel_id"] and settings["message_id"]):
        return "off"
    if unverified_count <= 0:
        return "cleanup" if settings.get("reminder_message_id") else "wait"
    if last_ts is not None and now_ts - last_ts < settings["reminder_minutes"] * 60:
        return "wait"
    return "send"


def reminder_content(settings: dict, *, role_id: int, guild_id: int | str = "") -> str:
    panel_link = ""
    if guild_id and settings.get("message_id") and settings.get("published_channel_id"):
        panel_link = f"\n➡️ https://discord.com/channels/{guild_id}/{settings['published_channel_id']}/{settings['message_id']}"
    return truncate(f"<@&{role_id}> {settings['reminder_text']}{panel_link}", 2000)


def validate_settings(settings: dict, raw_banner_url: str = "") -> list[str]:
    problems = []
    if settings["enabled"] and not settings["channel_id"]:
        problems.append("Bitte einen Verifikationskanal wählen, bevor du die Verifikation aktivierst.")
    if raw_banner_url.strip() and not settings["banner_url"]:
        problems.append("Die Banner-URL muss eine öffentliche https://-Bildadresse sein.")
    return problems


def add_error(settings: dict, when_iso: str, text: str) -> dict:
    """Keep only the newest MAX_ERRORS diagnostics (bounded, no member names)."""
    errors = list(settings.get("errors", []))[-(MAX_ERRORS - 1):]
    errors.append({"at": when_iso, "text": truncate(str(text), 300)})
    return dict(settings, errors=errors)


# --------------------------------------------------------------------------- role planning
def plan_join(role_ids, *, is_bot: bool, enabled: bool) -> str:
    """'assign' or a reason why nothing happens."""
    ids = {int(r) for r in role_ids}
    if not enabled:
        return "disabled"
    if is_bot:
        return "bot"
    if CITIZEN_ROLE_ID in ids:
        return "already_verified"
    if UNVERIFIED_ROLE_ID in ids:
        return "already_unverified"
    return "assign"


def plan_verify(role_ids) -> dict:
    """Which role changes a click needs – idempotent for repeated clicks."""
    ids = {int(r) for r in role_ids}
    has_citizen = CITIZEN_ROLE_ID in ids
    has_unverified = UNVERIFIED_ROLE_ID in ids
    return {
        "add_citizen": not has_citizen,
        "remove_unverified": has_unverified,
        "already_done": has_citizen and not has_unverified,
    }


def role_problems(*, bot_manage_roles: bool, bot_top_position: int, roles: dict) -> list[str]:
    """Why Pulse cannot change the roles (missing role, permission or hierarchy).

    roles: {"unverified": position or None, "citizen": position or None}
    """
    names = {"unverified": f"Unverifiziert-Rolle ({UNVERIFIED_ROLE_ID})", "citizen": f"Bürgerrolle ({CITIZEN_ROLE_ID})"}
    problems = []
    for key, position in roles.items():
        if position is None:
            problems.append(f"Die {names[key]} existiert auf dem Server nicht.")
    if not bot_manage_roles:
        problems.append("Pulse fehlt die Berechtigung „Rollen verwalten“.")
    for key, position in roles.items():
        if position is not None and position >= bot_top_position:
            problems.append(f"Die {names[key]} liegt in der Rollenliste nicht unter der höchsten Pulse-Rolle.")
    return problems


# --------------------------------------------------------------------------- panel
def banner_path(settings: dict, base_dir: str = "") -> str | None:
    """Absolute path of the uploaded banner, or None if unset/missing/unsafe."""
    name = os.path.basename(str(settings.get("banner_file") or ""))
    if not _BANNER_NAME_RE.fullmatch(name):
        return None
    path = os.path.join(base_dir, BANNER_DIR, name) if base_dir else os.path.join(BANNER_DIR, name)
    return path if os.path.isfile(path) else None


def build_panel_spec(settings: dict, *, icon_url: str = "", base_dir: str = "") -> dict:
    """An uploaded banner wins over the URL and is sent as attachment (no public URL needed)."""
    uploaded = banner_path(settings, base_dir)
    image = f"attachment://{os.path.basename(uploaded)}" if uploaded else settings.get("banner_url", "")
    return build_embed_spec(
        "info",
        title=neutralize_mentions(settings["title"]),
        description=neutralize_mentions(settings["description"]),
        thumbnail_url=icon_url,
        image_url=image,
        footer=None,
    )
