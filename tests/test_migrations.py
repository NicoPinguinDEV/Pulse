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

    def test_no_migrations_no_backup(self):
        self.assertEqual(pulse_migrations.run(self.db, self.backups, []), {"applied": [], "backup": "", "failed": ""})
        self.assertFalse(os.path.exists(self.backups))


if __name__ == "__main__":
    unittest.main()
