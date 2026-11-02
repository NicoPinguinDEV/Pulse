import copy
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from pulse_melonly_repository import MelonlyBackendError, MelonlyRepository
from pulse_melonly_store import MelonlyStore


class MemoryJson:
    def __init__(self, rows=None):
        self.rows = copy.deepcopy(rows or [])

    def load(self, _path, default):
        return copy.deepcopy(self.rows) if self.rows is not None else default

    def save(self, _path, rows):
        self.rows = copy.deepcopy(rows)


def make_sqlite_backend(root: Path, rows):
    db_path = root / "pulse.db"
    store = MelonlyStore(db_path)
    store.initialize()
    store.import_entries(rows, source_sha256="test-fixture")
    # The adapter must never do this itself. The fixture represents a separately
    # reviewed and explicitly activated deployment.
    with sqlite3.connect(db_path) as con:
        con.execute("UPDATE melonly_logs_meta SET value='sqlite' WHERE key='active_backend'")
    return db_path, store


class MelonlyRepositoryParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rows = [
            {"id": "duplicate", "target_user": "Alpha", "roblox_id": "100", "type": "Warn",
             "reason": "Erster Eintrag", "created_at": "old-format", "unknown": {"preserve": True}},
            {"id": "duplicate", "target_user": "Beta", "roblox_id": "200", "type": "Ban",
             "reason": "Zweiter Eintrag"},
            {"target_user": "AlphaAlt", "roblox_id": "100", "type": "Kick", "reason": "ID-Treffer"},
            {"id": "missing-id", "target_user": "Alpha", "roblox_id": "300", "type": "Notiz",
             "reason": "Unicode: äöü"},
            {"id": "unexpected", "target_user": "Unknown", "roblox_id": "N/A", "type": "Legacy",
             "reason": "Unbekannter Typ"},
        ]
        self.memory = MemoryJson(self.rows)
        self.lock = threading.RLock()
        self.db_path, self.store = make_sqlite_backend(self.root, self.rows)
        common = {
            "json_path": self.root / "logs.json",
            "db_path": self.db_path,
            "load_json": self.memory.load,
            "save_json": self.memory.save,
            "io_lock": self.lock,
        }
        self.json_repo = MelonlyRepository(**common, environ={})
        self.sqlite_repo = MelonlyRepository(**common, environ={"PULSE_LOGS_BACKEND": "sqlite"})

    def test_backend_selection_is_opt_in_and_does_not_create_a_database(self):
        root = self.root / "fresh"
        root.mkdir()
        memory = MemoryJson([{"id": "json-only"}])
        repo = MelonlyRepository(
            json_path=root / "logs.json", db_path=root / "pulse.db",
            load_json=memory.load, save_json=memory.save, environ={}
        )
        self.assertEqual(repo.backend_name, "json")
        self.assertEqual(repo.export_all(), [{"id": "json-only"}])
        self.assertFalse((root / "pulse.db").exists())

    def test_sqlite_selection_requires_matching_active_marker(self):
        with self.assertRaises(MelonlyBackendError):
            MelonlyRepository(
                json_path=self.root / "logs.json", db_path=self.db_path,
                load_json=self.memory.load, save_json=self.memory.save, environ={}
            )
        with self.assertRaises(MelonlyBackendError):
            MelonlyRepository(
                json_path=self.root / "logs.json", db_path=self.db_path,
                load_json=self.memory.load, save_json=self.memory.save,
                environ={"PULSE_LOGS_BACKEND": "postgres"}
            )

    def test_read_contract_is_equivalent_between_json_and_sqlite(self):
        methods = (
            ("count", ()),
            ("count_by_type", ()),
            ("latest", (3,)),
            ("latest_matching_types", (("Warn", "Ban", "Kick", "Notiz", "Ban BOLO"), 3)),
            ("export_all", ()),
            ("player_history", ("100",)),
            ("player_history", ("@Alpha",)),
            ("player_history", ("lph",)),
            ("roblox_user_history", ("100", "Alpha")),
            ("roblox_user_history", ("999", "Beta")),
            ("roblox_suggestions", (3,)),
            ("search_payload", ("ERSTER")),
            ("search_payload", ("unicode")),
        )
        for method, args in methods:
            with self.subTest(method=method, args=args):
                self.assertEqual(getattr(self.json_repo, method)(*args),
                                 getattr(self.sqlite_repo, method)(*args))

    def test_crud_roundtrip_and_duplicate_id_behavior_are_equivalent(self):
        for repo in (self.json_repo, self.sqlite_repo):
            created = {"id": "new", "target_user": "NewPlayer", "roblox_id": "400",
                       "type": "Warn", "reason": "Test", "unknown_field": [1, 2]}
            repo.append(created)
            found = repo.find_first_by_id("duplicate")
            self.assertIsNotNone(found)
            seq, entry = found
            entry = dict(entry)
            entry["reason"] = "Geändert"
            self.assertTrue(repo.update_by_seq(seq, entry))
            self.assertEqual(repo.find_first_by_id("duplicate")[1]["reason"], "Geändert")
            self.assertEqual(repo.delete_all_by_id("duplicate"), 2)
            self.assertIsNone(repo.find_first_by_id("duplicate"))
        self.assertEqual(self.json_repo.export_all(), self.sqlite_repo.export_all())

    def test_numeric_id_is_not_coerced_to_form_string_for_edit_or_delete(self):
        rows = [{"id": 123, "target_user": "Numeric"}, {"id": "123", "target_user": "Text"}]
        memory = MemoryJson(rows)
        json_repo = MelonlyRepository(
            json_path=self.root / "numeric.json", db_path=self.root / "numeric.db",
            load_json=memory.load, save_json=memory.save, environ={}
        )
        numeric_db, store = make_sqlite_backend(self.root / "numeric-db", rows)
        # use a separate path for this fixture and emulate an already-active SQLite backend
        with sqlite3.connect(numeric_db) as con:
            con.execute("UPDATE melonly_logs_meta SET value='sqlite' WHERE key='active_backend'")
        sqlite_repo = MelonlyRepository(
            json_path=self.root / "numeric.json", db_path=numeric_db,
            load_json=memory.load, save_json=memory.save, environ={"PULSE_LOGS_BACKEND": "sqlite"}
        )
        for repo in (json_repo, sqlite_repo):
            self.assertEqual(repo.find_first_by_id("123"), (2, rows[1]))
            self.assertEqual(repo.delete_all_by_id("123"), 1)
            self.assertEqual(repo.export_all(), [rows[0]])

    def test_page_filter_is_newest_matching_entries_not_newest_raw_rows(self):
        for repo in (self.json_repo, self.sqlite_repo):
            got = repo.latest_matching_types(("Warn", "Kick"), 2)
            self.assertEqual([x["type"] for x in got], ["Kick", "Warn"])
            self.assertEqual(got[0]["target_user"], "AlphaAlt")

    def test_explicit_json_rollback_exports_and_verifies_before_switching_marker(self):
        stale_json = self.root / "logs.json"
        stale_json.write_text(json.dumps([{"id": "stale"}]), encoding="utf-8")
        rolled_back = MelonlyRepository(
            json_path=stale_json, db_path=self.db_path,
            load_json=self.memory.load, save_json=self.memory.save,
            io_lock=self.lock, environ={"PULSE_LOGS_BACKEND": "json"}
        )
        self.assertEqual(rolled_back.backend_name, "json")
        self.assertEqual(json.loads(stale_json.read_text(encoding="utf-8")), self.rows)
        self.assertEqual(self.store.metadata()["active_backend"], "json")
        backups = list(self.root.glob("logs_pre_sqlite_*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), [{"id": "stale"}])


if __name__ == "__main__":
    unittest.main()
