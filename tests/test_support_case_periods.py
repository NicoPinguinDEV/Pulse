"""Regression tests for meeting-cycle support case reporting."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pulse_db


class SupportCasePeriodTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = pulse_db.DB_PATH
        pulse_db.DB_PATH = str(Path(self.temp_dir.name) / "pulse-test.db")
        pulse_db.init_db()

    def tearDown(self):
        pulse_db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def _start(self) -> str:
        start = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        pulse_db.set_setting("support_case_period_start", start)
        return start

    def test_counts_closed_cases_by_current_assignee(self):
        start = self._start()
        completed = pulse_db.create_ticket("1001", "2001", "3001", "Bürger", "support")
        self.assertTrue(pulse_db.claim_ticket(completed, 4001, "Matti"))
        self.assertTrue(pulse_db.close_ticket(completed, "Gelöst", closed_by_id=4001, closed_by_name="Matti"))

        still_open = pulse_db.create_ticket("1002", "2001", "3002", "Bürger Zwei", "support")
        self.assertTrue(pulse_db.claim_ticket(still_open, 4002, "Damon"))

        unassigned = pulse_db.create_ticket("1003", "2001", "3003", "Bürger Drei", "support")
        self.assertTrue(pulse_db.close_ticket(unassigned, "Ohne Übernahme", closed_by_id=4001, closed_by_name="Matti"))

        counts = pulse_db.support_case_counts(start)
        self.assertEqual(counts.get("4001"), 1)
        self.assertNotIn("4002", counts)

    def test_finished_period_is_archived_and_new_period_starts(self):
        self._start()
        ticket_id = pulse_db.create_ticket("1010", "2001", "3010", "Bürger", "support")
        self.assertTrue(pulse_db.claim_ticket(ticket_id, 4010, "Dawo"))
        self.assertTrue(pulse_db.close_ticket(ticket_id, "Gelöst", closed_by_id=4010, closed_by_name="Dawo"))
        with pulse_db.connect() as db:
            old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
            db.execute("UPDATE tickets SET closed_at=? WHERE id=?", (old, ticket_id))

        result = pulse_db.finish_support_case_period("9001", "Teamleitung")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["counts"].get("4010"), 1)
        self.assertEqual(pulse_db.get_support_case_period_start(), result["end_at"])
        history = pulse_db.get_setting("support_case_period_history", [])
        self.assertTrue(history)
        self.assertEqual(history[-1]["total"], 1)
        self.assertEqual(pulse_db.support_case_counts().get("4010", 0), 0)


if __name__ == "__main__":
    unittest.main()
