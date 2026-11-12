"""Audit #24: read-only moderation statistics per Discord guild.

Sources (all existing, nothing new is stored):
- ``mod_cases``: citizen moderation via /mod (type, result, DM delivery, proof, reason templates, duration).
- ``events`` of type "AutoMod-Maßnahme" / "AutoMod-Testtreffer": real AutoMod actions and simulated test hits.
- ``automod_false_positive_feedback``: reviewed false positives and the time until that review.

Guarantees: every figure is computed from stored rows of exactly one guild and one period. Rows without a
guild, with a foreign guild or with an unreadable timestamp are ignored, never guessed. Test hits never count
as real AutoMod actions. When the database cannot be read the result says so instead of showing zeros.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median

import pulse_db

PERIODS = (7, 30, 90)
DEFAULT_PERIOD = 30
ACTION_LABELS = {"warn": "Verwarnung", "timeout": "Timeout", "kick": "Kick", "ban": "Ban"}
STATUS_LABELS = {"active": "aktiv", "expired": "abgelaufen", "failed": "fehlgeschlagen"}
REPEAT_THRESHOLD = 2
MAX_ROWS = 20000  # hard cap per source keeps memory bounded on the 2-GiB VPS


def normalize_period(value) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PERIOD
    return days if days in PERIODS else DEFAULT_PERIOD


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _payload(raw) -> dict:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _rate(part: int, whole: int) -> float | None:
    return round(part * 100 / whole, 1) if whole else None


def _hours(values: list[float]) -> float | None:
    return round(median(values), 1) if values else None


def _delta(current: int, previous: int) -> int:
    return current - previous


def _table_exists(db, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _load(guild_id: str, since: datetime) -> tuple[list[dict], list[dict], list[dict]]:
    since_iso = since.isoformat(timespec="seconds")
    with pulse_db.connect() as db:
        cases = []
        if _table_exists(db, "mod_cases"):
            cases = [dict(r) for r in db.execute(
                "SELECT id,target_id,action,status,dm_sent,proof,reason_template_id,actor_id,actor_name,created_at,closed_at "
                "FROM mod_cases WHERE guild_id=? AND created_at>=? ORDER BY created_at DESC LIMIT ?",
                (guild_id, since_iso, MAX_ROWS)).fetchall()]
        events = [dict(r) for r in db.execute(
            "SELECT id,event_type,target_id,payload_json,created_at FROM events "
            "WHERE event_type IN ('AutoMod-Maßnahme','AutoMod-Testtreffer') AND created_at>=? "
            "ORDER BY created_at DESC LIMIT ?", (since_iso, MAX_ROWS)).fetchall()]
        feedback = []
        if _table_exists(db, "automod_false_positive_feedback"):
            feedback = [dict(r) for r in db.execute(
                "SELECT event_id,rule,created_at FROM automod_false_positive_feedback WHERE guild_id=? LIMIT ?",
                (guild_id, MAX_ROWS)).fetchall()]
    return cases, events, feedback


def _case_figures(cases: list[dict]) -> dict:
    by_action = Counter(c.get("action") for c in cases)
    by_status = Counter(c.get("status") or "active" for c in cases)
    durations = []
    for case in cases:
        start, end = _parse(case.get("created_at")), _parse(case.get("closed_at"))
        if start and end and end >= start:
            durations.append((end - start).total_seconds() / 3600)
    per_target = Counter(c.get("target_id") for c in cases if c.get("target_id"))
    repeat_targets = sum(1 for n in per_target.values() if n >= REPEAT_THRESHOLD)
    moderators = Counter((c.get("actor_id"), c.get("actor_name") or c.get("actor_id")) for c in cases)
    total = len(cases)
    return {
        "total": total,
        "by_action": [(key, ACTION_LABELS[key], by_action.get(key, 0)) for key in ACTION_LABELS],
        "by_status": [(key, STATUS_LABELS[key], by_status.get(key, 0)) for key in STATUS_LABELS],
        "other_status": sum(n for key, n in by_status.items() if key not in STATUS_LABELS),
        "dm_rate": _rate(sum(1 for c in cases if c.get("dm_sent")), total),
        "proof_rate": _rate(sum(1 for c in cases if (c.get("proof") or "").strip()), total),
        "template_rate": _rate(sum(1 for c in cases if c.get("reason_template_id")), total),
        "failure_rate": _rate(by_status.get("failed", 0), total),
        "closed_count": len(durations),
        "median_hours_to_close": _hours(durations),
        "affected_people": len(per_target),
        "repeat_people": repeat_targets,
        "moderators": [(name, count) for (_id, name), count in moderators.most_common(10)],
    }


def _automod_figures(guild_id: str, events: list[dict], feedback: list[dict]) -> dict:
    real, tests = [], 0
    for event in events:
        payload = _payload(event.get("payload_json"))
        if str(payload.get("guild_id", "")) != guild_id:
            continue
        if event.get("event_type") == "AutoMod-Testtreffer" or payload.get("simulation"):
            tests += 1
            continue
        real.append({**event, "payload": payload})
    real_ids = {e["id"]: e for e in real}
    reviewed = [f for f in feedback if f.get("event_id") in real_ids]
    review_hours = []
    for item in reviewed:
        start, end = _parse(real_ids[item["event_id"]].get("created_at")), _parse(item.get("created_at"))
        if start and end and end >= start:
            review_hours.append((end - start).total_seconds() / 3600)
    by_rule = Counter(str(e["payload"].get("rule") or "unbekannt") for e in real)
    false_by_rule = Counter(str(f.get("rule") or "unbekannt") for f in reviewed)
    by_action = Counter(str(e["payload"].get("action") or "unbekannt") for e in real)
    return {
        "total": len(real),
        "tests": tests,
        "false_positives": len(reviewed),
        "false_positive_rate": _rate(len(reviewed), len(real)),
        "median_hours_to_review": _hours(review_hours),
        "by_rule": [(rule, count, false_by_rule.get(rule, 0)) for rule, count in by_rule.most_common(10)],
        "by_action": by_action.most_common(),
    }


def _daily(cases: list[dict], automod_total_by_day: Counter, start: datetime, days: int) -> list[tuple[str, int, int]]:
    case_days = Counter()
    for case in cases:
        parsed = _parse(case.get("created_at"))
        if parsed:
            case_days[parsed.date().isoformat()] += 1
    out = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date().isoformat()
        out.append((day, case_days.get(day, 0), automod_total_by_day.get(day, 0)))
    return out


def collect(guild_id, days=DEFAULT_PERIOD, now: datetime | None = None) -> dict:
    """Statistics for one guild over the last ``days`` days plus the same-length previous period for comparison."""
    days = normalize_period(days)
    if guild_id in (None, "", 0):
        return {"available": False, "reason": "Kein Discord-Server konfiguriert.", "days": days}
    guild = str(guild_id)
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    previous_start = start - timedelta(days=days)
    try:
        cases_all, events_all, feedback = _load(guild, previous_start)
    except (sqlite3.Error, OSError) as exc:
        return {"available": False, "reason": f"Moderationsdaten nicht lesbar ({type(exc).__name__}).", "days": days}

    def in_window(row, lower, upper):
        parsed = _parse(row.get("created_at"))
        return parsed is not None and lower <= parsed < upper

    cases = [c for c in cases_all if in_window(c, start, now)]
    previous_cases = [c for c in cases_all if in_window(c, previous_start, start)]
    events = [e for e in events_all if in_window(e, start, now)]
    previous_events = [e for e in events_all if in_window(e, previous_start, start)]

    case_figures = _case_figures(cases)
    automod = _automod_figures(guild, events, feedback)
    previous_automod = _automod_figures(guild, previous_events, [])
    automod_days = Counter()
    for event in events:
        payload = _payload(event.get("payload_json"))
        parsed = _parse(event.get("created_at"))
        if parsed and str(payload.get("guild_id", "")) == guild and event.get("event_type") == "AutoMod-Maßnahme" \
                and not payload.get("simulation"):
            automod_days[parsed.date().isoformat()] += 1
    return {
        "available": True,
        "days": days,
        "since": start.isoformat(timespec="seconds"),
        "until": now.isoformat(timespec="seconds"),
        "cases": case_figures,
        "cases_delta": _delta(case_figures["total"], len(previous_cases)),
        "automod": automod,
        "automod_delta": _delta(automod["total"], previous_automod["total"]),
        "daily": _daily(cases, automod_days, start + timedelta(days=1), days),
        "has_data": bool(case_figures["total"] or automod["total"] or automod["tests"]),
    }


def csv_rows(stats: dict) -> list[tuple]:
    """Flat key-figure table for the CSV export (no personal data except moderator display names)."""
    if not stats.get("available"):
        return [("Hinweis", stats.get("reason", "Keine Daten"), "")]
    c, a = stats["cases"], stats["automod"]
    rows = [("Zeitraum (Tage)", stats["days"], ""), ("Fälle gesamt", c["total"], stats["cases_delta"])]
    rows += [(f"Fälle: {label}", count, "") for _key, label, count in c["by_action"]]
    rows += [(f"Ergebnis: {label}", count, "") for _key, label, count in c["by_status"]]
    rows += [("DM-Zustellquote %", c["dm_rate"], ""), ("Fälle mit Beweis %", c["proof_rate"], ""),
             ("Mit Begründungsvorlage %", c["template_rate"], ""), ("Fehlgeschlagen %", c["failure_rate"], ""),
             ("Median Stunden bis Abschluss", c["median_hours_to_close"], ""),
             ("Betroffene Personen", c["affected_people"], ""), ("Personen mit Wiederholung", c["repeat_people"], ""),
             ("AutoMod-Maßnahmen", a["total"], stats["automod_delta"]), ("AutoMod-Testtreffer", a["tests"], ""),
             ("AutoMod-Fehlalarme", a["false_positives"], ""), ("AutoMod-Fehlalarmquote %", a["false_positive_rate"], ""),
             ("Median Stunden bis Fehlalarmprüfung", a["median_hours_to_review"], "")]
    rows += [(f"AutoMod-Regel: {rule}", count, f"{false} Fehlalarme") for rule, count, false in a["by_rule"]]
    rows += [(f"Moderator: {name}", count, "") for name, count in c["moderators"]]
    return rows
