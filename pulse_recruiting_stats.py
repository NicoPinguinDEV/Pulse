"""Audit #102: recruiting statistics - funnel, conversion, waiting/processing times and reviewer workload.

Pure, read-only functions on the records of ``applications.json`` (no new storage). Timestamps are the stored
German format ``dd.mm.YYYY HH:MM`` (naive local time) or ISO; unparseable or missing values are skipped, never
guessed, so every average states how many applications it is based on (``n``). Counts are facts; averages are
indicators, not a performance rating of people.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median

import pulse_application_review as review

STATUSES = ("pending", "interview", "hired", "rejected")
_FORMATS = (("%d.%m.%Y %H:%M", 16), ("%d.%m.%Y %H:%M:%S", 19), ("%Y-%m-%dT%H:%M:%S", 19), ("%Y-%m-%dT%H:%M", 16),
            ("%Y-%m-%d %H:%M:%S", 19))


def parse(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt, length in _FORMATS:
        try:
            return datetime.strptime(text[:length], fmt)
        except ValueError:
            continue
    return None


def _hours(start, end) -> float | None:
    a, b = parse(start), parse(end)
    if a is None or b is None or b < a:
        return None
    return (b - a).total_seconds() / 3600


def _summary(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "avg_h": None, "median_h": None, "max_h": None}
    return {"n": len(values), "avg_h": round(sum(values) / len(values), 1), "median_h": round(median(values), 1),
            "max_h": round(max(values), 1)}


def _pct(a, b):
    return round(100 * a / b, 1) if b else None


def compute(apps: dict, now: datetime | None = None, days: int | None = None) -> dict:
    """Funnel, conversion, durations and reviewer table. ``days`` limits to applications created within that window
    (applications without a readable creation time are then excluded)."""
    now = now or datetime.now()
    items = [(k, v) for k, v in (apps or {}).items() if isinstance(v, dict)]
    if days:
        kept = []
        for key, value in items:
            created = parse(value.get("created_at"))
            if created is not None and 0 <= (now - created).days <= days:
                kept.append((key, value))
        items = kept
    status = {s: 0 for s in STATUSES}
    other = 0
    to_invite, to_decision, interview_to_decision, open_ages = [], [], [], []
    reviewers: dict[str, dict] = {}
    reached_interview = 0
    now_text = now.strftime("%d.%m.%Y %H:%M")
    for _key, item in items:
        st = str(item.get("status") or "")
        if st in status:
            status[st] += 1
        else:
            other += 1
        if st in ("interview", "hired") or (st == "rejected" and item.get("interview_invited_at")):
            reached_interview += 1
        h = _hours(item.get("created_at"), item.get("interview_invited_at"))
        if h is not None:
            to_invite.append(h)
        if st in ("hired", "rejected"):
            h = _hours(item.get("created_at"), item.get("decided_at"))
            if h is not None:
                to_decision.append(h)
            h = _hours(item.get("interview_invited_at"), item.get("decided_at"))
            if h is not None:
                interview_to_decision.append(h)
        elif st in review.OPEN_STATUSES:
            h = _hours(item.get("created_at"), now_text)
            if h is not None:
                open_ages.append(h)
        done = review.completed_reviews(item)
        for a in review.assignments(item):
            row = reviewers.setdefault(a["reviewer_id"], {"name": a.get("reviewer_name") or a["reviewer_id"],
                                                         "assigned": 0, "reviewed": 0})
            row["assigned"] += 1
            if a["reviewer_id"] in done:
                row["reviewed"] += 1
    decided = status["hired"] + status["rejected"]
    total = len(items)
    table = sorted(({"id": k, **v, "completion_pct": _pct(v["reviewed"], v["assigned"])} for k, v in reviewers.items()),
                   key=lambda r: (-r["assigned"], r["name"]))
    return {"total": total, "status": status, "other": other, "decided": decided,
            "conversion": {"interview_pct": _pct(reached_interview, total),
                           "hired_of_decided_pct": _pct(status["hired"], decided),
                           "hired_of_total_pct": _pct(status["hired"], total)},
            "durations": {"to_invite": _summary(to_invite), "to_decision": _summary(to_decision),
                          "interview_to_decision": _summary(interview_to_decision), "open_age": _summary(open_ages)},
            "reviewers": table, "days": days}


PENDING_DAYS = 3
INTERVIEW_DAYS = 7


def stale_applications(apps: dict, now: datetime | None = None, pending_days: int = PENDING_DAYS,
                       interview_days: int = INTERVIEW_DAYS) -> list[dict]:
    """#101: open applications nobody has dealt with for too long, oldest first.

    ``pending`` = created more than ``pending_days`` ago and still unreviewed; ``interview`` = invited more than
    ``interview_days`` ago without a decision. Each entry names who should act: the assigned reviewers who have
    not yet saved a complete review (``reviewers``; empty = nobody assigned, then only the statistics page shows it)."""
    now = now or datetime.now()
    out = []
    for app_id, item in (apps or {}).items():
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        if status == "pending":
            since, limit = parse(item.get("created_at")), pending_days
        elif status == "interview":
            since, limit = parse(item.get("interview_invited_at") or item.get("created_at")), interview_days
        else:
            continue
        if since is None or (now - since).total_seconds() < limit * 86400:
            continue
        done = review.completed_reviews(item)
        who = [a["reviewer_id"] for a in review.assignments(item) if a["reviewer_id"] not in done]
        out.append({"id": str(app_id), "name": str(item.get("name") or app_id), "status": status,
                    "days": int((now - since).total_seconds() // 86400), "reviewers": who})
    return sorted(out, key=lambda r: -r["days"])
