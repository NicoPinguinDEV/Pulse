"""Persistent moderator feedback for genuine AutoMod detections.

Only event metadata is retained. Message content is deliberately never stored.
A feedback entry is bound to the original AutoMod event and the originating guild.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

import pulse_db

_EVENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_ALLOWED_MODES = {"observe", "enforce"}


def _table(db) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS automod_false_positive_feedback (
            event_id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            rule TEXT NOT NULL,
            action TEXT NOT NULL,
            mode TEXT NOT NULL,
            reviewer_id TEXT NOT NULL,
            reviewer_name TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_automod_false_positive_guild "
        "ON automod_false_positive_feedback(guild_id, created_at DESC)"
    )


def record_false_positive(event_id, guild_id, reviewer_id, reviewer_name, reason) -> dict:
    """Mark one genuine AutoMod detection as a false positive, once, with reviewer context."""
    event_id = str(event_id or "").strip()
    reason = str(reason or "").strip()
    if not _EVENT_ID.fullmatch(event_id):
        raise ValueError("Bitte eine gültige Ereignis-ID aus der AutoMod-Meldung angeben.")
    if len(reason) < 8:
        raise ValueError("Bitte kurz begründen, warum die Regel hier nicht verletzt wurde (mindestens 8 Zeichen).")
    if len(reason) > 500:
        raise ValueError("Die Begründung darf höchstens 500 Zeichen enthalten.")
    if guild_id is None or reviewer_id is None:
        raise ValueError("Server und prüfendes Teammitglied müssen eindeutig sein.")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with pulse_db.connect() as db:
        _table(db)
        event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if event is None or event["event_type"] != "AutoMod-Maßnahme":
            raise ValueError("Der Fall wurde nicht als echte AutoMod-Maßnahme gefunden. Testtreffer können nicht markiert werden.")
        try:
            payload = json.loads(event["payload_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict) or str(payload.get("guild_id")) != str(guild_id):
            raise ValueError("Der AutoMod-Fall gehört nicht zu diesem Server.")
        mode = str(payload.get("mode") or "").strip().lower()
        if mode not in _ALLOWED_MODES or payload.get("simulation") is True:
            raise ValueError("Nur echte AutoMod-Treffer aus Beobachtungs- oder Aktivmodus können geprüft werden.")
        row = {
            "event_id": event_id,
            "guild_id": str(guild_id),
            "target_id": str(event["target_id"] or ""),
            "channel_id": str(payload.get("channel_id") or ""),
            "rule": str(payload.get("rule") or "unbekannt")[:64],
            "action": str(payload.get("action") or "")[:160],
            "mode": mode,
            "reviewer_id": str(reviewer_id),
            "reviewer_name": str(reviewer_name or reviewer_id)[:100],
            "reason": reason,
            "created_at": now,
        }
        try:
            db.execute(
                """
                INSERT INTO automod_false_positive_feedback(
                    event_id, guild_id, target_id, channel_id, rule, action, mode,
                    reviewer_id, reviewer_name, reason, created_at
                ) VALUES (
                    :event_id, :guild_id, :target_id, :channel_id, :rule, :action, :mode,
                    :reviewer_id, :reviewer_name, :reason, :created_at
                )
                """,
                row,
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Dieser AutoMod-Fall wurde bereits als Fehlalarm geprüft.") from exc

        audit_id = pulse_db.make_id("event")
        audit_payload = {
            "guild_id": str(guild_id),
            "original_event_id": event_id,
            "rule": row["rule"],
            "channel_id": row["channel_id"],
            "reason": reason,
        }
        db.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
            (
                audit_id,
                "AutoMod-Fehlalarm bestätigt",
                "automod_event",
                event_id,
                str(reviewer_id),
                row["reviewer_name"],
                json.dumps(audit_payload, ensure_ascii=False),
                now,
            ),
        )
    return row


def list_false_positives(guild_id, limit: int = 20) -> list[dict]:
    """Return recent feedback for one guild only, with a bounded result size."""
    if guild_id is None:
        return []
    try:
        bounded = max(1, min(100, int(limit)))
    except (TypeError, ValueError):
        bounded = 20
    with pulse_db.connect() as db:
        _table(db)
        rows = db.execute(
            """
            SELECT * FROM automod_false_positive_feedback
            WHERE guild_id=?
            ORDER BY created_at DESC, event_id DESC
            LIMIT ?
            """,
            (str(guild_id), bounded),
        ).fetchall()
    return [dict(row) for row in rows]
