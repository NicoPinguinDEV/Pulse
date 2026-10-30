"""Two page views that create the same new team profile at once must not fail (found by tools/load_test.py --concurrency).

GET /ultimate/team runs in worker threads and calls ensure_profile() for every member; the first view after a member
joined used to race between the SELECT and the INSERT – the slower thread hit the UNIQUE key and the page answered 500.
"""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pulse_db
import pulse_ultimate


class EnsureProfileRaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
                        patch.object(pulse_ultimate, "_INIT", False)]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        pulse_ultimate.setup()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_parallel_first_views_create_one_profile_without_error(self):
        member = SimpleNamespace(id=4242, display_name="Neu im Team")
        real_cx, barrier, errors = pulse_ultimate.cx, threading.Barrier(2), []

        class SlowSelect:
            """Both threads read "no profile yet" before either inserts – the window the race needs."""
            def __init__(self):
                self.c = real_cx()

            def __enter__(self):
                self.c.__enter__()
                return self

            def __exit__(self, *exc):
                return self.c.__exit__(*exc)

            def execute(self, sql, *args):
                result = self.c.execute(sql, *args)
                if sql.lstrip().upper().startswith("SELECT DISPLAY_NAME"):
                    rows = result.fetchone()
                    barrier.wait(timeout=5)
                    return SimpleNamespace(fetchone=lambda: rows)
                return result

        def view():
            try:
                pulse_ultimate.ensure_profile(member)
            except Exception as exc:  # the bug: sqlite3.IntegrityError in one of the two threads
                errors.append(exc)

        with patch.object(pulse_ultimate, "cx", SlowSelect):
            threads = [threading.Thread(target=view) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
        self.assertEqual(errors, [])
        with real_cx() as c:
            count = c.execute("SELECT COUNT(*) FROM ultimate_profiles WHERE user_id='4242'").fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
