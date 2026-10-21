"""U-11: notification preferences per team member – categories, optional Discord DM, quiet hours.

* A muted category still lands in the inbox, but already marked as read (nothing is lost, nothing nags).
  "System" (security, errors, everything without a known category) cannot be muted.
* Discord DMs are off by default. They are queued in a bounded deque and sent by the existing
  60-second automation loop (pulse_ultimate.automation_loop) – no extra loop, at most DM_BATCH per run.
* During quiet hours no DM is sent; the inbox entry stays unread as usual.
"""
from __future__ import annotations

import json
from collections import deque
from datetime import datetime

CATEGORIES = {
    "tickets": ("🎫 Tickets", ("ticket",)),
    "applications": ("📝 Bewerbungen", ("application", "bewerb")),
    "absence": ("🌴 Abmeldungen", ("/loa", "abmeld")),
    "work": ("✅ Aufgaben, Schichtplan & Schulungen", ("task", "schedule", "handover", "training")),
    "team": ("👥 Team, Beförderungen & Fälle", ("promotion", "approval", "case", "member", "person", "/team", "feedback")),
    "announcements": ("📣 Ankündigungen", ("announcement",)),
    "achievements": ("🏅 Achievements", ("achievement",)),
}
SYSTEM = "system"  # never muted
DEFAULTS = {"muted": [], "dm": False, "quiet_from": None, "quiet_to": None}
DM_QUEUE_MAX = 200
DM_BATCH = 10
_dm_queue: deque = deque(maxlen=DM_QUEUE_MAX)


def _key(user_id) -> str:
    return f"notify_prefs:{user_id}"


def category_for(url: str = "", title: str = "") -> str:
    text = f"{url or ''} {title or ''}".lower()
    for key, (_label, words) in CATEGORIES.items():
        if any(w in text for w in words):
            return key
    return SYSTEM


def _hour(value):
    try:
        hour = int(value)
    except (TypeError, ValueError):
        return None
    return hour if 0 <= hour <= 23 else None


def normalize(raw) -> dict:
    prefs = dict(DEFAULTS)
    if isinstance(raw, dict):
        prefs["muted"] = sorted({m for m in raw.get("muted", []) if m in CATEGORIES})
        prefs["dm"] = bool(raw.get("dm"))
        prefs["quiet_from"] = _hour(raw.get("quiet_from"))
        prefs["quiet_to"] = _hour(raw.get("quiet_to"))
        if prefs["quiet_from"] is None or prefs["quiet_to"] is None or prefs["quiet_from"] == prefs["quiet_to"]:
            prefs["quiet_from"] = prefs["quiet_to"] = None
    return prefs


def load(user_id) -> dict:
    import pulse_db
    try:
        return normalize(json.loads(pulse_db.get_setting(_key(user_id), "") or "{}"))
    except (ValueError, TypeError):
        return dict(DEFAULTS)


def save(user_id, prefs) -> dict:
    import pulse_db
    clean = normalize(prefs)
    pulse_db.set_setting(_key(user_id), json.dumps(clean))
    return clean


def in_quiet_hours(prefs: dict, now: datetime | None = None) -> bool:
    start, end = prefs.get("quiet_from"), prefs.get("quiet_to")
    if start is None or end is None:
        return False
    if now is None:
        try:
            import webserver
            now = webserver.now_de()
        except Exception:
            now = datetime.now()
    hour = now.hour
    return start <= hour < end if start < end else (hour >= start or hour < end)  # e.g. 22 → 7 wraps midnight


def decide(user_id, url: str = "", title: str = "", prefs: dict | None = None, now: datetime | None = None) -> dict:
    """What happens with a new notification: stored unread? also sent as DM?"""
    prefs = load(user_id) if prefs is None else prefs
    category = category_for(url, title)
    muted = category in prefs.get("muted", []) and category != SYSTEM
    return {"category": category, "unread": not muted,
            "dm": bool(prefs.get("dm")) and not muted and not in_quiet_hours(prefs, now)}


def queue_dm(user_id, title, body, url) -> None:
    _dm_queue.append((str(user_id), str(title)[:200], str(body)[:1500], str(url or "")))


def pending_dms() -> int:
    return len(_dm_queue)


async def drain_dms(bot, base_url: str = "", limit: int = DM_BATCH) -> dict:
    """Send up to `limit` queued DMs. Closed DMs are skipped; on a rate limit the rest waits for the next run."""
    import discord
    sent = skipped = 0
    while _dm_queue and sent + skipped < limit:
        user_id, title, body, url = _dm_queue.popleft()
        link = f"\n{base_url.rstrip('/')}{url}" if base_url and url.startswith("/") else ""
        try:
            user = bot.get_user(int(user_id)) or await bot.fetch_user(int(user_id))
            await user.send(f"**{title}**\n{body}{link}"[:1900])
            sent += 1
        except discord.Forbidden:
            skipped += 1  # DMs closed – the inbox entry is still there
        except discord.HTTPException as exc:
            if getattr(exc, "status", None) == 429:
                _dm_queue.appendleft((user_id, title, body, url))
                break
            skipped += 1
        except (ValueError, TypeError):
            skipped += 1
    return {"sent": sent, "skipped": skipped, "pending": len(_dm_queue)}
