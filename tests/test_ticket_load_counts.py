"""Regression tests for complete grouped open-ticket workload counts."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db


class OpenTicketCountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.temp.name) / "tickets.db"))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        pulse_db.init_db()

    def test_counts_all_open_tickets_without_list_tickets_page_cap(self):
        rows = [
            (
                f"ticket-{index}",
                "guild-1",
                "customer",
                "Kunde",
                "support",
                "closed" if index == 2000 else "in_progress",
                "normal",
                "100",
                f"2026-11-20T00:{index % 60:02d}:00+00:00",
            )
            for index in range(2005)
        ]
        rows.extend([
            ("ticket-other", "guild-1", "customer", "Kunde", "support", "open", "normal", "200", "2026-11-20T01:00:00+00:00"),
            ("ticket-unclaimed", "guild-1", "customer", "Kunde", "support", "open", "normal", None, "2026-11-20T01:01:00+00:00"),
        ])
        with pulse_db.connect() as connection:
            connection.executemany(
                "INSERT INTO tickets(id,guild_id,user_id,user_name,category,status,priority,claimed_by_id,opened_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                rows,
            )

        counts = pulse_db.open_ticket_counts(["100", 200, "missing", "100"])
        self.assertEqual(counts, {"100": 2004, "200": 1, "missing": 0})

    def test_empty_or_invalid_assignee_list_returns_empty_counts(self):
        self.assertEqual(pulse_db.open_ticket_counts([]), {})
        self.assertEqual(pulse_db.open_ticket_counts(None), {})

    def test_ids_are_normalized_and_zero_open_load_is_returned(self):
        counts = pulse_db.open_ticket_counts([None, "", 321, "321", "0"])
        self.assertEqual(counts, {"321": 0, "0": 0})


if __name__ == "__main__":
    unittest.main()
