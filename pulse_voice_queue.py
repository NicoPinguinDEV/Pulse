"""BOT-07: voice-support queue position, wait estimate, closed-case history and supporter statistics.

Pure helpers; cogs/voice_support.py stores the history when a case closes and tells the citizen their
position. The history is bounded (HISTORY_MAX) and lives in pulse.db next to the existing cases.
"""
from __future__ import annotations

import statistics
from datetime import datetime

HISTORY_KEY = "voice_support_history"
HISTORY_MAX = 1000
ESTIMATE_SAMPLE = 30  # last claimed cases used for the wait estimate


def _ts(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def waiting_cases(cases: dict) -> list[dict]:
    """Unclaimed cases in the shared waiting room, oldest first."""
    rows = [c for c in (cases or {}).values() if isinstance(c, dict) and c.get("waiting_in_shared_room") and not c.get("assigned_staff_id")]
    return sorted(rows, key=lambda c: str(c.get("created_at") or ""))


def queue_position(cases: dict, user_id) -> int | None:
    for index, case in enumerate(waiting_cases(cases), 1):
        if str(case.get("user_id")) == str(user_id):
            return index
    return None


def typical_wait_seconds(history: list[dict]) -> float | None:
    """Median time from opening to being taken over, over the last claimed cases."""
    waits = []
    for item in reversed(history or []):
        start, taken = _ts(item.get("created_at")), _ts(item.get("support_started_at"))
        if start and taken and taken >= start:
            waits.append((taken - start).total_seconds())
        if len(waits) >= ESTIMATE_SAMPLE:
            break
    return statistics.median(waits) if waits else None


def estimate_text(position: int | None, history: list[dict], staff_on_duty: int) -> str:
    if not position:
        return ""
    typical = typical_wait_seconds(history)
    if typical is None:
        return f"Du bist **Nr. {position}** in der Warteschlange."
    minutes = max(1, round(typical * position / max(1, staff_on_duty) / 60))
    return f"Du bist **Nr. {position}** in der Warteschlange – geschätzte Wartezeit etwa **{minutes} Min.**"


def history_entry(case: dict, reason: str, closed_at: str) -> dict:
    return {k: case.get(k) for k in ("case_id", "user_id", "user_name", "assigned_staff_id", "assigned_staff_name",
                                      "created_at", "support_started_at", "outside_opening_hours")} | {
        "closed_at": closed_at, "reason": str(reason or "")[:200]}


def append_history(history: list, entry: dict) -> list:
    history = [h for h in (history if isinstance(history, list) else []) if isinstance(h, dict)]
    history.append(entry)
    return history[-HISTORY_MAX:]


def supporter_stats(history: list[dict], ratings: list[dict]) -> list[dict]:
    """Per supporter: handled cases, median wait until takeover, median duration, average rating."""
    rating_by_case = {str(r.get("case_id")): int(r.get("rating") or 0) for r in (ratings or []) if isinstance(r, dict) and r.get("rating")}
    per: dict[str, dict] = {}
    for item in history or []:
        staff = str(item.get("assigned_staff_id") or "")
        if not staff or staff == "0":
            continue
        row = per.setdefault(staff, {"staff_id": staff, "name": item.get("assigned_staff_name") or staff, "cases": 0,
                                     "waits": [], "durations": [], "ratings": []})
        row["cases"] += 1
        start, taken, closed = _ts(item.get("created_at")), _ts(item.get("support_started_at")), _ts(item.get("closed_at"))
        if start and taken and taken >= start:
            row["waits"].append((taken - start).total_seconds())
        if taken and closed and closed >= taken:
            row["durations"].append((closed - taken).total_seconds())
        if str(item.get("case_id")) in rating_by_case:
            row["ratings"].append(rating_by_case[str(item.get("case_id"))])
    out = []
    for row in per.values():
        out.append({"staff_id": row["staff_id"], "name": row["name"], "cases": row["cases"],
                    "median_wait_min": round(statistics.median(row["waits"]) / 60, 1) if row["waits"] else None,
                    "median_duration_min": round(statistics.median(row["durations"]) / 60, 1) if row["durations"] else None,
                    "rating": round(sum(row["ratings"]) / len(row["ratings"]), 2) if row["ratings"] else None,
                    "rated": len(row["ratings"])})
    return sorted(out, key=lambda r: (-r["cases"], str(r["name"])))
