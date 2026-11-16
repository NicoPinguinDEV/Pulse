"""Persistent profanity strike counter and history (SQLite, the existing Pulse database).

One row per counted violation – the history is never deleted by the escalation window. The window
(`strike_window_minutes`) only decides how many rows count for the next escalation step. Counters are separate
per Discord server and user and survive bot restarts.

Message content is never stored: only IDs, the user name at that moment, the category, the strike number,
the action taken and its outcome. (guild_id, message_id) is UNIQUE, so parallel Discord events or repeated
edit events for the same message can never produce a second strike. The table is created on first use,
without touching existing tables or data.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

import pulse_db

RULE = "profanity"


def _table(db) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS profanity_strikes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL DEFAULT '',
            message_id TEXT,
            channel_id TEXT NOT NULL DEFAULT '',
            rule TEXT NOT NULL DEFAULT 'profanity',
            category TEXT NOT NULL DEFAULT '',
            strike_number INTEGER NOT NULL,
            window_minutes INTEGER NOT NULL,
            action TEXT NOT NULL DEFAULT '',
            timeout_minutes INTEGER NOT NULL DEFAULT 0,
            outcome TEXT NOT NULL DEFAULT 'pending',
            event_id TEXT NOT NULL DEFAULT '',
            created_ts INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_profanity_strikes_message ON profanity_strikes(guild_id, message_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_profanity_strikes_user ON profanity_strikes(guild_id, user_id, created_ts)")


def register_violation(
    guild_id, user_id, user_name, message_id, channel_id, category, window_minutes, *, now: float | None = None
) -> dict | None:
    """Count one violation atomically. Returns {"id", "strike_number"} or None if this message was already counted.

    The strike number is the number of violations of this user on this server inside the window, including this one.
    pulse_db.connect() holds the process-wide DB lock for the whole block, so count + insert cannot interleave.
    """
    stamp = float(now if now is not None else time.time())
    window = max(1, int(window_minutes)) * 60
    with pulse_db.connect() as db:
        _table(db)
        before = db.execute(
            "SELECT COUNT(*) FROM profanity_strikes WHERE guild_id=? AND user_id=? AND created_ts>=?",
            (str(guild_id), str(user_id), int(stamp - window)),
        ).fetchone()[0]
        number = int(before) + 1
        try:
            cursor = db.execute(
                """
                INSERT INTO profanity_strikes(guild_id, user_id, user_name, message_id, channel_id, rule, category,
                                              strike_number, window_minutes, created_ts, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(guild_id), str(user_id), str(user_name or "")[:100], str(message_id) if message_id else None,
                    str(channel_id or ""), RULE, str(category or "")[:32], number, int(window_minutes), int(stamp),
                    datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds"),
                ),
            )
        except sqlite3.IntegrityError:
            return None
        return {"id": int(cursor.lastrowid), "strike_number": number}


def finalize(strike_id: int, *, action: str, timeout_minutes: int, outcome: str, event_id: str | None) -> None:
    """Record what was actually done (outcome: ok / partial / error) and the linked activity-log event."""
    with pulse_db.connect() as db:
        _table(db)
        db.execute(
            "UPDATE profanity_strikes SET action=?, timeout_minutes=?, outcome=?, event_id=? WHERE id=?",
            (str(action)[:160], int(timeout_minutes), str(outcome)[:16], str(event_id or ""), int(strike_id)),
        )


def count_in_window(guild_id, user_id, window_minutes, *, now: float | None = None) -> int:
    stamp = float(now if now is not None else time.time())
    with pulse_db.connect() as db:
        _table(db)
        return int(db.execute(
            "SELECT COUNT(*) FROM profanity_strikes WHERE guild_id=? AND user_id=? AND created_ts>=?",
            (str(guild_id), str(user_id), int(stamp - max(1, int(window_minutes)) * 60)),
        ).fetchone()[0])


def history(guild_id, user_id=None, limit: int = 50) -> list[dict]:
    bounded = max(1, min(200, int(limit)))
    with pulse_db.connect() as db:
        _table(db)
        if user_id is None:
            rows = db.execute("SELECT * FROM profanity_strikes WHERE guild_id=? ORDER BY id DESC LIMIT ?",
                              (str(guild_id), bounded)).fetchall()
        else:
            rows = db.execute("SELECT * FROM profanity_strikes WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT ?",
                              (str(guild_id), str(user_id), bounded)).fetchall()
    return [dict(row) for row in rows]


def summary(guild_id, days: int = 7, *, now: float | None = None) -> dict:
    """Dashboard numbers for one server: violations, distinct users and timeouts in the last `days` days."""
    since = int(float(now if now is not None else time.time()) - max(1, int(days)) * 86400)
    with pulse_db.connect() as db:
        _table(db)
        row = db.execute(
            "SELECT COUNT(*) AS total, COUNT(DISTINCT user_id) AS users, "
            "COALESCE(SUM(CASE WHEN timeout_minutes>0 AND outcome='ok' THEN 1 ELSE 0 END),0) AS timeouts "
            "FROM profanity_strikes WHERE guild_id=? AND created_ts>=?",
            (str(guild_id), since),
        ).fetchone()
    return {"total": int(row["total"]), "users": int(row["users"]), "timeouts": int(row["timeouts"])}
