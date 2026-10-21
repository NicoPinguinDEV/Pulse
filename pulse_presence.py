"""Central, rate-limited Discord presence updates for Pulse TeamOS.

The bot, Pulse Pro housekeeping and Suite actions all call this module. Only this
module talks to Discord's change_presence API, avoiding competing status writers.
"""
from __future__ import annotations

import logging
import time

import discord

log = logging.getLogger(__name__)

# Keep status churn low on the 250-MB bot-hosting budget and avoid frequent gateway writes.
MIN_UPDATE_INTERVAL_SECONDS = 180


def active_shift_count() -> int | None:
    """Return the number of online/on-break shifts, or None if runtime data is unavailable."""
    try:
        import webserver

        shifts = webserver.load_shifts().get("active_shifts", {})
        return sum(
            1
            for shift in shifts.values()
            if isinstance(shift, dict) and shift.get("status") in {"online", "break"}
        )
    except Exception:
        log.exception("Aktive Schichten konnten für die Discord-Präsenz nicht gelesen werden.")
        return None


async def update_presence(bot, *, active_count: int | None = None, force: bool = False) -> bool:
    """Apply the desired status once; regular changes are limited to one per three minutes.

    Failed reads/writes do not update the remembered state. force is intended for
    startup/reconnect so Discord receives the presence again after the gateway reconnects.
    """
    if active_count is None:
        active_count = active_shift_count()
    if active_count is None:
        return False
    try:
        count = max(0, int(active_count))
    except (TypeError, ValueError):
        log.warning("Ungültiger Wert für die aktive Schichtanzahl: %r", active_count)
        return False

    desired = f"Pulse · {count} im Dienst"
    previous = getattr(bot, "_pulse_presence_name", None)
    if desired == previous and not force:
        return False

    now = time.monotonic()
    last_update = getattr(bot, "_pulse_presence_last_at", None)
    if last_update is not None and not force and now - last_update < MIN_UPDATE_INTERVAL_SECONDS:
        # The next existing five-minute housekeeping pass re-reads the current count.
        return False

    try:
        await bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name=desired)
        )
    except Exception:
        log.exception("Discord-Präsenz konnte nicht aktualisiert werden.")
        return False

    bot._pulse_presence_name = desired
    bot._pulse_presence_last_at = now
    return True
