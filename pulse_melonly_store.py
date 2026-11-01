"""Transactional SQLite repository for Melonly moderation records.

This module is deliberately opt-in: importing it creates no files and changes no
runtime backend. Production callers continue using logs.json until a separately
reviewed migration and activation step is implemented.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


_SCHEMA = """
CREATE TABLE IF NOT EXISTS melonly_logs (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id          TEXT,
    target_user     TEXT,
    target_user_key TEXT,
    roblox_id       TEXT,
    action_type     TEXT,
    created_at      TEXT,
    moderator_id    TEXT,
    payload         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_melonly_logs_log_id
    ON melonly_logs(log_id, seq);
CREATE INDEX IF NOT EXISTS idx_melonly_logs_roblox_id
    ON melonly_logs(roblox_id, seq);
CREATE INDEX IF NOT EXISTS idx_melonly_logs_target_user
    ON melonly_logs(target_user_key, seq);
CREATE INDEX IF NOT EXISTS idx_melonly_logs_action
    ON melonly_logs(action_type, seq);
CREATE TABLE IF NOT EXISTS melonly_logs_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_META_DEFAULTS = {
    "format_version": "1",
    "generation": "",
    "active_backend": "json",
    "revision": "0",
    "import_source_sha256": "",
    "import_count": "0",
}


class MelonlyStoreError(RuntimeError):
    """Base error for a failed Melonly storage operation."""


class MelonlyStoreNotEmpty(MelonlyStoreError):
    """Raised when an import would overwrite an existing SQLite dataset."""


class MelonlyStore:
    """Small connection-per-operation repository backed by one SQLite file."""

    def __init__(self, db_path: str | os.PathLike[str], *, timeout: float = 15.0):
        self.db_path = os.fspath(db_path)
        self.timeout = max(0.1, float(timeout))

    def _connect(self) -> sqlite3.Connection:
        parent = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(parent, exist_ok=True)
        con = sqlite3.connect(self.db_path, timeout=self.timeout, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute(f"PRAGMA busy_timeout={int(self.timeout * 1000)}")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.execute("COMMIT")
        except Exception:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=False)

    @staticmethod
    def _index_values(entry: Any) -> tuple[str | None, ...]:
        if not isinstance(entry, Mapping):
            return (None, None, None, None, None, None, None)
        target = entry.get("target_user")
        target_text = None if target is None else str(target)
        target_key = (target_text or "").strip().lstrip("@").casefold() or None
        values = (
            entry.get("id"),
            target_text,
            target_key,
            entry.get("roblox_id"),
            entry.get("type"),
            entry.get("created_at"),
            entry.get("moderator_id"),
        )
        return tuple(None if value is None else str(value) for value in values)

    @classmethod
    def _insert(cls, con: sqlite3.Connection, entry: Any, seq: int | None = None) -> int:
        values = cls._index_values(entry)
        payload = cls._json(entry)
        if seq is None:
            cursor = con.execute(
                """INSERT INTO melonly_logs
                   (log_id,target_user,target_user_key,roblox_id,action_type,created_at,moderator_id,payload)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (*values, payload),
            )
            return int(cursor.lastrowid)
        con.execute(
            """INSERT INTO melonly_logs
               (seq,log_id,target_user,target_user_key,roblox_id,action_type,created_at,moderator_id,payload)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (int(seq), *values, payload),
        )
        return int(seq)

    def initialize(self) -> None:
        """Create the additive schema. Safe to call repeatedly."""
        con = self._connect()
        try:
            con.executescript(_SCHEMA)
            con.execute("BEGIN IMMEDIATE")
            for key, value in _META_DEFAULTS.items():
                if key == "generation":
                    value = uuid.uuid4().hex
                con.execute(
                    "INSERT OR IGNORE INTO melonly_logs_meta(key,value) VALUES (?,?)",
                    (key, value),
                )
            con.execute("COMMIT")
        except Exception:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def count(self) -> int:
        with self._read() as con:
            row = con.execute("SELECT COUNT(*) FROM melonly_logs").fetchone()
            return int(row[0])

    def count_by_type(self) -> dict[str, int]:
        with self._read() as con:
            rows = con.execute(
                """SELECT COALESCE(action_type, '') AS action_type, COUNT(*) AS amount
                   FROM melonly_logs GROUP BY action_type ORDER BY action_type"""
            ).fetchall()
            return {str(row["action_type"]): int(row["amount"]) for row in rows}

    def latest(self, limit: int = 50) -> list[Any]:
        safe_limit = max(0, min(int(limit), 10000))
        with self._read() as con:
            rows = con.execute(
                "SELECT payload FROM melonly_logs ORDER BY seq DESC LIMIT ?", (safe_limit,)
            ).fetchall()
            return [json.loads(row["payload"]) for row in reversed(rows)]

    def append(self, entry: Mapping[str, Any]) -> int:
        if not isinstance(entry, Mapping):
            raise TypeError("Melonly entries must be mappings")
        with self._write() as con:
            seq = self._insert(con, dict(entry))
            self._bump_revision(con)
            return seq

    def find_first_by_id(self, log_id: str) -> tuple[int, Any] | None:
        with self._read() as con:
            row = con.execute(
                "SELECT seq,payload FROM melonly_logs WHERE log_id=? ORDER BY seq ASC LIMIT 1",
                (str(log_id),),
            ).fetchone()
            return None if row is None else (int(row["seq"]), json.loads(row["payload"]))

    def update_by_seq(self, seq: int, entry: Mapping[str, Any]) -> bool:
        if not isinstance(entry, Mapping):
            raise TypeError("Melonly entries must be mappings")
        values = self._index_values(entry)
        with self._write() as con:
            cursor = con.execute(
                """UPDATE melonly_logs SET log_id=?,target_user=?,target_user_key=?,roblox_id=?,
                   action_type=?,created_at=?,moderator_id=?,payload=? WHERE seq=?""",
                (*values, self._json(dict(entry)), int(seq)),
            )
            changed = cursor.rowcount == 1
            if changed:
                self._bump_revision(con)
            return changed

    def delete_by_seq(self, seq: int) -> bool:
        with self._write() as con:
            cursor = con.execute("DELETE FROM melonly_logs WHERE seq=?", (int(seq),))
            changed = cursor.rowcount == 1
            if changed:
                self._bump_revision(con)
            return changed

    def export_all(self) -> list[Any]:
        with self._read() as con:
            rows = con.execute("SELECT payload FROM melonly_logs ORDER BY seq ASC").fetchall()
            return [json.loads(row["payload"]) for row in rows]

    def iter_all(self, batch_size: int = 500) -> Iterator[Any]:
        size = max(1, min(int(batch_size), 5000))
        with self._read() as con:
            cursor = con.execute("SELECT payload FROM melonly_logs ORDER BY seq ASC")
            while True:
                rows = cursor.fetchmany(size)
                if not rows:
                    break
                for row in rows:
                    yield json.loads(row["payload"])

    def import_entries(
        self,
        entries: Iterable[Any],
        *,
        source_sha256: str = "",
        active_backend: str = "json",
    ) -> int:
        """Import once into an empty table; caller must verify source bytes and backup first."""
        if active_backend != "json":
            raise ValueError("This foundation phase cannot activate SQLite; active_backend must remain 'json'")
        materialized = list(entries)
        with self._write() as con:
            existing = int(con.execute("SELECT COUNT(*) FROM melonly_logs").fetchone()[0])
            marker = con.execute(
                "SELECT value FROM melonly_logs_meta WHERE key='import_source_sha256'"
            ).fetchone()
            if existing or (marker and marker[0]):
                raise MelonlyStoreNotEmpty("Melonly SQLite storage already contains an import")
            for position, entry in enumerate(materialized, start=1):
                self._insert(con, entry, seq=position)
            meta = {
                "format_version": "1",
                "generation": uuid.uuid4().hex,
                "active_backend": active_backend,
                "revision": "0",
                "import_source_sha256": str(source_sha256),
                "import_count": str(len(materialized)),
            }
            for key, value in meta.items():
                con.execute(
                    """INSERT INTO melonly_logs_meta(key,value) VALUES (?,?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (key, value),
                )
            return len(materialized)

    def metadata(self) -> dict[str, str]:
        with self._read() as con:
            return {str(row["key"]): str(row["value"]) for row in con.execute(
                "SELECT key,value FROM melonly_logs_meta"
            ).fetchall()}

    @staticmethod
    def _bump_revision(con: sqlite3.Connection) -> None:
        con.execute(
            """INSERT INTO melonly_logs_meta(key,value) VALUES ('revision','1')
               ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1"""
        )


    def player_history(self, query: str) -> list[Any]:
        """Return the complete player history using the legacy ID/name fallback rules."""
        needle = str(query or "").strip()[:80].lstrip("@").strip().casefold()
        if not needle:
            return []

        with self._read() as con:
            rows = con.execute(
                "SELECT payload FROM melonly_logs ORDER BY seq ASC"
            ).fetchall()
        entries = [json.loads(row["payload"]) for row in rows]

        def player_name(entry: Any) -> str:
            if not isinstance(entry, Mapping):
                return ""
            return str(entry.get("target_user") or "").strip().lstrip("@").casefold()

        def player_id(entry: Any) -> str:
            if not isinstance(entry, Mapping):
                return ""
            value = str(entry.get("roblox_id") or "").strip()
            return value if value.isdigit() else ""

        candidates = []
        if needle.isdigit():
            candidates = [entry for entry in entries if player_id(entry) == needle]
        if not candidates:
            exact = [entry for entry in entries if player_name(entry) == needle]
            candidates = exact or [
                entry for entry in entries if needle in player_name(entry)
            ]

        names = {player_name(entry) for entry in candidates if player_name(entry)}
        ids = {player_id(entry) for entry in candidates if player_id(entry)}
        return [
            entry for entry in entries
            if (player_name(entry) and player_name(entry) in names)
            or (player_id(entry) and player_id(entry) in ids)
        ]

    def roblox_suggestions(self, limit: int = 5000) -> list[Any]:
        """Return the newest bounded candidate window, newest entry first."""
        safe_limit = max(0, min(int(limit), 5000))
        if not safe_limit:
            return []
        with self._read() as con:
            rows = con.execute(
                "SELECT payload FROM melonly_logs ORDER BY seq DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
            return [json.loads(row["payload"]) for row in rows]

    def search_payload(self, query: str) -> list[Any]:
        """Search serialized payloads with the legacy case-insensitive substring rule."""
        needle = str(query or "").lower().strip()
        if not needle:
            return []
        with self._read() as con:
            rows = con.execute(
                "SELECT payload FROM melonly_logs ORDER BY seq ASC"
            ).fetchall()
        matches = []
        for row in rows:
            entry = json.loads(row["payload"])
            if needle in json.dumps(entry, ensure_ascii=False).lower():
                matches.append(entry)
        return matches
