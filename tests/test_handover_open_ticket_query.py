"""Regression test for the handover page's open-ticket query limit."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_pro
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401


class HandoverOpenTicketQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_closed_high_priority_tickets_do_not_hide_open_ticket(self):
        open_ticket_id = pulse_db.create_ticket(
            "open-channel", "guild-a", "42", "Teammitglied", "Support", priority="normal"
        )
        for index in range(105):
            closed_ticket_id = pulse_db.create_ticket(
                f"closed-channel-{index}",
                "guild-a",
                "42",
                "Teammitglied",
                "Support",
                priority="urgent",
            )
            self.assertTrue(pulse_db.close_ticket(closed_ticket_id, "Erledigt"))

        # The old list-then-filter path returned an empty result: the first 100
        # rows were all closed urgent tickets sorted ahead of the normal ticket.
        results = pulse_pro.open_handover_tickets(limit=100)

        self.assertEqual([ticket["id"] for ticket in results], [open_ticket_id])
        self.assertTrue(all(ticket["status"] != "closed" for ticket in results))


if __name__ == "__main__":
    unittest.main()
