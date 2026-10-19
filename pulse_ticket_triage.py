"""Explainable ticket triage: who is waiting on whom, since when, and what to do next.

Pure logic (no Discord/DB) so the rules are testable. Thresholds reuse the ticket
automation settings, so the dashboard and the reminders agree on what "overdue" means.
"""
from __future__ import annotations

from datetime import datetime, timezone

PRIORITY_ORDER = {"urgent": 0, "high": 1, "normal": 2, "low": 3}

STATE_LABELS = {
    "new": "Neu – wartet auf Übernahme",
    "waiting_team": "Wartet auf Team-Antwort",
    "waiting_user": "Wartet auf Ersteller",
    "in_progress": "In Bearbeitung",
    "closed": "Geschlossen",
}


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def format_wait(seconds: float | None) -> str:
    if seconds is None:
        return "unbekannt"
    minutes = max(0, int(seconds // 60))
    if minutes < 60:
        return f"{minutes} Min."
    hours = minutes // 60
    if hours < 48:
        return f"{hours} Std. {minutes % 60} Min." if hours < 6 else f"{hours} Std."
    return f"{hours // 24} Tagen"


def triage(ticket: dict, now: datetime, settings: dict | None = None) -> dict:
    """Classify one ticket. Missing activity data is reported as unknown, never guessed."""
    settings = settings or {}
    status = str(ticket.get("status") or "open")
    opened = _parse(ticket.get("opened_at"))
    last_user = _parse(ticket.get("last_user_message_at"))
    last_staff = _parse(ticket.get("last_staff_message_at"))
    claimed = bool(ticket.get("claimed_by_id"))
    priority = str(ticket.get("priority") or "normal")

    if status == "closed":
        state, since, limit, next_step = "closed", _parse(ticket.get("closed_at")), 0, "Bei Bedarf wieder öffnen oder Transkript ansehen."
    elif not claimed:
        state, since = "new", opened
        limit = int(settings.get("unclaimed_ping_minutes") or 30) * 60
        next_step = "Ticket übernehmen."
    elif last_user and (not last_staff or last_user > last_staff):
        state, since = "waiting_team", last_user
        limit = int(settings.get("staff_reply_hours") or 12) * 3600
        next_step = "Dem Ersteller antworten."
    elif last_staff and (not last_user or last_staff >= last_user):
        state, since = "waiting_user", last_staff
        limit = int(settings.get("user_reminder_hours") or 24) * 3600
        next_step = "Auf Antwort warten oder mit „Erinnern“ nachhaken."
    else:
        # Claimed, but no recorded messages yet (e.g. ticket older than activity tracking).
        state, since, limit = "in_progress", _parse(ticket.get("claimed_at")) or opened, 0
        next_step = "Anliegen im Ticket klären."

    waited = (now - since).total_seconds() if since else None
    overdue = bool(limit and waited is not None and waited >= limit)
    if state == "closed":
        reason = "Abgeschlossen."
    elif waited is None:
        reason = "Keine Zeitangabe vorhanden."
    else:
        reason = f"{STATE_LABELS[state]} seit {format_wait(waited)}"
        if overdue:
            reason += f" – länger als die Schwelle von {format_wait(limit)}"
    return {
        "state": state,
        "label": STATE_LABELS[state],
        "waited_seconds": waited,
        "overdue": overdue,
        "reason": reason,
        "next_step": next_step,
        # Overdue first, then priority, then the longest wait; closed tickets last.
        "sort_key": (
            1 if state == "closed" else 0,
            0 if overdue else 1,
            PRIORITY_ORDER.get(priority, 2),
            -(waited or 0),
        ),
    }


def matches_filter(ticket: dict, info: dict, view: str, actor_id: str) -> bool:
    """Dashboard tabs. Keys 'open'/'claimed'/'closed'/'all' stay compatible with existing redirects."""
    closed = info["state"] == "closed"
    if view == "all":
        return True
    if view == "closed":
        return closed
    if closed:
        return False
    if view == "open":
        return not ticket.get("claimed_by_id")
    if view == "claimed":
        return bool(ticket.get("claimed_by_id"))
    if view == "mine":
        return str(ticket.get("claimed_by_id") or "") == str(actor_id)
    if view == "waiting":
        return info["state"] in {"new", "waiting_team"}
    if view == "overdue":
        return info["overdue"]
    return True  # "active": every open ticket
