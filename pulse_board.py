"""F-07: applications as a board – Eingang → Prüfung → Gespräch → Probezeit → Team / Abgelehnt.

Pure and read-only: the stage is derived from the existing application fields (no new status values, so
the decision flow with its Discord roles, DMs and audit stays the only place that changes an application).
Deadlines per stage are configurable; an application that waits longer is marked overdue.
"""
from __future__ import annotations

from datetime import datetime

from pulse_time import parse_timestamp

STAGES = (
    ("eingang", "📥 Eingang"), ("pruefung", "🔎 Prüfung"), ("gespraech", "🗣 Gespräch"),
    ("probezeit", "🧪 Probezeit"), ("team", "✅ Team"), ("abgelehnt", "✖ Abgelehnt"),
)
DEADLINES_KEY = "application_board_deadlines"
DEFAULT_DEADLINES = {"eingang": 3, "pruefung": 5, "gespraech": 7}
FINISHED_VISIBLE_DAYS = 30


def normalize_deadlines(raw) -> dict:
    out = dict(DEFAULT_DEADLINES)
    for key in DEFAULT_DEADLINES:
        try:
            value = int((raw or {}).get(key, out[key]))
        except (TypeError, ValueError, AttributeError):
            continue
        out[key] = max(0, min(value, 90))
    return out


def stage_of(item: dict, team_entry: dict | None) -> tuple[str, str]:
    """(stage, timestamp the stage began)."""
    status = str(item.get("status") or "")
    if status == "pending":
        reviewed = item.get("upvotes") or item.get("downvotes") or item.get("reviews")
        return ("pruefung" if reviewed else "eingang"), str(item.get("created_at") or "")
    if status == "interview":
        return "gespraech", str(item.get("interview_invited_at") or item.get("created_at") or "")
    if status in {"hired", "accepted"}:
        probation = (team_entry or {}).get("probation")
        if isinstance(probation, dict) and probation.get("active"):
            return "probezeit", str(probation.get("started_at") or item.get("decided_at") or "")
        return "team", str(item.get("decided_at") or "")
    if status == "rejected":
        return "abgelehnt", str(item.get("decided_at") or "")
    return "", ""


def board(apps: dict, team_db: dict, deadlines: dict, now: datetime) -> dict:
    """{stage: [card, ...]} with cards {"id", "name", "career", "days", "overdue", "deadline"} – oldest first."""
    columns: dict[str, list] = {key: [] for key, _ in STAGES}
    for app_id, item in (apps or {}).items():
        if not isinstance(item, dict):
            continue
        stage, since = stage_of(item, (team_db or {}).get(str(item.get("user_id"))))
        if not stage:
            continue
        began = parse_timestamp(since)
        days = (now - began).days if began else None
        if stage in {"team", "abgelehnt"} and (days is None or days > FINISHED_VISIBLE_DAYS):
            continue  # finished cases stay in the history view of /applications
        limit = deadlines.get(stage)
        columns[stage].append({
            "id": str(app_id), "name": str(item.get("name") or item.get("user_id") or app_id),
            "career": str(item.get("career_label") or ""), "days": days, "deadline": limit,
            "overdue": bool(limit and days is not None and days > limit),
            "interview_at": str(item.get("interview_at") or ""),
        })
    for cards in columns.values():
        cards.sort(key=lambda c: -(c["days"] if c["days"] is not None else -1))
    return columns
