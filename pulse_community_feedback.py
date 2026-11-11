"""Guild-scoped Discord community suggestions stored in pulse.db for the Pulse TeamOS feedback area."""
from __future__ import annotations

import re

import pulse_db

_FEEDBACK_STATUSES = {"new", "reviewing", "voting", "planned", "done", "rejected"}
_CATEGORY_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
MAX_TITLE_LENGTH = 120
MAX_CONTENT_LENGTH = 3000
MAX_MANAGER_NOTE_LENGTH = 2000


def _ensure_table(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS discord_community_feedback (
            id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            anonymous INTEGER NOT NULL DEFAULT 0,
            author_id TEXT,
            author_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            manager_note TEXT NOT NULL DEFAULT '',
            reviewed_by_id TEXT,
            reviewed_by_name TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_discord_community_feedback_guild "
        "ON discord_community_feedback(guild_id, status, created_at DESC)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS discord_community_feedback_votes (
            id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            feedback_id TEXT NOT NULL,
            voter_id TEXT NOT NULL,
            vote TEXT NOT NULL CHECK(vote IN ('for', 'against')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(guild_id, feedback_id, voter_id),
            FOREIGN KEY(feedback_id) REFERENCES discord_community_feedback(id) ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_discord_community_feedback_votes "
        "ON discord_community_feedback_votes(guild_id, feedback_id, vote)"
    )


def submit_feedback(
    guild_id, author_id, author_name: str, category: str,
    title: str, content: str, anonymous: bool = False,
) -> dict:
    """Create a suggestion scoped to exactly one Discord guild."""
    if guild_id is None or author_id is None:
        raise ValueError("Server und einreichende Person müssen eindeutig sein.")
    guild = str(guild_id).strip()
    author = str(author_id).strip()
    category = str(category or "").strip().lower()
    title = str(title or "").strip()
    content = str(content or "").strip()
    if not guild.isdigit() or not author.isdigit():
        raise ValueError("Ungültige Server- oder Nutzer-ID.")
    if not _CATEGORY_RE.fullmatch(category):
        raise ValueError("Ungültige Vorschlagskategorie.")
    if len(title) < 3 or len(title) > MAX_TITLE_LENGTH:
        raise ValueError(f"Der Titel muss 3 bis {MAX_TITLE_LENGTH} Zeichen enthalten.")
    if len(content) < 20 or len(content) > MAX_CONTENT_LENGTH:
        raise ValueError(f"Die Beschreibung muss 20 bis {MAX_CONTENT_LENGTH} Zeichen enthalten.")
    is_anonymous = bool(anonymous)
    now = pulse_db._now()
    row = {
        "id": pulse_db.make_id("dcf"),
        "guild_id": guild,
        "category": category,
        "title": title,
        "content": content,
        "anonymous": int(is_anonymous),
        "author_id": None if is_anonymous else author,
        "author_name": "Anonym" if is_anonymous else str(author_name or author)[:100],
        "status": "new",
        "manager_note": "",
        "reviewed_by_id": None,
        "reviewed_by_name": "",
        "created_at": now,
        "updated_at": now,
    }
    with pulse_db.connect() as connection:
        _ensure_table(connection)
        connection.execute(
            """
            INSERT INTO discord_community_feedback(
                id, guild_id, category, title, content, anonymous, author_id, author_name,
                status, manager_note, reviewed_by_id, reviewed_by_name, created_at, updated_at
            ) VALUES (
                :id, :guild_id, :category, :title, :content, :anonymous, :author_id, :author_name,
                :status, :manager_note, :reviewed_by_id, :reviewed_by_name, :created_at, :updated_at
            )
            """,
            row,
        )
    return row


def list_feedback(guild_id, limit: int = 200) -> list[dict]:
    """List recent suggestions for one guild only; result size is always bounded."""
    if guild_id is None:
        return []
    try:
        bounded = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        bounded = 200
    with pulse_db.connect() as connection:
        _ensure_table(connection)
        rows = connection.execute(
            """
            SELECT f.*,
                (SELECT COUNT(*) FROM discord_community_feedback_votes v
                 WHERE v.guild_id=f.guild_id AND v.feedback_id=f.id AND v.vote='for') AS votes_for,
                (SELECT COUNT(*) FROM discord_community_feedback_votes v
                 WHERE v.guild_id=f.guild_id AND v.feedback_id=f.id AND v.vote='against') AS votes_against
            FROM discord_community_feedback f
            WHERE f.guild_id=?
            ORDER BY f.created_at DESC, f.id DESC
            LIMIT ?
            """,
            (str(guild_id), bounded),
        ).fetchall()
    return [dict(row) for row in rows]


def update_feedback(guild_id, feedback_id, status: str, manager_note: str, reviewer_id, reviewer_name: str) -> dict:
    """Update one suggestion only when it belongs to the reviewing user's current guild."""
    if guild_id is None or reviewer_id is None:
        raise ValueError("Server und prüfende Person müssen eindeutig sein.")
    feedback_id = str(feedback_id or "").strip()
    status = str(status or "").strip().lower()
    note = str(manager_note or "").strip()
    if status not in _FEEDBACK_STATUSES:
        raise ValueError("Ungültiger Feedbackstatus.")
    if not feedback_id.startswith("dcf_"):
        raise ValueError("Ungültige Discord-Vorschlags-ID.")
    if len(note) > MAX_MANAGER_NOTE_LENGTH:
        raise ValueError(f"Die interne Notiz darf höchstens {MAX_MANAGER_NOTE_LENGTH} Zeichen enthalten.")
    now = pulse_db._now()
    with pulse_db.connect() as connection:
        _ensure_table(connection)
        cursor = connection.execute(
            """
            UPDATE discord_community_feedback
            SET status=?, manager_note=?, reviewed_by_id=?, reviewed_by_name=?, updated_at=?
            WHERE id=? AND guild_id=?
            """,
            (status, note, str(reviewer_id), str(reviewer_name or reviewer_id)[:100],
             now, feedback_id, str(guild_id)),
        )
        if cursor.rowcount != 1:
            raise KeyError("Der Vorschlag wurde in diesem Server nicht gefunden.")
        row = connection.execute(
            "SELECT * FROM discord_community_feedback WHERE id=? AND guild_id=?",
            (feedback_id, str(guild_id)),
        ).fetchone()
    return dict(row)



def vote_counts(guild_id, feedback_id) -> dict:
    """Return aggregated votes for a suggestion from its originating guild only."""
    if guild_id is None or feedback_id is None:
        return {"for": 0, "against": 0}
    with pulse_db.connect() as connection:
        _ensure_table(connection)
        rows = connection.execute(
            """
            SELECT vote, COUNT(*) AS total
            FROM discord_community_feedback_votes
            WHERE guild_id=? AND feedback_id=?
            GROUP BY vote
            """,
            (str(guild_id), str(feedback_id)),
        ).fetchall()
    counts = {"for": 0, "against": 0}
    for row in rows:
        if row["vote"] in counts:
            counts[row["vote"]] = int(row["total"])
    return counts


def list_voting_feedback(guild_id, limit: int = 25) -> list[dict]:
    """List only leadership-approved polls currently open for voting in this guild."""
    if guild_id is None:
        return []
    try:
        bounded = max(1, min(int(limit), 25))
    except (TypeError, ValueError):
        bounded = 25
    with pulse_db.connect() as connection:
        _ensure_table(connection)
        rows = connection.execute(
            """
            SELECT * FROM discord_community_feedback
            WHERE guild_id=? AND status='voting'
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (str(guild_id), bounded),
        ).fetchall()
        result = []
        for raw in rows:
            row = dict(raw)
            counts = connection.execute(
                """
                SELECT vote, COUNT(*) AS total FROM discord_community_feedback_votes
                WHERE guild_id=? AND feedback_id=? GROUP BY vote
                """,
                (str(guild_id), row["id"]),
            ).fetchall()
            row["votes_for"] = 0
            row["votes_against"] = 0
            for item in counts:
                if item["vote"] == "for":
                    row["votes_for"] = int(item["total"])
                elif item["vote"] == "against":
                    row["votes_against"] = int(item["total"])
            result.append(row)
    return result


def cast_vote(guild_id, feedback_id, voter_id, vote: str) -> dict:
    """Cast or change one member's vote on a suggestion opened for voting in this guild."""
    if guild_id is None or voter_id is None:
        raise ValueError("Server und abstimmende Person müssen eindeutig sein.")
    guild = str(guild_id).strip()
    voter = str(voter_id).strip()
    choice = str(vote or "").strip().lower()
    if not guild.isdigit() or not voter.isdigit():
        raise ValueError("Ungültige Server- oder Nutzer-ID.")
    if choice not in {"for", "against"}:
        raise ValueError("Bitte Dafür oder Dagegen auswählen.")
    now = pulse_db._now()
    with pulse_db.connect() as connection:
        _ensure_table(connection)
        suggestion = connection.execute(
            "SELECT id FROM discord_community_feedback WHERE id=? AND guild_id=? AND status='voting'",
            (str(feedback_id), guild),
        ).fetchone()
        if suggestion is None:
            raise KeyError("Der Vorschlag ist nicht (mehr) zur Abstimmung freigegeben.")
        connection.execute(
            """
            INSERT INTO discord_community_feedback_votes(
                id, guild_id, feedback_id, voter_id, vote, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, feedback_id, voter_id)
            DO UPDATE SET vote=excluded.vote, updated_at=excluded.updated_at
            """,
            (pulse_db.make_id("dcv"), guild, str(feedback_id), voter, choice, now, now),
        )
        rows = connection.execute(
            """
            SELECT vote, COUNT(*) AS total FROM discord_community_feedback_votes
            WHERE guild_id=? AND feedback_id=? GROUP BY vote
            """,
            (guild, str(feedback_id)),
        ).fetchall()
    counts = {"for": 0, "against": 0}
    for row in rows:
        counts[row["vote"]] = int(row["total"])
    return {"vote": choice, **counts}
