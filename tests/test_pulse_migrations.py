import sqlite3
import tempfile
import unittest
from pathlib import Path

import pulse_migrations


class PulseMigrationFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.database = self.root / "pulse.db"
        self.backups = self.root / "backups"
        with sqlite3.connect(self.database) as con:
            con.execute("CREATE TABLE existing_data (value TEXT)")
            con.execute("INSERT INTO existing_data VALUES ('before')")

    def test_database_backup_precedes_even_creation_of_migration_ledger(self):
        def migration(con):
            con.execute("CREATE TABLE added_by_migration (value TEXT)")
            con.execute("INSERT INTO added_by_migration VALUES ('after')")

        result = pulse_migrations.run(
            str(self.database), str(self.backups), [("test_add_table", migration)]
        )
        self.assertEqual(result["applied"], ["test_add_table"])
        backup = Path(result["backup"])
        self.assertTrue(backup.is_file())
        with sqlite3.connect(backup) as con:
            self.assertEqual(con.execute("SELECT value FROM existing_data").fetchone()[0], "before")
            self.assertIsNone(
                con.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
            )
            self.assertIsNone(
                con.execute("SELECT 1 FROM sqlite_master WHERE name='added_by_migration'").fetchone()
            )

    def test_failed_migration_rolls_back_ddl_and_does_not_record_name(self):
        def migration(con):
            con.execute("CREATE TABLE should_rollback (value TEXT)")
            raise RuntimeError("intentional test failure")

        result = pulse_migrations.run(
            str(self.database), str(self.backups), [("test_failure", migration)]
        )
        self.assertIn("test_failure", result["failed"])
        with sqlite3.connect(self.database) as con:
            self.assertIsNone(
                con.execute("SELECT 1 FROM sqlite_master WHERE name='should_rollback'").fetchone()
            )
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM schema_migrations WHERE name='test_failure'").fetchone()[0],
                0,
            )

    def test_applied_migration_is_idempotent_and_does_not_create_another_backup(self):
        calls = []

        def migration(con):
            calls.append("ran")
            con.execute("CREATE TABLE once_only (value TEXT)")

        first = pulse_migrations.run(
            str(self.database), str(self.backups), [("test_once", migration)]
        )
        second = pulse_migrations.run(
            str(self.database), str(self.backups), [("test_once", migration)]
        )
        self.assertEqual(first["applied"], ["test_once"])
        self.assertEqual(second["applied"], [])
        self.assertEqual(second["backup"], "")
        self.assertEqual(calls, ["ran"])


if __name__ == "__main__":
    unittest.main()
