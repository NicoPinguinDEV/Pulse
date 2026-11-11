"""Guild-scoped Discord player reports stored alongside Pulse Suite cases."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import pulse_paths
import pulse_sqlite

DB_PATH = os.path.join(str(pulse_paths.DATA_DIR), "pulse_suite.db")
REPORT_STATUSES = {"open", "in_progress", "closed"}
REPORT_PRIORITIES = {"low", "normal", "high", "urgent"}
REPORT_CATEGORIES = {"rule_violation", "cheating", "harassment", "other"}
MAX_DESCRIPTION_LENGTH = 3000
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
_ALLOWED_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".mp4": "video/mp4",
}


def _connect():
    connection = pulse_sqlite.connect(DB_PATH, timeout=3)
    connection.execute("PRAGMA busy_timeout=3000")
    return connection


def _ensure_table(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS discord_player_reports (
            id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            reporter_id TEXT NOT NULL,
            reporter_name TEXT NOT NULL,
            target_name TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'normal',
            status TEXT NOT NULL DEFAULT 'open',
            assignee_id TEXT NOT NULL DEFAULT '',
            assignee_name TEXT NOT NULL DEFAULT '',
            evidence_json TEXT NOT NULL DEFAULT '[]',
            reviewed_by_id TEXT NOT NULL DEFAULT '',
            reviewed_by_name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_discord_player_reports_guild "
        "ON discord_player_reports(guild_id, status, created_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_discord_player_reports_reporter "
        "ON discord_player_reports(guild_id, reporter_id, created_at DESC)"
    )


def normalize_evidence(evidence) -> list[dict]:
    """Validate and retain only safe Discord CDN evidence metadata, never arbitrary URLs."""
    if evidence is None:
        return []
    if not isinstance(evidence, dict):
        raise ValueError("Der Beleg ist ungültig.")
    filename = PurePosixPath(str(evidence.get("filename") or "").replace("\\", "/")).name
    extension = PurePosixPath(filename).suffix.lower()
    expected_type = _ALLOWED_MEDIA.get(extension)
    content_type = str(evidence.get("content_type") or "").split(";", 1)[0].strip().lower()
    try:
        size = int(evidence.get("size") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Die Dateigröße des Belegs ist ungültig.") from exc
    url = str(evidence.get("url") or "").strip()
    parsed = urlsplit(url)
    if not expected_type or (content_type and content_type != expected_type):
        raise ValueError("Zulässige Belege sind PNG, JPG, WEBP, PDF oder MP4.")
    if size <= 0 or size > MAX_EVIDENCE_BYTES:
        raise ValueError("Ein Beleg darf höchstens 10 MB groß sein.")
    if parsed.scheme != "https" or parsed.hostname not in {"cdn.discordapp.com", "media.discordapp.net"}:
        raise ValueError("Belege müssen direkt aus dem Discord-CDN stammen.")
    if not parsed.path.startswith("/attachments/"):
        raise ValueError("Die Beleg-URL ist keine gültige Discord-Anlage.")
    return [{
        "filename": filename[:160],
        "content_type": expected_type,
        "size": size,
        "url": url[:2000],
    }]


def create_report(
    guild_id,
    reporter_id,
    reporter_name: str,
    target_name: str,
    category: str,
    description: str,
    priority: str = "normal",
    evidence=None,
) -> dict:
    """Create a player report visible only to the originating Discord guild and its staff."""
    if guild_id is None or reporter_id is None:
        raise ValueError("Server und meldende Person müssen eindeutig sein.")
    guild = str(guild_id).strip()
    reporter = str(reporter_id).strip()
    target = " ".join(str(target_name or "").split())
    category = str(category or "").strip().lower()
    priority = str(priority or "normal").strip().lower()
    description = str(description or "").strip()
    if not guild.isdigit() or not reporter.isdigit():
        raise ValueError("Ungültige Server- oder Nutzer-ID.")
    if len(target) < 2 or len(target) > 100:
        raise ValueError("Das gemeldete Spielerprofil muss 2 bis 100 Zeichen enthalten.")
    if category not in REPORT_CATEGORIES:
        raise ValueError("Ungültige Meldekategorie.")
    if priority not in REPORT_PRIORITIES:
        raise ValueError("Ungültige Priorität.")
    if len(description) < 20 or len(description) > MAX_DESCRIPTION_LENGTH:
        raise ValueError(f"Die Beschreibung muss 20 bis {MAX_DESCRIPTION_LENGTH} Zeichen enthalten.")
    evidence_rows = normalize_evidence(evidence)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = {
        "id": "RPT-" + uuid.uuid4().hex[:12].upper(),
        "guild_id": guild,
        "reporter_id": reporter,
        "reporter_name": str(reporter_name or reporter)[:100],
        "target_name": target,
        "category": category,
        "description": description,
        "priority": priority,
        "status": "open",
        "assignee_id": "",
        "assignee_name": "",
        "evidence_json": json.dumps(evidence_rows, ensure_ascii=False),
        "reviewed_by_id": "",
        "reviewed_by_name": "",
        "created_at": now,
        "updated_at": now,
    }
    with _connect() as connection:
        _ensure_table(connection)
        connection.execute(
            """
            INSERT INTO discord_player_reports(
                id, guild_id, reporter_id, reporter_name, target_name, category, description,
                priority, status, assignee_id, assignee_name, evidence_json,
                reviewed_by_id, reviewed_by_name, created_at, updated_at
            ) VALUES (
                :id, :guild_id, :reporter_id, :reporter_name, :target_name, :category, :description,
                :priority, :status, :assignee_id, :assignee_name, :evidence_json,
                :reviewed_by_id, :reviewed_by_name, :created_at, :updated_at
            )
            """,
            row,
        )
    return row


def _decode(row) -> dict:
    result = dict(row)
    try:
        decoded = json.loads(result.get("evidence_json") or "[]")
    except (TypeError, ValueError):
        decoded = []
    result["evidence"] = decoded if isinstance(decoded, list) else []
    return result


def list_reports(guild_id, limit: int = 200) -> list[dict]:
    if guild_id is None:
        return []
    try:
        bounded = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        bounded = 200
    with _connect() as connection:
        _ensure_table(connection)
        rows = connection.execute(
            "SELECT * FROM discord_player_reports WHERE guild_id=? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (str(guild_id), bounded),
        ).fetchall()
    return [_decode(row) for row in rows]


def list_user_reports(guild_id, reporter_id, limit: int = 10) -> list[dict]:
    if guild_id is None or reporter_id is None:
        return []
    try:
        bounded = max(1, min(int(limit), 25))
    except (TypeError, ValueError):
        bounded = 10
    with _connect() as connection:
        _ensure_table(connection)
        rows = connection.execute(
            "SELECT * FROM discord_player_reports WHERE guild_id=? AND reporter_id=? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (str(guild_id), str(reporter_id), bounded),
        ).fetchall()
    return [_decode(row) for row in rows]


def update_report(
    guild_id, report_id, status: str, assignee_id: str, assignee_name: str,
    reviewer_id, reviewer_name: str,
) -> dict:
    """Update only a report from the manager's current guild."""
    if guild_id is None or reviewer_id is None:
        raise ValueError("Server und prüfende Person müssen eindeutig sein.")
    report_id = str(report_id or "").strip()
    status = str(status or "").strip().lower()
    if status not in REPORT_STATUSES:
        raise ValueError("Ungültiger Meldungsstatus.")
    if not report_id.startswith("RPT-"):
        raise ValueError("Ungültige Spielermeldungs-ID.")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect() as connection:
        _ensure_table(connection)
        cursor = connection.execute(
            """
            UPDATE discord_player_reports
            SET status=?, assignee_id=?, assignee_name=?,
                reviewed_by_id=?, reviewed_by_name=?, updated_at=?
            WHERE id=? AND guild_id=?
            """,
            (
                status, str(assignee_id or ""), str(assignee_name or "")[:100],
                str(reviewer_id), str(reviewer_name or reviewer_id)[:100],
                now, report_id, str(guild_id),
            ),
        )
        if cursor.rowcount != 1:
            raise KeyError("Die Spielermeldung wurde in diesem Server nicht gefunden.")
        row = connection.execute(
            "SELECT * FROM discord_player_reports WHERE id=? AND guild_id=?",
            (report_id, str(guild_id)),
        ).fetchone()
    return _decode(row)
