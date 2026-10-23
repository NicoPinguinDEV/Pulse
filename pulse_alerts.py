"""O-07: throttled system alerts for the optional PULSE_SYSTEM_ALERT_CHANNEL_ID – no loop of its own.

Error paths (failed background jobs, backups, restore tests, database checks) call `raise_alert()`.
It only queues the text; the existing 60-second automation loop calls `flush()` and posts the queue.
Bounded for the RAM budget: at most 20 waiting alerts and one cooldown record per key (max. 200 keys).
The same key alerts again only after its cooldown (default 30 minutes). Never pass secrets or user content.
"""
from __future__ import annotations

import os
import time
from collections import OrderedDict, deque

MAX_PENDING = 20
MAX_KEYS = 200
MAX_PER_FLUSH = 5
DEFAULT_COOLDOWN_SECONDS = 1800

_PENDING: deque = deque(maxlen=MAX_PENDING)
_LAST: "OrderedDict[str, float]" = OrderedDict()
_STATS = {"raised": 0, "throttled": 0, "sent": 0, "dropped": 0}


def raise_alert(key: str, message: str, cooldown: float = DEFAULT_COOLDOWN_SECONDS, now: float | None = None) -> bool:
    """Queue one alert. Returns False when the same key is still in its cooldown."""
    now = time.monotonic() if now is None else now
    key = str(key)[:120]
    last = _LAST.get(key)
    if last is not None and now - last < cooldown:
        _STATS["throttled"] += 1
        return False
    _LAST[key] = now
    _LAST.move_to_end(key)
    while len(_LAST) > MAX_KEYS:
        _LAST.popitem(last=False)
    if len(_PENDING) == _PENDING.maxlen:
        _STATS["dropped"] += 1
    _PENDING.append(str(message)[:1500])
    _STATS["raised"] += 1
    return True


def pending() -> list[str]:
    return list(_PENDING)


def stats() -> dict:
    return dict(_STATS, pending=len(_PENDING))


def _channel_id() -> int:
    raw = os.getenv("PULSE_SYSTEM_ALERT_CHANNEL_ID", "").strip()
    return int(raw) if raw.isdigit() else 0


async def flush(bot) -> int:
    """Post up to MAX_PER_FLUSH queued alerts. Without a configured/reachable channel the queue is discarded
    (the alerts are in the log already). Discord errors stop this run; the rest waits for the next minute."""
    if not _PENDING:
        return 0
    channel_id = _channel_id()
    channel = bot.get_channel(channel_id) if (bot and channel_id) else None
    if channel is None and bot and channel_id:
        import discord
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            channel = None
    if channel is None or not hasattr(channel, "send"):
        _STATS["dropped"] += len(_PENDING)
        _PENDING.clear()
        return 0
    import discord
    sent = 0
    while _PENDING and sent < MAX_PER_FLUSH:
        message = _PENDING[0]
        try:
            await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            _STATS["dropped"] += len(_PENDING)
            _PENDING.clear()
            print("⚠️ Systemalarm: keine Schreibrechte im Alert-Kanal – Alarme verworfen.")
            break
        except discord.HTTPException:
            break  # rate limit / Discord outage: retry next minute, the queue stays bounded
        _PENDING.popleft()
        sent += 1
        _STATS["sent"] += 1
    return sent


def reset() -> None:
    """Tests only."""
    _PENDING.clear()
    _LAST.clear()
    for key in _STATS:
        _STATS[key] = 0
