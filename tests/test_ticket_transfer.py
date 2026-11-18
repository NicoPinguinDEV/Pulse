"""Database regression tests for atomic ticket reassignment."""


import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401


class TicketTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.temp.name) / "tickets.db"))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)

    def _ticket(self):
        return pulse_db.create_ticket("100", "1", "2", "Ersteller", "support")

    def test_transfer_replaces_existing_assignee_and_records_actor(self):
        ticket_id = self._ticket()
        self.assertTrue(pulse_db.claim_ticket(ticket_id, "10", "Alter Bearbeiter"))

        self.assertTrue(
            pulse_db.transfer_ticket(
                ticket_id,
                "20",
                "Neuer Bearbeiter",
                actor_id="30",
                actor_name="Leitung",
            )
        )

        ticket = pulse_db.get_ticket(ticket_id=ticket_id)
        self.assertEqual(ticket["claimed_by_id"], "20")
        self.assertEqual(ticket["claimed_by_name"], "Neuer Bearbeiter")
        self.assertEqual(ticket["status"], "in_progress")
        with pulse_db.connect() as connection:
            event = connection.execute(
                "SELECT actor_id,actor_name,event_type,details FROM ticket_events "
                "WHERE ticket_id=? ORDER BY rowid DESC LIMIT 1",
                (ticket_id,),
            ).fetchone()
        self.assertEqual(event["actor_id"], "30")
        self.assertEqual(event["actor_name"], "Leitung")
        self.assertEqual(event["event_type"], "transferred")
        self.assertIn("Alter Bearbeiter → Neuer Bearbeiter", event["details"])

    def test_transfer_rejects_missing_closed_and_same_assignee_tickets(self):
        self.assertFalse(pulse_db.transfer_ticket("does-not-exist", "20", "Neuer Bearbeiter"))

        ticket_id = self._ticket()
        self.assertTrue(pulse_db.claim_ticket(ticket_id, "10", "Alter Bearbeiter"))
        self.assertFalse(pulse_db.transfer_ticket(ticket_id, "10", "Alter Bearbeiter"))
        self.assertTrue(pulse_db.close_ticket(ticket_id, "Erledigt"))
        self.assertFalse(pulse_db.transfer_ticket(ticket_id, "20", "Neuer Bearbeiter"))


if __name__ == "__main__":
    unittest.main()
