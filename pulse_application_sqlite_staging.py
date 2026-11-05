"""Isolated, non-runtime SQLite staging for the legacy applications.json payload.

This module never selects or changes the active application backend. Callers must
supply exact JSON bytes and a new, explicit staging path. The original byte stream
is embedded in the staging manifest so rollback can reproduce it exactly.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pulse_application_import import (
    ApplicationImportError,
    build_application_import_snapshot,
    verify_application_import_snapshot,
)


STAGING_SCHEMA_VERSION = 1
_APPLICATION_SCHEMA_VERSION = 1


class ApplicationSQLiteStagingError(RuntimeError):
    """The isolated application SQLite staging database is invalid or unsafe."""


class ApplicationSQLiteStagingConflict(ApplicationSQLiteStagingError):
    """The requested staging path or source content conflicts with an existing stage."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _decode_records(payload: bytes | bytearray) -> tuple[bytes, dict[str, dict[str, Any]]]:
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes or bytearray")
    source = bytes(payload)
    try:
        snapshot = build_application_import_snapshot(source)
        verify_application_import_snapshot(snapshot)
        records = json.loads(source.decode("utf-8", errors="strict"))
        if not isinstance(records, dict):
            raise ApplicationImportError("application payload must be a JSON object")
        normalized: dict[str, dict[str, Any]] = {}
        for application_id, record in records.items():
            if not isinstance(application_id, str) or not application_id.strip():
                raise ApplicationImportError("application ID must be a non-empty string")
            if not isinstance(record, dict):
                raise ApplicationImportError("application record must be a JSON object")
            # Confirm every row can be represented by strict JSON without changing values.
            json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            normalized[application_id] = record
    except (ApplicationImportError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ApplicationSQLiteStagingError("application payload is not a valid strict mapping") from exc
    return source, normalized


def _projection(record: dict[str, Any], field: str) -> str | None:
    value = record.get(field)
    return value if isinstance(value, str) else None


def _read_only_connection(path: str | os.PathLike[str]) -> sqlite3.Connection:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ApplicationSQLiteStagingError("staging database must be an existing regular file")
    connection = sqlite3.connect(
        source.resolve().as_uri() + "?mode=ro",
        uri=True,
        timeout=5,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _manifest(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    if not {"application_records", "application_staging_manifest"}.issubset(tables):
        raise ApplicationSQLiteStagingError("staging database schema is incomplete")
    row = connection.execute(
        """SELECT schema_version, source_sha256, source_record_count, source_payload_bytes,
                  source_payload, created_at
           FROM application_staging_manifest WHERE singleton=1"""
    ).fetchone()
    if row is None:
        raise ApplicationSQLiteStagingError("staging manifest is missing")
    payload = bytes(row["source_payload"])
    if _sha256(payload) != str(row["source_sha256"]):
        raise ApplicationSQLiteStagingError("embedded source payload digest does not match")
    if len(payload) != int(row["source_payload_bytes"]):
        raise ApplicationSQLiteStagingError("embedded source payload size does not match")
    if int(row["schema_version"]) != STAGING_SCHEMA_VERSION:
        raise ApplicationSQLiteStagingError("unsupported staging schema version")
    source, records = _decode_records(payload)
    if len(records) != int(row["source_record_count"]):
        raise ApplicationSQLiteStagingError("embedded source record count does not match")
    return {
        "schema_version": int(row["schema_version"]),
        "source_sha256": str(row["source_sha256"]),
        "source_record_count": int(row["source_record_count"]),
        "source_payload_bytes": int(row["source_payload_bytes"]),
        "source_payload": source,
        "created_at": str(row["created_at"]),
        "records": records,
    }


def create_staging_database(
    payload: bytes | bytearray,
    staging_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Create a new isolated SQLite stage from explicit JSON bytes.

    The target must not exist. Existing files, symlinks and runtime defaults are
    never replaced or selected. The original JSON bytes are stored verbatim.
    """
    source, records = _decode_records(payload)
    target = Path(staging_path)
    if not str(staging_path).strip():
        raise ValueError("staging_path must be explicit")
    if target.is_symlink() or target.exists():
        raise ApplicationSQLiteStagingConflict("staging path already exists")
    if target.parent.is_symlink() or not target.parent.is_dir():
        raise ApplicationSQLiteStagingError("staging parent must be an existing regular directory")

    # O_EXCL closes the check/create race and gives the new file private permissions on POSIX.
    try:
        descriptor = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ApplicationSQLiteStagingConflict("staging path already exists") from exc
    os.close(descriptor)

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(target, timeout=10)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.executescript(
            """
            CREATE TABLE application_staging_manifest (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                schema_version INTEGER NOT NULL,
                source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64),
                source_record_count INTEGER NOT NULL CHECK(source_record_count>=0),
                source_payload_bytes INTEGER NOT NULL CHECK(source_payload_bytes>=0),
                source_payload BLOB NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE application_records (
                application_id TEXT PRIMARY KEY,
                user_id TEXT,
                status TEXT,
                created_at TEXT,
                updated_at TEXT,
                schema_version INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX idx_application_records_user
                ON application_records(user_id, application_id);
            CREATE INDEX idx_application_records_status_updated
                ON application_records(status, updated_at);
            """
        )
        # executescript implicitly ends any prior transaction; begin only after DDL.
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO application_staging_manifest
               (singleton,schema_version,source_sha256,source_record_count,
                source_payload_bytes,source_payload,created_at)
               VALUES (1,?,?,?,?,?,?)""",
            (
                STAGING_SCHEMA_VERSION,
                _sha256(source),
                len(records),
                len(source),
                sqlite3.Binary(source),
                _now(),
            ),
        )
        for application_id, record in records.items():
            payload_json = json.dumps(
                record,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            connection.execute(
                """INSERT INTO application_records
                   (application_id,user_id,status,created_at,updated_at,schema_version,payload_json)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    application_id,
                    _projection(record, "user_id"),
                    _projection(record, "status"),
                    _projection(record, "created_at"),
                    _projection(record, "updated_at"),
                    _APPLICATION_SCHEMA_VERSION,
                    payload_json,
                ),
            )
        connection.commit()
        connection.close()
        connection = None

        verification = verify_staging_database(target, source_payload=source)
        if not verification["data_equal"] or not verification["source_bytes_match"]:
            raise ApplicationSQLiteStagingError("staging parity verification failed")
        return {
            "staging_path": str(target),
            "staging_schema_version": STAGING_SCHEMA_VERSION,
            "application_schema_version": _APPLICATION_SCHEMA_VERSION,
            "source_sha256": _sha256(source),
            "source_record_count": len(records),
            "source_payload_bytes": len(source),
            "integrity_check": verification["integrity_check"],
            "data_equal": verification["data_equal"],
            "source_bytes_match": verification["source_bytes_match"],
        }
    except Exception:
        if connection is not None:
            connection.rollback()
            connection.close()
        # Remove only the new target created above; never touch an existing path.
        try:
            if target.is_file() and not target.is_symlink():
                target.unlink()
            for suffix in ("-journal", "-wal", "-shm"):
                sidecar = Path(str(target) + suffix)
                if sidecar.is_file() and not sidecar.is_symlink():
                    sidecar.unlink()
        except OSError:
            pass
        raise


def verify_staging_database(
    staging_path: str | os.PathLike[str],
    *,
    source_payload: bytes | bytearray | None = None,
) -> dict[str, Any]:
    """Compare every staged payload with the embedded source snapshot, read-only."""
    connection = _read_only_connection(staging_path)
    try:
        manifest = _manifest(connection)
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        staged_rows = connection.execute(
            """SELECT application_id,user_id,status,created_at,updated_at,
                      schema_version,payload_json
               FROM application_records ORDER BY application_id"""
        ).fetchall()
        staged: dict[str, dict[str, Any]] = {}
        projection_mismatches = 0
        invalid_payload_rows = 0
        for row in staged_rows:
            application_id = str(row["application_id"])
            try:
                record = json.loads(str(row["payload_json"]))
                if not isinstance(record, dict):
                    raise ValueError("record payload must be an object")
            except (json.JSONDecodeError, TypeError, ValueError):
                invalid_payload_rows += 1
                continue
            staged[application_id] = record
            expected_projection = (
                _projection(record, "user_id"),
                _projection(record, "status"),
                _projection(record, "created_at"),
                _projection(record, "updated_at"),
            )
            actual_projection = (
                row["user_id"], row["status"], row["created_at"], row["updated_at"],
            )
            if expected_projection != actual_projection or int(row["schema_version"]) != _APPLICATION_SCHEMA_VERSION:
                projection_mismatches += 1

        source_records = manifest["records"]
        source_ids = set(source_records)
        staged_ids = set(staged)
        missing_ids = source_ids - staged_ids
        extra_ids = staged_ids - source_ids
        changed_records = sum(
            staged[application_id] != source_records[application_id]
            for application_id in source_ids & staged_ids
        )
        current_source_bytes_match = True
        current_source_digest = manifest["source_sha256"]
        if source_payload is not None:
            current_source, current_records = _decode_records(source_payload)
            current_source_digest = _sha256(current_source)
            current_source_bytes_match = current_source == manifest["source_payload"]
            if current_records != source_records:
                changed_records = max(changed_records, 1)

        data_equal = (
            integrity == "ok"
            and not missing_ids
            and not extra_ids
            and changed_records == 0
            and not projection_mismatches
            and not invalid_payload_rows
            and len(staged_rows) == int(manifest["source_record_count"])
        )
        return {
            "read_only": True,
            "staging_schema_version": manifest["schema_version"],
            "source_sha256": manifest["source_sha256"],
            "current_source_sha256": current_source_digest,
            "source_record_count": manifest["source_record_count"],
            "staged_record_count": len(staged_rows),
            "missing_records": len(missing_ids),
            "extra_records": len(extra_ids),
            "changed_records": changed_records,
            "projection_mismatches": projection_mismatches,
            "invalid_payload_rows": invalid_payload_rows,
            "source_bytes_match": current_source_bytes_match,
            "data_equal": data_equal and current_source_bytes_match,
            "integrity_check": integrity,
        }
    finally:
        connection.close()


def export_original_json(
    staging_path: str | os.PathLike[str],
) -> bytes:
    """Return the exact pre-migration JSON bytes for a rollback rehearsal."""
    connection = _read_only_connection(staging_path)
    try:
        manifest = _manifest(connection)
        return bytes(manifest["source_payload"])
    finally:
        connection.close()


def staging_manifest(staging_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Return non-personal migration metadata without application payloads."""
    connection = _read_only_connection(staging_path)
    try:
        manifest = _manifest(connection)
        return {
            "schema_version": manifest["schema_version"],
            "source_sha256": manifest["source_sha256"],
            "source_record_count": manifest["source_record_count"],
            "source_payload_bytes": manifest["source_payload_bytes"],
            "created_at": manifest["created_at"],
        }
    finally:
        connection.close()
