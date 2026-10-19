"""One-time, idempotent repair of absence rows written with shifted columns.

Before the fix in webserver.py ("submit_loa"), the dashboard inserted absences
positionally. In databases where the column `von` had been appended later by
ALTER TABLE (physical order: user_id, user_name, grund, bis, original_nick,
guild_id, von) this stored:

    bis           ← start date      (should be in von)
    original_nick ← end date        (should be in bis)
    guild_id      ← display name    (text)
    von           ← guild id        (digits)

Consequences: absences ended at their *start* date and the member's nickname was
later "restored" to the end date. This module detects exactly that signature and
moves the values back. A file backup is written before the first change; rows
that do not match the signature are never touched, so running it again is a no-op.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from datetime import datetime

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SNOWFLAKE = re.compile(r"^\d{15,22}$")
SUFFIX = " | Abgemeldet"


def _is_shifted(row: dict) -> bool:
    guild_value = row.get("guild_id")
    return (
        isinstance(guild_value, str) and not guild_value.strip().isdigit()
        and _SNOWFLAKE.match(str(row.get("von") or "")) is not None
        and _DATE.match(str(row.get("bis") or "")) is not None
        and _DATE.match(str(row.get("original_nick") or "")) is not None
    )


def find_shifted_rows(conn: sqlite3.Connection) -> list[dict]:
    try:
        cursor = conn.execute("SELECT user_id, user_name, grund, von, bis, original_nick, guild_id FROM abmeldungen")
    except sqlite3.OperationalError:
        return []  # table missing or very old schema – nothing to repair
    names = [c[0] for c in cursor.description]
    return [row for row in (dict(zip(names, values)) for values in cursor.fetchall()) if _is_shifted(row)]


def repair_shifted_absences(db_path: str, *, now: datetime | None = None) -> dict:
    """Repair matching rows. Returns {"repaired": n, "backup": path or ""}."""
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        rows = find_shifted_rows(conn)
        if not rows:
            return {"repaired": 0, "backup": ""}
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        backup = f"{db_path}.vor-reparatur-{stamp}.bak"
        conn.commit()
        shutil.copy2(db_path, backup)
        with conn:
            for row in rows:
                display = str(row["guild_id"]).replace(SUFFIX, "").strip()
                conn.execute(
                    "UPDATE abmeldungen SET von=?, bis=?, original_nick=?, guild_id=? WHERE user_id=?",
                    (row["bis"], row["original_nick"], display or None, int(row["von"]), row["user_id"]),
                )
        return {"repaired": len(rows), "backup": backup}
    finally:
        conn.close()
