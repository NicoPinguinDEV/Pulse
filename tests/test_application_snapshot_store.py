"""Synthetic tests for durable application snapshots and guarded recovery."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from pulse_application_snapshot_store import (
    ApplicationSnapshotConflict,
    ApplicationSnapshotError,
    ApplicationSnapshotNeedsRecovery,
    PersistentApplicationSnapshotManager,
)


class FakeRepository:
    def __init__(self, path: Path, lock=None):
        self.path = str(path)
        self._lock = lock or threading.RLock()


class SnapshotManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.app_path = root / "applications.json"
        self.db_path = root / "pulse.db"
        self.original = b'{"app-old":{"status":"legacy","unknown_field":{"keep":true}}}\n'
        self.app_path.write_bytes(self.original)
        self.repository = FakeRepository(self.app_path)
        self.manager = PersistentApplicationSnapshotManager(
            self.repository, connect_factory=self._connect
        )

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _json(records):
        return json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def test_snapshot_survives_new_manager_and_duplicate_ids_are_rejected(self):
        saved = self.manager.create_snapshot(purpose="test-baseline", snapshot_id="snapshot-fixed")
        reopened = PersistentApplicationSnapshotManager(
            self.repository, connect_factory=self._connect
        )
        self.assertEqual(reopened.export_snapshot("snapshot-fixed"), self.original)
        self.assertEqual(reopened.get_snapshot("snapshot-fixed")["sha256"], saved["sha256"])
        with self.assertRaises(ApplicationSnapshotConflict):
            reopened.create_snapshot(purpose="duplicate", snapshot_id="snapshot-fixed")

    def test_snapshot_export_rechecks_persisted_payload_integrity(self):
        saved = self.manager.create_snapshot(snapshot_id="tamper-me")
        with self._connect() as db:
            db.execute(
                "UPDATE application_snapshots SET payload=? WHERE snapshot_id=?",
                (sqlite3.Binary(b'{"app-old":{"status":"hired"}}'), saved["snapshot_id"]),
            )
        with self.assertRaisesRegex(ApplicationSnapshotError, "integrity"):
            self.manager.export_snapshot(saved["snapshot_id"])

    def test_dry_run_classifies_records_and_blocks_deletions(self):
        current = {
            "same": {"status": "pending", "note": "unchanged"},
            "changed": {"status": "legacy", "note": "old"},
            "removed": {"status": "rejected"},
        }
        self.app_path.write_bytes(self._json(current))
        incoming = {
            "same": {"status": "pending", "note": "unchanged"},
            "changed": {"status": "legacy", "note": "new", "unknown": [1, 2]},
            "new": {"status": "accepted", "future_field": "preserve"},
        }
        before = self.app_path.read_bytes()
        preview = self.manager.preview_import(self._json(incoming))
        self.assertFalse(preview["valid"])
        self.assertEqual(preview["new_count"], 1)
        self.assertEqual(preview["changed_count"], 1)
        self.assertEqual(preview["unchanged_count"], 1)
        self.assertEqual(preview["removed_count"], 1)
        self.assertIn("import_would_remove_existing_records", preview["conflicts"])
        self.assertEqual(self.app_path.read_bytes(), before)

    def test_dry_run_flags_status_changes_without_normalizing_legacy_statuses(self):
        incoming = self._json({"app-old": {"status": "hired", "unknown_field": {"keep": True}}})
        preview = self.manager.preview_import(incoming)
        self.assertFalse(preview["valid"])
        self.assertIn("import_would_change_application_status", preview["conflicts"])
        self.assertEqual(json.loads(self.app_path.read_bytes())["app-old"]["status"], "legacy")

    def test_invalid_mapping_and_non_object_records_are_rejected(self):
        for payload in (b"{invalid", b'{"app": []}', b'{"app": {}, "app": {}}', b'{"x":{"n":NaN}}'):
            with self.subTest(payload=payload):
                preview = self.manager.preview_import(payload)
                self.assertFalse(preview["valid"])
                self.assertTrue(preview["errors"])
                self.assertEqual(self.app_path.read_bytes(), self.original)

    def test_apply_preserves_unknown_fields_and_rollback_restores_exact_bytes(self):
        incoming = self._json({
            "app-old": {"status": "legacy", "unknown_field": {"keep": True}},
            "app-new": {"status": "accepted", "unrecognized": "untouched"},
        })
        preview = self.manager.preview_import(incoming)
        self.assertTrue(preview["valid"], preview)
        applied = self.manager.apply_import(incoming, preview)
        self.assertEqual(applied["state"], "completed")
        self.assertEqual(self.app_path.read_bytes(), incoming)
        self.assertEqual(self.manager.state()["generation"], 1)
        self.assertEqual(self.manager.state()["revision"], 1)

        result = self.manager.rollback_operation(
            applied["operation_id"],
            expected_current_sha256=applied["sha256"],
            expected_generation=1,
            expected_revision=1,
        )
        self.assertEqual(result["state"], "completed")
        self.assertEqual(self.app_path.read_bytes(), self.original)
        self.assertEqual(self.manager.state()["generation"], 2)
        self.assertEqual(self.manager.state()["revision"], 2)
        journal = self.manager.list_journal()
        self.assertEqual([row["state"] for row in journal[:2]], ["completed", "completed"])

    def test_stale_preview_is_rejected_before_any_snapshot_or_write(self):
        incoming = self._json({
            "app-old": {"status": "legacy", "unknown_field": {"keep": True}},
            "app-new": {"status": "pending"},
        })
        preview = self.manager.preview_import(incoming)
        self.app_path.write_bytes(self._json({
            "app-old": {"status": "legacy", "unknown_field": {"keep": True}},
            "concurrent": {"status": "pending"},
        }))
        latest = self.app_path.read_bytes()
        with self.assertRaises(ApplicationSnapshotConflict):
            self.manager.apply_import(incoming, preview)
        self.assertEqual(self.app_path.read_bytes(), latest)
        self.assertEqual(self.manager.list_journal(), [])

    def test_failed_atomic_write_keeps_original_and_closes_journal(self):
        incoming = self._json({
            "app-old": {"status": "legacy", "unknown_field": {"keep": True}},
            "app-new": {"status": "pending"},
        })
        preview = self.manager.preview_import(incoming)
        with patch.object(self.manager, "_atomic_write", side_effect=ApplicationSnapshotError("synthetic")):
            with self.assertRaises(ApplicationSnapshotError):
                self.manager.apply_import(incoming, preview)
        self.assertEqual(self.app_path.read_bytes(), self.original)
        self.assertFalse(self.manager.state()["recovery_required"])
        self.assertEqual(self.manager.list_journal()[0]["state"], "rolled_back")

    def test_recovery_finalizes_a_complete_file_written_before_journal_commit(self):
        incoming = self._json({
            "app-old": {"status": "legacy", "unknown_field": {"keep": True}},
            "app-new": {"status": "pending"},
        })
        preview = self.manager.preview_import(incoming)
        with patch.object(
            self.manager, "_finalize_operation",
            side_effect=ApplicationSnapshotError("simulated process interruption"),
        ):
            with self.assertRaises(ApplicationSnapshotNeedsRecovery):
                self.manager.apply_import(incoming, preview)
        self.assertEqual(self.app_path.read_bytes(), incoming)
        self.assertTrue(self.manager.state()["recovery_required"])

        result = self.manager.recover_pending()
        self.assertEqual(result["operations"][0]["state"], "completed")
        self.assertFalse(self.manager.state()["recovery_required"])
        self.assertEqual(self.manager.state()["generation"], 1)
        self.assertEqual(self.manager.state()["revision"], 1)
        self.assertEqual(self.manager.list_journal()[0]["state"], "completed")

    def test_unknown_file_state_is_not_overwritten_and_requires_manual_recovery(self):
        incoming = self._json({
            "app-old": {"status": "legacy", "unknown_field": {"keep": True}},
            "app-new": {"status": "pending"},
        })
        preview = self.manager.preview_import(incoming)
        with patch.object(
            self.manager, "_finalize_operation",
            side_effect=ApplicationSnapshotError("simulated interruption"),
        ):
            with self.assertRaises(ApplicationSnapshotNeedsRecovery):
                self.manager.apply_import(incoming, preview)
        unknown = b'{"manual-change":{"status":"pending"}}'
        self.app_path.write_bytes(unknown)

        recovery = self.manager.recover_pending()
        self.assertTrue(recovery["recovery_required"])
        self.assertEqual(recovery["operations"][0]["state"], "needs_recovery")
        self.assertEqual(self.app_path.read_bytes(), unknown)
        with self.assertRaises(ApplicationSnapshotNeedsRecovery):
            self.manager.create_snapshot()

    def test_parallel_apply_uses_one_compare_and_swap_winner_and_monotonic_revision(self):
        incoming = self._json({
            "app-old": {"status": "legacy", "unknown_field": {"keep": True}},
            "app-new": {"status": "pending"},
        })
        preview = self.manager.preview_import(incoming)

        def attempt():
            try:
                self.manager.apply_import(incoming, preview)
                return "success"
            except ApplicationSnapshotConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _i: attempt(), range(2)))
        self.assertCountEqual(results, ["success", "conflict"])
        self.assertEqual(self.manager.state()["generation"], 1)
        self.assertEqual(self.manager.state()["revision"], 1)

    def test_full_backup_contains_and_restores_persistent_snapshot_tables(self):
        import pulse_backup
        import pulse_db

        base = Path(self.temp.name) / "backup-root"
        base.mkdir()
        app = base / "applications.json"
        app.write_bytes(self.original)
        db_path = base / "pulse.db"
        backup_dir = base / "backups"
        repository = FakeRepository(app)
        with (
            patch.object(pulse_db, "DB_PATH", str(db_path)),
            patch.object(pulse_db, "_SCHEMA_READY", set()),
        ):
            manager = PersistentApplicationSnapshotManager(repository)
            saved = manager.create_snapshot(snapshot_id="inside-full-backup")
            archive, manifest = pulse_backup.create_full_backup(base, backup_dir)
            self.assertIn("db/pulse.db", {entry["name"] for entry in manifest["files"]})

            with sqlite3.connect(db_path) as db:
                db.execute("DELETE FROM application_snapshots WHERE snapshot_id=?", (saved["snapshot_id"],))
            with self.assertRaises(ApplicationSnapshotError):
                manager.export_snapshot(saved["snapshot_id"])

            pulse_backup.restore_full_backup(archive, base, backup_dir)
            self.assertEqual(manager.export_snapshot(saved["snapshot_id"]), self.original)
            self.assertEqual(pulse_backup._integrity(db_path), "ok")


if __name__ == "__main__":
    unittest.main()
