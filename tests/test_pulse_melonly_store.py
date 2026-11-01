import json
import os
import tempfile
import threading
import unittest

from pulse_melonly_store import MelonlyStore, MelonlyStoreNotEmpty


class MelonlyStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "pulse.db")
        self.store = MelonlyStore(self.db)
        self.store.initialize()

    def test_initialize_is_idempotent_and_does_not_create_logs_json(self):
        self.store.initialize()
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "logs.json")))
        self.assertEqual(self.store.count(), 0)

    def test_import_preserves_order_duplicates_and_unknown_payload_fields(self):
        entries = [
            {"id": "same", "target_user": "First", "type": "Warn", "extra": {"x": 1}},
            {"id": "same", "target_user": "Second", "type": "Ban", "created_at": "old-format"},
            {"target_user": "Third", "type": "Other", "optional": None},
        ]
        self.assertEqual(self.store.import_entries(entries, source_sha256="abc"), 3)
        self.assertEqual(self.store.export_all(), entries)
        self.assertEqual(self.store.metadata()["import_count"], "3")
        self.assertEqual(self.store.metadata()["import_source_sha256"], "abc")
        with self.assertRaises(MelonlyStoreNotEmpty):
            self.store.import_entries(entries, source_sha256="abc")

    def test_latest_returns_newest_items_in_original_list_order(self):
        entries = [{"id": str(i), "target_user": f"User{i}"} for i in range(5)]
        self.store.import_entries(entries)
        self.assertEqual(self.store.latest(2), entries[-2:])
        self.assertEqual(self.store.latest(0), [])

    def test_append_update_and_delete_are_transactional_and_revisioned(self):
        first = self.store.append({"id": "a", "target_user": "Nico", "type": "Warn"})
        second = self.store.append({"id": "b", "target_user": "BOCHUM", "type": "Ban"})
        self.assertEqual((first, second), (1, 2))
        self.assertEqual(self.store.metadata()["revision"], "2")
        self.assertTrue(self.store.update_by_seq(first, {"id": "a", "target_user": "Nico", "type": "Kick"}))
        self.assertEqual(self.store.metadata()["revision"], "3")
        self.assertFalse(self.store.update_by_seq(999, {"id": "x"}))
        self.assertTrue(self.store.delete_by_seq(second))
        self.assertFalse(self.store.delete_by_seq(second))
        self.assertEqual(self.store.metadata()["revision"], "4")
        self.assertEqual(self.store.export_all(), [{"id": "a", "target_user": "Nico", "type": "Kick"}])

    def test_duplicate_ids_resolve_to_first_original_position(self):
        entries = [{"id": "duplicate", "value": 1}, {"id": "duplicate", "value": 2}]
        self.store.import_entries(entries)
        self.assertEqual(self.store.find_first_by_id("duplicate"), (1, entries[0]))

    def test_count_and_type_aggregates_include_unknown_and_missing_types(self):
        self.store.import_entries([
            {"type": "Warn"}, {"type": "Warn"}, {"type": "Ban"}, {"other": True}
        ])
        self.assertEqual(self.store.count(), 4)
        self.assertEqual(self.store.count_by_type(), {"": 1, "Ban": 1, "Warn": 2})

    def test_iterator_streams_in_order(self):
        entries = [{"id": str(i)} for i in range(7)]
        self.store.import_entries(entries)
        self.assertEqual(list(self.store.iter_all(batch_size=2)), entries)

    def test_parallel_appends_have_unique_monotonic_sequences(self):
        errors = []
        def add(index):
            try:
                self.store.append({"id": str(index)})
            except Exception as exc:  # collected for an assertion in the test thread
                errors.append(exc)
        threads = [threading.Thread(target=add, args=(i,)) for i in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.store.count(), 20)
        with sqlite3_connect(self.db) as con:
            seqs = [row[0] for row in con.execute("SELECT seq FROM melonly_logs ORDER BY seq")]
        self.assertEqual(seqs, list(range(1, 21)))


def sqlite3_connect(path):
    import sqlite3
    return sqlite3.connect(path)


if __name__ == "__main__":
    unittest.main()
