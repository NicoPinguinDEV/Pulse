"""Isolated, explicitly requested SQLite staging for application migration prep.

This module never selects a runtime backend and never opens Pulse runtime files or
databases. Callers must supply verified snapshot bytes and a new staging-only path.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from pulse_application_import import (
    ApplicationImportSnapshot,
    verify_application_import_snapshot,
)

APPLICATION_STAGING_SCHEMA_VERSION = 1
_STAGING_SUFFIX = ".staging.sqlite3"


class ApplicationStagingError(RuntimeError):
    """The isolated staging database could not be created or verified safely."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise ApplicationStagingError("application record cannot be represented as strict JSON") from exc


def _records_from_snapshot(snapshot: ApplicationImportSnapshot) -> dict[str, dict[str, Any]]:
    try:
        verify_application_import_snapshot(snapshot)
        records = json.loads(snapshot.payload.decode("utf-8", errors="strict"))
    except Exception as exc:
        if isinstance(exc, ApplicationStagingError):
            raise
        raise ApplicationStagingError("source snapshot failed its integrity validation") from exc

    if not isinstance(records, dict):
        raise ApplicationStagingError("source payload must be an application mapping")

    validated: dict[str, dict[str, Any]] = {}
    for application_id, record in records.items():
        if not isinstance(application_id, str) or not application_id.strip() or "\x00" in application_id:
            raise ApplicationStagingError("source contains an empty or invalid application id")
        if not isinstance(record, dict):
            raise ApplicationStagingError("source contains a non-object application record")
        validated[application_id] = record
    return validated


def _project_text(record: dict[str, Any], field: str) -> str | None:
    value = record.get(field)
    return value if isinstance(value, str) else None


def _create_schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE application_staging_meta (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            schema_version INTEGER NOT NULL,
            source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
            source_generation INTEGER NOT NULL CHECK (source_generation >= 0),
            source_revision INTEGER NOT NULL CHECK (source_revision >= 0),
            source_record_count INTEGER NOT NULL CHECK (source_record_count >= 0)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE application_records (
            application_id TEXT PRIMARY KEY,
            user_id TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            record_schema_version INTEGER NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX idx_application_records_user "
        "ON application_records(user_id, application_id)"
    )
    db.execute(
        "CREATE INDEX idx_application_records_status_updated "
        "ON application_records(status, updated_at)"
    )
    db.execute(f"PRAGMA user_version = {APPLICATION_STAGING_SCHEMA_VERSION}")


def _insert_record(
    db: sqlite3.Connection, application_id: str, record: dict[str, Any]
) -> None:
    db.execute(
        """
        INSERT INTO application_records (
            application_id, user_id, status, created_at, updated_at,
            record_schema_version, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            application_id,
            _project_text(record, "user_id"),
            _project_text(record, "status"),
            _project_text(record, "created_at"),
            _project_text(record, "updated_at"),
            APPLICATION_STAGING_SCHEMA_VERSION,
            _canonical_json(record),
        ),
    )


def _staging_path(value: str | os.PathLike[str]) -> Path:
    try:
        path = Path(os.path.abspath(os.fspath(value)))
    except (TypeError, ValueError) as exc:
        raise ApplicationStagingError("an explicit staging database path is required") from exc
    if not path.name.endswith(_STAGING_SUFFIX):
        raise ApplicationStagingError(
            f"staging database path must end with {_STAGING_SUFFIX}"
        )
    if not path.parent.is_dir():
        raise ApplicationStagingError("staging database parent directory must already exist")
    return path


def create_application_staging_database(
    snapshot: ApplicationImportSnapshot,
    staging_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Create a new staging-only SQLite file from explicitly supplied snapshot bytes.

    Existing targets are never replaced. A failure removes only the file created
    by this invocation. No source path, runtime database, or active backend is read.
    """
    records = _records_from_snapshot(snapshot)
    target = _staging_path(staging_path)

    try:
        descriptor = os.open(
            os.fspath(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
    except FileExistsError as exc:
        raise ApplicationStagingError(
            "staging target already exists; refusing to overwrite"
        ) from exc
    except (OSError, ValueError) as exc:
        raise ApplicationStagingError("staging target could not be created safely") from exc

    os.close(descriptor)
    db: sqlite3.Connection | None = None
    try:
        if os.name == "posix":
            os.chmod(target, 0o600)

        db = sqlite3.connect(os.fspath(target), timeout=5, isolation_level=None)
        journal_mode = db.execute("PRAGMA journal_mode = DELETE").fetchone()
        if not journal_mode or str(journal_mode[0]).lower() != "delete":
            raise ApplicationStagingError("staging database did not enter rollback-journal mode")
        db.execute("PRAGMA synchronous = FULL")
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("BEGIN IMMEDIATE")
        _create_schema(db)
        db.execute(
            """
            INSERT INTO application_staging_meta (
                singleton, schema_version, source_sha256,
                source_generation, source_revision, source_record_count
            ) VALUES (1, ?, ?, ?, ?, ?)
            """,
            (
                APPLICATION_STAGING_SCHEMA_VERSION,
                snapshot.sha256,
                snapshot.generation,
                snapshot.revision,
                len(records),
            ),
        )
        for application_id, record in records.items():
            _insert_record(db, application_id, record)
        db.commit()

        integrity = db.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ApplicationStagingError("staging database failed SQLite integrity_check")
        db.close()
        db = None

        if os.name == "posix":
            os.chmod(target, 0o600)
        descriptor = os.open(os.fspath(target), os.O_RDWR)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

        report = verify_application_staging_database(snapshot, target)
        if not report["valid"]:
            raise ApplicationStagingError("staging database failed its immediate parity check")
        return report
    except Exception as exc:
        if db is not None:
            try:
                if db.in_transaction:
                    db.rollback()
            except sqlite3.Error:
                pass
            try:
                db.close()
            except sqlite3.Error:
                pass
        for candidate in (
            target,
            Path(os.fspath(target) + "-journal"),
            Path(os.fspath(target) + "-wal"),
            Path(os.fspath(target) + "-shm"),
        ):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(exc, ApplicationStagingError):
            raise
        raise ApplicationStagingError(
            "staging database creation failed; created artifacts were removed"
        ) from exc


def verify_application_staging_database(
    snapshot: ApplicationImportSnapshot,
    staging_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Read-only parity verification; reports aggregate checks but no record data."""
    records = _records_from_snapshot(snapshot)
    target = _staging_path(staging_path)
    if not target.is_file():
        raise ApplicationStagingError("staging database does not exist")

    try:
        db = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            db.execute("PRAGMA query_only = ON")
            integrity_row = db.execute("PRAGMA integrity_check").fetchone()
            integrity = str(integrity_row[0]) if integrity_row else "missing"
            user_version = db.execute("PRAGMA user_version").fetchone()[0]
            metadata_rows = db.execute(
                """
                SELECT schema_version, source_sha256, source_generation,
                       source_revision, source_record_count
                FROM application_staging_meta WHERE singleton = 1
                """
            ).fetchall()
            schema_objects = {
                tuple(row) for row in db.execute(
                    "SELECT type, name FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
                ).fetchall()
            }
            expected_objects = {
                ("table", "application_staging_meta"),
                ("table", "application_records"),
                ("index", "idx_application_records_user"),
                ("index", "idx_application_records_status_updated"),
            }
            expected_columns = {
                "application_staging_meta": (
                    "singleton", "schema_version", "source_sha256",
                    "source_generation", "source_revision", "source_record_count",
                ),
                "application_records": (
                    "application_id", "user_id", "status", "created_at",
                    "updated_at", "record_schema_version", "payload_json",
                ),
            }
            actual_columns = {
                table: tuple(
                    column[1]
                    for column in db.execute(f"PRAGMA table_info({table})").fetchall()
                )
                for table in expected_columns
            }
            index_columns = {
                "idx_application_records_user": tuple(
                    row[2] for row in db.execute(
                        "PRAGMA index_info(idx_application_records_user)"
                    ).fetchall()
                ),
                "idx_application_records_status_updated": tuple(
                    row[2] for row in db.execute(
                        "PRAGMA index_info(idx_application_records_status_updated)"
                    ).fetchall()
                ),
            }
            rows = db.execute(
                """
                SELECT application_id, user_id, status, created_at, updated_at,
                       record_schema_version, payload_json
                FROM application_records ORDER BY application_id
                """
            ).fetchall()
        finally:
            db.close()
    except sqlite3.Error as exc:
        raise ApplicationStagingError(
            "staging database could not be verified read-only"
        ) from exc

    schema_matches = (
        schema_objects == expected_objects
        and actual_columns == expected_columns
        and index_columns == {
            "idx_application_records_user": ("user_id", "application_id"),
            "idx_application_records_status_updated": ("status", "updated_at"),
        }
    )
    metadata_matches = len(metadata_rows) == 1
    if metadata_matches:
        metadata_matches = tuple(metadata_rows[0]) == (
            APPLICATION_STAGING_SCHEMA_VERSION,
            snapshot.sha256,
            snapshot.generation,
            snapshot.revision,
            len(records),
        )

    stored_ids = [str(row[0]) for row in rows]
    record_ids_equal = (
        len(stored_ids) == len(set(stored_ids)) and set(stored_ids) == set(records)
    )
    payloads_equal = record_ids_equal
    projected_columns_equal = record_ids_equal

    if record_ids_equal:
        for row in rows:
            record = records[str(row[0])]
            if str(row[6]) != _canonical_json(record):
                payloads_equal = False
            expected_projection = (
                _project_text(record, "user_id"),
                _project_text(record, "status"),
                _project_text(record, "created_at"),
                _project_text(record, "updated_at"),
                APPLICATION_STAGING_SCHEMA_VERSION,
            )
            if tuple(row[1:6]) != expected_projection:
                projected_columns_equal = False

    valid = (
        integrity == "ok"
        and user_version == APPLICATION_STAGING_SCHEMA_VERSION
        and metadata_matches
        and schema_matches
        and record_ids_equal
        and payloads_equal
        and projected_columns_equal
        and len(rows) == len(records)
    )
    return {
        "valid": valid,
        "read_only": True,
        "schema_version": APPLICATION_STAGING_SCHEMA_VERSION,
        "source_sha256": snapshot.sha256,
        "source_generation": snapshot.generation,
        "source_revision": snapshot.revision,
        "source_record_count": len(records),
        "staged_record_count": len(rows),
        "record_ids_equal": record_ids_equal,
        "payloads_equal": payloads_equal,
        "projected_columns_equal": projected_columns_equal,
        "metadata_matches": metadata_matches,
        "schema_matches": schema_matches,
        "integrity_check": integrity,
    }


__all__ = [
    "APPLICATION_STAGING_SCHEMA_VERSION",
    "ApplicationStagingError",
    "create_application_staging_database",
    "verify_application_staging_database",
]
