"""BOT-06: ticket tags reuse automation_state and retain existing automation fields."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db


class TicketTagTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()
        self.ticket_id = pulse_db.create_ticket(
            channel_id=12345, guild_id=1, user_id=2, user_name="Bürger", category="support",
        )

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_tags_preserve_existing_automation_state_and_record_history(self):
        pulse_db.set_ticket_automation_state(self.ticket_id, {"last_nudge_at": "2026-10-20", "nudge_count": 2})
        saved = pulse_db.set_ticket_tags(
            self.ticket_id, "  Dringend, Login, dringend, Bug ",
            actor_id=9, actor_name="Team A",
        )
        self.assertEqual(saved, ["Dringend", "Login", "Bug"])
        ticket = pulse_db.get_ticket(ticket_id=self.ticket_id)
        self.assertEqual(pulse_db.ticket_tags_from_state(ticket["automation_state"]), saved)
        self.assertIn("2026-10-20", ticket["automation_state"])
        event = next(event for event in pulse_db.ticket_events(self.ticket_id) if event["event_type"] == "tags")
        self.assertIn("Dringend, Login, Bug", event["details"])
        self.assertEqual(event["actor_name"], "Team A")

    def test_empty_tags_clear_only_tag_list(self):
        pulse_db.set_ticket_automation_state(self.ticket_id, {"nudge_count": 4, "tags": ["old"]})
        self.assertEqual(pulse_db.set_ticket_tags(self.ticket_id, ""), [])
        ticket = pulse_db.get_ticket(ticket_id=self.ticket_id)
        self.assertEqual(pulse_db.ticket_tags_from_state(ticket["automation_state"]), [])
        self.assertIn('"nudge_count": 4', ticket["automation_state"])

    def test_limits_and_missing_ticket_are_safe(self):
        with self.assertRaises(ValueError):
            pulse_db.set_ticket_tags(self.ticket_id, "a,b,c,d,e,f,g,h,i")
        with self.assertRaises(ValueError):
            pulse_db.set_ticket_tags(self.ticket_id, "x" * 25)
        self.assertIsNone(pulse_db.set_ticket_tags("missing-ticket", "test"))

    def test_corrupt_state_is_not_overwritten(self):
        with pulse_db.connect() as db:
            db.execute("UPDATE tickets SET automation_state=? WHERE id=?", ("not-json", self.ticket_id))
        with self.assertRaises(ValueError):
            pulse_db.set_ticket_tags(self.ticket_id, "Dringend")
        self.assertEqual(pulse_db.get_ticket(ticket_id=self.ticket_id)["automation_state"], "not-json")
        self.assertEqual(pulse_db.ticket_tags_from_state("not-json"), [])


if __name__ == "__main__":
    unittest.main()
