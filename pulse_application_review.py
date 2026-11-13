"""Audit #96/#97: reviewer assignment with load balancing and independent (blind) multi-reviewer evaluation.

Pure functions on the application records that already live in ``applications.json`` (no storage migration,
no new table): a record may carry ``assignments`` (who should review) next to its existing ``reviews``
(one entry per reviewer). Old records without these fields keep working.

Rules:
- Independence: a reviewer who is not leadership sees other people's ratings only after their own review is saved,
  so nobody is anchored by an earlier score. Leadership (``can_promote`` / admin) sees everything.
- A review counts as independent only if it is complete (all ratings stored) and belongs to a reviewer id.
- An optional minimum number of independent reviews can gate the final decision (policy, default off).
- Load = open applications (pending/interview) assigned to a person without a review of theirs yet.
- Suggestions are hints only; assigning stays a human action.
"""
from __future__ import annotations

from collections import Counter

MAX_ASSIGNED = 5
MAX_MIN_REVIEWS = 5
OPEN_STATUSES = {"pending", "interview"}
RECOMMENDATIONS = {"accept": "Einstellen", "reject": "Ablehnen", "unsure": "Unsicher"}
POLICY_KEY = "application_review_policy"


def _id(value) -> str:
    text = str(value or "").strip()
    return text if text.isdigit() and len(text) <= 25 else ""


def assignments(item: dict) -> list[dict]:
    raw = item.get("assignments") if isinstance(item, dict) else None
    result, seen = [], set()
    for entry in raw if isinstance(raw, list) else []:
        reviewer_id = _id(entry.get("reviewer_id")) if isinstance(entry, dict) else ""
        if reviewer_id and reviewer_id not in seen:
            seen.add(reviewer_id)
            result.append({"reviewer_id": reviewer_id, "reviewer_name": str(entry.get("reviewer_name") or "")[:80],
                           "assigned_at": str(entry.get("assigned_at") or ""), "assigned_by": str(entry.get("assigned_by") or "")[:80]})
    return result


def assign(item: dict, reviewers: list[tuple[str, str]], by_name: str, when: str) -> list[dict]:
    """Replace the assignment list (1..MAX_ASSIGNED distinct reviewers). Existing reviews are never touched."""
    clean, seen = [], set()
    for reviewer_id, name in reviewers:
        reviewer_id = _id(reviewer_id)
        if reviewer_id and reviewer_id not in seen:
            seen.add(reviewer_id)
            clean.append((reviewer_id, str(name or "")[:80]))
    if not clean:
        raise ValueError("Bitte mindestens eine Prüferin oder einen Prüfer auswählen.")
    if len(clean) > MAX_ASSIGNED:
        raise ValueError(f"Höchstens {MAX_ASSIGNED} Prüfer je Bewerbung.")
    previous = {entry["reviewer_id"]: entry for entry in assignments(item)}
    item["assignments"] = [
        previous.get(reviewer_id) or {"reviewer_id": reviewer_id, "reviewer_name": name, "assigned_at": when, "assigned_by": str(by_name)[:80]}
        for reviewer_id, name in clean
    ]
    return item["assignments"]


def completed_reviews(item: dict) -> dict[str, dict]:
    reviews = item.get("reviews") if isinstance(item, dict) else None
    result = {}
    for key, review in (reviews.items() if isinstance(reviews, dict) else []):
        ratings = review.get("ratings") if isinstance(review, dict) else None
        if _id(key) and isinstance(ratings, dict) and ratings:
            result[str(key)] = review
    return result


def workload(apps: dict, reviewer_ids) -> Counter:
    load = Counter({str(r): 0 for r in reviewer_ids})
    for item in (apps.values() if isinstance(apps, dict) else []):
        if not isinstance(item, dict) or item.get("status") not in OPEN_STATUSES:
            continue
        done = completed_reviews(item)
        for entry in assignments(item):
            if entry["reviewer_id"] in load and entry["reviewer_id"] not in done:
                load[entry["reviewer_id"]] += 1
    return load


def suggest(apps: dict, candidates, count: int = 2, exclude=()) -> list[str]:
    """The ``count`` least loaded candidates (ties: lower id first); excluded ids are skipped."""
    skip = {str(x) for x in exclude}
    pool = [str(c) for c in candidates if str(c) not in skip]
    load = workload(apps, pool)
    return sorted(pool, key=lambda reviewer_id: (load[reviewer_id], int(reviewer_id)))[: max(0, min(int(count), MAX_ASSIGNED))]


def visible_reviews(item: dict, viewer_id, leadership: bool) -> dict:
    """Reviews the viewer may see: everything for leadership, own review always, others' only after submitting."""
    done = completed_reviews(item)
    if leadership or str(viewer_id) in done:
        return done
    return {}


def progress(item: dict) -> dict:
    """Assigned vs. completed reviews and the tally of recommendations (counts only, no names)."""
    done = completed_reviews(item)
    assigned = [entry["reviewer_id"] for entry in assignments(item)]
    tally = Counter(str(review.get("recommendation")) for review in done.values() if review.get("recommendation") in RECOMMENDATIONS)
    return {"assigned": len(assigned), "completed": len(done),
            "missing": [reviewer_id for reviewer_id in assigned if reviewer_id not in done], "tally": dict(tally)}


def normalize_recommendation(value) -> str | None:
    text = str(value or "").strip()
    return text if text in RECOMMENDATIONS else None


def normalize_policy(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    try:
        minimum = int(raw.get("min_reviews") or 0)
    except (TypeError, ValueError):
        minimum = 0
    return {"min_reviews": minimum if 0 <= minimum <= MAX_MIN_REVIEWS else 0}


def decision_gate(item: dict, policy: dict) -> str:
    """Empty string = decision allowed; otherwise a short reason."""
    minimum = normalize_policy(policy)["min_reviews"]
    have = len(completed_reviews(item))
    if minimum and have < minimum:
        return f"Für die Entscheidung fehlen unabhängige Bewertungen ({have} von {minimum})."
    return ""
