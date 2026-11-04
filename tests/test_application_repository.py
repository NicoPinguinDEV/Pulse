"""Synthetic tests for the JSON application repository adapter."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from pulse_applications_repository import (
    ApplicationRepositoryError,
    JsonApplicationRepository,
)


class JsonApplicationRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "applications.json"
        self.lock = threading.RLock()
        self.write_count = 0
        self.repository = JsonApplicationRepository(
            self.path,
            read_json=self._read_json,
            write_json=self._write_json,
            lock=self.lock,
        )

    def _read_json(self, path: str, default: Any):
        source = Path(path)
        if not source.exists():
            return default
        return json.loads(source.read_text(encoding="utf-8"))

    def _write_json(self, path: str, records: dict[str, Any]) -> None:
        self.write_count += 1
        Path(path).write_text(
            json.dumps(records, ensure_ascii=False, indent=4),
            encoding="utf-8",
        )

    def test_load_and_save_preserve_legacy_mapping(self):
        original = {
            "legacy-app": {
                "status": "accepted",
                "unknown_legacy_field": {"values": [1, 2, 3]},
                "text": "Grüße",
            }
        }
        self.repository.save(original)
        self.assertEqual(self.repository.load({}), original)
        self.assertEqual(self.write_count, 1)

    def test_load_returns_the_existing_loader_default(self):
        marker = {"fallback": True}
        self.assertIs(self.repository.load(marker), marker)

    def test_save_rejects_non_mapping_payloads_before_writing(self):
        with self.assertRaises(ApplicationRepositoryError):
            self.repository.save([])
        self.assertEqual(self.write_count, 0)
        self.assertFalse(self.path.exists())

    def test_mutate_is_atomic_and_returns_the_callback_result(self):
        self.repository.save({"a": {"status": "pending"}})

        def change(records):
            records["a"]["status"] = "interview"
            records["b"] = {"status": "pending"}
            return "updated"

        result = self.repository.mutate(change)
        self.assertEqual(result, "updated")
        self.assertEqual(
            self.repository.load({}),
            {"a": {"status": "interview"}, "b": {"status": "pending"}},
        )
        self.assertEqual(self.write_count, 2)

    def test_mutate_does_not_write_when_callback_raises(self):
        original = {"a": {"status": "pending"}}
        self.repository.save(original)

        def fail(_records):
            raise ValueError("synthetic failure")

        with self.assertRaisesRegex(ValueError, "synthetic failure"):
            self.repository.mutate(fail)
        self.assertEqual(self.repository.load({}), original)
        self.assertEqual(self.write_count, 1)

    def test_mutate_serializes_concurrent_updates(self):
        self.repository.save({"count": 0})

        def increment(_index):
            for _ in range(25):
                self.repository.mutate(
                    lambda records: records.__setitem__("count", records["count"] + 1)
                )

        threads = [threading.Thread(target=increment, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(self.repository.load({})["count"], 100)

    def test_snapshot_and_export_keep_the_exact_source_bytes(self):
        payload = (
            b'{\n  "app_1": {"status": "legacy", "unknown": [1, 2]},\n'
            b'  "app_2": {"status": "pending"}\n}\n'
        )
        self.path.write_bytes(payload)

        snapshot = self.repository.snapshot(generation=3, revision=8)

        self.assertEqual(self.repository.export_snapshot(snapshot), payload)
        self.assertEqual(snapshot.generation, 3)
        self.assertEqual(snapshot.revision, 8)
        self.assertEqual(snapshot.record_count, 2)
        self.assertEqual(
            self.repository.snapshot_manifest(snapshot),
            {
                "schema_version": 1,
                "generation": 3,
                "revision": 8,
                "sha256": snapshot.sha256,
                "record_count": 2,
                "payload_bytes": len(payload),
            },
        )
        self.assertEqual(self.write_count, 0)

    def test_import_preview_validates_explicit_bytes_without_writing(self):
        payload = b'{"app": {"status": "pending", "future_field": "kept"}}'
        snapshot = self.repository.preview_import(payload, generation=5, revision=2)
        self.assertEqual(self.repository.export_snapshot(snapshot), payload)
        self.assertEqual(snapshot.record_count, 1)
        self.assertEqual(self.write_count, 0)
        self.assertFalse(self.path.exists())

    def test_import_preview_rejects_duplicate_keys(self):
        with self.assertRaises(ValueError):
            self.repository.preview_import(b'{"app": {}, "app": {}}')
        self.assertEqual(self.write_count, 0)

    def test_snapshot_requires_an_existing_explicit_json_file(self):
        with self.assertRaises(FileNotFoundError):
            self.repository.snapshot()


if __name__ == "__main__":
    unittest.main()
