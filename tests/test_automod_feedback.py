"""Tests for evidence-linked, guild-scoped AutoMod false-positive feedback."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_automod_feedback as feedback


class FeedbackDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def event(self, *, guild_id=1, event_type="AutoMod-Maßnahme", mode="observe", simulation=False):
        return pulse_db.record_event(
            event_type,
            "user",
            123,
            "99",
            "Pulse AutoMod",
            {
                "guild_id": guild_id,
                "rule": "keywords",
                "channel_id": 456,
                "action": "Nur beobachtet (keine Maßnahme)",
                "mode": mode,
                "simulation": simulation,
            },
        )

    def test_records_feedback_and_audit_event_without_message_content(self):
        event_id = self.event()
        row = feedback.record_false_positive(
            event_id, 1, 77, "Moderator", "Der Begriff wurde im erlaubten Kontext verwendet."
        )
        self.assertEqual((row["event_id"], row["rule"], row["reviewer_id"]), (event_id, "keywords", "77"))
        self.assertEqual(row["mode"], "observe")
        stored = feedback.list_false_positives(1)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["reason"], "Der Begriff wurde im erlaubten Kontext verwendet.")
        audit = pulse_db.events(event_type="AutoMod-Fehlalarm bestätigt")
        self.assertEqual(len(audit), 1)
        payload = json.loads(audit[0]["payload_json"])
        self.assertEqual(payload["original_event_id"], event_id)
        self.assertNotIn("content", payload)

    def test_only_the_originating_guild_can_mark_an_event(self):
        event_id = self.event(guild_id=1)
        with self.assertRaisesRegex(ValueError, "gehört nicht"):
            feedback.record_false_positive(event_id, 2, 77, "Moderator", "Falscher Kontext hier.")

    def test_test_mode_and_simulation_hits_cannot_be_marked(self):
        test_event = self.event(event_type="AutoMod-Testtreffer", mode="test", simulation=True)
        with self.assertRaisesRegex(ValueError, "echte AutoMod-Maßnahme"):
            feedback.record_false_positive(test_event, 1, 77, "Moderator", "Nur eine Simulation.")
        simulation = self.event(mode="test", simulation=True)
        with self.assertRaisesRegex(ValueError, "Beobachtungs- oder Aktivmodus"):
            feedback.record_false_positive(simulation, 1, 77, "Moderator", "Nur eine Simulation.")

    def test_duplicate_feedback_is_rejected_and_original_record_is_unchanged(self):
        event_id = self.event()
        feedback.record_false_positive(event_id, 1, 77, "Moderator", "Erste nachvollziehbare Prüfung.")
        with self.assertRaisesRegex(ValueError, "bereits"):
            feedback.record_false_positive(event_id, 1, 88, "Zweiter Moderator", "Andere Begründung dazu.")
        self.assertEqual(len(feedback.list_false_positives(1)), 1)
        self.assertEqual(len(pulse_db.events(event_type="AutoMod-Fehlalarm bestätigt")), 1)

    def test_invalid_ids_and_reasons_are_rejected(self):
        with self.assertRaises(ValueError):
            feedback.record_false_positive("../events/1", 1, 77, "Moderator", "Ausreichende Begründung.")
        event_id = self.event()
        for reason in ("kurz", "x" * 501):
            with self.assertRaises(ValueError):
                feedback.record_false_positive(event_id, 1, 77, "Moderator", reason)

    def test_list_is_guild_scoped_and_limit_is_bounded(self):
        for _ in range(3):
            event_id = self.event(guild_id=1)
            feedback.record_false_positive(event_id, 1, 77, "Moderator", "Nachvollziehbarer Fehlalarm.")
        other = self.event(guild_id=2)
        feedback.record_false_positive(other, 2, 88, "Andere Leitung", "Nachvollziehbarer Fehlalarm.")
        self.assertEqual(len(feedback.list_false_positives(1, limit=2)), 2)
        self.assertEqual(len(feedback.list_false_positives(2, limit=50)), 1)
        self.assertEqual(feedback.list_false_positives(None), [])


if __name__ == "__main__":
    unittest.main()
