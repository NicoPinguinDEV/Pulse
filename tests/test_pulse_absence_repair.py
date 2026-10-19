import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

from pulse_absence_repair import find_shifted_rows, repair_shifted_absences

GUILD = 1474514929351524616


def migrated_db(path):
    """Old layout: 'von' appended last, exactly like the production migration."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE abmeldungen (user_id INTEGER PRIMARY KEY, user_name TEXT NOT NULL, grund TEXT NOT NULL, "
                 "bis TEXT NOT NULL, original_nick TEXT, guild_id INTEGER)")
    conn.execute("ALTER TABLE abmeldungen ADD COLUMN von TEXT DEFAULT ''")
    # The old positional dashboard insert:
    conn.execute("INSERT OR REPLACE INTO abmeldungen VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (1, "Stv.TL ✗ Max", "Urlaub", "2026-10-10", "2026-10-19", "Stv.TL ✗ Max | Abgemeldet", GUILD))
    # A correct row written by the slash command (named columns) must stay untouched:
    conn.execute("INSERT INTO abmeldungen(user_id,user_name,grund,von,bis,original_nick,guild_id) VALUES (?,?,?,?,?,?,?)",
                 (2, "Lena", "Krank", "12.10.2026", "20.10.2026", "Lena", GUILD))
    conn.commit()
    conn.close()


class AbsenceRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "abmeldungen.db")
        migrated_db(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        conn = sqlite3.connect(self.path)
        try:
            return {r[0]: r[1:] for r in conn.execute("SELECT user_id, von, bis, original_nick, guild_id FROM abmeldungen")}
        finally:
            conn.close()

    def test_shifted_row_is_detected_and_repaired_with_backup(self):
        conn = sqlite3.connect(self.path)
        self.assertEqual([r["user_id"] for r in find_shifted_rows(conn)], [1])
        conn.close()
        result = repair_shifted_absences(self.path, now=datetime(2026, 10, 19, 12, 0))
        self.assertEqual(result["repaired"], 1)
        self.assertTrue(os.path.isfile(result["backup"]))
        rows = self.rows()
        self.assertEqual(rows[1], ("2026-10-10", "2026-10-19", "Stv.TL ✗ Max", GUILD))
        self.assertEqual(rows[2], ("12.10.2026", "20.10.2026", "Lena", GUILD))

    def test_repair_is_idempotent(self):
        repair_shifted_absences(self.path)
        before = self.rows()
        second = repair_shifted_absences(self.path)
        self.assertEqual(second, {"repaired": 0, "backup": ""})
        self.assertEqual(self.rows(), before)

    def test_missing_table_is_harmless(self):
        empty = os.path.join(self.tmp.name, "leer.db")
        sqlite3.connect(empty).close()
        self.assertEqual(repair_shifted_absences(empty), {"repaired": 0, "backup": ""})


if __name__ == "__main__":
    unittest.main()
