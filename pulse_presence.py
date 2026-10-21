"""BOT-13: the bot's Discord status is set in exactly one place.

Before, main.py, cogs/pulse_pro.py (twice) and cogs/pulse_suite.py each called change_presence with
their own text and their own way of counting who is on duty. Now everyone calls `update(bot)`: one
text ("N im Dienst · M Tickets offen"), at most every MIN_INTERVAL seconds and only when it changed.
"""
from __future__ import annotations

import time

MIN_INTERVAL = 300  # seconds between two presence updates (Discord rate-limits presence changes)
_state = {"text": None, "at": 0.0}


def on_duty(shifts: dict) -> int:
    """People currently on duty – a running shift or a break, like the dashboard counts it."""
    return sum(1 for s in (shifts.get("active_shifts") or {}).values() if isinstance(s, dict) and s.get("status") in ("online", "break"))


def open_tickets() -> int:
    import pulse_db
    try:
        with pulse_db.connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM tickets WHERE status!='closed'").fetchone()[0])
    except Exception:
        return 0


def presence_text(active: int, tickets: int) -> str:
    return f"{active} im Dienst · {tickets} {'Ticket' if tickets == 1 else 'Tickets'} offen"


async def update(bot, force: bool = False, now: float | None = None) -> bool:
    """Sets the status if it changed and the last update is old enough. Returns True when Discord was called."""
    import discord
    import webserver
    now = time.monotonic() if now is None else now
    if not force and now - _state["at"] < MIN_INTERVAL:
        return False
    text = presence_text(on_duty(webserver.load_shifts()), open_tickets())
    if text == _state["text"] and not force:
        _state["at"] = now
        return False
    try:
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=text))
    except Exception as exc:
        print(f"⚠️ Discord-Status konnte nicht gesetzt werden: {type(exc).__name__}")
        return False
    _state.update(text=text, at=now)
    return True
