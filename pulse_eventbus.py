"""Audit #117: real-time sync of both surfaces on top of the durable ``events`` table.

Design: the ``events`` table (SQLite ``rowid`` as cursor) is the single source of truth, so nothing is lost
on a restart and *every* writer is covered – ``pulse_events.emit`` as well as the 33 legacy
``record_event`` callers. Two transports read the same cursor:

* :func:`poll` – plain request/response. This is the guaranteed fallback and works without any streaming.
* :func:`stream` – Server-Sent-Events generator. It wakes within ~0.25 s after ``pulse_events.emit`` (via
  :func:`bump`) and otherwise re-checks the table every few seconds, so legacy writers arrive with a short
  delay instead of never. A client that loses the stream reconnects with ``Last-Event-ID`` (the cursor) or
  falls back to :func:`poll` with the same cursor – both give the same events.

Security: events are filtered per viewer (:func:`visible_to`, fail-closed, built on ``pulse_policy``) and only
a minimal summary leaves the server (:func:`public_view`) – never the payload, which can contain reasons,
errors or personal data. The cursor advances over events a viewer may not see, so nobody is stuck on them.

Not wired into any route yet (a route needs ROUTEN.md/route-snapshot updates that belong to the integration
step); everything here is testable without Discord or a browser.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pulse_case_registry as registry
import pulse_events as events
import pulse_inbox_contract as inbox
import pulse_policy as policy

SCAN_MAX = 500            # rows read per poll; keeps one request bounded
POLL_LIMIT_DEFAULT = 100
STREAM_INTERVAL = 3.0     # seconds between table checks when nothing woke the stream
WAKE_STEP = 0.25
HEARTBEAT_SECONDS = 15.0
STREAM_MAX_SECONDS = 300.0
MAX_STREAMS_TOTAL = 50
MAX_STREAMS_PER_USER = 3

# Domain -> policy action a viewer needs to see that domain's events. Everything not listed is admin-only.
DOMAIN_ACTION = {
    "ticket": "ticket.manage",
    "moderation": "case.view.BM",
    "task": "task.manage",
    "handover": "handover.manage",
    "announcement": "announcement.manage",
    "promotion": "promotion.manage",
    "recruiting": "application.manage",
    "team": "moderation.warn",
}

_version = 0
_version_lock = threading.Lock()
_slots_lock = threading.Lock()
_slots_total = 0
_slots_user: dict[str, int] = {}


def bump() -> None:
    """Called after a contract event was stored; wakes running streams. Never raises."""
    global _version
    with _version_lock:
        _version += 1


def _current_version() -> int:
    with _version_lock:
        return _version


# --------------------------------------------------------------------------- visibility

def visible_to(item: dict, perms) -> bool:
    """May this viewer see this described event? Fail-closed; a case reference also needs its case right."""
    if not isinstance(perms, dict):
        return False
    if policy.can(perms, "admin.area"):
        return True
    action = DOMAIN_ACTION.get(item.get("domain"))
    if action is None or not policy.can(perms, action):
        return False
    ref = item.get("case_ref")
    if ref:
        parsed = registry.parse_ref(ref)
        if not parsed or parsed[0] not in registry.allowed_kinds(perms):
            return False
    return True


def public_view(item: dict) -> dict:
    """What a client receives: identifiers and a link, no payload and no actor name."""
    return {
        "id": item["id"], "seq": item["seq"], "at": item["at"],
        "domain": item["domain"], "domain_label": item["domain_label"], "action": item["action"],
        "target_type": item["target_type"], "target_id": item["target_id"],
        "case_ref": item["case_ref"], "correlation_id": item["correlation_id"],
        "url": inbox.link_for(case_ref=item["case_ref"] or None, domain=item["domain"], target_id=item["target_id"]),
    }


# --------------------------------------------------------------------------- polling (guaranteed fallback)

def _max_seq(db) -> int:
    row = db.execute("SELECT COALESCE(MAX(rowid), 0) FROM events").fetchone()
    return int(row[0])


def poll(perms, cursor=None, limit: int = POLL_LIMIT_DEFAULT) -> dict:
    """Events after ``cursor`` that ``perms`` may see: ``{"events", "cursor", "reset"}``.

    ``cursor=None`` starts live at the current end (no replay). A cursor beyond the end of the table (table
    emptied/restored) is clamped and reported as ``reset`` so the client reloads its state instead of
    waiting for events that will never come.
    """
    import pulse_db
    limit = max(1, min(int(limit), POLL_LIMIT_DEFAULT * 5))
    with pulse_db.connect() as db:
        end = _max_seq(db)
        if cursor is None:
            return {"events": [], "cursor": end, "reset": False}
        try:
            cursor = max(0, int(cursor))
        except (TypeError, ValueError):
            return {"events": [], "cursor": end, "reset": True}
        if cursor > end:
            return {"events": [], "cursor": end, "reset": True}
        rows = db.execute("SELECT rowid AS _seq, * FROM events WHERE rowid > ? ORDER BY rowid LIMIT ?",
                          (cursor, SCAN_MAX)).fetchall()
    out: list[dict] = []
    last = cursor
    for row in rows:
        row = dict(row)
        last = int(row["_seq"])
        item = events.describe(row)
        item["seq"] = last
        if visible_to(item, perms):
            out.append(public_view(item))
            if len(out) >= limit:
                break
    return {"events": out, "cursor": last, "reset": False}


# --------------------------------------------------------------------------- streaming (SSE)

def sse_frame(name: str, data, event_id=None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {name}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def acquire_slot(user_id) -> bool:
    """Reserve one stream connection (global and per-user caps); pair with :func:`release_slot`."""
    global _slots_total
    uid = str(user_id)
    with _slots_lock:
        if _slots_total >= MAX_STREAMS_TOTAL or _slots_user.get(uid, 0) >= MAX_STREAMS_PER_USER:
            return False
        _slots_total += 1
        _slots_user[uid] = _slots_user.get(uid, 0) + 1
        return True


def release_slot(user_id) -> None:
    global _slots_total
    uid = str(user_id)
    with _slots_lock:
        if _slots_user.get(uid, 0) > 0:
            _slots_user[uid] -= 1
            if _slots_user[uid] == 0:
                del _slots_user[uid]
            _slots_total = max(0, _slots_total - 1)


async def stream(perms, cursor, is_disconnected, *, max_seconds: float = STREAM_MAX_SECONDS,
                 interval: float = STREAM_INTERVAL, sleep=asyncio.sleep, clock=time.monotonic):
    """SSE generator. Ends by itself after ``max_seconds`` (client reconnects with the last cursor)."""
    started = last_beat = clock()
    seen_version = _current_version()
    next_check = started
    yield "retry: 3000\n\n"
    first = poll(perms, cursor)
    cursor = first["cursor"]
    yield sse_frame("reset" if first["reset"] else "ready", {"cursor": cursor}, cursor)
    for item in first["events"]:
        yield sse_frame("pulse", item, item["seq"])
    while clock() - started < max_seconds:
        if await is_disconnected():
            return
        now = clock()
        version = _current_version()
        if version != seen_version or now >= next_check:
            seen_version = version
            next_check = now + interval
            batch = await asyncio.to_thread(poll, perms, cursor)
            cursor = batch["cursor"]
            if batch["reset"]:
                yield sse_frame("reset", {"cursor": cursor}, cursor)
            for item in batch["events"]:
                yield sse_frame("pulse", item, item["seq"])
                last_beat = clock()
        if clock() - last_beat >= HEARTBEAT_SECONDS:
            yield ": ping\n\n"
            last_beat = clock()
        await sleep(WAKE_STEP)
    yield sse_frame("expired", {"cursor": cursor}, cursor)
