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

import pulse_migrations
from pulse_melonly_store import MelonlyStore, MelonlyStoreNotEmpty


MIGRATION_NAME = "melonly_logs_sqlite_v1"


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


def _applied_migrations(db_path: Path) -> set[str]:
    if not db_path.is_file():
        return set()
    try:
        with sqlite3.connect(str(db_path), timeout=15) as con:
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if not exists:
                return set()
            return {str(row[0]) for row in con.execute("SELECT name FROM schema_migrations")}
    except sqlite3.Error as exc:
        raise MelonlyMigrationError("Migrationsstatus der SQLite-Datenbank ist nicht lesbar.") from exc


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

        # Back up the exact JSON bytes before the versioned SQLite migration.
        json_backup = _backup_json(source, backups, raw)
        database.parent.mkdir(parents=True, exist_ok=True)
        store = MelonlyStore(database)

        def apply_migration(con: sqlite3.Connection) -> None:
            store.initialize_in_transaction(con)
            existing = int(con.execute("SELECT COUNT(*) FROM melonly_logs").fetchone()[0])
            marker = con.execute(
                "SELECT value FROM melonly_logs_meta WHERE key='import_source_sha256'"
            ).fetchone()
            if existing or (marker and marker[0]):
                raise MelonlyStoreNotEmpty(
                    "SQLite enthält bereits Daten oder eine andere Migration."
                )
            for position, entry in enumerate(entries, start=1):
                store._insert(con, entry, seq=position)
            metadata = {
                "format_version": "1",
                "generation": uuid.uuid4().hex,
                "active_backend": "json",
                "revision": "0",
                "import_source_sha256": source_hash,
                "import_count": str(len(entries)),
            }
            for key, value in metadata.items():
                con.execute(
                    """INSERT INTO melonly_logs_meta(key,value) VALUES (?,?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (key, value),
                )

        report = pulse_migrations.run(
            str(database), str(backups), [(MIGRATION_NAME, apply_migration)]
        )
        if report.get("failed"):
            raise MelonlyMigrationError(
                f"SQLite-Migration fehlgeschlagen: {report['failed']}"
            )

        metadata = store.metadata()
        existing_count = store.count()
        imported_hash = metadata.get("import_source_sha256", "")
        if (
            imported_hash != source_hash
            or metadata.get("import_count") != str(len(entries))
            or existing_count != len(entries)
            or store.export_all() != entries
        ):
            raise MelonlyStoreNotEmpty(
                "SQLite enthält keinen nachweislich identischen Import. "
                "Es wurde nichts überschrieben; bitte Datenbestand manuell prüfen."
            )
        _verify_import(store, entries)

        # Prove the source was not changed while the migration was running.
        if _sha256_file(source) != source_hash:
            raise MelonlyMigrationError("logs.json wurde während der Migration verändert.")
        if metadata.get("active_backend") != "json":
            raise MelonlyMigrationError("Die Migration darf das aktive Backend nicht umschalten.")

        return {
            "status": "already_migrated" if MIGRATION_NAME in _applied_migrations(database) else "migrated",
            "count": existing_count,
            "source_sha256": source_hash,
            "json_backup": str(json_backup),
            "database_backup": str(report.get("backup") or ""),
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
