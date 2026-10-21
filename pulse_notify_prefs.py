"""U-11: notification preferences per team member – categories, optional Discord DM, quiet hours.

* A muted category still lands in the inbox, but already marked as read (nothing is lost, nothing nags).
  "System" (security, errors, everything without a known category) cannot be muted.
* Discord DMs are off by default. They are queued in a bounded deque and sent by the existing
  60-second automation loop (pulse_ultimate.automation_loop) – no extra loop, at most DM_BATCH per run.
* During quiet hours (the person's own and the server's, default 23–8 Uhr – BOT-12) DMs wait in the
  queue and go out afterwards, several notes to the same person bundled into one message.
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


# BOT-12: server-wide quiet hours for reminder DMs (dashboard: inbox page, admins). Default 23–8 Uhr.
SERVER_QUIET_KEY = "dm_quiet_hours"
SERVER_QUIET_DEFAULT = {"quiet_from": 23, "quiet_to": 8}
BUNDLE_MAX = 5  # at most this many notes in one bundled DM


def server_quiet() -> dict:
    import pulse_db
    try:
        raw = pulse_db.get_setting(SERVER_QUIET_KEY, None)
    except Exception:
        raw = None
    if raw is None:
        return dict(SERVER_QUIET_DEFAULT)
    clean = normalize(raw if isinstance(raw, dict) else {})
    return {"quiet_from": clean["quiet_from"], "quiet_to": clean["quiet_to"]}


def save_server_quiet(quiet_from, quiet_to) -> dict:
    import pulse_db
    clean = normalize({"quiet_from": quiet_from, "quiet_to": quiet_to})
    value = {"quiet_from": clean["quiet_from"], "quiet_to": clean["quiet_to"]}  # both None = no server quiet time
    pulse_db.set_setting(SERVER_QUIET_KEY, value)
    return value


def dm_paused(user_id, now: datetime | None = None) -> bool:
    """True during the server quiet time or the person's own quiet time – DMs then wait in the queue."""
    return in_quiet_hours(server_quiet(), now) or in_quiet_hours(load(user_id), now)


def decide(user_id, url: str = "", title: str = "", prefs: dict | None = None, now: datetime | None = None) -> dict:
    """What happens with a new notification: stored unread? also queued as DM? (Quiet hours no longer drop the
    DM – it waits in the queue and is sent bundled afterwards, BOT-12.)"""
    prefs = load(user_id) if prefs is None else prefs
    category = category_for(url, title)
    muted = category in prefs.get("muted", []) and category != SYSTEM
    return {"category": category, "unread": not muted, "dm": bool(prefs.get("dm")) and not muted}


def queue_dm(user_id, title, body, url) -> None:
    _dm_queue.append((str(user_id), str(title)[:200], str(body)[:1500], str(url or "")))


def pending_dms() -> int:
    return len(_dm_queue)


def bundle_text(notes: list[tuple[str, str, str]], base_url: str = "") -> str:
    """One DM for one or several notes of the same person."""
    def line(title, body, url):
        link = f"\n{base_url.rstrip('/')}{url}" if base_url and url.startswith("/") else ""
        return f"**{title}**\n{body}{link}"
    if len(notes) == 1:
        return line(*notes[0])[:1900]
    head = f"📬 **{len(notes)} Hinweise von Pulse**\n\n"
    return (head + "\n\n".join(line(*n) for n in notes))[:1900]


async def drain_dms(bot, base_url: str = "", limit: int = DM_BATCH, now: datetime | None = None) -> dict:
    """Send queued DMs to up to `limit` people, several notes per person bundled into one message.
    People in a quiet time keep their notes queued; closed DMs are skipped; on a rate limit the rest waits."""
    import discord
    sent = skipped = held = 0
    waiting: list = []
    per_user: dict[str, list] = {}
    order: list[str] = []
    while _dm_queue:
        item = _dm_queue.popleft()
        if dm_paused(item[0], now):
            waiting.append(item)
            continue
        if item[0] not in per_user:
            order.append(item[0])
        per_user.setdefault(item[0], []).append(item[1:])
    held = len(waiting)
    stop = False
    for user_id in order:
        notes = per_user[user_id]
        if stop or sent + skipped >= limit:
            waiting.extend((user_id, *n) for n in notes)
            continue
        batch, rest = notes[:BUNDLE_MAX], notes[BUNDLE_MAX:]
        waiting.extend((user_id, *n) for n in rest)
        try:
            user = bot.get_user(int(user_id)) or await bot.fetch_user(int(user_id))
            await user.send(bundle_text(batch, base_url))
            sent += 1
        except discord.Forbidden:
            skipped += 1  # DMs closed – the inbox entry is still there
        except discord.HTTPException as exc:
            if getattr(exc, "status", None) == 429:
                waiting.extend((user_id, *n) for n in batch)
                stop = True
                continue
            skipped += 1
        except (ValueError, TypeError):
            skipped += 1
    _dm_queue.extend(waiting)  # bounded deque: the oldest drop out first, the inbox keeps everything
    return {"sent": sent, "skipped": skipped, "held": held, "pending": len(_dm_queue)}
