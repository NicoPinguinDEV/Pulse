"""F-07 / Audit #95: applications as a board – Eingang → Prüfung → Gespräch → Probezeit → Team / Abgelehnt.

Pure logic, no writes: the stage is derived from the existing application fields (no new status values), and
every card only *offers* the actions of the one existing decision flow (``POST /action`` ``decide_app`` with its
Discord roles, DMs, lock, conflict check and audit). A column is never a status of its own, so moving work
forward can never bypass a required review, release or interview decision.
Deadlines per stage are configurable; an application that waits longer is marked overdue.
"""
from __future__ import annotations

from datetime import datetime

import pulse_application_review as app_review
from pulse_time import parse_timestamp

STAGES = (
    ("eingang", "📥 Eingang"), ("pruefung", "🔎 Prüfung"), ("gespraech", "🗣 Gespräch"),
    ("probezeit", "🧪 Probezeit"), ("team", "✅ Team"), ("abgelehnt", "✖ Abgelehnt"),
)
DEADLINES_KEY = "application_board_deadlines"
DEFAULT_DEADLINES = {"eingang": 3, "pruefung": 5, "gespraech": 7}
FINISHED_VISIBLE_DAYS = 30
RETURN_PATH = "/applications/board"

# The single source of truth for allowed workflow transitions (used by the board *and* by ``decide_app``):
# status -> decision -> (label, target stage). "accept" is only the invitation to the mandatory interview.
DECISIONS = {
    "pending": {"accept": ("🎙️ Zum Gespräch einladen", "gespraech"), "reject": ("Ablehnen", "abgelehnt")},
    "interview": {"hire_after_interview": ("✅ Gespräch geführt – einstellen", "team"),
                  "reject_after_interview": ("Nach Gespräch ablehnen", "abgelehnt")},
}


def allowed_decisions(status) -> set[str]:
    return set(DECISIONS.get(str(status or ""), {}))


def card_actions(item: dict, policy: dict, can_decide: bool) -> dict:
    """What a card may offer: decisions (with the gate reason that blocks them, if any) – never more than the server accepts."""
    status = str(item.get("status") or "")
    decisions = DECISIONS.get(status, {})
    if not can_decide or not decisions:
        return {"decisions": [], "blocked": ""}
    return {"decisions": [{"decision": key, "label": label, "target": target} for key, (label, target) in decisions.items()],
            "blocked": app_review.decision_gate(item, policy),
            "needs_role": status == "interview"}


def stale_reason(item: dict, expected_status) -> str:
    """Empty string = the user acts on what they saw; otherwise a message (somebody else changed the application first)."""
    expected = str(expected_status or "").strip()
    current = str(item.get("status") or "")
    if expected and expected != current:
        return f"Die Bewerbung wurde inzwischen bearbeitet (Status jetzt: {current or 'unbekannt'}). Bitte das Board neu laden."
    return ""


def safe_return(value, fallback: str = "/applications") -> str:
    """Only the board itself may be the return target of an action – never a free redirect."""
    return RETURN_PATH if str(value or "") == RETURN_PATH else fallback


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


def board(apps: dict, team_db: dict, deadlines: dict, now: datetime, *, guild_id=None, policy: dict | None = None,
          can_decide: bool = False, leadership: bool = False) -> dict:
    """{stage: [card, ...]} – oldest first. Cards: id, name, career, days, overdue, deadline, interview_at, status,
    reviewers (names), assigned/completed counts, votes, actions. Records of another guild are skipped."""
    columns: dict[str, list] = {key: [] for key, _ in STAGES}
    for app_id, item in (apps or {}).items():
        if not isinstance(item, dict):
            continue
        if guild_id is not None and item.get("guild_id") not in (None, "") and str(item.get("guild_id")) != str(guild_id):
            continue  # guild isolation: foreign records are neither shown nor actionable
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
            "interview_at": str(item.get("interview_at") or ""), "status": str(item.get("status") or ""),
            "user_id": str(item.get("user_id") or ""), "stage": stage,
            "reviewers": [entry["reviewer_name"] or entry["reviewer_id"] for entry in app_review.assignments(item)],
            "reviewer_ids": [entry["reviewer_id"] for entry in app_review.assignments(item)],
            "completed": len(app_review.completed_reviews(item)), "assigned": len(app_review.assignments(item)),
            "votes_up": len(item.get("upvotes") or []), "votes_down": len(item.get("downvotes") or []),
            "decided_by": str(item.get("decided_by") or ""),
            "actions": card_actions(item, policy or {}, can_decide),
            "unassigned": status_open(item) and not app_review.assignments(item),
        })
    for cards in columns.values():
        cards.sort(key=lambda c: -(c["days"] if c["days"] is not None else -1))
    return columns


def status_open(item: dict) -> bool:
    return str(item.get("status") or "") in app_review.OPEN_STATUSES


def filter_columns(columns: dict, query: str = "", mode: str = "", viewer_id="") -> dict:
    """Search (name, career, id, reviewer) and one quick filter: overdue | mine | unassigned. Unknown mode = no filter."""
    needle = str(query or "").strip().lower()[:80]
    viewer = str(viewer_id or "")
    result = {}
    for key, cards in columns.items():
        kept = []
        for card in cards:
            if needle and needle not in " ".join([card["name"], card["career"], card["id"], card["user_id"], *card["reviewers"]]).lower():
                continue
            if mode == "overdue" and not card["overdue"]:
                continue
            if mode == "mine" and viewer not in card["reviewer_ids"]:
                continue
            if mode == "unassigned" and not card["unassigned"]:
                continue
            kept.append(card)
        result[key] = kept
    return result
