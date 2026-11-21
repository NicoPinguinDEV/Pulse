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
            ("ticket-foreign-guild", "guild-2", "customer", "Kunde", "support", "open", "normal", "100", "2026-11-20T01:02:00+00:00"),
        ])
        with pulse_db.connect() as connection:
            connection.executemany(
                "INSERT INTO tickets(id,guild_id,user_id,user_name,category,status,priority,claimed_by_id,opened_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                rows,
            )

        counts = pulse_db.open_ticket_counts(["100", 200, "missing", "100"], guild_id="guild-1")
        self.assertEqual(counts, {"100": 2004, "200": 1, "missing": 0})

    def test_empty_or_invalid_assignee_list_returns_empty_counts(self):
        self.assertEqual(pulse_db.open_ticket_counts([], guild_id="guild-1"), {})
        self.assertEqual(pulse_db.open_ticket_counts(None, guild_id="guild-1"), {})
        self.assertEqual(pulse_db.open_ticket_counts(["100"], guild_id=None), {"100": 0})

    def test_ids_are_normalized_and_zero_open_load_is_returned(self):
        counts = pulse_db.open_ticket_counts([None, "", 321, "321", "0"], guild_id="guild-1")
        self.assertEqual(counts, {"321": 0, "0": 0})

    def test_category_counts_include_all_open_tickets_and_exclude_closed(self):
        rows = [
            ("support-1", "guild-1", "customer", "Kunde", "support", "open", "normal", None, "2026-11-20T00:00:00+00:00"),
            ("support-2", "guild-1", "customer", "Kunde", "support", "closed", "normal", None, "2026-11-20T00:01:00+00:00"),
            ("bug-1", "guild-1", "customer", "Kunde", "bug", "in_progress", "high", None, "2026-11-20T00:02:00+00:00"),
            ("foreign-support", "guild-2", "customer", "Kunde", "support", "open", "normal", None, "2026-11-20T00:03:00+00:00"),
            ("foreign-bug", "guild-2", "customer", "Kunde", "bug", "open", "normal", None, "2026-11-20T00:04:00+00:00"),
        ]
        with pulse_db.connect() as connection:
            connection.executemany(
                "INSERT INTO tickets(id,guild_id,user_id,user_name,category,status,priority,claimed_by_id,opened_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                rows,
            )

        counts = pulse_db.open_ticket_category_counts(["support", "bug", "missing", "support"], guild_id="guild-1")
        self.assertEqual(counts, {"support": 1, "bug": 1, "missing": 0})
        self.assertEqual(
            pulse_db.open_ticket_category_counts(["support", "bug"], guild_id=None),
            {"support": 0, "bug": 0},
        )


if __name__ == "__main__":
    unittest.main()
