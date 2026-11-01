import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pulse_melonly_migration import (
    MelonlyMigrationError,
    export_sqlite_to_json,
    migrate_json_to_sqlite,
)
from pulse_melonly_store import MelonlyStore, MelonlyStoreNotEmpty


class MelonlyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "logs.json"
        self.database = self.root / "pulse.db"
        self.backups = self.root / "backups"

    def write_source(self, entries):
        raw = json.dumps(entries, ensure_ascii=False, indent=2).encode("utf-8")
        self.source.write_bytes(raw)
        return raw

    def test_migration_preserves_payload_order_and_source_bytes(self):
        entries = [
            {"id": "same", "target_user": "Alpha", "roblox_id": "100", "type": "Warn",
             "created_at": "01.02.2024 03:04", "extra": {"unknown": True}},
            {"id": "same", "target_user": "Beta", "type": "Unlisted", "reason": None},
            {"target_user": "Alpha", "roblox_id": "100", "type": "Ban BOLO"},
        ]
        original = self.write_source(entries)
        result = migrate_json_to_sqlite(self.source, self.database, self.backups)
        self.assertEqual(result["status"], "migrated")
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["active_backend"], "json")
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(hashlib.sha256(original).hexdigest(), result["source_sha256"])
        self.assertEqual(MelonlyStore(self.database).export_all(), entries)
        self.assertTrue(Path(result["json_backup"]).is_file())
        self.assertEqual(Path(result["json_backup"]).read_bytes(), original)
        with sqlite3.connect(self.database) as con:
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(
                con.execute("SELECT seq FROM melonly_logs ORDER BY seq").fetchall(),
                [(1,), (2,), (3,)],
            )

    def test_same_source_migration_is_idempotent(self):
        entries = [{"id": "a"}, {"id": "b"}]
        self.write_source(entries)
        first = migrate_json_to_sqlite(self.source, self.database, self.backups)
        second = migrate_json_to_sqlite(self.source, self.database, self.backups)
        self.assertEqual(first["status"], "migrated")
        self.assertEqual(second["status"], "already_migrated")
        self.assertEqual(MelonlyStore(self.database).count(), 2)

    def test_different_source_refuses_existing_sqlite_data(self):
        self.write_source([{"id": "original"}])
        migrate_json_to_sqlite(self.source, self.database, self.backups)
        self.write_source([{"id": "changed"}])
        with self.assertRaises(MelonlyStoreNotEmpty):
            migrate_json_to_sqlite(self.source, self.database, self.backups)
        self.assertEqual(MelonlyStore(self.database).export_all(), [{"id": "original"}])

    def test_invalid_json_and_wrong_top_level_abort_before_database_creation(self):
        self.source.write_text("{broken", encoding="utf-8")
        with self.assertRaises(MelonlyMigrationError):
            migrate_json_to_sqlite(self.source, self.database, self.backups)
        self.assertFalse(self.database.exists())
        self.write_source({"not": "a list"})
        with self.assertRaises(MelonlyMigrationError):
            migrate_json_to_sqlite(self.source, self.database, self.backups)
        self.assertFalse(self.database.exists())

    def test_non_object_entry_is_rejected_without_touching_source(self):
        raw = self.write_source([{"id": "valid"}, "invalid"])
        with self.assertRaises(MelonlyMigrationError):
            migrate_json_to_sqlite(self.source, self.database, self.backups)
        self.assertEqual(self.source.read_bytes(), raw)
        self.assertFalse(self.database.exists())

    def test_existing_database_is_backed_up_before_schema_is_added(self):
        with sqlite3.connect(self.database) as con:
            con.execute("CREATE TABLE existing_data (value TEXT)")
            con.execute("INSERT INTO existing_data VALUES ('keep')")
        self.write_source([{"id": "a"}])
        result = migrate_json_to_sqlite(self.source, self.database, self.backups)
        backup = Path(result["database_backup"])
        self.assertTrue(backup.is_file())
        with sqlite3.connect(backup) as con:
            self.assertEqual(con.execute("SELECT value FROM existing_data").fetchone()[0], "keep")
            self.assertEqual(
                con.execute("SELECT name FROM sqlite_master WHERE name='melonly_logs'").fetchone(),
                None,
            )

    def test_nonempty_unregistered_store_aborts_without_overwriting_existing_rows(self):
        store = MelonlyStore(self.database)
        store.initialize()
        store.append({"id": "preexisting", "target_user": "KeepMe"})
        original = self.write_source([{"id": "incoming"}])
        with self.assertRaises(MelonlyMigrationError):
            migrate_json_to_sqlite(self.source, self.database, self.backups)
        self.assertEqual(store.export_all(), [{"id": "preexisting", "target_user": "KeepMe"}])
        self.assertEqual(self.source.read_bytes(), original)
        with sqlite3.connect(self.database) as con:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM schema_migrations WHERE name='melonly_logs_sqlite_v1'").fetchone()[0],
                0,
            )

    def test_sqlite_export_requires_sqlite_backend_marker(self):
        self.write_source([{"id": "a"}])
        migrate_json_to_sqlite(self.source, self.database, self.backups)
        destination = self.root / "rollback.json"
        with self.assertRaises(MelonlyMigrationError):
            export_sqlite_to_json(self.database, destination)
        self.assertFalse(destination.exists())

    def test_sqlite_export_is_atomic_and_verifies_readback(self):
        entries = [{"id": "a", "unknown": ["x", 1]}, {"id": "b"}]
        self.write_source(entries)
        migrate_json_to_sqlite(self.source, self.database, self.backups)
        with sqlite3.connect(self.database) as con:
            con.execute(
                "UPDATE melonly_logs_meta SET value='sqlite' WHERE key='active_backend'"
            )
        destination = self.root / "rollback.json"
        result = export_sqlite_to_json(self.database, destination)
        self.assertEqual(result["status"], "exported")
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), entries)
        self.assertEqual(result["count"], 2)
        self.assertTrue(result["sha256"])


if __name__ == "__main__":
    unittest.main()
