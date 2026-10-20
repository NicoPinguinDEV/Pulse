"""A-05: migrations run once, in a transaction, with a backup before the first change."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_migrations


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = str(Path(self.tmp.name) / "pulse.db")
        self.backups = str(Path(self.tmp.name) / "backups")
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE t(v TEXT)")
        con.execute("INSERT INTO t VALUES ('alt')")
        con.commit()
        con.close()

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        con = sqlite3.connect(self.db)
        try:
            return [r[0] for r in con.execute("SELECT v FROM t ORDER BY rowid")]
        finally:
            con.close()

    def test_runs_once_with_backup(self):
        migrations = [("001_neu", lambda c: c.execute("INSERT INTO t VALUES ('neu')"))]
        first = pulse_migrations.run(self.db, self.backups, migrations)
        self.assertEqual(first["applied"], ["001_neu"])
        self.assertTrue(os.path.isfile(first["backup"]))
        second = pulse_migrations.run(self.db, self.backups, migrations)
        self.assertEqual(second, {"applied": [], "backup": "", "failed": ""})
        self.assertEqual(self.rows(), ["alt", "neu"])
        backup = sqlite3.connect(first["backup"])
        self.assertEqual([r[0] for r in backup.execute("SELECT v FROM t")], ["alt"])  # state before the migration
        backup.close()

    def test_failed_migration_leaves_nothing_and_stops(self):
        def broken(con):
            con.execute("INSERT INTO t VALUES ('halb')")
            raise ValueError("kaputt")
        migrations = [("001_kaputt", broken), ("002_danach", lambda c: c.execute("INSERT INTO t VALUES ('x')"))]
        with patch("builtins.print"):
            report = pulse_migrations.run(self.db, self.backups, migrations)
        self.assertIn("001_kaputt", report["failed"])
        self.assertEqual(report["applied"], [])
        self.assertEqual(self.rows(), ["alt"])

    def test_failed_schema_change_is_rolled_back_and_retried(self):
        def ddl_then_fail(con):
            con.execute("CREATE TABLE neu(x TEXT)")
            con.execute("ALTER TABLE t ADD COLUMN extra TEXT")
            raise ValueError("abbruch")
        with patch("builtins.print"):
            report = pulse_migrations.run(self.db, self.backups, [("001_ddl", ddl_then_fail)])
        self.assertIn("001_ddl", report["failed"])
        con = sqlite3.connect(self.db)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = [r[1] for r in con.execute("PRAGMA table_info(t)")]
        con.close()
        self.assertNotIn("neu", tables)          # schema change undone …
        self.assertNotIn("extra", columns)
        fixed = [("001_ddl", lambda c: c.execute("CREATE TABLE neu(x TEXT)"))]
        self.assertEqual(pulse_migrations.run(self.db, self.backups, fixed)["applied"], ["001_ddl"])  # … so a retry works

    def test_only_the_newest_pre_migration_backups_are_kept(self):
        for i in range(pulse_migrations.KEEP_BACKUPS + 3):
            pulse_migrations.run(self.db, self.backups, [(f"{i:03d}", lambda c: None)])
        kept = [n for n in os.listdir(self.backups) if n.startswith("pre_migration_")]
        self.assertEqual(len(kept), pulse_migrations.KEEP_BACKUPS)

    def test_no_migrations_no_backup(self):
        self.assertEqual(pulse_migrations.run(self.db, self.backups, []), {"applied": [], "backup": "", "failed": ""})
        self.assertFalse(os.path.exists(self.backups))


if __name__ == "__main__":
    unittest.main()
