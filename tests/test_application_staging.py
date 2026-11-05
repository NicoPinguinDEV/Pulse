"""Synthetic tests for isolated Phase 15.6f SQLite staging and parity checks."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pulse_application_import import build_application_import_snapshot
from pulse_application_staging import (
    APPLICATION_STAGING_SCHEMA_VERSION,
    ApplicationStagingError,
    create_application_staging_database,
    verify_application_staging_database,
)


class ApplicationStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.payload = (
            b'{\n'
            b'  "app-1": {"user_id":"user-1","status":"pending","created_at":"2026-01-01",'
            b'"unknown_legacy_field":{"keep":[1,true,null]}},\n'
            b'  "app-2": {"user_id":"user-2","status":"future_status","updated_at":"2026-02-02"},\n'
            b'  "app-3": {"unknown":"preserve-without-status"}\n'
            b'}\n'
        )
        self.snapshot = build_application_import_snapshot(
            self.payload, generation=7, revision=11
        )
        self.target = self.root / "applications.phase15-6f.staging.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_staging_import_preserves_full_payload_and_unknown_statuses(self):
        report = create_application_staging_database(self.snapshot, self.target)
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["source_sha256"], self.snapshot.sha256)
        self.assertEqual(report["source_generation"], 7)
        self.assertEqual(report["source_revision"], 11)
        self.assertEqual(report["source_record_count"], 3)
        self.assertEqual(report["staged_record_count"], 3)
        self.assertTrue(report["record_ids_equal"])
        self.assertTrue(report["payloads_equal"])
        self.assertTrue(report["projected_columns_equal"])
        self.assertTrue(report["metadata_matches"])
        self.assertEqual(self.snapshot.payload, self.payload)

        with sqlite3.connect(self.target) as db:
            self.assertEqual(
                db.execute("PRAGMA user_version").fetchone()[0],
                APPLICATION_STAGING_SCHEMA_VERSION,
            )
            rows = db.execute(
                "SELECT application_id, status, payload_json "
                "FROM application_records ORDER BY application_id"
            ).fetchall()
        self.assertEqual([row[0] for row in rows], ["app-1", "app-2", "app-3"])
        self.assertEqual([row[1] for row in rows], ["pending", "future_status", None])
        self.assertIn('"unknown_legacy_field"', rows[0][2])
        self.assertIn('"unknown":"preserve-without-status"', rows[2][2])

    def test_staging_file_is_private_on_posix(self):
        create_application_staging_database(self.snapshot, self.target)
        if os.name == "posix":
            self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)

    def test_verify_is_read_only_and_reports_tampered_payload(self):
        create_application_staging_database(self.snapshot, self.target)
        with sqlite3.connect(self.target) as db:
            db.execute(
                "UPDATE application_records SET payload_json=? WHERE application_id=?",
                ('{"status":"changed"}', "app-1"),
            )
        report = verify_application_staging_database(self.snapshot, self.target)
        self.assertTrue(report["read_only"])
        self.assertFalse(report["valid"])
        self.assertFalse(report["payloads_equal"])
        with sqlite3.connect(self.target.as_uri() + "?mode=ro", uri=True) as db:
            self.assertEqual(
                db.execute(
                    "SELECT payload_json FROM application_records WHERE application_id=?",
                    ("app-1",),
                ).fetchone()[0],
                '{"status":"changed"}',
            )

    def test_schema_drift_is_detected_by_read_only_verification(self):
        create_application_staging_database(self.snapshot, self.target)
        with sqlite3.connect(self.target) as db:
            db.execute("DROP INDEX idx_application_records_user")
        report = verify_application_staging_database(self.snapshot, self.target)
        self.assertFalse(report["valid"])
        self.assertFalse(report["schema_matches"])

    def test_non_mapping_records_are_rejected_before_creating_target(self):
        invalid = build_application_import_snapshot(b'{"app-1":[]}')
        with self.assertRaises(ApplicationStagingError):
            create_application_staging_database(invalid, self.target)
        self.assertFalse(self.target.exists())

    def test_blank_application_ids_are_rejected(self):
        invalid = build_application_import_snapshot(b'{"   ":{"status":"pending"}}')
        with self.assertRaisesRegex(ApplicationStagingError, "application id"):
            create_application_staging_database(invalid, self.target)
        self.assertFalse(self.target.exists())

    def test_invalid_os_path_is_rejected_without_creating_artifacts(self):
        invalid_target = str(self.root / "bad\x00.staging.sqlite3")
        with self.assertRaises(ApplicationStagingError):
            create_application_staging_database(self.snapshot, invalid_target)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_file_is_never_overwritten(self):
        self.target.write_bytes(b"do-not-touch")
        with self.assertRaisesRegex(ApplicationStagingError, "already exists"):
            create_application_staging_database(self.snapshot, self.target)
        self.assertEqual(self.target.read_bytes(), b"do-not-touch")

    def test_path_must_be_explicitly_marked_as_staging(self):
        invalid_target = self.root / "pulse.db"
        with self.assertRaisesRegex(ApplicationStagingError, "staging.sqlite3"):
            create_application_staging_database(self.snapshot, invalid_target)
        self.assertFalse(invalid_target.exists())

    def test_missing_parent_directory_is_not_created_implicitly(self):
        invalid_target = self.root / "missing" / "run.staging.sqlite3"
        with self.assertRaisesRegex(ApplicationStagingError, "parent directory"):
            create_application_staging_database(self.snapshot, invalid_target)
        self.assertFalse(invalid_target.parent.exists())

    def test_failed_transaction_removes_only_its_new_staging_artifact(self):
        with patch(
            "pulse_application_staging._insert_record",
            side_effect=RuntimeError("synthetic insertion failure"),
        ):
            with self.assertRaisesRegex(
                ApplicationStagingError, "artifacts were removed"
            ):
                create_application_staging_database(self.snapshot, self.target)
        self.assertFalse(self.target.exists())
        self.assertFalse(Path(str(self.target) + "-journal").exists())

    def test_tampered_source_snapshot_is_rejected_without_artifacts(self):
        from dataclasses import replace

        tampered = replace(self.snapshot, sha256="0" * 64)
        with self.assertRaises(ApplicationStagingError):
            create_application_staging_database(tampered, self.target)
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
