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
        self.assertTrue(pulse_db.claim_ticket(open_ticket, "42", "Teammitglied"))
        self.assertTrue(pulse_db.claim_ticket(open_ticket, "42", "Teammitglied"))
        for index in range(55):
            closed_ticket = pulse_db.create_ticket(
                f"closed-channel-{index}",
                "guild-a",
                "42",
                "Teammitglied",
                "Support",
                priority="urgent",
            )
            self.assertTrue(pulse_db.claim_ticket(closed_ticket, "42", "Teammitglied"))
            self.assertTrue(pulse_db.close_ticket(closed_ticket, "Erledigt"))

        rows = pulse_home.member_open_tickets("42", limit=50)

        self.assertEqual([row["id"] for row in rows], [open_ticket])
        self.assertTrue(all(row["status"] != "closed" for row in rows))

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
