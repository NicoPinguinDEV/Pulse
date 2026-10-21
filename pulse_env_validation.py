"""Validate optional Pulse environment settings without exposing their values.

The validator is intentionally advisory: optional integrations stay optional and a
bad optional value does not prevent the Discord bot from starting. Messages contain
only variable names and expected types/ranges, never the supplied values or secrets.
"""
from __future__ import annotations

import math
import os
from urllib.parse import urlsplit


INTEGER_RANGES: dict[str, tuple[int, int]] = {
    "SERVER_PORT": (1, 65535),
    "DISCORD_MESSAGE_CACHE_SIZE": (100, 1000),
    "SESSION_DAYS": (1, 365),
    "AUTO_BACKUP_HOURS": (0, 8760),
    "MAX_BACKUPS": (1, 1000),
    "PULSE_SUPPORT_MAX_TOKENS": (300, 2200),
    "PULSE_AI_MODERATION_INTERVAL_SECONDS": (1, 3600),
    "PULSE_AI_MODERATION_MAX_QUEUE_AGE_SECONDS": (1, 86400),
    "PULSE_AI_MODERATION_OUTAGE_NOTICE_INTERVAL_SECONDS": (30, 86400),
    "PULSE_DM_QUIET_FROM": (0, 23),
    "PULSE_DM_QUIET_TO": (0, 23),
    "PULSE_TICKET_REMINDER_QUIET_FROM": (0, 23),
    "PULSE_TICKET_REMINDER_QUIET_TO": (0, 23),
    "PULSE_RAID_WATCH_JOIN_WINDOW_SECONDS": (30, 600),
    "PULSE_RAID_WATCH_JOIN_THRESHOLD": (5, 100),
    "PULSE_RAID_WATCH_NEW_ACCOUNT_THRESHOLD": (0, 365),
    "PULSE_RAID_WATCH_MENTION_THRESHOLD": (1, 100),
    "PULSE_RAID_WATCH_DUPLICATE_CHANNELS": (2, 100),
    "PULSE_BACKUP_MAX_MB": (1, 10000),
    "PULSE_BACKUP_TOTAL_MAX_MB": (1, 100000),
}
FLOAT_RANGES: dict[str, tuple[float, float]] = {
    "MAX_SHIFT_HOURS": (0.5, 24.0),
    "PULSE_AI_MODERATION_CONFIDENCE": (0.0, 1.0),
    "PULSE_AI_MODERATION_LOW_CONFIDENCE": (0.0, 1.0),
    "PULSE_SESSION_IDLE_HOURS": (0.25, 720.0),
}
ID_RANGES: dict[str, tuple[int, int]] = {
    "DISCORD_CLIENT_ID": (0, 2**63 - 1),
    "DISCORD_GUILD_ID": (0, 2**63 - 1),
    "PULSE_BACKUP_DISCORD_CHANNEL_ID": (0, 2**63 - 1),
    "PULSE_SUPPORT_CHANNEL_ID": (0, 2**63 - 1),
    "PULSE_SUPPORT_TEAM_CHANNEL_ID": (0, 2**63 - 1),
    "PULSE_AI_MODERATION_REVIEW_CHANNEL_ID": (0, 2**63 - 1),
    "PULSE_REPORT_CHANNEL_ID": (0, 2**63 - 1),
    "PULSE_SYSTEM_ALERT_CHANNEL_ID": (0, 2**63 - 1),
    "PULSE_RAID_WATCH_ALERT_CHANNEL_ID": (0, 2**63 - 1),
}
ID_LIST_VARS = (
    "PULSE_SUPPORT_TEAM_ROLE_IDS",
    "PULSE_AI_MODERATION_EXCLUDED_CHANNEL_IDS",
    "PULSE_AI_MODERATION_EXCLUDED_CATEGORY_IDS",
)
URL_VARS = (
    "PUBLIC_BASE_URL",
    "DISCORD_REDIRECT_URI",
    "DISCORD_APPLICATION_REDIRECT_URI",
    "GEMINI_BASE_URL",
    "OPENAI_BASE_URL",
    "PULSE_RSSHUB_BASE_URL",
)
URL_LIST_VARS = ("PULSE_RSSHUB_FALLBACK_URLS",)


def _configured(env, name: str) -> str:
    return str(env.get(name, "") or "").strip()


def validate_environment(environ=None) -> list[str]:
    """Return safe, value-free configuration warnings in deterministic order."""
    env = os.environ if environ is None else environ
    warnings: list[str] = []

    for name, (minimum, maximum) in INTEGER_RANGES.items():
        raw = _configured(env, name)
        if not raw:
            continue
        try:
            value = int(raw, 10)
        except (TypeError, ValueError, OverflowError):
            warnings.append(f"{name}: erwartet eine ganze Zahl ({minimum}–{maximum}).")
            continue
        if not minimum <= value <= maximum:
            warnings.append(f"{name}: außerhalb des erlaubten Bereichs ({minimum}–{maximum}).")

    for name, (minimum, maximum) in FLOAT_RANGES.items():
        raw = _configured(env, name)
        if not raw:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError):
            warnings.append(f"{name}: erwartet eine Zahl zwischen {minimum} und {maximum}.")
            continue
        if not math.isfinite(value) or not minimum <= value <= maximum:
            warnings.append(f"{name}: außerhalb des erlaubten Bereichs ({minimum}–{maximum}).")

    for name, (minimum, maximum) in ID_RANGES.items():
        raw = _configured(env, name)
        if not raw:
            continue
        try:
            value = int(raw, 10)
        except (TypeError, ValueError, OverflowError):
            warnings.append(f"{name}: erwartet eine numerische Discord-ID oder leer.")
            continue
        if not minimum <= value <= maximum:
            warnings.append(f"{name}: ungültiger Bereich für eine Discord-ID.")

    for name in ID_LIST_VARS:
        raw = _configured(env, name)
        if not raw:
            continue
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        if any(not part.isdecimal() or not 0 < int(part) < 2**63 for part in parts):
            warnings.append(f"{name}: erwartet kommaseparierte positive Discord-IDs.")

    for name in URL_VARS:
        raw = _configured(env, name)
        if not raw:
            continue
        try:
            parsed = urlsplit(raw)
            valid = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
        except ValueError:
            valid = False
        if not valid:
            warnings.append(f"{name}: erwartet eine vollständige HTTP(S)-URL.")

    for name in URL_LIST_VARS:
        raw = _configured(env, name)
        if not raw:
            continue
        entries = [item.strip() for item in raw.split(",") if item.strip()]
        for item in entries:
            try:
                parsed = urlsplit(item)
                valid = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
            except ValueError:
                valid = False
            if not valid:
                warnings.append(f"{name}: enthält einen Eintrag ohne gültige HTTP(S)-URL.")
                break

    base_url = _configured(env, "PUBLIC_BASE_URL")
    if base_url and "your-domain.example" in base_url.casefold():
        warnings.append("PUBLIC_BASE_URL: enthält noch die Beispiel-Domain.")

    # These settings are path/model strings rather than secrets or numeric values.
    # They are deliberately not echoed, opened, or checked for filesystem existence here.
    return warnings
