"""U-11: notification preferences per team member – categories, optional Discord DM, quiet hours.

* A muted category still lands in the inbox, but already marked as read (nothing is lost, nothing nags).
  "System" (security, errors, everything without a known category) cannot be muted.
* Discord DMs are off by default. They are queued in a bounded deque and sent by the existing
  60-second automation loop (pulse_ultimate.automation_loop) – no extra loop, at most DM_BATCH per run.
* During quiet hours no DM is sent; the inbox entry stays unread as usual.
"""
from __future__ import annotations

import json
import os
import threading
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
DM_QUEUE_MAX = 200  # maximum number of individual queued notifications
DM_BATCH = 10  # maximum individual notifications examined per automation run
DM_USER_BATCH_MAX = 10
MAX_DM_MESSAGE_CHARS = 1850
_dm_queue: deque = deque()  # entries: (user_id, [(title, body, url), ...])
_QUEUE_LOCK = threading.RLock()


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


def global_quiet_hours(now: datetime | None = None) -> bool:
    """Server-wide baseline quiet window for DMs; per-user preferences may add a stricter window."""
    def configured_hour(name: str, default: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default
        return value if 0 <= value <= 23 else default

    start = configured_hour("PULSE_DM_QUIET_FROM", 23)
    end = configured_hour("PULSE_DM_QUIET_TO", 8)
    if start == end:
        return False

    try:
        zone = ZoneInfo(os.getenv("PANEL_TIMEZONE", "Europe/Berlin"))
    except (ZoneInfoNotFoundError, ValueError):
        zone = datetime.now().astimezone().tzinfo
    if now is None:
        try:
            import webserver
            now = webserver.now_de()
        except Exception:
            now = datetime.now(zone)
    if now.tzinfo is None and zone is not None:
        now = now.replace(tzinfo=zone)
    elif zone is not None:
        now = now.astimezone(zone)
    hour = now.hour
    return start <= hour < end if start < end else hour >= start or hour < end


def decide(user_id, url: str = "", title: str = "", prefs: dict | None = None, now: datetime | None = None) -> dict:
    """What happens with a new notification: stored unread? also sent as DM?"""
    prefs = load(user_id) if prefs is None else prefs
    category = category_for(url, title)
    muted = category in prefs.get("muted", []) and category != SYSTEM
    # Quiet hours are checked at delivery time, when the queued DM can be deferred until morning.
    return {"category": category, "unread": not muted,
            "dm": bool(prefs.get("dm")) and not muted}


def _pending_dms_locked() -> int:
    return sum(len(items) for _user_id, items in _dm_queue)


def pending_dms() -> int:
    """Number of individual notification DMs pending, not the number of recipient buckets."""
    with _QUEUE_LOCK:
        return _pending_dms_locked()


def _drop_oldest_locked() -> None:
    if not _dm_queue:
        return
    user_id, messages = _dm_queue.popleft()
    remaining = list(messages)[1:]
    if remaining:
        _dm_queue.appendleft((user_id, remaining))


def queue_dm(user_id, title, body, url) -> None:
    """Queue an opt-in DM, bundling up to ten notifications for the same user."""
    uid = str(user_id)
    message = (str(title)[:200], str(body)[:1500], str(url or ""))
    with _QUEUE_LOCK:
        while _pending_dms_locked() >= DM_QUEUE_MAX:
            _drop_oldest_locked()

        # Grouping the small bounded queue avoids sending a separate DM for every
        # reminder. The per-user list retains each notification's category and URL,
        # so send-time preference checks still apply to every item.
        for index, (queued_uid, messages) in enumerate(_dm_queue):
            if queued_uid == uid and len(messages) < DM_USER_BATCH_MAX:
                updated = list(messages)
                updated.append(message)
                _dm_queue[index] = (uid, updated)
                return
        _dm_queue.append((uid, [message]))


def _delivery_status(user_id: str, message: tuple, prefs: dict | None = None) -> str:
    """Return 'send', 'quiet', or 'skip' using the user's current preferences."""
    prefs = load(user_id) if prefs is None else prefs
    _title, _body, url = message
    category = category_for(url, _title)
    if not prefs.get("dm") or (category in prefs.get("muted", []) and category != SYSTEM):
        return "skip"
    return "quiet" if global_quiet_hours() or in_quiet_hours(prefs) else "send"


def _render_dm(messages: list[tuple], base_url: str) -> str:
    count = len(messages)
    noun = "Hinweis" if count == 1 else "Hinweise"
    header = f"**Pulse TeamOS · {count} {noun}**\\n\\n"
    remaining = MAX_DM_MESSAGE_CHARS - len(header)
    parts = []
    for title, body, url in messages:
        separator = "\\n\\n" if parts else ""
        title_line = f"**{title}**\\n"
        link = f"\\n{base_url.rstrip('/')}{url}" if base_url and url.startswith("/") else ""
        link = link[:260]
        budget = max(0, remaining - len(separator) - len(title_line) - len(link))
        text = str(body or "")
        # A single oversized notification is shortened for Discord's message limit.
        # Multi-item candidates keep their content intact so the chunker can split
        # them into multiple DMs instead of silently dropping later reminders.
        if len(messages) == 1 and len(text) > budget:
            text = (text[:max(0, budget - 1)] + "…") if budget else ""
        part = separator + title_line + text + link
        parts.append(part)
        remaining -= len(part)
    return header + "".join(parts)


def _chunk_dm_messages(messages: list[tuple], base_url: str) -> list[list[tuple]]:
    chunks: list[list[tuple]] = []
    current: list[tuple] = []
    for message in messages:
        candidate = current + [message]
        if current and len(_render_dm(candidate, base_url)) > MAX_DM_MESSAGE_CHARS:
            chunks.append(current)
            current = [message]
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _requeue_front(records: list[tuple[str, tuple]]) -> None:
    if not records:
        return
    by_user: dict[str, list[tuple]] = {}
    for user_id, message in records:
        by_user.setdefault(str(user_id), []).append(message)
    with _QUEUE_LOCK:
        for user_id, messages in reversed(list(by_user.items())):
            _dm_queue.appendleft((user_id, messages))
        # If other threads filled the queue while an HTTP request was in flight,
        # preserve the older unsent messages and drop only the newest overflow.
        while _pending_dms_locked() > DM_QUEUE_MAX:
            user_id, messages = _dm_queue.pop()
            remaining = list(messages)[:-1]
            if remaining:
                _dm_queue.append((user_id, remaining))


def _requeue_tail(records: list[tuple[str, tuple]]) -> None:
    if not records:
        return
    by_user: dict[str, list[tuple]] = {}
    for user_id, message in records:
        by_user.setdefault(str(user_id), []).append(message)
    with _QUEUE_LOCK:
        for user_id, messages in by_user.items():
            _dm_queue.append((user_id, messages))
        while _pending_dms_locked() > DM_QUEUE_MAX:
            _drop_oldest_locked()


async def drain_dms(bot, base_url: str = "", limit: int = DM_BATCH) -> dict:
    """Send batched opt-in DMs, rechecking quiet hours at actual send time.

    The queue is snapshotted under a thread lock because background workers can
    enqueue reminders. Queue/database locks are never held across Discord awaits.
    """
    import discord

    try:
        budget = max(0, int(limit))
    except (TypeError, ValueError):
        budget = DM_BATCH
    batch: list[tuple[str, tuple]] = []
    with _QUEUE_LOCK:
        remaining = min(budget, _pending_dms_locked())
        while _dm_queue and remaining > 0:
            user_id, messages = _dm_queue.popleft()
            take = list(messages[:remaining])
            rest = list(messages[remaining:])
            batch.extend((str(user_id), message) for message in take)
            remaining -= len(take)
            if rest:
                _dm_queue.appendleft((str(user_id), rest))
                break

    # Preserve user order while bundling all notifications selected for a user.
    groups: dict[str, list[tuple]] = {}
    for user_id, message in batch:
        groups.setdefault(user_id, []).append(message)

    sent = skipped = deferred = 0
    deferred_records: list[tuple[str, tuple]] = []
    group_list = list(groups.items())

    for group_index, (user_id, messages) in enumerate(group_list):
        eligible = []
        prefs = load(user_id)
        for message in messages:
            status = _delivery_status(user_id, message, prefs)
            if status == "skip":
                skipped += 1
            elif status == "quiet":
                deferred_records.append((user_id, message))
                deferred += 1
            else:
                eligible.append(message)
        if not eligible:
            continue

        chunks = _chunk_dm_messages(eligible, base_url)
        rate_limited = False
        for chunk_index, chunk in enumerate(chunks):
            # Preferences can change while an earlier recipient's DM is in flight.
            current_prefs = load(user_id)
            send_now = []
            for message in chunk:
                status = _delivery_status(user_id, message, current_prefs)
                if status == "skip":
                    skipped += 1
                elif status == "quiet":
                    deferred_records.append((user_id, message))
                    deferred += 1
                else:
                    send_now.append(message)
            if not send_now:
                continue

            actual_send_items = list(send_now)
            try:
                user = bot.get_user(int(user_id))
                if user is None:
                    fetch_user = getattr(bot, "fetch_user", None)
                    if not callable(fetch_user):
                        raise TypeError("User is not cached and fetch_user is unavailable")
                    user = await fetch_user(int(user_id))
                # A quiet period can begin while the API lookup is running.
                latest_prefs = load(user_id)
                still_allowed = []
                for message in send_now:
                    status = _delivery_status(user_id, message, latest_prefs)
                    if status == "skip":
                        skipped += 1
                    elif status == "quiet":
                        deferred_records.append((user_id, message))
                        deferred += 1
                    else:
                        still_allowed.append(message)
                actual_send_items = still_allowed
                if not actual_send_items:
                    continue
                await user.send(_render_dm(actual_send_items, base_url))
                sent += 1
            except discord.Forbidden:
                skipped += len(actual_send_items)  # DMs closed; inbox notifications remain
                # There is no point attempting remaining chunks for this user.
                skipped += sum(len(rest) for rest in chunks[chunk_index + 1:])
                break
            except discord.HTTPException as exc:
                if getattr(exc, "status", None) == 429:
                    unsent = [(user_id, message) for message in actual_send_items]
                    for rest in chunks[chunk_index + 1:]:
                        unsent.extend((user_id, message) for message in rest)
                    for later_user, later_messages in group_list[group_index + 1:]:
                        unsent.extend((later_user, message) for message in later_messages)
                    _requeue_front(unsent)
                    rate_limited = True
                    break
                skipped += len(actual_send_items)
            except (ValueError, TypeError):
                skipped += len(actual_send_items)

        if rate_limited:
            break

    _requeue_tail(deferred_records)
    return {"sent": sent, "skipped": skipped, "deferred": deferred, "pending": pending_dms()}
