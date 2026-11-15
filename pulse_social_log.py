"""Audit #54: Veröffentlichungsübersicht und Filter für den Social-Media-Monitor.

* ``record`` schreibt jede tatsächlich gesendete Ankündigung in ``social_publications`` (Quelle, Plattform, Titel, Link, Zeit).
  Ein Fehler hier bricht nie die Ankündigung ab.
* ``filter_sources`` filtert die überwachten Quellen nach Plattform, Status und Suchtext (rein lesend, ändert nichts).
* ``recent`` und ``summary`` liefern die Übersicht der letzten Tage, gruppiert nach Plattform und Quelle.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pulse_db

STATUSES = {"aktiv": "aktiv", "pausiert": "pausiert", "gestoert": "Feed gestört", "wartet": "wartet auf Feed"}
MAX_Q = 80


def source_status(source: dict) -> str:
    if not source.get("enabled", True):
        return "pausiert"
    if source.get("feed_status") == "pending" or not source.get("initialized"):
        return "wartet"
    if source.get("feed_status") == "degraded":
        return "gestoert"
    return "aktiv"


def filter_sources(sources, platform: str = "", status: str = "", q: str = "") -> list[dict]:
    needle = str(q or "").strip().casefold()[:MAX_Q]
    platform = str(platform or "").strip().lower()
    status = str(status or "").strip().lower()
    out = []
    for source in sources or ():
        if not isinstance(source, dict):
            continue
        if platform and str(source.get("platform", "")).lower() != platform:
            continue
        if status in STATUSES and source_status(source) != status:
            continue
        if needle and needle not in f"{source.get('name', '')} {source.get('feed_title', '')} {source.get('custom_name', '')}".casefold():
            continue
        out.append(source)
    return out


def record(source: dict, item: dict, now: datetime | None = None) -> bool:
    try:
        with pulse_db.connect() as db:
            db.execute("INSERT INTO social_publications (id,source_id,source_name,platform,title,url,announced_at) VALUES (?,?,?,?,?,?,?)",
                       ("pub_" + secrets.token_hex(6), str(source.get("id", ""))[:60], str(source.get("name", ""))[:100], str(source.get("platform", ""))[:20],
                        str(item.get("title", ""))[:200], str(item.get("link") or item.get("url") or "")[:300],
                        (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")))
        return True
    except Exception:  # noqa: BLE001 - the announcement itself must never fail because of the log
        return False


def recent(days: int = 30, platform: str = "", source_id: str = "", limit: int = 50, now: datetime | None = None) -> list[dict]:
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=max(1, min(int(days), 365)))).isoformat(timespec="seconds")
    sql, args = "SELECT * FROM social_publications WHERE announced_at>=?", [since]
    if platform:
        sql += " AND platform=?"
        args.append(str(platform).lower())
    if source_id:
        sql += " AND source_id=?"
        args.append(str(source_id))
    with pulse_db.connect() as db:
        rows = db.execute(sql + " ORDER BY announced_at DESC LIMIT ?", (*args, max(1, min(int(limit), 200)))).fetchall()
    return [dict(r) for r in rows]


def summary(days: int = 30, now: datetime | None = None) -> dict:
    """``{total, by_platform: {platform: n}, by_source: [(name, n)]}`` for the last ``days`` days."""
    rows = recent(days, limit=200, now=now)
    by_platform: dict = {}
    by_source: dict = {}
    for row in rows:
        by_platform[row["platform"]] = by_platform.get(row["platform"], 0) + 1
        by_source[row["source_name"]] = by_source.get(row["source_name"], 0) + 1
    return {"total": len(rows), "by_platform": by_platform, "by_source": sorted(by_source.items(), key=lambda kv: (-kv[1], kv[0]))[:10]}
