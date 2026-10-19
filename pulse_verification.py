"""Member verification: settings, role-change planning and panel embed (pure, testable).

Flow (operator-specified role IDs):
  join   → Pulse adds the unverified role 1532762237284913363
  button → Pulse adds the citizen role 1531303733311307997 FIRST and only then
           removes the unverified role, so a failure never leaves a member without
           both protection and access.
cogs/verification.py performs the Discord calls; everything decided here is pure.
"""
from __future__ import annotations

import re

from pulse_embed_design import build_embed_spec, neutralize_mentions, safe_image_url, truncate

SETTING_KEY = "verification_config"
UNVERIFIED_ROLE_ID = 1532762237284913363
CITIZEN_ROLE_ID = 1531303733311307997
BUTTON_CUSTOM_ID = "pulse:verify:v1"
MAX_ERRORS = 10

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
    "message_id": "",
    "published_channel_id": "",
    "errors": [],
}


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
        "message_id": _snowflake(raw.get("message_id")),
        "published_channel_id": _snowflake(raw.get("published_channel_id")),
        "errors": errors,
    }


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
def build_panel_spec(settings: dict, *, icon_url: str = "") -> dict:
    return build_embed_spec(
        "info",
        title=neutralize_mentions(settings["title"]),
        description=neutralize_mentions(settings["description"]),
        thumbnail_url=icon_url,
        image_url=settings.get("banner_url", ""),
        footer=None,
    )
