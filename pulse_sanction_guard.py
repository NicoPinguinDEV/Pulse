"""Rate limit for Pulse-executed kicks and bans per moderator (audit finding DISC-6).

Anti-Nuke trusts the bot's own user id, so a mass of kicks/bans that Pulse executes *for* a moderator
(`/mod ban`, AI review, second-approval server exit) shows up in the Discord audit log as "the bot" and is
never counted. This guard closes that gap at the source: a moderator may trigger only a few kicks/bans in a
sliding window; further attempts are refused, written to the Pulse audit and reported.

In-memory and bounded (RAM budget): at most ``MAX_TRACKED`` moderators are tracked, entries older than the
window are dropped. A restart resets the counters, which only makes the limit more permissive for one window.
The guild owner is exempt (the owner cannot be stopped on Discord either).
"""

from __future__ import annotations

import threading
import time
from collections import deque

LIMIT = 5            # kicks/bans per moderator ...
WINDOW_SECONDS = 600  # ... within ten minutes
MAX_TRACKED = 500

_LOCK = threading.Lock()
_events: dict[tuple[str, str], deque] = {}


def _prune(now: float) -> None:
    for key in [k for k, q in _events.items() if not q or now - q[-1] > WINDOW_SECONDS]:
        _events.pop(key, None)


def reserve(guild_id, actor_id, action: str, *, owner_id=None, now: float | None = None,
            limit: int = LIMIT, window: int = WINDOW_SECONDS) -> tuple[bool, str]:
    """Count one kick/ban for ``actor_id``. Returns (allowed, message); a refusal records an audit entry."""
    if owner_id is not None and str(owner_id) == str(actor_id):
        return True, ""
    current = time.monotonic() if now is None else now
    key = (str(guild_id), str(actor_id))
    with _LOCK:
        if len(_events) >= MAX_TRACKED and key not in _events:
            _prune(current)
        queue = _events.setdefault(key, deque())
        while queue and current - queue[0] > window:
            queue.popleft()
        if len(queue) >= limit:
            blocked = len(queue)
        else:
            queue.append(current)
            return True, ""
    _report(guild_id, actor_id, action, blocked, window)
    return False, (f"Sicherheitsgrenze: Du hast in den letzten {window // 60} Minuten bereits {blocked} Kicks/Bans "
                   "ausgelöst. Weitere Maßnahmen sind vorübergehend gesperrt; die Serverleitung wurde im Audit "
                   "informiert.")


def release(guild_id, actor_id) -> None:
    """Give back the reservation when the action was not carried out (e.g. Discord refused)."""
    with _LOCK:
        queue = _events.get((str(guild_id), str(actor_id)))
        if queue:
            queue.pop()


def reset() -> None:
    with _LOCK:
        _events.clear()


def _report(guild_id, actor_id, action, count, window) -> None:
    try:
        import webserver
        webserver.log_audit(
            "Pulse Sanktionsgrenze", str(actor_id), "Massen-Sanktion blockiert",
            f"{action} abgelehnt: {count} Kicks/Bans in {window // 60} Min. durch {actor_id} (Guild {guild_id})",
        )
    except Exception as exc:  # noqa: BLE001 - reporting must never turn a refusal into an error
        print(f"⚠️ Sanktionsgrenze: Audit-Eintrag fehlgeschlagen ({type(exc).__name__}).")
