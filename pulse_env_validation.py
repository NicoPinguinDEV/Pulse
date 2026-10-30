"""O-06: check optional .env settings at start and on the system page – without ever showing their values.

Advisory only: a bad optional value never stops the bot. The ranges are the ones the code really applies
(clamping or fallback); a value outside means "Pulse silently uses something else" or, for the few settings
parsed with a bare int()/float() at import, "Pulse will not start" – both worth a clear message.
Messages contain only the variable name and the expected type/range, never the configured value.
"""
from __future__ import annotations

import math
import os
from urllib.parse import urlsplit

# name: (minimum, maximum) – taken from the parsing code named in the comment.
INTEGER_RANGES: dict[str, tuple[int, int]] = {
    "SERVER_PORT": (1, 65535),                                      # main.py (int() – crash on text)
    "DISCORD_MESSAGE_CACHE_SIZE": (100, 1000),                      # main.py (clamped)
    "SESSION_DAYS": (1, 365),                                       # webserver.py (int() – crash on text)
    "MAX_BACKUPS": (1, 1000),                                       # webserver.py (int() – crash on text)
    "PULSE_SUPPORT_MAX_TOKENS": (300, 2200),                        # cogs/support_assistant.py (clamped)
    "PULSE_AI_MODERATION_MAX_QUEUE_AGE_SECONDS": (60, 3600),        # cogs/ai_moderation.py (clamped)
    "PULSE_AI_MODERATION_QUEUE_SIZE": (10, 250),                    # cogs/ai_moderation.py (clamped)
    "PULSE_AI_MODERATION_OUTAGE_NOTICE_INTERVAL_SECONDS": (3600, 86400),  # cogs/ai_moderation.py (clamped)
    "PULSE_RAID_JOINS_PER_MINUTE": (1, 1000),                       # pulse_raid.py (min 1)
    "PULSE_RAID_NEW_ACCOUNTS_5MIN": (1, 1000),
    "PULSE_RAID_NEW_ACCOUNT_DAYS": (1, 3650),
    "PULSE_RAID_MASS_MENTIONS": (1, 1000),
    "PULSE_RAID_COPY_CHANNELS": (1, 1000),
}
FLOAT_RANGES: dict[str, tuple[float, float]] = {
    "MAX_SHIFT_HOURS": (0.5, 24.0),                                 # pulse_shift_utils.parse_max_shift_hours (E-2)
    "AUTO_BACKUP_HOURS": (0.0, 8760.0),                             # webserver.py (float() – crash on text; 0 = off)
    "PULSE_BACKUP_MAX_MB": (1.0, 100000.0),                         # pulse_backup.py (float() at import)
    "PULSE_BACKUP_TOTAL_MAX_MB": (1.0, 1000000.0),
    "PULSE_SESSION_IDLE_HOURS": (0.25, 8760.0),                     # pulse_sessions.py (min 15 min)
    "PULSE_AI_MODERATION_INTERVAL_SECONDS": (1.0, 30.0),            # cogs/ai_moderation.py (clamped)
    "PULSE_AI_MODERATION_CONFIDENCE": (0.65, 0.99),
    "PULSE_AI_MODERATION_LOW_CONFIDENCE": (0.65, 0.99),
}
# Discord snowflakes / numeric ids; empty means "not used".
ID_VARS = (
    "DISCORD_CLIENT_ID", "DISCORD_GUILD_ID", "PULSE_BACKUP_DISCORD_CHANNEL_ID", "PULSE_SUPPORT_CHANNEL_ID",
    "PULSE_SUPPORT_TEAM_CHANNEL_ID", "PULSE_AI_MODERATION_REVIEW_CHANNEL_ID", "PULSE_REPORT_CHANNEL_ID",
    "PULSE_SYSTEM_ALERT_CHANNEL_ID", "PULSE_ROBLOX_UNIVERSE_ID",
)
ID_LIST_VARS = ("PULSE_SUPPORT_TEAM_ROLE_IDS", "PULSE_AI_MODERATION_EXCLUDED_CHANNEL_IDS", "PULSE_AI_MODERATION_EXCLUDED_CATEGORY_IDS")
URL_VARS = ("PUBLIC_BASE_URL", "DISCORD_REDIRECT_URI", "DISCORD_APPLICATION_REDIRECT_URI", "GEMINI_BASE_URL", "OPENAI_BASE_URL",
            "PULSE_RSSHUB_BASE_URL")
URL_LIST_VARS = ("PULSE_RSSHUB_FALLBACK_URLS",)
CHOICES: dict[str, tuple[str, ...]] = {
    "PULSE_SUPPORT_PROVIDER": ("gemini", "openai"),
    "PULSE_SUPPORT_REASONING_EFFORT": ("none", "minimal", "low", "medium", "high", "off"),
}
BOOLEAN_VARS = ("PULSE_SUPPORT_ENABLED", "PULSE_AI_MODERATION_ENABLED", "PULSE_AI_MODERATION_EXCLUDE_TEAM_KI",
                "PULSE_FORCE_COMMAND_SYNC", "PULSE_SUPPORT_OFFLINE_FALLBACK")
_BOOLEAN_WORDS = {"1", "0", "true", "false", "yes", "no", "on", "off", ""}
# Read as `== "1"` / `!= "0"` in the code: "true" would silently mean OFF for SYNC_WARN_ROLES.
STRICT_SWITCH_VARS = ("SYNC_WARN_ROLES", "PULSE_RAID_WATCH")


def _value(env, name: str) -> str:
    return str(env.get(name, "") or "").strip()


def _valid_url(raw: str) -> bool:
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_environment(environ=None) -> list[str]:
    """Value-free warnings in a stable order (empty list = everything fine)."""
    env = os.environ if environ is None else environ
    warnings: list[str] = []

    for name, (low, high) in INTEGER_RANGES.items():
        raw = _value(env, name)
        if not raw:
            continue
        try:
            number = int(raw, 10)
        except ValueError:
            warnings.append(f"{name}: erwartet eine ganze Zahl ({low}–{high}).")
            continue
        if not low <= number <= high:
            warnings.append(f"{name}: außerhalb des erlaubten Bereichs ({low}–{high}).")

    for name, (low, high) in FLOAT_RANGES.items():
        raw = _value(env, name)
        if not raw:
            continue
        try:
            number = float(raw)
        except ValueError:
            warnings.append(f"{name}: erwartet eine Zahl ({low:g}–{high:g}).")
            continue
        if not math.isfinite(number) or not low <= number <= high:
            warnings.append(f"{name}: außerhalb des erlaubten Bereichs ({low:g}–{high:g}).")

    for name in ID_VARS:
        raw = _value(env, name)
        if raw and raw != "0" and not (raw.isdecimal() and 0 < int(raw) < 2**63):  # "0" = not used (env_int default)
            warnings.append(f"{name}: erwartet eine numerische ID oder leer.")

    for name in ID_LIST_VARS:
        parts = [part.strip() for part in _value(env, name).split(",") if part.strip()]
        if any(not (part.isdecimal() and 0 < int(part) < 2**63) for part in parts):
            warnings.append(f"{name}: erwartet kommagetrennte numerische IDs.")

    for name in URL_VARS:
        raw = _value(env, name)
        if raw and not _valid_url(raw):
            warnings.append(f"{name}: erwartet eine vollständige http(s)-Adresse.")

    for name in URL_LIST_VARS:
        entries = [item.strip() for item in _value(env, name).split(",") if item.strip()]
        if any(not _valid_url(item) for item in entries):
            warnings.append(f"{name}: enthält einen Eintrag ohne gültige http(s)-Adresse.")

    for name, allowed in CHOICES.items():
        raw = _value(env, name).lower()
        if raw and raw not in allowed:
            warnings.append(f"{name}: erlaubt sind {', '.join(allowed)}.")

    for name in BOOLEAN_VARS:
        if _value(env, name).lower() not in _BOOLEAN_WORDS:
            warnings.append(f"{name}: erwartet 1/0 (oder true/false).")

    for name in STRICT_SWITCH_VARS:
        if _value(env, name) not in {"", "0", "1"}:
            warnings.append(f"{name}: nur 1 (an) oder 0 (aus) – andere Werte werden anders gelesen als erwartet.")

    if "your-domain.example" in _value(env, "PUBLIC_BASE_URL").casefold():
        warnings.append("PUBLIC_BASE_URL: enthält noch die Beispiel-Domain aus .env.example.")
    return warnings


def system_check(environ=None):
    """(name, ok, detail) for the system page (U-12 format)."""
    warnings = validate_environment(environ)
    if not warnings:
        return ("Konfigurationswerte", True, "alle gesetzten optionalen Werte gültig")
    return ("Konfigurationswerte", False, f"{len(warnings)} Hinweis(e): " + " · ".join(warnings[:3]) + (" …" if len(warnings) > 3 else ""))
