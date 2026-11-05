"""Synthetic tests for the JSON application repository adapter."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

from pulse_applications_repository import (
    ApplicationRepositoryConflict,
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

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(increment, index) for index in range(4)]
            for future in futures:
                future.result()

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


    def test_update_entry_preserves_concurrent_fields_and_merges_history(self):
        self.repository.save({
            "app": {
                "status": "pending",
                "name": "Synthetic Applicant",
                "decision_reason": "",
                "reviews": {"reviewer_a": {"notes": "original"}},
                "history": [{"event": "created"}],
            }
        })
        before = self.repository.load({})
        updated = json.loads(json.dumps(before))
        updated["app"]["status"] = "interview"
        updated["app"]["decision_reason"] = "synthetic reason"
        updated["app"]["history"].append({"event": "decision"})

        # A review and history event land while Discord is processing the decision.
        latest = self.repository.load({})
        latest["app"]["reviews"]["reviewer_b"] = {"notes": "concurrent review"}
        latest["app"]["history"].append({"event": "concurrent review"})
        latest["app"]["concurrent_field"] = "keep me"
        self.repository.save(latest)
        writes_before_merge = self.write_count

        self.repository.update_entry(
            "app",
            expected_status="pending",
            before=before["app"],
            updated=updated["app"],
        )

        result = self.repository.load({})["app"]
        self.assertEqual(result["status"], "interview")
        self.assertEqual(result["decision_reason"], "synthetic reason")
        self.assertEqual(result["reviews"]["reviewer_a"]["notes"], "original")
        self.assertEqual(result["reviews"]["reviewer_b"]["notes"], "concurrent review")
        self.assertEqual(result["concurrent_field"], "keep me")
        self.assertEqual(
            result["history"],
            [
                {"event": "created"},
                {"event": "concurrent review"},
                {"event": "decision"},
            ],
        )
        self.assertEqual(self.write_count, writes_before_merge + 1)

    def test_update_entry_rejects_a_stale_status_without_writing(self):
        self.repository.save({"app": {"status": "pending", "name": "Synthetic"}})
        before = self.repository.load({})
        updated = json.loads(json.dumps(before))
        updated["app"]["status"] = "interview"

        latest = self.repository.load({})
        latest["app"]["status"] = "rejected"
        self.repository.save(latest)
        writes_before_conflict = self.write_count

        with self.assertRaises(ApplicationRepositoryConflict):
            self.repository.update_entry(
                "app",
                expected_status="pending",
                before=before["app"],
                updated=updated["app"],
            )

        self.assertEqual(self.repository.load({})["app"]["status"], "rejected")
        self.assertEqual(self.write_count, writes_before_conflict)

    def test_update_entry_rejects_a_deleted_application(self):
        self.repository.save({"app": {"status": "pending"}})
        before = self.repository.load({})
        updated = json.loads(json.dumps(before))
        updated["app"]["status"] = "interview"
        self.repository.save({})

        with self.assertRaises(ApplicationRepositoryConflict):
            self.repository.update_entry(
                "app",
                expected_status="pending",
                before=before["app"],
                updated=updated["app"],
            )
        self.assertEqual(self.repository.load({}), {})

    def test_webserver_helpers_delegate_to_the_lazy_application_repository(self):
        import webserver as ws

        stored = {}
        expected = {"app": {"status": "pending", "unknown_field": ["kept"]}}

        def read(_path, default):
            return stored.get("records", default)

        def write(_path, records):
            stored["records"] = records.copy()

        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "applications.json")
            with (
                patch.object(ws, "APPS_FILE", path),
                patch.object(ws, "_APPLICATION_REPOSITORY", None),
                patch.object(ws, "_APPLICATION_REPOSITORY_PATH", ""),
                patch.object(ws, "_load_json_file", side_effect=read),
                patch.object(ws, "_write_json_file", side_effect=write),
            ):
                ws.save_json(path, expected)
                self.assertEqual(ws.load_json(path, {}), expected)
                self.assertEqual(ws.get_applications_repository().path, str(Path(path).resolve()))
                self.assertEqual(stored["records"], expected)


if __name__ == "__main__":
    unittest.main()
