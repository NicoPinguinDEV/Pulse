"""Synthetic tests for isolated SQLite application migration staging."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pulse_application_sqlite_staging import (
    ApplicationSQLiteStagingConflict,
    ApplicationSQLiteStagingError,
    create_staging_database,
    export_original_json,
    staging_manifest,
    verify_staging_database,
)


class ApplicationSQLiteStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = (
            b'{\n'
            b'  "app_1": {"user_id":"111", "status":"rejected", "text":"Gr\\xc3\\xbc\\xc3\\x9fe", '
            b'"unknown":{"keep":[1,true,null]}},\n'
            b'  "app_2": {"user_id":"222", "status":"legacy_custom", "created_at":"2026-11-01T10:00:00Z"},\n'
            b'  "app_3": {"status": "pending"}\n'
            b'}\n'
        )
        self.stage = self.root / "staging.sqlite"

    def test_stage_preserves_all_records_unknown_fields_and_exact_source_bytes(self):
        result = create_staging_database(self.source, self.stage)

        self.assertTrue(result["data_equal"])
        self.assertTrue(result["source_bytes_match"])
        self.assertEqual(result["source_record_count"], 3)
        self.assertEqual(result["integrity_check"], "ok")
        self.assertEqual(export_original_json(self.stage), self.source)

        verified = verify_staging_database(self.stage, source_payload=self.source)
        self.assertTrue(verified["read_only"])
        self.assertTrue(verified["data_equal"])
        self.assertEqual(verified["missing_records"], 0)
        self.assertEqual(verified["extra_records"], 0)
        self.assertEqual(verified["changed_records"], 0)
        self.assertEqual(verified["projection_mismatches"], 0)

        with sqlite3.connect(self.stage) as con:
            stored = json.loads(con.execute(
                "SELECT payload_json FROM application_records WHERE application_id='app_1'"
            ).fetchone()[0])
            self.assertEqual(stored["unknown"], {"keep": [1, True, None]})
            indexes = {row[1] for row in con.execute(
                "PRAGMA index_list('application_records')"
            ).fetchall()}
        self.assertIn("idx_application_records_user", indexes)
        self.assertIn("idx_application_records_status_updated", indexes)

    def test_unknown_and_noncanonical_status_values_are_preserved_without_normalization(self):
        create_staging_database(self.source, self.stage)
        with sqlite3.connect(self.stage) as con:
            row = con.execute(
                "SELECT status,payload_json FROM application_records WHERE application_id='app_2'"
            ).fetchone()
        self.assertEqual(row[0], "legacy_custom")
        self.assertEqual(json.loads(row[1])["status"], "legacy_custom")

    def test_rollback_returns_the_exact_original_json_byte_stream(self):
        create_staging_database(self.source, self.stage)
        restored = export_original_json(self.stage)
        self.assertEqual(restored, self.source)
        self.assertEqual(len(restored), len(self.source))

    def test_existing_target_is_never_overwritten(self):
        self.stage.write_bytes(b"do-not-touch")
        with self.assertRaises(ApplicationSQLiteStagingConflict):
            create_staging_database(self.source, self.stage)
        self.assertEqual(self.stage.read_bytes(), b"do-not-touch")

    def test_symlink_target_is_rejected_and_target_bytes_remain_unchanged(self):
        if not hasattr(os, "symlink"):
            self.skipTest("Symlinks are unavailable on this platform.")
        protected = self.root / "protected.sqlite"
        protected.write_bytes(b"protected")
        try:
            self.stage.symlink_to(protected)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is not permitted.")
        with self.assertRaises(ApplicationSQLiteStagingConflict):
            create_staging_database(self.source, self.stage)
        self.assertEqual(protected.read_bytes(), b"protected")

    def test_invalid_payloads_are_rejected_before_a_stage_file_is_created(self):
        invalid_payloads = (
            b"\\xff",
            b'{"app": {"status":"pending"}, "app": {"status":"hired"}}',
            b"[]",
            b'{"app": "not-an-object"}',
            b'{"   ": {"status":"pending"}}',
            b'{"app": {"value": NaN}}',
            b'{"app": {"value": 1e400}}',
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ApplicationSQLiteStagingError):
                    create_staging_database(payload, self.stage)
                self.assertFalse(self.stage.exists())

    def test_source_byte_drift_is_not_hidden_by_equal_decoded_records(self):
        create_staging_database(self.source, self.stage)
        same_records_different_bytes = self.source.replace(b'{\n', b'{')
        report = verify_staging_database(
            self.stage, source_payload=same_records_different_bytes
        )
        self.assertFalse(report["source_bytes_match"])
        self.assertFalse(report["data_equal"])
        self.assertEqual(report["changed_records"], 0)

    def test_staged_payload_tampering_is_detected(self):
        create_staging_database(self.source, self.stage)
        with sqlite3.connect(self.stage) as con:
            con.execute(
                "UPDATE application_records SET payload_json=? WHERE application_id=?",
                ('{"status":"hired"}', "app_1"),
            )
        report = verify_staging_database(self.stage)
        self.assertFalse(report["data_equal"])
        self.assertEqual(report["changed_records"], 1)

    def test_embedded_rollback_payload_tampering_is_detected(self):
        create_staging_database(self.source, self.stage)
        with sqlite3.connect(self.stage) as con:
            con.execute(
                "UPDATE application_staging_manifest SET source_payload=? WHERE singleton=1",
                (sqlite3.Binary(b'{"tampered":{}}'),),
            )
        with self.assertRaises(ApplicationSQLiteStagingError):
            export_original_json(self.stage)

    def test_manifest_contains_only_integrity_metadata(self):
        create_staging_database(self.source, self.stage)
        manifest = staging_manifest(self.stage)
        self.assertEqual(manifest["source_record_count"], 3)
        self.assertEqual(manifest["source_payload_bytes"], len(self.source))
        self.assertEqual(manifest["source_sha256"], __import__("hashlib").sha256(self.source).hexdigest())
        self.assertNotIn("payload", manifest)
        self.assertNotIn("application_id", manifest)

    def test_import_module_does_not_create_runtime_files_or_directories(self):
        # The staging module has no import-time database or filesystem initialization.
        before = {item.name for item in self.root.iterdir()}
        __import__("pulse_application_sqlite_staging")
        after = {item.name for item in self.root.iterdir()}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
