"""Explicit, non-activating migration helpers for Melonly logs.json.

The normal application startup never imports or runs this module. Migration is an
operator-invoked step and deliberately leaves PULSE_LOGS_BACKEND on JSON.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ContextManager

from pulse_melonly_store import MelonlyStore, MelonlyStoreNotEmpty


class MelonlyMigrationError(RuntimeError):
    """Raised when a Melonly migration cannot be proven safe."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_source(path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    if not path.is_file():
        raise MelonlyMigrationError(f"Melonly-Quelldatei fehlt: {path}")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MelonlyMigrationError("logs.json ist kein gültiges UTF-8-JSON.") from exc
    if not isinstance(value, list):
        raise MelonlyMigrationError("logs.json muss eine JSON-Liste enthalten.")
    if any(not isinstance(item, dict) for item in value):
        raise MelonlyMigrationError("Jeder Melonly-Eintrag muss ein JSON-Objekt sein.")
    return raw, value


def _unique_backup_path(directory: Path, prefix: str, suffix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return directory / f"{prefix}{stamp}_{uuid.uuid4().hex[:8]}{suffix}"


def _backup_json(source: Path, backup_dir: Path, source_bytes: bytes) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = _unique_backup_path(backup_dir, "logs_pre_sqlite_", ".json")
    shutil.copy2(source, target)
    if target.read_bytes() != source_bytes or _sha256_file(target) != _sha256_bytes(source_bytes):
        target.unlink(missing_ok=True)
        raise MelonlyMigrationError("Sicherheitskopie von logs.json konnte nicht verifiziert werden.")
    return target


def _backup_database(db_path: Path, backup_dir: Path) -> Path | None:
    if not db_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = _unique_backup_path(backup_dir, "pulse_pre_melonly_", ".db")
    source = sqlite3.connect(str(db_path), timeout=15)
    destination = sqlite3.connect(str(target), timeout=15)
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise MelonlyMigrationError("SQLite-Sicherheitskopie besteht integrity_check nicht.")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        destination.close()
        source.close()
    return target


def _verify_import(store: MelonlyStore, expected: list[dict[str, Any]]) -> None:
    if store.count() != len(expected):
        raise MelonlyMigrationError("SQLite-Zeilenanzahl stimmt nicht mit logs.json überein.")
    actual = store.export_all()
    if actual != expected:
        raise MelonlyMigrationError("SQLite-Payload oder Reihenfolge weicht vom JSON-Original ab.")
    with sqlite3.connect(store.db_path, timeout=15) as con:
        rows = con.execute(
            """SELECT seq,log_id,target_user,target_user_key,roblox_id,
                      action_type,created_at,moderator_id,payload
               FROM melonly_logs ORDER BY seq ASC"""
        ).fetchall()
    if len(rows) != len(expected):
        raise MelonlyMigrationError("SQLite-Prüfung lieferte eine abweichende Zeilenanzahl.")
    for position, (row, entry) in enumerate(zip(rows, expected), start=1):
        seq, log_id, target_user, target_key, roblox_id, action_type, created_at, moderator_id, payload = row
        if seq != position:
            raise MelonlyMigrationError("SQLite-Sequenz bildet die ursprüngliche Listenposition nicht ab.")
        values = MelonlyStore._index_values(entry)
        if (log_id, target_user, target_key, roblox_id, action_type, created_at, moderator_id) != values:
            raise MelonlyMigrationError(f"SQLite-Indexfelder weichen an Position {position} von der Payload ab.")
        if json.loads(payload) != entry:
            raise MelonlyMigrationError(f"SQLite-Payload ist an Position {position} nicht identisch.")


def migrate_json_to_sqlite(
    json_path: str | os.PathLike[str],
    db_path: str | os.PathLike[str],
    backup_dir: str | os.PathLike[str],
    *,
    io_lock: ContextManager[Any] | None = None,
) -> dict[str, Any]:
    """Back up and import logs.json into pulse.db without switching the active backend.

    The source JSON is never modified. Re-running against the same source is
    accepted only when the existing imported payload and source hash match exactly.
    """
    source = Path(json_path)
    database = Path(db_path)
    backups = Path(backup_dir)
    lock = io_lock if io_lock is not None else nullcontext()

    with lock:
        raw, entries = _load_source(source)
        source_hash = _sha256_bytes(raw)

        # A database snapshot is created before initialize() can add any schema.
        db_backup = _backup_database(database, backups)
        json_backup = _backup_json(source, backups, raw)

        store = MelonlyStore(database)
        store.initialize()
        metadata = store.metadata()
        existing_count = store.count()
        imported_hash = metadata.get("import_source_sha256", "")

        if existing_count or imported_hash:
            if (
                imported_hash == source_hash
                and metadata.get("import_count") == str(len(entries))
                and existing_count == len(entries)
                and store.export_all() == entries
            ):
                _verify_import(store, entries)
                return {
                    "status": "already_migrated",
                    "count": len(entries),
                    "source_sha256": source_hash,
                    "json_backup": str(json_backup),
                    "database_backup": str(db_backup) if db_backup else "",
                    "active_backend": metadata.get("active_backend", "json"),
                }
            raise MelonlyStoreNotEmpty(
                "SQLite enthält bereits Daten oder eine andere Migration. "
                "Es wurde nichts überschrieben; bitte Datenbestand manuell prüfen."
            )

        imported = store.import_entries(entries, source_sha256=source_hash, active_backend="json")
        if imported != len(entries):
            raise MelonlyMigrationError("SQLite-Import meldet eine unerwartete Datensatzanzahl.")
        _verify_import(store, entries)

        # Prove the source was not changed while the migration was running.
        if _sha256_file(source) != source_hash:
            raise MelonlyMigrationError("logs.json wurde während der Migration verändert.")

        metadata = store.metadata()
        if metadata.get("active_backend") != "json":
            raise MelonlyMigrationError("Die Migration darf das aktive Backend nicht umschalten.")
        return {
            "status": "migrated",
            "count": imported,
            "source_sha256": source_hash,
            "json_backup": str(json_backup),
            "database_backup": str(db_backup) if db_backup else "",
            "active_backend": metadata["active_backend"],
        }


def export_sqlite_to_json(
    db_path: str | os.PathLike[str],
    json_path: str | os.PathLike[str],
    *,
    io_lock: ContextManager[Any] | None = None,
) -> dict[str, Any]:
    """Atomically export the authoritative SQLite dataset during an explicit rollback.

    This helper refuses to run unless SQLite is already marked as the active backend.
    It does not change metadata; the caller may mark JSON active only after read-back
    verification and a separately controlled startup transition.
    """
    database = Path(db_path)
    destination = Path(json_path)
    lock = io_lock if io_lock is not None else nullcontext()

    with lock:
        store = MelonlyStore(database)
        metadata = store.metadata()
        if metadata.get("active_backend") != "sqlite":
            raise MelonlyMigrationError(
                "Rückexport verweigert: SQLite ist laut Metadaten nicht das aktive Backend."
            )
        entries = store.export_all()
        if any(not isinstance(item, dict) for item in entries):
            raise MelonlyMigrationError("SQLite enthält Einträge außerhalb des unterstützten JSON-Objektformats.")

        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                json.dump(entries, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            check = json.loads(temp.read_text(encoding="utf-8"))
            if check != entries:
                raise MelonlyMigrationError("Temporärer JSON-Rückexport besteht den Read-back-Vergleich nicht.")
            os.replace(temp, destination)
            restored = json.loads(destination.read_text(encoding="utf-8"))
            if restored != entries:
                raise MelonlyMigrationError("Aktivierte JSON-Datei stimmt nach os.replace nicht mit SQLite überein.")
        finally:
            temp.unlink(missing_ok=True)

        return {
            "status": "exported",
            "count": len(entries),
            "revision": metadata.get("revision", ""),
            "generation": metadata.get("generation", ""),
            "sha256": _sha256_file(destination),
            "path": str(destination),
        }
