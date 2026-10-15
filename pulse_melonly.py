"""Pure helpers for Melonly search ranking and access decisions."""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Iterable, Mapping, Any

PUNISHMENT_LOG_TYPES = frozenset({"Warn", "Kick", "Ban", "Ban BOLO"})
VALID_LOG_TYPES = PUNISHMENT_LOG_TYPES | {"Notiz"}


def _normalize_username(value: object) -> str:
    return str(value or "").strip().lstrip("@").strip().lower()


def roblox_similarity_score(query: str, username: str) -> float:
    """Return a bounded matching score; exact and prefix matches win."""
    q = _normalize_username(query)
    name = _normalize_username(username)
    if not q or not name:
        return 0.0
    if q == name:
        return 1.0
    ratio = SequenceMatcher(None, q, name).ratio()
    score = ratio * 0.80
    if name.startswith(q):
        score += 0.15
    if q in name:
        score += 0.05
    return min(score, 0.99)


def rank_roblox_candidates(
    query: str,
    candidates: Iterable[Mapping[str, Any]],
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Deduplicate Roblox candidates and return the most similar names first."""
    q = _normalize_username(query)
    if not q:
        return []
    unique: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        name = str(candidate.get("name") or "").strip().lstrip("@")
        key = name.lower()
        if not key:
            continue
        normalized = {
            "id": str(candidate.get("id") or ""),
            "name": name,
            "displayName": str(candidate.get("displayName") or name),
        }
        existing = unique.get(key)
        if existing is None:
            unique[key] = normalized
        elif (not existing["id"].isdigit()) and normalized["id"].isdigit():
            unique[key] = normalized

    ranked = []
    for candidate in unique.values():
        score = roblox_similarity_score(q, candidate["name"])
        ranked.append((
            score,
            candidate["name"].lower() == q,
            candidate["name"].lower().startswith(q),
            {**candidate, "match_score": round(score * 100)},
        ))
    ranked.sort(key=lambda item: (item[1], item[0], item[2], item[3]["name"].lower()), reverse=True)
    try:
        safe_limit = max(1, min(int(limit), 20))
    except (TypeError, ValueError):
        safe_limit = 8
    return [item[3] for item in ranked[:safe_limit]]


def can_manage_melonly_type(
    log_type: str,
    *,
    can_warn: bool = False,
    can_add_notes: bool = False,
    manager: bool = False,
) -> bool:
    """Only moderation roles can manage matching case types; managers can manage all."""
    if manager:
        return log_type in VALID_LOG_TYPES
    if log_type == "Notiz":
        return bool(can_add_notes)
    return log_type in PUNISHMENT_LOG_TYPES and bool(can_warn)
