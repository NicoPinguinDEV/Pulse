import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pulse_sqlite  # noqa: E402


class ClosingConnectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        with pulse_sqlite.connect(self.path) as c:
            c.execute("CREATE TABLE t(v INTEGER)")

    def tearDown(self):
        self.tmp.cleanup()

    def _count(self):
        with pulse_sqlite.connect(self.path) as c:
            return c.execute("SELECT COUNT(*) FROM t").fetchone()[0]

    def test_with_block_commits_and_closes(self):
        with pulse_sqlite.connect(self.path) as c:
            c.execute("INSERT INTO t VALUES(1)")
        with self.assertRaises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")
        self.assertEqual(self._count(), 1)

    def test_exception_rolls_back_and_closes(self):
        c = pulse_sqlite.connect(self.path)
        with self.assertRaises(RuntimeError):
            with c:
                c.execute("INSERT INTO t VALUES(1)")
                raise RuntimeError("boom")
        with self.assertRaises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")
        self.assertEqual(self._count(), 0)

    def test_rows_stay_readable_after_close(self):
        with pulse_sqlite.connect(self.path) as c:
            c.execute("INSERT INTO t VALUES(7)")
            row = c.execute("SELECT v FROM t").fetchone()
        self.assertEqual(row["v"], 7)

    def test_module_helpers_use_closing_connection(self):
        from unittest import mock

        import pulse_db
        import pulse_next
        import pulse_ultimate

        # Isolated temp DB: never touch the real pulse.db from a test.
        with mock.patch.object(pulse_db, "DB_PATH", self.path):
            for helper in (pulse_ultimate.cx, pulse_next.cx):
                with helper() as c:
                    self.assertIsInstance(c, pulse_sqlite.ClosingConnection)
                    self.assertEqual(os.path.abspath(self.path), os.path.abspath(c.execute("PRAGMA database_list").fetchone()[2]))
                with self.assertRaises(sqlite3.ProgrammingError):
                    c.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
