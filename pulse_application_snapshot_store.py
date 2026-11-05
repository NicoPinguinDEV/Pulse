"""Durable snapshots and guarded replacement for the legacy applications.json store.

The module has no import-time filesystem or database side effects. All persistence
is initialized only when an explicit manager method is called.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pulse_application_import import (
    ApplicationImportError,
    ApplicationImportSnapshot,
    build_application_import_snapshot,
    verify_application_import_snapshot,
)


class ApplicationSnapshotError(RuntimeError):
    """A snapshot or controlled application replacement cannot be completed safely."""


class ApplicationSnapshotConflict(ApplicationSnapshotError):
    """The live application store changed after a preview or another operation is active."""


class ApplicationSnapshotNeedsRecovery(ApplicationSnapshotError):
    """A previous replacement has an ambiguous on-disk state and needs explicit recovery."""


_PENDING_STATES = ("prepared", "applying", "needs_recovery")
_TERMINAL_STATES = ("completed", "rolled_back", "failed")
_JOURNAL_STATES = (*_PENDING_STATES, *_TERMINAL_STATES)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _decode_mapping(payload: bytes) -> dict[str, Any]:
    """Use the strict shared parser first, then enforce the legacy record shape."""
    build_application_import_snapshot(payload)
    try:
        records = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApplicationSnapshotError("application JSON is invalid") from exc
    if not isinstance(records, dict):
        raise ApplicationSnapshotError("application JSON must be a mapping")
    for application_id, record in records.items():
        if not isinstance(application_id, str) or not application_id.strip():
            raise ApplicationSnapshotError("application mapping contains an empty or invalid key")
        if not isinstance(record, dict):
            raise ApplicationSnapshotError("application mapping contains a non-object record")
    return records


class PersistentApplicationSnapshotManager:
    """Persist exact application JSON snapshots in the already-backed-up pulse.db.

    Generation is the installed application-dataset generation. It advances only
    after a controlled import/restore has been durably finalized. Revision is the
    monotonically increasing count of finalized controlled replacements. Neither
    counter claims to count ordinary JSON writes made by existing application
    code; those are detected by the live file SHA-256 compare-and-swap guard.
    Snapshot creation alone does not advance either counter.
    """

    def __init__(self, repository, *, connect_factory=None) -> None:
        if not getattr(repository, "path", None) or not getattr(repository, "_lock", None):
            raise TypeError("repository must expose its configured path and shared lock")
        self.path = Path(os.path.abspath(os.fspath(repository.path)))
        self._lock = repository._lock
        self._connect_factory = connect_factory or self._default_connect

    @staticmethod
    def _default_connect():
        # Delayed import: importing this module must not initialize pulse.db.
        import pulse_db
        return pulse_db.connect()

    def _ensure_schema(self, db) -> None:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS application_snapshot_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
                revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
                snapshot_sequence INTEGER NOT NULL DEFAULT 0 CHECK (snapshot_sequence >= 0),
                active_operation_id TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS application_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                purpose TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                generation INTEGER NOT NULL CHECK (generation >= 0),
                revision INTEGER NOT NULL CHECK (revision >= 0),
                snapshot_sequence INTEGER NOT NULL CHECK (snapshot_sequence > 0),
                sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
                record_count INTEGER NOT NULL CHECK (record_count >= 0),
                payload_bytes INTEGER NOT NULL CHECK (payload_bytes >= 0),
                payload BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_application_snapshots_created
                ON application_snapshots(created_at DESC);

            CREATE TABLE IF NOT EXISTS application_snapshot_journal (
                operation_id TEXT PRIMARY KEY,
                operation_type TEXT NOT NULL CHECK (operation_type IN ('import', 'restore', 'rollback')),
                state TEXT NOT NULL CHECK (state IN (
                    'prepared', 'applying', 'completed', 'rolled_back', 'failed', 'needs_recovery'
                )),
                before_snapshot_id TEXT NOT NULL,
                target_snapshot_id TEXT NOT NULL,
                before_sha256 TEXT NOT NULL CHECK (length(before_sha256) = 64),
                target_sha256 TEXT NOT NULL CHECK (length(target_sha256) = 64),
                expected_generation INTEGER NOT NULL CHECK (expected_generation >= 0),
                expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
                resulting_generation INTEGER,
                resulting_revision INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (before_snapshot_id) REFERENCES application_snapshots(snapshot_id),
                FOREIGN KEY (target_snapshot_id) REFERENCES application_snapshots(snapshot_id)
            );
            CREATE INDEX IF NOT EXISTS idx_application_snapshot_journal_state
                ON application_snapshot_journal(state, created_at);
            """
        )
        db.execute(
            """INSERT OR IGNORE INTO application_snapshot_state
               (singleton, generation, revision, snapshot_sequence, active_operation_id, updated_at)
               VALUES (1, 0, 0, 0, NULL, ?)""",
            (_now(),),
        )
        # Idempotent upgrade for a partially initialized pre-sequence schema.
        state_columns = {str(row[1]) for row in db.execute(
            "PRAGMA table_info(application_snapshot_state)"
        ).fetchall()}
        if "snapshot_sequence" not in state_columns:
            db.execute(
                "ALTER TABLE application_snapshot_state ADD COLUMN snapshot_sequence INTEGER NOT NULL DEFAULT 0"
            )
        snapshot_columns = {str(row[1]) for row in db.execute(
            "PRAGMA table_info(application_snapshots)"
        ).fetchall()}
        if "snapshot_sequence" not in snapshot_columns:
            db.execute(
                "ALTER TABLE application_snapshots ADD COLUMN snapshot_sequence INTEGER NOT NULL DEFAULT 0"
            )
            existing = db.execute(
                "SELECT snapshot_id FROM application_snapshots ORDER BY created_at,snapshot_id"
            ).fetchall()
            for sequence, row in enumerate(existing, start=1):
                db.execute(
                    "UPDATE application_snapshots SET snapshot_sequence=? WHERE snapshot_id=?",
                    (sequence, str(row[0])),
                )
            db.execute(
                "UPDATE application_snapshot_state SET snapshot_sequence=? WHERE singleton=1",
                (len(existing),),
            )
        db.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_application_snapshots_sequence
               ON application_snapshots(snapshot_sequence)"""
        )

    @staticmethod
    def _state_from_db(db) -> dict[str, Any]:
        row = db.execute(
            """SELECT generation, revision, snapshot_sequence, active_operation_id, updated_at
               FROM application_snapshot_state WHERE singleton=1"""
        ).fetchone()
        if row is None:
            raise ApplicationSnapshotError("snapshot state is not initialized")
        return {
            "generation": int(row[0]),
            "revision": int(row[1]),
            "snapshot_sequence": int(row[2]),
            "active_operation_id": row[3],
            "updated_at": str(row[4]),
        }

    def state(self) -> dict[str, Any]:
        """Return durable counters and the current recovery-lock state."""
        with self._lock:
            with self._connect_factory() as db:
                self._ensure_schema(db)
                state = self._state_from_db(db)
                return {
                    "generation": state["generation"],
                    "revision": state["revision"],
                    "snapshot_sequence": state["snapshot_sequence"],
                    "recovery_required": bool(state["active_operation_id"]),
                }

    def _read_current(self) -> bytes:
        if self.path.is_symlink():
            raise ApplicationSnapshotError("application store path must not be a symbolic link")
        try:
            return self.path.read_bytes()
        except OSError as exc:
            raise ApplicationSnapshotError("application store cannot be read") from exc

    @staticmethod
    def _make_snapshot(payload: bytes, generation: int, revision: int) -> ApplicationImportSnapshot:
        try:
            return build_application_import_snapshot(
                payload, generation=generation, revision=revision
            )
        except (ApplicationImportError, TypeError) as exc:
            raise ApplicationSnapshotError("application snapshot failed validation") from exc

    def _insert_snapshot(
        self, db, payload: bytes, purpose: str, generation: int, revision: int,
        *, snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self._make_snapshot(payload, generation, revision)
        identifier = str(snapshot_id or uuid.uuid4())
        created = _now()
        state = self._state_from_db(db)
        sequence = state["snapshot_sequence"] + 1
        db.execute(
            """UPDATE application_snapshot_state SET snapshot_sequence=?,updated_at=?
               WHERE singleton=1""",
            (sequence, created),
        )
        try:
            db.execute(
                """INSERT INTO application_snapshots
                   (snapshot_id,purpose,schema_version,generation,revision,snapshot_sequence,
                    sha256,record_count,payload_bytes,payload,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    identifier, purpose, snapshot.schema_version, snapshot.generation,
                    snapshot.revision, sequence, snapshot.sha256, snapshot.record_count,
                    len(snapshot.payload), sqlite3.Binary(snapshot.payload), created,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ApplicationSnapshotConflict("snapshot identifier already exists") from exc
        return {
            "snapshot_id": identifier,
            "purpose": purpose,
            "schema_version": snapshot.schema_version,
            "generation": snapshot.generation,
            "revision": snapshot.revision,
            "snapshot_sequence": sequence,
            "sha256": snapshot.sha256,
            "record_count": snapshot.record_count,
            "payload_bytes": len(snapshot.payload),
            "created_at": created,
        }

    def create_snapshot(
        self, payload: bytes | bytearray | None = None, *, purpose: str = "manual",
        snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist an immutable snapshot of explicit bytes or the current file."""
        if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 64:
            raise ValueError("purpose must be a non-empty label of at most 64 characters")
        with self._lock:
            source = self._read_current() if payload is None else bytes(payload)
            try:
                _decode_mapping(source)
            except (ApplicationSnapshotError, ApplicationImportError, TypeError) as exc:
                raise ApplicationSnapshotError("snapshot payload is not a valid application mapping") from exc
            with self._connect_factory() as db:
                self._ensure_schema(db)
                state = self._state_from_db(db)
                if state["active_operation_id"]:
                    raise ApplicationSnapshotNeedsRecovery("application replacement requires recovery")
                db.commit()
                db.execute("BEGIN IMMEDIATE")
                state = self._state_from_db(db)
                if state["active_operation_id"]:
                    raise ApplicationSnapshotNeedsRecovery("application replacement requires recovery")
                return self._insert_snapshot(
                    db, source, purpose.strip(), state["generation"], state["revision"],
                    snapshot_id=snapshot_id,
                )

    def _read_snapshot(self, db, snapshot_id: str) -> tuple[ApplicationImportSnapshot, dict[str, Any]]:
        if not isinstance(snapshot_id, str) or not snapshot_id or len(snapshot_id) > 128:
            raise ValueError("invalid snapshot identifier")
        row = db.execute(
            """SELECT snapshot_id,purpose,schema_version,generation,revision,snapshot_sequence,
                      sha256,record_count,payload_bytes,payload,created_at
               FROM application_snapshots WHERE snapshot_id=?""",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise ApplicationSnapshotError("snapshot does not exist")
        payload = bytes(row[9])
        snapshot = ApplicationImportSnapshot(
            schema_version=int(row[2]),
            generation=int(row[3]),
            revision=int(row[4]),
            sha256=str(row[6]),
            record_count=int(row[7]),
            payload=payload,
        )
        try:
            verify_application_import_snapshot(snapshot)
            if len(payload) != int(row[8]):
                raise ApplicationImportError("snapshot payload size does not match")
        except ApplicationImportError as exc:
            raise ApplicationSnapshotError("stored snapshot failed integrity verification") from exc
        metadata = {
            "snapshot_id": str(row[0]), "purpose": str(row[1]),
            "schema_version": int(row[2]), "generation": int(row[3]),
            "revision": int(row[4]), "snapshot_sequence": int(row[5]),
            "sha256": str(row[6]), "record_count": int(row[7]),
            "payload_bytes": int(row[8]), "created_at": str(row[10]),
        }
        return snapshot, metadata

    def export_snapshot(self, snapshot_id: str) -> bytes:
        """Return exact original bytes only after rechecking all persisted metadata."""
        with self._lock:
            with self._connect_factory() as db:
                self._ensure_schema(db)
                snapshot, _ = self._read_snapshot(db, snapshot_id)
                return snapshot.payload

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        """Return non-personal snapshot metadata without exposing application content."""
        with self._lock:
            with self._connect_factory() as db:
                self._ensure_schema(db)
                _, metadata = self._read_snapshot(db, snapshot_id)
                return metadata

    def _preview_locked(self, payload: bytes | bytearray) -> dict[str, Any]:
        try:
            source = bytes(payload)
        except Exception:
            source = b""
        source_digest = _sha256(source)
        current = self._read_current()
        current_digest = _sha256(current)
        with self._connect_factory() as db:
            self._ensure_schema(db)
            state = self._state_from_db(db)

        result: dict[str, Any] = {
            "valid": False,
            "source_sha256": source_digest,
            "current_sha256": current_digest,
            "generation": state["generation"],
            "revision": state["revision"],
            "snapshot_sequence": state["snapshot_sequence"],
            "schema_version": None,
            "record_count": 0,
            "payload_bytes": len(source),
            "new_count": 0,
            "changed_count": 0,
            "unchanged_count": 0,
            "removed_count": 0,
            "warnings": [],
            "errors": [],
            "conflicts": [],
        }
        if state["active_operation_id"]:
            result["conflicts"].append("recovery_required")
        try:
            source_records = _decode_mapping(source)
            source_snapshot = self._make_snapshot(source, state["generation"], state["revision"])
            result["schema_version"] = source_snapshot.schema_version
            result["record_count"] = source_snapshot.record_count
        except (ApplicationSnapshotError, ApplicationImportError, TypeError):
            result["errors"].append("invalid_source_mapping")
            return result

        try:
            current_records = _decode_mapping(current)
        except (ApplicationSnapshotError, ApplicationImportError, TypeError):
            result["errors"].append("invalid_current_mapping")
            return result

        source_ids = set(source_records)
        current_ids = set(current_records)
        new_ids = source_ids - current_ids
        removed_ids = current_ids - source_ids
        common_ids = source_ids & current_ids
        changed_ids = {key for key in common_ids if source_records[key] != current_records[key]}
        unchanged_ids = common_ids - changed_ids

        result.update({
            "new_count": len(new_ids),
            "changed_count": len(changed_ids),
            "unchanged_count": len(unchanged_ids),
            "removed_count": len(removed_ids),
        })
        if removed_ids:
            result["conflicts"].append("import_would_remove_existing_records")
        for key in changed_ids:
            before_status = current_records[key].get("status")
            target_status = source_records[key].get("status")
            if before_status != target_status:
                result["conflicts"].append("import_would_change_application_status")
                break
        result["valid"] = not result["errors"] and not result["conflicts"]
        return result

    def preview_import(self, payload: bytes | bytearray) -> dict[str, Any]:
        """Compare explicit import bytes to the live mapping without writing anything."""
        if not isinstance(payload, (bytes, bytearray)):
            return {
                "valid": False, "source_sha256": "", "current_sha256": "",
                "generation": 0, "revision": 0, "schema_version": None,
                "record_count": 0, "payload_bytes": 0, "new_count": 0,
                "changed_count": 0, "unchanged_count": 0, "removed_count": 0,
                "warnings": [], "errors": ["payload_must_be_bytes"], "conflicts": [],
            }
        with self._lock:
            return self._preview_locked(payload)

    def _insert_operation(
        self, db, *, operation_type: str, before_payload: bytes, target_payload: bytes,
        expected_generation: int, expected_revision: int, target_snapshot_id: str | None = None,
    ) -> tuple[str, str, str]:
        state = self._state_from_db(db)
        if state["active_operation_id"]:
            raise ApplicationSnapshotNeedsRecovery("another application replacement requires recovery")
        if (state["generation"], state["revision"]) != (expected_generation, expected_revision):
            raise ApplicationSnapshotConflict("application generation or revision changed")
        operation_id = str(uuid.uuid4())
        before = self._insert_snapshot(
            db, before_payload, f"before_{operation_type}",
            expected_generation, expected_revision,
        )
        if target_snapshot_id:
            target_snapshot, target_meta = self._read_snapshot(db, target_snapshot_id)
            target_payload = target_snapshot.payload
            target_id = target_meta["snapshot_id"]
        else:
            target = self._insert_snapshot(
                db, target_payload, f"{operation_type}_target",
                expected_generation, expected_revision,
            )
            target_id = target["snapshot_id"]
        before_digest = _sha256(before_payload)
        target_digest = _sha256(target_payload)
        now = _now()
        db.execute(
            """INSERT INTO application_snapshot_journal
               (operation_id,operation_type,state,before_snapshot_id,target_snapshot_id,
                before_sha256,target_sha256,expected_generation,expected_revision,
                resulting_generation,resulting_revision,created_at,updated_at)
               VALUES (?,?, 'prepared', ?,?,?,?,?,?,NULL,NULL,?,?)""",
            (
                operation_id, operation_type, before["snapshot_id"], target_id,
                before_digest, target_digest, expected_generation, expected_revision,
                now, now,
            ),
        )
        db.execute(
            """UPDATE application_snapshot_state SET active_operation_id=?,updated_at=?
               WHERE singleton=1""",
            (operation_id, now),
        )
        return operation_id, before_digest, target_digest

    def _set_journal_state(self, operation_id: str, state: str) -> None:
        if state not in _JOURNAL_STATES:
            raise ValueError("invalid journal state")
        with self._connect_factory() as db:
            self._ensure_schema(db)
            db.execute(
                "UPDATE application_snapshot_journal SET state=?,updated_at=? WHERE operation_id=?",
                (state, _now(), operation_id),
            )

    def _mark_failed_or_recovery(self, operation_id: str, current_digest: str | None, before_digest: str, target_digest: str) -> str:
        if current_digest == before_digest:
            final_state = "rolled_back"
            with self._connect_factory() as db:
                self._ensure_schema(db)
                db.execute(
                    """UPDATE application_snapshot_journal SET state=?,updated_at=?
                       WHERE operation_id=?""",
                    (final_state, _now(), operation_id),
                )
                db.execute(
                    """UPDATE application_snapshot_state SET active_operation_id=NULL,updated_at=?
                       WHERE singleton=1 AND active_operation_id=?""",
                    (_now(), operation_id),
                )
            return final_state
        final_state = "needs_recovery"
        with self._connect_factory() as db:
            self._ensure_schema(db)
            db.execute(
                """UPDATE application_snapshot_journal SET state=?,updated_at=?
                   WHERE operation_id=?""",
                (final_state, _now(), operation_id),
            )
        return final_state

    def _finalize_operation(
        self, operation_id: str, expected_generation: int, expected_revision: int
    ) -> None:
        with self._connect_factory() as db:
            self._ensure_schema(db)
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            state = self._state_from_db(db)
            journal = db.execute(
                """SELECT state FROM application_snapshot_journal WHERE operation_id=?""",
                (operation_id,),
            ).fetchone()
            if journal is None:
                raise ApplicationSnapshotError("operation journal is missing")
            if (
                state["generation"] == expected_generation
                and state["revision"] == expected_revision
                and state["active_operation_id"] == operation_id
            ):
                resulting_generation = expected_generation + 1
                resulting_revision = expected_revision + 1
                db.execute(
                    """UPDATE application_snapshot_state
                       SET generation=?,revision=?,active_operation_id=NULL,updated_at=?
                       WHERE singleton=1""",
                    (resulting_generation, resulting_revision, _now()),
                )
            elif (
                state["generation"] == expected_generation + 1
                and state["revision"] == expected_revision + 1
                and state["active_operation_id"] in (None, operation_id)
            ):
                resulting_generation = state["generation"]
                resulting_revision = state["revision"]
            else:
                db.execute(
                    """UPDATE application_snapshot_journal SET state='needs_recovery',updated_at=?
                       WHERE operation_id=?""",
                    (_now(), operation_id),
                )
                raise ApplicationSnapshotNeedsRecovery("application counters changed during replacement")
            db.execute(
                """UPDATE application_snapshot_journal
                   SET state='completed',resulting_generation=?,resulting_revision=?,updated_at=?
                   WHERE operation_id=?""",
                (resulting_generation, resulting_revision, _now(), operation_id),
            )

    def _atomic_write(self, payload: bytes) -> None:
        if self.path.is_symlink():
            raise ApplicationSnapshotError("application store path must not be a symbolic link")
        parent = self.path.parent
        if not parent.is_dir():
            raise ApplicationSnapshotError("application store directory does not exist")
        descriptor = None
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=str(parent)
            )
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
            if os.name == "posix":
                try:
                    directory_fd = os.open(parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except OSError:
                    # Some filesystems do not permit fsync on a directory.
                    pass
        except OSError as exc:
            raise ApplicationSnapshotError("atomic application write failed") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _replace_payload(
        self, target_payload: bytes, *, operation_type: str, expected_current_sha256: str,
        expected_generation: int, expected_revision: int, target_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            before_payload = self._read_current()
            before_digest = _sha256(before_payload)
            if before_digest != expected_current_sha256:
                raise ApplicationSnapshotConflict("application file changed after preview")
            if _sha256(target_payload) == before_digest:
                raise ApplicationSnapshotConflict("target payload is already the current application store")
            with self._connect_factory() as db:
                self._ensure_schema(db)
                db.commit()
                db.execute("BEGIN IMMEDIATE")
                operation_id, recorded_before, target_digest = self._insert_operation(
                    db,
                    operation_type=operation_type,
                    before_payload=before_payload,
                    target_payload=target_payload,
                    expected_generation=expected_generation,
                    expected_revision=expected_revision,
                    target_snapshot_id=target_snapshot_id,
                )
            if recorded_before != before_digest:
                raise ApplicationSnapshotError("pre-operation snapshot digest mismatch")
            self._set_journal_state(operation_id, "applying")
            try:
                self._atomic_write(target_payload)
                if _sha256(self._read_current()) != target_digest:
                    raise ApplicationSnapshotError("application file digest mismatch after write")
                self._finalize_operation(operation_id, expected_generation, expected_revision)
            except BaseException as exc:
                try:
                    current_digest = _sha256(self._read_current())
                except ApplicationSnapshotError:
                    current_digest = None
                state = self._mark_failed_or_recovery(
                    operation_id, current_digest, before_digest, target_digest
                )
                if state == "needs_recovery":
                    raise ApplicationSnapshotNeedsRecovery(
                        "application replacement interrupted; explicit recovery is required"
                    ) from exc
                raise
            return {
                "operation_id": operation_id,
                "state": "completed",
                "generation": expected_generation + 1,
                "revision": expected_revision + 1,
                "sha256": target_digest,
            }

    def apply_import(self, payload: bytes | bytearray, preview: dict[str, Any]) -> dict[str, Any]:
        """Apply only the exact valid dry-run while its live hash/counters still match."""
        if not isinstance(payload, (bytes, bytearray)) or not isinstance(preview, dict):
            raise TypeError("apply_import requires payload bytes and a preview result")
        with self._lock:
            fresh = self._preview_locked(payload)
            required = ("source_sha256", "current_sha256", "generation", "revision")
            if not preview.get("valid") or any(preview.get(k) != fresh.get(k) for k in required):
                raise ApplicationSnapshotConflict("import preview is invalid or stale")
            if not fresh["valid"]:
                raise ApplicationSnapshotConflict("import preview contains validation errors or conflicts")
            if fresh["source_sha256"] == fresh["current_sha256"]:
                raise ApplicationSnapshotConflict("import payload is identical to the current application store")
            target = bytes(payload)
            try:
                _decode_mapping(target)
            except ApplicationSnapshotError as exc:
                raise ApplicationSnapshotConflict("import payload failed final validation") from exc
            return self._replace_payload(
                target,
                operation_type="import",
                expected_current_sha256=fresh["current_sha256"],
                expected_generation=fresh["generation"],
                expected_revision=fresh["revision"],
            )

    def restore_snapshot(
        self, snapshot_id: str, *, expected_current_sha256: str,
        expected_generation: int, expected_revision: int,
    ) -> dict[str, Any]:
        """Restore exact snapshot bytes using a new, compare-and-swap guarded journal entry."""
        with self._lock:
            with self._connect_factory() as db:
                self._ensure_schema(db)
                snapshot, _ = self._read_snapshot(db, snapshot_id)
            target = snapshot.payload
            _decode_mapping(target)
            return self._replace_payload(
                target,
                operation_type="restore",
                expected_current_sha256=expected_current_sha256,
                expected_generation=expected_generation,
                expected_revision=expected_revision,
                target_snapshot_id=snapshot_id,
            )

    def rollback_operation(
        self, operation_id: str, *, expected_current_sha256: str,
        expected_generation: int, expected_revision: int,
    ) -> dict[str, Any]:
        """Create a new guarded restore operation targeting an operation's before-snapshot."""
        with self._lock:
            with self._connect_factory() as db:
                self._ensure_schema(db)
                row = db.execute(
                    """SELECT before_snapshot_id FROM application_snapshot_journal
                       WHERE operation_id=? AND state='completed'""",
                    (operation_id,),
                ).fetchone()
                if row is None:
                    raise ApplicationSnapshotError("completed operation does not exist")
                snapshot_id = str(row[0])
            with self._connect_factory() as db:
                self._ensure_schema(db)
                snapshot, _ = self._read_snapshot(db, snapshot_id)
            return self._replace_payload(
                snapshot.payload,
                operation_type="rollback",
                expected_current_sha256=expected_current_sha256,
                expected_generation=expected_generation,
                expected_revision=expected_revision,
                target_snapshot_id=snapshot_id,
            )

    def recover_pending(self) -> dict[str, Any]:
        """Reconcile interrupted writes by hashes only; never overwrite an ambiguous file."""
        with self._lock:
            with self._connect_factory() as db:
                self._ensure_schema(db)
                rows = db.execute(
                    """SELECT operation_id,operation_type,state,before_sha256,target_sha256,
                              expected_generation,expected_revision
                       FROM application_snapshot_journal
                       WHERE state IN ('prepared','applying','needs_recovery')
                       ORDER BY created_at,operation_id"""
                ).fetchall()
                pending = [dict(row) for row in rows]
                state = self._state_from_db(db)
            outcomes = []
            try:
                current_digest = _sha256(self._read_current())
            except ApplicationSnapshotError:
                current_digest = None
            for row in pending:
                operation_id = str(row["operation_id"])
                if current_digest == str(row["before_sha256"]):
                    with self._connect_factory() as db:
                        self._ensure_schema(db)
                        db.execute(
                            """UPDATE application_snapshot_journal SET state='rolled_back',updated_at=?
                               WHERE operation_id=? AND state IN ('prepared','applying','needs_recovery')""",
                            (_now(), operation_id),
                        )
                        db.execute(
                            """UPDATE application_snapshot_state SET active_operation_id=NULL,updated_at=?
                               WHERE singleton=1 AND active_operation_id=?""",
                            (_now(), operation_id),
                        )
                    outcomes.append({"operation_id": operation_id, "state": "rolled_back"})
                elif current_digest == str(row["target_sha256"]):
                    try:
                        self._finalize_operation(
                            operation_id, int(row["expected_generation"]), int(row["expected_revision"])
                        )
                        outcomes.append({"operation_id": operation_id, "state": "completed"})
                    except ApplicationSnapshotError:
                        with self._connect_factory() as db:
                            self._ensure_schema(db)
                            db.execute(
                                """UPDATE application_snapshot_journal SET state='needs_recovery',updated_at=?
                                   WHERE operation_id=?""",
                                (_now(), operation_id),
                            )
                        outcomes.append({"operation_id": operation_id, "state": "needs_recovery"})
                else:
                    with self._connect_factory() as db:
                        self._ensure_schema(db)
                        db.execute(
                            """UPDATE application_snapshot_journal SET state='needs_recovery',updated_at=?
                               WHERE operation_id=?""",
                            (_now(), operation_id),
                        )
                    outcomes.append({"operation_id": operation_id, "state": "needs_recovery"})
            orphaned_lock = bool(state["active_operation_id"]) and not any(
                row["operation_id"] == state["active_operation_id"] for row in pending
            )
            return {
                "checked": len(pending),
                "recovery_required": orphaned_lock or any(item["state"] == "needs_recovery" for item in outcomes),
                "operations": outcomes,
            }

    def list_journal(self, limit: int = 50) -> list[dict[str, Any]]:
        """List operation metadata only; never return payloads or applicant information."""
        bounded = max(1, min(int(limit), 200))
        with self._lock:
            with self._connect_factory() as db:
                self._ensure_schema(db)
                rows = db.execute(
                    """SELECT operation_id,operation_type,state,before_sha256,target_sha256,
                              expected_generation,expected_revision,resulting_generation,
                              resulting_revision,created_at,updated_at
                       FROM application_snapshot_journal
                       ORDER BY created_at DESC,operation_id DESC LIMIT ?""",
                    (bounded,),
                ).fetchall()
                return [dict(row) for row in rows]
