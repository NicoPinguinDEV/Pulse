"""Regression tests for priority updates on open and closed tickets."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401


class TicketPriorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.temp.name) / "tickets.db"))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        pulse_db.init_db()

    def _ticket(self):
        return pulse_db.create_ticket("100", "1", "2", "Ersteller", "support")

    def _priority_events(self, ticket_id):
        with pulse_db.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM ticket_events WHERE ticket_id=? AND event_type='priority'",
                (ticket_id,),
            ).fetchone()
        return int(row[0])

    def test_open_ticket_priority_changes_and_records_event(self):
        ticket_id = self._ticket()

        self.assertTrue(
            pulse_db.update_ticket_priority(ticket_id, "urgent", actor_id="30", actor_name="Leitung")
        )
        ticket = pulse_db.get_ticket(ticket_id=ticket_id)
        self.assertEqual(ticket["priority"], "urgent")
        self.assertEqual(self._priority_events(ticket_id), 1)

    def test_closed_ticket_priority_change_fails_without_audit_event(self):
        ticket_id = self._ticket()
        self.assertTrue(pulse_db.close_ticket(ticket_id, "Erledigt"))
        events_before = self._priority_events(ticket_id)

        self.assertFalse(
            pulse_db.update_ticket_priority(ticket_id, "urgent", actor_id="30", actor_name="Leitung")
        )
        ticket = pulse_db.get_ticket(ticket_id=ticket_id)
        self.assertEqual(ticket["priority"], "normal")
        self.assertEqual(self._priority_events(ticket_id), events_before)


if __name__ == "__main__":
    unittest.main()
