"""Audit #16: explainable repeat-offence hint across all case files.

Counts the cases of one person created within a time window, over every case file the viewer may see
(``pulse_case_registry.person_history``). Revoked and failed cases and cases whose appeal was accepted do not
count. The result is a hint for the moderator, never a sanction: it lists the counted cases and the rule
that produced the level, and it says which case files were not visible or readable (so a low count is never
mistaken for a clean record).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pulse_case_registry as reg

WINDOW_DAYS = 90
REPEAT_AT = 2
MULTIPLE_AT = 3
NOT_COUNTED_STATUS = {"revoked", "failed"}
LEVELS = {"none": "kein Hinweis", "repeat": "Wiederholung", "multiple": "mehrfach auffällig"}


def level_for(count: int) -> str:
    if count >= MULTIPLE_AT:
        return "multiple"
    return "repeat" if count >= REPEAT_AT else "none"


def repeat_hint(user_id, perms, *, guild_id=None, now: datetime | None = None, window_days: int = WINDOW_DAYS) -> dict:
    now = now or datetime.now(timezone.utc)
    try:
        window_days = max(1, min(int(window_days), 365))
    except (TypeError, ValueError):
        window_days = WINDOW_DAYS
    since = now - timedelta(days=window_days)
    history = reg.person_history(user_id, perms, guild_id=guild_id, limit=500)
    counted: dict[str, dict] = {}
    for entry in history["entries"]:
        if entry.get("event") != "created" or entry["status"] in NOT_COUNTED_STATUS or entry.get("appeal_status") == "accepted":
            continue
        created = reg._ts(entry.get("at"))
        if created is None or created < since or created > now + timedelta(minutes=5):
            continue
        counted.setdefault(entry["ref"], entry)
    by_kind: dict[str, int] = {}
    for entry in counted.values():
        by_kind[entry["kind"]] = by_kind.get(entry["kind"], 0) + 1
    count = len(counted)
    level = level_for(count)
    refs = [e["ref"] for e in sorted(counted.values(), key=lambda e: e.get("at") or "", reverse=True)]
    limits = []
    if len(history["kinds"]) < len(reg.KIND_ORDER):
        limits.append("nur Fallarten, für die du berechtigt bist")
    if history["unavailable"]:
        limits.append("nicht lesbar: " + ", ".join(reg.KINDS[k] for k in history["unavailable"]))
    text = (
        f"{LEVELS[level]}: {count} Fälle in {window_days} Tagen" if level != "none"
        else f"Kein Wiederholungshinweis ({count} Fall{'' if count == 1 else 'e'} in {window_days} Tagen)"
    )
    return {
        "level": level, "level_label": LEVELS[level], "count": count, "window_days": window_days,
        "by_kind": {reg.KINDS[k]: n for k, n in by_kind.items()}, "refs": refs[:10], "text": text,
        "rule": f"ab {REPEAT_AT} Fällen Wiederholung, ab {MULTIPLE_AT} mehrfach; widerrufene, fehlgeschlagene und erfolgreich "
                f"widersprochene Fälle zählen nicht. Nur ein Hinweis, keine automatische Maßnahme.",
        "limits": limits,
    }
