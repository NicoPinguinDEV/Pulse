"""Synthetic regression tests for the Phase 15.6d import integrity contract."""
from dataclasses import FrozenInstanceError, replace
import unittest

from pulse_application_import import (
    APPLICATION_IMPORT_SCHEMA_VERSION,
    ApplicationImportError,
    application_import_manifest,
    build_application_import_snapshot,
    export_application_import_payload,
    verify_application_import_snapshot,
)


class ApplicationImportSnapshotTests(unittest.TestCase):
    def test_snapshot_keeps_exact_bytes_unknown_fields_and_metadata(self):
        payload = (
            b'{\n  "app_1": {"status":"legacy", "unknown": {"x": [1, 2]}, '
            b'"text":"Gr\xc3\xbc\xc3\x9fe"},\n  "app_2": {"status":"pending"}\n}\n'
        )
        snapshot = build_application_import_snapshot(payload, generation=4, revision=9)
        self.assertEqual(snapshot.schema_version, APPLICATION_IMPORT_SCHEMA_VERSION)
        self.assertEqual(snapshot.generation, 4)
        self.assertEqual(snapshot.revision, 9)
        self.assertEqual(snapshot.record_count, 2)
        self.assertTrue(verify_application_import_snapshot(snapshot))
        self.assertEqual(export_application_import_payload(snapshot), payload)
        self.assertEqual(application_import_manifest(snapshot), {
            "schema_version": 1, "generation": 4, "revision": 9,
            "sha256": snapshot.sha256, "record_count": 2, "payload_bytes": len(payload),
        })

    def test_empty_application_object_is_a_valid_snapshot(self):
        snapshot = build_application_import_snapshot(b"{}")
        self.assertEqual(snapshot.record_count, 0)
        self.assertEqual(export_application_import_payload(snapshot), b"{}")

    def test_payload_must_be_utf8_json_and_a_top_level_object(self):
        invalid_payloads = (
            b"\xff", b"{invalid", b"[]",
            b'{"app": {"value": NaN}}',
            b'{"app": {"value": Infinity}}',
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ApplicationImportError):
                    build_application_import_snapshot(payload)

    def test_duplicate_keys_are_rejected_at_every_object_level(self):
        payloads = (
            b'{"app": {"status": "pending"}, "app": {"status": "hired"}}',
            b'{"app": {"unknown": 1, "unknown": 2}}',
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ApplicationImportError):
                    build_application_import_snapshot(payload)

    def test_generation_and_revision_require_non_negative_integers(self):
        for value in (-1, True, 1.5, "1"):
            with self.subTest(value=value):
                with self.assertRaises(ApplicationImportError):
                    build_application_import_snapshot(b"{}", generation=value)
                with self.assertRaises(ApplicationImportError):
                    build_application_import_snapshot(b"{}", revision=value)

    def test_non_bytes_payload_is_rejected(self):
        with self.assertRaises(TypeError):
            build_application_import_snapshot('{"app": {}}')

    def test_snapshot_is_immutable(self):
        snapshot = build_application_import_snapshot(b'{"app": {}}')
        with self.assertRaises(FrozenInstanceError):
            snapshot.revision = 1

    def test_digest_tampering_is_detected_before_export(self):
        snapshot = build_application_import_snapshot(b'{"app": {}}')
        tampered = replace(snapshot, payload=b'{"app": {"status": "hired"}}')
        with self.assertRaisesRegex(ApplicationImportError, "digest"):
            export_application_import_payload(tampered)

    def test_record_count_tampering_is_detected(self):
        snapshot = build_application_import_snapshot(b'{"app": {}}')
        tampered = replace(snapshot, record_count=99)
        with self.assertRaisesRegex(ApplicationImportError, "record count"):
            verify_application_import_snapshot(tampered)

    def test_unknown_schema_versions_are_rejected(self):
        snapshot = build_application_import_snapshot(b"{}")
        unsupported = replace(snapshot, schema_version=APPLICATION_IMPORT_SCHEMA_VERSION + 1)
        with self.assertRaisesRegex(ApplicationImportError, "schema version"):
            verify_application_import_snapshot(unsupported)


if __name__ == "__main__":
    unittest.main()
