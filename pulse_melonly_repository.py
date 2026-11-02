"""Runtime repository adapter for Melonly records.

JSON remains the default source. SQLite is used only after an explicit startup
setting and a matching active_backend marker have both been verified.
The adapter never initializes or migrates a database on its own.
"""
from __future__ import annotations

import copy
import json
import os
import sqlite3
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


class MelonlyBackendError(RuntimeError):
    """The configured Melonly backend cannot be selected safely."""


def _existing_metadata(db_path: str | os.PathLike[str]) -> dict[str, str]:
    """Read Melonly metadata without creating a database or schema."""
    path = Path(db_path)
    if not path.is_file():
        return {}
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as con:
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='melonly_logs_meta'"
            ).fetchone()
            if not exists:
                return {}
            return {str(key): str(value) for key, value in con.execute(
                "SELECT key,value FROM melonly_logs_meta"
            ).fetchall()}
    except sqlite3.Error as exc:
        raise MelonlyBackendError(
            f"Melonly-Metadaten in {path.name} sind nicht lesbar ({type(exc).__name__})."
        ) from exc


class JsonMelonlyRepository:
    """Compatibility implementation with a shared, file-signature-backed snapshot.

    The snapshot is valid only while the atomically-written JSON file keeps the
    same device/inode/size/mtime/ctime signature. All returned records are copied
    so callers cannot mutate the cached source data.
    """

    def __init__(
        self,
        json_path: str | os.PathLike[str],
        load_json: Callable[[Any, Any], Any],
        save_json: Callable[[Any, Any], Any],
        io_lock=None,
    ):
        self.json_path = os.fspath(json_path)
        self._load_json = load_json
        self._save_json = save_json
        self._lock = io_lock if io_lock is not None else nullcontext()
        self._cache_signature = None
        self._cache_rows: list[Any] | None = None
        self._cache_indexes: dict[str, dict[Any, list[int]]] | None = None
        self._cache_search_texts: list[str] | None = None

    def _file_signature(self):
        try:
            stat = os.stat(self.json_path)
        except OSError:
            return None
        return (
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        )

    @staticmethod
    def _player_name(entry: Any) -> str:
        if not isinstance(entry, Mapping):
            return ""
        return str(entry.get("target_user") or "").strip().lstrip("@").casefold()

    @staticmethod
    def _player_id(entry: Any) -> str:
        if not isinstance(entry, Mapping):
            return ""
        value = str(entry.get("roblox_id") or "").strip()
        return value if value.isdigit() else ""

    @staticmethod
    def _add_index(index: dict[Any, list[int]], key: Any, position: int) -> None:
        try:
            index.setdefault(key, []).append(position)
        except TypeError:
            # JSON permits lists/dicts as arbitrary legacy IDs. They cannot be
            # dictionary keys, so those rare values stay on the compatibility scan.
            return

    @classmethod
    def _build_indexes(cls, rows: list[Any]) -> dict[str, dict[Any, list[int]]]:
        indexes: dict[str, dict[Any, list[int]]] = {
            "log_id": {},
            "player_name": {},
            "player_id": {},
            "roblox_id": {},
            "roblox_name": {},
        }
        for position, entry in enumerate(rows):
            if not isinstance(entry, Mapping):
                continue
            cls._add_index(indexes["log_id"], entry.get("id"), position)
            name = cls._player_name(entry)
            player_id = cls._player_id(entry)
            if name:
                cls._add_index(indexes["player_name"], name, position)
            if player_id:
                cls._add_index(indexes["player_id"], player_id, position)
            cls._add_index(indexes["roblox_id"], str(entry.get("roblox_id")), position)
            cls._add_index(indexes["roblox_name"], str(entry.get("target_user", "")).lower(), position)
        return indexes

    def _remember_snapshot(self, rows: list[Any], signature) -> None:
        if signature is None:
            self._cache_signature = None
            self._cache_rows = None
            self._cache_indexes = None
            self._cache_search_texts = None
            return
        self._cache_rows = rows
        self._cache_signature = signature
        self._cache_indexes = self._build_indexes(rows)
        self._cache_search_texts = None

    def _snapshot_locked(self):
        for _attempt in range(2):
            before = self._file_signature()
            if (
                before is not None
                and before == self._cache_signature
                and self._cache_rows is not None
                and self._cache_indexes is not None
            ):
                return self._cache_rows, self._cache_indexes

            rows = self._load_json(self.json_path, [])
            if not isinstance(rows, list):
                raise MelonlyBackendError(
                    "logs.json enthält keine gültige Liste; Melonly-Schreibzugriffe wurden gestoppt."
                )
            after = self._file_signature()
            if before is None and after is None:
                return rows, self._build_indexes(rows)
            if before is not None and before == after:
                self._remember_snapshot(rows, after)
                return rows, self._cache_indexes

        # A concurrent external replacement occurred while loading. Use this
        # complete read for the current operation, but do not cache a moving file.
        return rows, self._build_indexes(rows)

    def _snapshot(self):
        with self._lock:
            return self._snapshot_locked()

    def _save_snapshot_locked(self, rows: list[Any]) -> None:
        self._save_json(self.json_path, rows)
        self._remember_snapshot(rows, self._file_signature())

    def count(self) -> int:
        rows, _indexes = self._snapshot()
        return len(rows)

    def count_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        rows, _indexes = self._snapshot()
        for entry in rows:
            action = str(entry.get("type") or "") if isinstance(entry, Mapping) else ""
            counts[action] = counts.get(action, 0) + 1
        return counts

    def latest(self, limit: int = 50) -> list[Any]:
        safe_limit = max(0, min(int(limit), 10000))
        if not safe_limit:
            return []
        rows, _indexes = self._snapshot()
        return copy.deepcopy(rows[-safe_limit:])

    def latest_matching_types(self, log_types: Iterable[str], limit: int = 120) -> list[Any]:
        allowed = tuple(log_types)
        safe_limit = max(0, min(int(limit), 10000))
        if not safe_limit or not allowed:
            return []
        rows, _indexes = self._snapshot()
        out = []
        for entry in reversed(rows):
            if isinstance(entry, Mapping) and entry.get("type") in allowed:
                out.append(copy.deepcopy(entry))
                if len(out) >= safe_limit:
                    break
        return out

    def append(self, entry: Mapping[str, Any]) -> int:
        if not isinstance(entry, Mapping):
            raise TypeError("Melonly entries must be mappings")
        with self._lock:
            rows, _indexes = self._snapshot_locked()
            rows = list(rows)
            rows.append(copy.deepcopy(dict(entry)))
            self._save_snapshot_locked(rows)
            return len(rows)

    def find_first_by_id(self, log_id: str) -> tuple[int, Any] | None:
        rows, indexes = self._snapshot()
        try:
            positions = indexes["log_id"].get(log_id, ())
        except TypeError:
            positions = ()
        for position in positions:
            entry = rows[position]
            if isinstance(entry, Mapping) and entry.get("id") == log_id:
                return position + 1, copy.deepcopy(entry)
        # Preserve the old Python equality behavior for unhashable legacy IDs.
        if not positions:
            for position, entry in enumerate(rows):
                if isinstance(entry, Mapping) and entry.get("id") == log_id:
                    return position + 1, copy.deepcopy(entry)
        return None

    def update_by_seq(self, seq: int, entry: Mapping[str, Any]) -> bool:
        if not isinstance(entry, Mapping):
            raise TypeError("Melonly entries must be mappings")
        with self._lock:
            current, _indexes = self._snapshot_locked()
            rows = list(current)
            index = int(seq) - 1
            if index < 0 or index >= len(rows):
                return False
            rows[index] = copy.deepcopy(dict(entry))
            self._save_snapshot_locked(rows)
            return True

    def delete_by_seq(self, seq: int) -> bool:
        with self._lock:
            current, _indexes = self._snapshot_locked()
            rows = list(current)
            index = int(seq) - 1
            if index < 0 or index >= len(rows):
                return False
            del rows[index]
            self._save_snapshot_locked(rows)
            return True

    def delete_all_by_id(self, log_id: str) -> int:
        with self._lock:
            current, indexes = self._snapshot_locked()
            try:
                positions = set(indexes["log_id"].get(log_id, ()))
            except TypeError:
                positions = set()
            if not positions:
                # Rare unhashable IDs preserve the legacy comparison behavior.
                positions = {
                    position for position, entry in enumerate(current)
                    if isinstance(entry, Mapping) and entry.get("id") == log_id
                }
            if not positions:
                return 0
            kept = [
                entry for position, entry in enumerate(current)
                if position not in positions
                or not isinstance(entry, Mapping)
                or entry.get("id") != log_id
            ]
            removed = len(current) - len(kept)
            if removed:
                self._save_snapshot_locked(kept)
            return removed

    def export_all(self) -> list[Any]:
        rows, _indexes = self._snapshot()
        return copy.deepcopy(rows)

    def iter_all(self, batch_size: int = 500):
        # Copy one row at a time: CSV/backup consumers need the full sequence,
        # but should not duplicate the entire cached dataset in memory.
        rows, _indexes = self._snapshot()
        for entry in rows:
            yield copy.deepcopy(entry)

    def player_history(self, query: str) -> list[Any]:
        needle = str(query or "").strip()[:80].lstrip("@").strip().casefold()
        if not needle:
            return []
        rows, indexes = self._snapshot()
        candidates: list[int] = []
        if needle.isdigit():
            candidates = list(indexes["player_id"].get(needle, ()))
        if not candidates:
            exact = indexes["player_name"].get(needle, ())
            candidates = list(exact) if exact else [
                position
                for name, positions in indexes["player_name"].items()
                if needle in name
                for position in positions
            ]
        names = {self._player_name(rows[position]) for position in candidates}
        ids = {self._player_id(rows[position]) for position in candidates}
        matched: set[int] = set()
        for name in names - {""}:
            matched.update(indexes["player_name"].get(name, ()))
        for player_id in ids - {""}:
            matched.update(indexes["player_id"].get(player_id, ()))
        return [copy.deepcopy(rows[position]) for position in sorted(matched)]

    def roblox_user_history(self, roblox_id: str, username: str) -> list[Any]:
        id_value, name_value = str(roblox_id), str(username).lower()
        rows, indexes = self._snapshot()
        positions = set(indexes["roblox_id"].get(id_value, ()))
        positions.update(indexes["roblox_name"].get(name_value, ()))
        return [
            copy.deepcopy(rows[position])
            for position in sorted(positions)
            if isinstance(rows[position], Mapping) and (
                str(rows[position].get("roblox_id")) == id_value
                or str(rows[position].get("target_user", "")).lower() == name_value
            )
        ]

    def roblox_suggestions(self, limit: int = 5000) -> list[Any]:
        safe_limit = max(0, min(int(limit), 5000))
        if not safe_limit:
            return []
        rows, _indexes = self._snapshot()
        return copy.deepcopy(list(reversed(rows[-safe_limit:])))

    def search_payload(self, query: str) -> list[Any]:
        needle = str(query or "").lower().strip()
        if not needle:
            return []
        with self._lock:
            rows, _indexes = self._snapshot_locked()
            if rows is self._cache_rows:
                if self._cache_search_texts is None:
                    search_texts = [
                        json.dumps(entry, ensure_ascii=False).lower() for entry in rows
                    ]
                    # Keep the lazy index tied to the exact immutable snapshot
                    # that was serialized while the repository lock was held.
                    if rows is self._cache_rows:
                        self._cache_search_texts = search_texts
                else:
                    search_texts = self._cache_search_texts
            else:
                search_texts = [
                    json.dumps(entry, ensure_ascii=False).lower() for entry in rows
                ]

        # A writer replaces the complete cached list; both captured objects stay
        # immutable and valid after releasing the lock, so the full scan itself
        # must not hold up CRUD requests.
        return [
            copy.deepcopy(rows[position])
            for position, text in enumerate(search_texts)
            if needle in text
        ]


class MelonlyRepository:
    """Select the JSON or already-activated SQLite implementation safely."""

    _WRITE_METHODS = frozenset({"append", "update_by_seq", "delete_by_seq", "delete_all_by_id"})

    def __init__(
        self,
        *,
        json_path: str | os.PathLike[str],
        db_path: str | os.PathLike[str],
        load_json: Callable[[Any, Any], Any],
        save_json: Callable[[Any, Any], Any],
        io_lock=None,
        environ=None,
    ):
        self.json_path = os.fspath(json_path)
        self.db_path = os.fspath(db_path)
        self.io_lock = io_lock if io_lock is not None else nullcontext()
        env = os.environ if environ is None else environ
        requested = str(env.get("PULSE_LOGS_BACKEND", "") or "").strip().lower()
        if requested not in {"", "json", "sqlite"}:
            raise MelonlyBackendError("PULSE_LOGS_BACKEND erlaubt ausschließlich 'json' oder 'sqlite'.")

        metadata = _existing_metadata(self.db_path)
        active = metadata.get("active_backend", "")
        if active not in {"", "json", "sqlite"}:
            raise MelonlyBackendError("Unbekannter active_backend-Marker in der Melonly-Datenbank.")

        if active == "sqlite":
            if requested == "json":
                self._rollback_to_json(metadata)
                self.backend_name = "json"
                self._backend = JsonMelonlyRepository(
                    self.json_path, load_json, save_json, self.io_lock
                )
                return
            if requested != "sqlite":
                raise MelonlyBackendError(
                    "SQLite ist als Melonly-Backend markiert. PULSE_LOGS_BACKEND=sqlite muss ausdrücklich gesetzt sein; "
                    "ein stiller Rückfall auf logs.json ist gesperrt."
                )
            from pulse_melonly_store import MelonlyStore
            self._backend = MelonlyStore(self.db_path)
            self.backend_name = "sqlite"
            return

        if requested == "sqlite":
            raise MelonlyBackendError(
                "SQLite kann nicht allein per Umgebungsvariable aktiviert werden. "
                "Die Datenbank muss nach einer geprüften Migration ausdrücklich als active_backend=sqlite markiert sein."
            )

        self._backend = JsonMelonlyRepository(
            self.json_path, load_json, save_json, self.io_lock
        )
        self.backend_name = "json"

    def _rollback_to_json(self, metadata: dict[str, str]) -> None:
        """Explicit startup rollback: export and verify JSON before changing the backend marker."""
        from pulse_melonly_migration import export_sqlite_to_json
        from pulse_melonly_store import MelonlyStore
        with self.io_lock:
            report = export_sqlite_to_json(self.db_path, self.json_path, io_lock=self.io_lock)
            store = MelonlyStore(self.db_path)
            store.set_active_backend(
                "json",
                expected_generation=metadata.get("generation"),
                expected_revision=report.get("revision"),
            )

    def __getattr__(self, name: str):
        target = getattr(self._backend, name)
        if name in self._WRITE_METHODS:
            def guarded(*args, **kwargs):
                # The same lock brackets SQLite mutations and the complete pulse.db/logs.json
                # snapshot in pulse_backup. SQLite's own transaction remains the DB lock.
                with self.io_lock:
                    return target(*args, **kwargs)
            return guarded
        return target
