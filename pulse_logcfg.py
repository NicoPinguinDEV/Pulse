"""BOT-02: routing, ignore lists and batched sending for the moderation log (builds on K-12).

* Each category can go to its own channel; without one it uses the K-12 log channel (unchanged default).
* Ignore lists: channels, roles and (by default) bots.
* Voice logging can be switched off and is throttled per person.
* Entries are collected per channel for a few seconds and sent together (up to 10 per message), so a busy
  moment does not hit Discord's rate limits. The buffer is bounded; nothing runs when nothing is logged.
"""
from __future__ import annotations

from collections import deque

SETTING_KEY = "log_routing"
CATEGORIES = {
    "messages": "Nachrichten (gelöscht/bearbeitet)",
    "members": "Beitritt/Austritt, Nicknames",
    "moderation": "Bans, Unbans, Kicks, Timeouts",
    "roles": "Rollen (Mitglieder und Serverrollen)",
    "channels": "Kanäle (erstellt/gelöscht/geändert)",
    "voice": "Voice (betreten/verlassen/wechseln)",
}
DEFAULTS = {"channels": {}, "ignore_channels": [], "ignore_roles": [], "ignore_bots": True, "voice": True}
BATCH_SECONDS = 3.0
BATCH_MAX = 10           # embeds per Discord message
BUFFER_MAX = 200         # per channel; older entries drop out first
VOICE_THROTTLE = 10.0    # seconds per person
MESSAGE_CHARS = 5800     # Discord allows 6000 characters across all embeds of one message
RETRY_SECONDS = 30.0     # after a rate limit


def _ids(values) -> list[int]:
    out = []
    for v in values or []:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def normalize(raw) -> dict:
    cfg = {**DEFAULTS, "channels": {}}
    if not isinstance(raw, dict):
        return cfg
    channels = raw.get("channels") if isinstance(raw.get("channels"), dict) else {}
    for key, value in channels.items():
        if key in CATEGORIES and str(value).isdigit() and int(value):
            cfg["channels"][key] = int(value)
    cfg["ignore_channels"] = _ids(raw.get("ignore_channels"))
    cfg["ignore_roles"] = _ids(raw.get("ignore_roles"))
    cfg["ignore_bots"] = bool(raw.get("ignore_bots", True))
    cfg["voice"] = bool(raw.get("voice", True))
    return cfg


_CACHE = {"at": -1e9, "cfg": None}
CACHE_SECONDS = 30.0  # log events are frequent; the setting changes rarely


def load() -> dict:
    import time

    import pulse_db
    now = time.monotonic()
    if _CACHE["cfg"] is not None and now - _CACHE["at"] < CACHE_SECONDS:
        return _CACHE["cfg"]
    try:
        cfg = normalize(pulse_db.get_setting(SETTING_KEY, {}))
    except Exception:
        cfg = normalize({})
    _CACHE.update(at=now, cfg=cfg)
    return cfg


def save(raw) -> dict:
    import pulse_db
    cfg = normalize(raw)
    pulse_db.set_setting(SETTING_KEY, cfg)
    _CACHE.update(at=-1e9, cfg=None)
    return cfg


def route(category: str, cfg: dict, fallback_channel_id: int) -> int:
    """Channel id for this category: its own channel, else the K-12 log channel (0 = off)."""
    if category == "voice" and not cfg.get("voice", True):
        return 0
    return int(cfg["channels"].get(category) or fallback_channel_id or 0)


def ignored(cfg: dict, *, channel_id=None, role_ids=(), is_bot=False) -> bool:
    if is_bot and cfg.get("ignore_bots", True):
        return True
    if channel_id is not None and int(channel_id) in cfg.get("ignore_channels", []):
        return True
    return bool(set(int(r) for r in role_ids) & set(cfg.get("ignore_roles", [])))


class LogBuffer:
    """Per-channel bounded queue; `take` returns up to BATCH_MAX entries for one message."""

    def __init__(self):
        self.queues: dict[int, deque] = {}

    def add(self, channel_id: int, item) -> bool:
        """Returns True when this is the first pending entry (the caller schedules one flush)."""
        queue = self.queues.setdefault(int(channel_id), deque(maxlen=BUFFER_MAX))
        first = not queue
        queue.append(item)
        return first

    def take(self, channel_id: int, max_chars: int | None = None, size=len) -> list:
        """Up to BATCH_MAX entries; with max_chars also at most that many characters in total (at least one entry)."""
        queue = self.queues.get(int(channel_id))
        if not queue:
            return []
        out, total = [], 0
        while queue and len(out) < BATCH_MAX:
            length = size(queue[0]) if max_chars else 0
            if out and max_chars and total + length > max_chars:
                break
            out.append(queue.popleft())
            total += length
        return out

    def push_front(self, channel_id: int, items: list) -> None:
        """Put a batch back (rate limit) – in its original order, ahead of newer entries."""
        queue = self.queues.setdefault(int(channel_id), deque(maxlen=BUFFER_MAX))
        for item in reversed(items):
            queue.appendleft(item)

    def pending(self, channel_id: int) -> int:
        return len(self.queues.get(int(channel_id)) or ())


class Throttle:
    """At most one entry per key within `seconds` (bounded map)."""

    def __init__(self, seconds: float, max_keys: int = 2000):
        self.seconds, self.max_keys, self.last = seconds, max_keys, {}

    def allow(self, key, now: float) -> bool:
        last = self.last.get(key)
        if last is not None and now - last < self.seconds:
            return False
        self.last[key] = now
        if len(self.last) > self.max_keys:
            for old in sorted(self.last, key=self.last.get)[: self.max_keys // 2]:
                self.last.pop(old, None)
        return True
