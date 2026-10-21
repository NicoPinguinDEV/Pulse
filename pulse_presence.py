"""BOT-13: single, rate-limited source of truth for the Discord presence."""
from __future__ import annotations

import asyncio
import time

import discord

MIN_PRESENCE_INTERVAL_SECONDS = 180
_LOCK_ATTR = "_pulse_presence_lock"
_LAST_AT_ATTR = "_pulse_presence_last_at"
_LAST_NAME_ATTR = "_pulse_presence_last_name"


def _log_failure(operation: str, exc: Exception) -> None:
    try:
        import pulse_logging
        pulse_logging.get("presence").warning(
            "%s fehlgeschlagen (%s)", operation, type(exc).__name__
        )
    except Exception:
        # Logging must not make the bot's ready/housekeeping handlers fail.
        pass


async def update_presence(bot) -> bool:
    """Update the activity once from the current shift data.

    Calls made during the cooldown are skipped. The first successful call always
    initializes the activity; unchanged values do not generate Discord API traffic.
    This helper deliberately starts no task and does not retain additional bot objects.
    """
    if bot is None or not callable(getattr(bot, "change_presence", None)):
        return False

    lock = getattr(bot, _LOCK_ATTR, None)
    if lock is None:
        lock = asyncio.Lock()
        setattr(bot, _LOCK_ATTR, lock)

    async with lock:
        now = time.monotonic()
        last_at = getattr(bot, _LAST_AT_ATTR, None)
        if last_at is not None and now - last_at < MIN_PRESENCE_INTERVAL_SECONDS:
            return False

        try:
            import webserver
            shifts = await asyncio.to_thread(webserver.load_shifts)
            active_shifts = shifts.get("active_shifts", {}) if isinstance(shifts, dict) else {}
            active = sum(
                1
                for shift in active_shifts.values()
                if isinstance(shift, dict) and shift.get("status") in {"online", "break"}
            )
        except Exception as exc:
            _log_failure("Schichtstatus für Discord-Präsenz lesen", exc)
            return False

        name = f"Pulse · {active} im Dienst"
        if getattr(bot, _LAST_NAME_ATTR, None) == name:
            return False

        try:
            await bot.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name=name,
                )
            )
        except Exception as exc:
            _log_failure("Discord-Präsenz setzen", exc)
            return False

        setattr(bot, _LAST_AT_ATTR, now)
        setattr(bot, _LAST_NAME_ATTR, name)
        return True
