"""Runtime repository adapter for Melonly records.

JSON remains the default source. SQLite is used only after an explicit startup
setting and a matching active_backend marker have both been verified.
The adapter never initializes or migrates a database on its own.
"""
from __future__ import annotations

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
    """Compatibility implementation with the same method contract as MelonlyStore."""

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

    def _read(self) -> list[Any]:
        rows = self._load_json(self.json_path, [])
        if not isinstance(rows, list):
            raise MelonlyBackendError("logs.json enthält keine gültige Liste; Melonly-Schreibzugriffe wurden gestoppt.")
        return rows

    def count(self) -> int:
        with self._lock:
            return len(self._read())

    def count_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            rows = self._read()
        for entry in rows:
            action = str(entry.get("type") or "") if isinstance(entry, Mapping) else ""
            counts[action] = counts.get(action, 0) + 1
        return counts

    def latest(self, limit: int = 50) -> list[Any]:
        safe_limit = max(0, min(int(limit), 10000))
        if not safe_limit:
            return []
        with self._lock:
            return self._read()[-safe_limit:]

    def latest_matching_types(self, log_types: Iterable[str], limit: int = 120) -> list[Any]:
        allowed = set(log_types)
        safe_limit = max(0, min(int(limit), 10000))
        if not safe_limit or not allowed:
            return []
        with self._lock:
            rows = self._read()
        out = []
        for entry in reversed(rows):
            if isinstance(entry, Mapping) and entry.get("type") in allowed:
                out.append(entry)
                if len(out) >= safe_limit:
                    break
        return out

    def append(self, entry: Mapping[str, Any]) -> int:
        if not isinstance(entry, Mapping):
            raise TypeError("Melonly entries must be mappings")
        with self._lock:
            rows = self._read()
            rows.append(dict(entry))
            self._save_json(self.json_path, rows)
            return len(rows)

    def find_first_by_id(self, log_id: str) -> tuple[int, Any] | None:
        with self._lock:
            rows = self._read()
        for index, entry in enumerate(rows, start=1):
            if isinstance(entry, Mapping) and entry.get("id") == log_id:
                return index, entry
        return None

    def update_by_seq(self, seq: int, entry: Mapping[str, Any]) -> bool:
        if not isinstance(entry, Mapping):
            raise TypeError("Melonly entries must be mappings")
        with self._lock:
            rows = self._read()
            index = int(seq) - 1
            if index < 0 or index >= len(rows):
                return False
            rows[index] = dict(entry)
            self._save_json(self.json_path, rows)
            return True

    def delete_by_seq(self, seq: int) -> bool:
        with self._lock:
            rows = self._read()
            index = int(seq) - 1
            if index < 0 or index >= len(rows):
                return False
            del rows[index]
            self._save_json(self.json_path, rows)
            return True

    def delete_all_by_id(self, log_id: str) -> int:
        """Retain the existing POST /log/delete behavior for duplicate IDs."""
        with self._lock:
            rows = self._read()
            kept = [entry for entry in rows
                    if not isinstance(entry, Mapping) or entry.get("id") != log_id]
            removed = len(rows) - len(kept)
            if removed:
                self._save_json(self.json_path, kept)
            return removed

    def export_all(self) -> list[Any]:
        with self._lock:
            return list(self._read())

    def iter_all(self, batch_size: int = 500):
        # JSON is already materialized by load_json; preserve its complete list order.
        yield from self.export_all()

    def player_history(self, query: str) -> list[Any]:
        rows = self.export_all()
        needle = str(query or "").strip()[:80].lstrip("@").strip().casefold()
        if not needle:
            return []

        def player_name(entry):
            return str(entry.get("target_user") or "").strip().lstrip("@").casefold() if isinstance(entry, Mapping) else ""

        def player_id(entry):
            value = str(entry.get("roblox_id") or "").strip() if isinstance(entry, Mapping) else ""
            return value if value.isdigit() else ""

        candidates = [entry for entry in rows if player_id(entry) == needle] if needle.isdigit() else []
        if not candidates:
            exact = [entry for entry in rows if player_name(entry) == needle]
            candidates = exact or [entry for entry in rows if needle in player_name(entry)]
        names = {player_name(entry) for entry in candidates if player_name(entry)}
        ids = {player_id(entry) for entry in candidates if player_id(entry)}
        return [entry for entry in rows if (player_name(entry) and player_name(entry) in names)
                or (player_id(entry) and player_id(entry) in ids)]

    def roblox_user_history(self, roblox_id: str, username: str) -> list[Any]:
        id_value, name_value = str(roblox_id), str(username).lower()
        return [entry for entry in self.export_all() if isinstance(entry, Mapping) and (
            str(entry.get("roblox_id")) == id_value
            or str(entry.get("target_user", "")).lower() == name_value
        )]

    def roblox_suggestions(self, limit: int = 5000) -> list[Any]:
        safe_limit = max(0, min(int(limit), 5000))
        if not safe_limit:
            return []
        with self._lock:
            return list(reversed(self._read()[-safe_limit:]))

    def search_payload(self, query: str) -> list[Any]:
        needle = str(query or "").lower().strip()
        if not needle:
            return []
        return [entry for entry in self.export_all()
                if needle in json.dumps(entry, ensure_ascii=False).lower()]


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
