"""BOT-06: ticket handovers require a note and atomically update assignee + history."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db


class TicketTransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()
        self.ticket_id = pulse_db.create_ticket(
            channel_id=123, guild_id=1, user_id=2, user_name="Bürger", category="support",
        )
        self.assertTrue(pulse_db.claim_ticket(self.ticket_id, 10, "Teammitglied A"))

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def transfer(self, **overrides):
        values = {
            "expected_assignee_id": "10",
            "new_assignee_id": "20",
            "new_assignee_name": "Teammitglied B",
            "actor_id": 10,
            "actor_name": "Teammitglied A",
            "note": "Bereits geprüft: Loginproblem reproduziert, Logs angefordert.",
        }
        values.update(overrides)
        return pulse_db.transfer_ticket(self.ticket_id, **values)

    def test_handover_updates_assignee_and_persists_required_note(self):
        self.assertTrue(self.transfer())
        ticket = pulse_db.get_ticket(ticket_id=self.ticket_id)
        self.assertEqual(ticket["claimed_by_id"], "20")
        self.assertEqual(ticket["claimed_by_name"], "Teammitglied B")
        self.assertEqual(ticket["status"], "in_progress")
        event = next(e for e in pulse_db.ticket_events(self.ticket_id) if e["event_type"] == "transferred")
        self.assertIn("Notiz:", event["details"])
        self.assertIn("Loginproblem reproduziert", event["details"])

    def test_stale_assignee_cannot_overwrite_a_concurrent_transfer(self):
        self.assertTrue(self.transfer())
        self.assertFalse(self.transfer(expected_assignee_id="10", new_assignee_id="30", new_assignee_name="Teammitglied C"))
        self.assertEqual(pulse_db.get_ticket(ticket_id=self.ticket_id)["claimed_by_id"], "20")
        self.assertEqual(len([e for e in pulse_db.ticket_events(self.ticket_id) if e["event_type"] == "transferred"]), 1)

    def test_transfer_requires_a_reasonable_note_and_different_assignee(self):
        with self.assertRaises(ValueError):
            self.transfer(note="zu kurz")
        self.assertFalse(self.transfer(new_assignee_id="10", new_assignee_name="Teammitglied A"))
        self.assertEqual(pulse_db.get_ticket(ticket_id=self.ticket_id)["claimed_by_id"], "10")


if __name__ == "__main__":
    unittest.main()
