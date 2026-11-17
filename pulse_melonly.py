"""Pure helpers for Melonly search ranking and access decisions."""
from __future__ import annotations

import re
from datetime import datetime
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
    can_moderate_kicks: bool = False,
    can_moderate_bans: bool = False,
    can_moderate_bolo: bool = False,
    manager: bool = False,
) -> bool:
    """Check permission per action type; a warn permission never grants ban rights."""
    if manager:
        return log_type in VALID_LOG_TYPES
    permissions = {
        "Warn": can_warn,
        "Notiz": can_add_notes,
        "Kick": can_moderate_kicks,
        "Ban": can_moderate_bans,
        "Ban BOLO": can_moderate_bolo,
    }
    return bool(permissions.get(log_type, False))


# --------------------------------------------------------------------------- personal player file (Spielerakte)
_PLAYER_ID = re.compile(r"\d{1,15}")
_LOG_TIME_FORMAT = "%d.%m.%Y %H:%M"


def is_player_id(value: object) -> bool:
    """A usable Roblox player id (digits only); "N/A", empty or None are not."""
    return bool(_PLAYER_ID.fullmatch(str(value or "").strip()))


def parse_log_time(value: object) -> datetime | None:
    try:
        return datetime.strptime(str(value or "").strip(), _LOG_TIME_FORMAT)
    except ValueError:
        return None


def entry_matches_player(entry: Mapping[str, Any], roblox_id: str, username: str = "") -> str | None:
    """How a stored entry belongs to a player: "id", "name" or None.

    The Roblox player id is authoritative: an entry with a numeric id belongs to that id and to nobody else - even
    if it carries the same (possibly reused or changed) name. Only legacy entries WITHOUT a usable id ("N/A") are
    assigned by an exact, case-insensitive name match, because they cannot be assigned any other way."""
    if not isinstance(entry, Mapping) or not is_player_id(roblox_id):
        return None
    stored_id = str(entry.get("roblox_id") or "").strip()
    if is_player_id(stored_id):
        return "id" if stored_id == str(roblox_id).strip() else None
    name = _normalize_username(username)
    if name and _normalize_username(entry.get("target_user")) == name:
        return "name"
    return None


def build_player_file(rows: Iterable[Mapping[str, Any]], roblox_id: str, username: str = "") -> dict[str, Any]:
    """Personal record of exactly one player from already loaded rows (see entry_matches_player).

    Entries are returned newest first (by their recorded date, store order as tie-breaker); the counts use the
    stored action types: Ban + Ban BOLO together, Warn, Kick, Notiz."""
    matched: list[tuple[datetime, int, dict[str, Any], str]] = []
    for index, entry in enumerate(rows):
        how = entry_matches_player(entry, roblox_id, username)
        if how:
            matched.append((parse_log_time(entry.get("created_at")) or datetime.min, index, dict(entry), how))
    matched.sort(key=lambda item: (item[0], item[1]), reverse=True)
    entries = [item[2] for item in matched]
    types = [str(entry.get("type") or "Log") for entry in entries]
    current = _normalize_username(username)
    names: list[str] = []
    for entry in entries:  # earlier spellings of the same player id, newest first
        name = str(entry.get("target_user") or "").strip().lstrip("@")
        if name and _normalize_username(name) != current and name.casefold() not in {n.casefold() for n in names}:
            names.append(name)
    display = str(username or "").strip().lstrip("@") or (
        str(entries[0].get("target_user") or "").strip().lstrip("@") if entries else "")
    dated = [item[0] for item in matched if item[0] != datetime.min]
    return {
        "roblox_id": str(roblox_id).strip(),
        "username": display,
        "entries": entries,
        "counts": {
            "bans": sum(1 for t in types if t in ("Ban", "Ban BOLO")),
            "kicks": types.count("Kick"),
            "warns": types.count("Warn"),
            "notes": types.count("Notiz"),
            "total": len(entries),
        },
        "other_names": names[:10],
        "name_matched": sum(1 for item in matched if item[3] == "name"),
        "first_at": min(dated).strftime(_LOG_TIME_FORMAT) if dated else "",
        "last_at": max(dated).strftime(_LOG_TIME_FORMAT) if dated else "",
    }
