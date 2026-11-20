"""Regression tests for dashboard ticket filtering and analytics query reuse."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_home
import pulse_ultimate


class DashboardTicketQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_path_patch.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db_path_patch.stop()
        self.tmp.cleanup()

    def test_personal_dashboard_filters_closed_tickets_before_applying_limit(self):
        """Many old urgent closed tickets must not push an active ticket out of the first page."""
        open_ticket = pulse_db.create_ticket(
            "open-channel", "guild-a", "42", "Teammitglied", "Support", priority="normal"
        )
        for index in range(55):
            closed_ticket = pulse_db.create_ticket(
                f"closed-channel-{index}",
                "guild-a",
                "42",
                "Teammitglied",
                "Support",
                priority="urgent",
            )
            self.assertTrue(pulse_db.close_ticket(closed_ticket, "Erledigt"))

        rows = pulse_home.member_open_tickets("42", limit=50)

        self.assertEqual([row["id"] for row in rows], [open_ticket])
        self.assertTrue(all(row["status"] != "closed" for row in rows))

    def test_ticket_totals_by_assignee_matches_the_bounded_python_snapshot(self):
        fixtures = [
            ("42", "normal", "open"),
            ("42", "urgent", "closed"),
            ("77", "high", "open"),
            ("None", "urgent", "open"),
            ("77", "normal", "closed"),
            ("42", "low", "open"),
            ("None", "normal", "open"),
        ]
        for index, (assignee, priority, status) in enumerate(fixtures):
            ticket_id = pulse_db.create_ticket(
                f"channel-{index}", "guild-a", assignee, "Teammitglied", "Support", priority=priority
            )
            with pulse_db.connect() as connection:
                connection.execute(
                    "UPDATE tickets SET opened_at=? WHERE id=?",
                    (f"2026-11-20T10:{index:02d}:00+00:00", ticket_id),
                )
            if status == "closed":
                self.assertTrue(pulse_db.close_ticket(ticket_id, "Erledigt"))

        bounded_rows = pulse_db.list_tickets(limit=5)
        expected_claimed = {}
        expected_closed = {}
        for ticket in bounded_rows:
            key = str(ticket.get("claimed_by_id"))
            expected_claimed[key] = expected_claimed.get(key, 0) + 1
            if ticket.get("status") == "closed":
                expected_closed[key] = expected_closed.get(key, 0) + 1
        expected = {
            key: (count, expected_closed.get(key, 0))
            for key, count in expected_claimed.items()
        }

        self.assertEqual(pulse_db.ticket_totals_by_assignee(limit=5), expected)

    def test_analytics_ticket_totals_reuse_the_prefetched_context(self):
        """The analytics view can derive both totals from the already loaded ticket snapshot."""
        shared = {
            "claimed": {"42": 3, "77": 2, "None": 4},
            "closed": {"42": 1, "77": 2, "None": 1},
        }
        self.assertEqual(pulse_ultimate.analytics_ticket_totals(shared), (9, 4))

    def test_analytics_ticket_totals_handles_empty_context(self):
        self.assertEqual(pulse_ultimate.analytics_ticket_totals({"claimed": {}, "closed": {}}), (0, 0))


if __name__ == "__main__":
    unittest.main()
