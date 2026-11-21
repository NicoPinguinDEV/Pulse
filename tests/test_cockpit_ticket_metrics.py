"""Regression tests for complete ticket KPIs used by the leadership cockpit."""
import sqlite3
import unittest
from datetime import date


from pulse_cockpit import cockpit_ticket_summary


class CockpitTicketSummaryTests(unittest.TestCase):
    def test_summary_counts_every_ticket_before_any_display_limit(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE tickets(status TEXT, claimed_by_id TEXT, opened_at TEXT, closed_at TEXT)"
        )
        with connection:
            connection.executemany(
                "INSERT INTO tickets(status,claimed_by_id,opened_at,closed_at) VALUES(?,?,?,?)",
                [
                    ("open", "9", "2026-11-01T10:00:00+00:00", None)
                    for _ in range(2005)
                ] + [
                    ("closed", "9", "2026-11-18T09:00:00+00:00", "2026-11-18T10:00:00+00:00"),
                    ("closed", "9", "2026-11-10T09:00:00+00:00", "2026-11-10T10:00:00+00:00"),
                    ("closed", "9", "2026-11-10T09:00:00+00:00", "invalid"),
                    ("open", None, "2026-11-20T08:00:00+00:00", None),
                    ("open", "", "2026-11-19T08:00:00+00:00", None),
                    ("in_progress", None, "2026-11-17T08:00:00+00:00", None),
                ],
            )

        summary = cockpit_ticket_summary(
            connection,
            date(2026, 11, 16),
            date(2026, 11, 22),
            date(2026, 11, 9),
            date(2026, 11, 15),
        )
        self.assertEqual(summary["closed_this"], 1)
        self.assertEqual(summary["closed_prev"], 1)
        self.assertEqual(summary["unclaimed_count"], 2)
        self.assertEqual(summary["oldest_unclaimed_opened_at"], "2026-11-19T08:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
