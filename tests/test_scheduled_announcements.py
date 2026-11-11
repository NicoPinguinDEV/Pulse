"""Regression tests for two-person approval and scheduled announcement delivery."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pulse_db
from cogs.scheduled_announcements import parse_scheduled_at


class ScheduledAnnouncementStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def create(self, *, when=None, guild="123", author="456", kind="info"):
        scheduled = when or (datetime.now(timezone.utc) + timedelta(hours=2))
        return pulse_db.create_scheduled_announcement(
            guild,
            "Teamabend",
            "Der Teamabend beginnt um 19 Uhr.",
            author,
            "Entwurf-Verfasser",
            kind,
            scheduled.isoformat(timespec="seconds"),
        )

    def test_draft_is_guild_scoped_and_not_published_before_approval(self):
        row = self.create()
        self.assertEqual(row["status"], "draft")
        self.assertEqual(pulse_db.list_scheduled_announcements("123")[0]["id"], row["id"])
        self.assertEqual(pulse_db.list_scheduled_announcements("999"), [])
        self.assertEqual(pulse_db.claim_due_scheduled_announcements(), [])
        exported = pulse_db.export_state()
        self.assertIn("scheduled_announcements", exported)
        self.assertEqual(exported["scheduled_announcements"][0]["author_id"], "456")
        from pulse_retention import _ID_COLUMNS
        self.assertIn("author_id", _ID_COLUMNS)
        self.assertIn("approved_by_id", _ID_COLUMNS)

    def test_creator_cannot_approve_and_second_person_can_approve_once(self):
        row = self.create()
        with self.assertRaisesRegex(ValueError, "Verfasser"):
            pulse_db.approve_scheduled_announcement(row["id"], "123", "456", "Creator")
        approved = pulse_db.approve_scheduled_announcement(
            row["id"], "123", "789", "Leitung"
        )
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["approved_by_id"], "789")
        self.assertEqual(approved["approved_by_name"], "Leitung")
        with self.assertRaisesRegex(ValueError, "kein offener Entwurf"):
            pulse_db.approve_scheduled_announcement(row["id"], "123", "790", "Andere Leitung")
        with self.assertRaisesRegex(ValueError, "nicht gefunden"):
            pulse_db.approve_scheduled_announcement(row["id"], "999", "789", "Leitung")

    def test_due_items_are_claimed_once_and_completion_is_atomic_with_history(self):
        row = self.create()
        pulse_db.approve_scheduled_announcement(row["id"], "123", "789", "Leitung")
        due_at = datetime.fromisoformat(row["scheduled_at"]) + timedelta(minutes=1)

        first_claim = pulse_db.claim_due_scheduled_announcements(now=due_at)
        second_claim = pulse_db.claim_due_scheduled_announcements(now=due_at)

        self.assertEqual([item["id"] for item in first_claim], [row["id"]])
        self.assertEqual(first_claim[0]["status"], "publishing")
        self.assertEqual(second_claim, [])
        history_id = pulse_db.complete_scheduled_announcement(row["id"], "987654321")
        self.assertTrue(history_id)
        self.assertIsNone(pulse_db.complete_scheduled_announcement(row["id"], "987654321"))
        saved = pulse_db.list_scheduled_announcements("123")[0]
        self.assertEqual((saved["status"], saved["discord_message_id"]), ("published", "987654321"))
        history = pulse_db.announcements(limit=5)
        self.assertEqual(history[0]["id"], history_id)
        self.assertEqual(history[0]["discord_message_id"], "987654321")
        self.assertEqual(history[0]["title"], "Teamabend")

    def test_failed_delivery_is_visible_and_never_automatically_retried(self):
        row = self.create()
        pulse_db.approve_scheduled_announcement(row["id"], "123", "789", "Leitung")
        due_at = datetime.fromisoformat(row["scheduled_at"]) + timedelta(minutes=1)
        self.assertEqual(len(pulse_db.claim_due_scheduled_announcements(now=due_at)), 1)
        self.assertTrue(
            pulse_db.fail_scheduled_announcement(row["id"], "Team-Updates-Kanal fehlt.")
        )
        self.assertEqual(pulse_db.claim_due_scheduled_announcements(now=due_at), [])
        failed = pulse_db.list_scheduled_announcements("123")[0]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("Kanal fehlt", failed["last_error"])

    def test_draft_and_approved_items_can_be_cancelled_but_publishing_cannot(self):
        draft = self.create()
        self.assertTrue(pulse_db.cancel_scheduled_announcement(draft["id"], "123"))
        self.assertFalse(pulse_db.cancel_scheduled_announcement(draft["id"], "123"))

        approved = self.create(author="457")
        pulse_db.approve_scheduled_announcement(approved["id"], "123", "789", "Leitung")
        self.assertTrue(pulse_db.cancel_scheduled_announcement(approved["id"], "123"))

        publishing = self.create(author="458")
        pulse_db.approve_scheduled_announcement(publishing["id"], "123", "789", "Leitung")
        due_at = datetime.fromisoformat(publishing["scheduled_at"]) + timedelta(minutes=1)
        claimed = pulse_db.claim_due_scheduled_announcements(now=due_at)
        self.assertEqual([row["id"] for row in claimed], [publishing["id"]])
        self.assertFalse(pulse_db.cancel_scheduled_announcement(publishing["id"], "123"))

    def test_invalid_identity_payload_and_kind_are_rejected(self):
        scheduled = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        with self.assertRaises(ValueError):
            pulse_db.create_scheduled_announcement("guild", "Titel", "Text", "456", "Name", "info", scheduled)
        with self.assertRaises(ValueError):
            pulse_db.create_scheduled_announcement("123", "", "Text", "456", "Name", "info", scheduled)
        with self.assertRaises(ValueError):
            pulse_db.create_scheduled_announcement("123", "Titel", "Text", "456", "Name", "unknown", scheduled)
        with self.assertRaises(ValueError):
            pulse_db.create_scheduled_announcement("123", "Titel", "Text", "456", "Name", "info", "2030-01-01T12:00:00")


class ScheduledAnnouncementTimeTests(unittest.TestCase):
    def test_berlin_time_is_converted_to_utc(self):
        now = datetime(2030, 1, 1, 11, 0, tzinfo=timezone.utc)
        scheduled = parse_scheduled_at("2030-01-01 12:15", now=now)
        self.assertEqual(scheduled, datetime(2030, 1, 1, 11, 15, tzinfo=timezone.utc))

    def test_nonexistent_daylight_saving_time_is_rejected(self):
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "Zeitumstellung"):
            parse_scheduled_at("2030-03-31 02:30", now=now)

    def test_minimum_lead_and_maximum_horizon_are_enforced(self):
        now = datetime(2030, 1, 1, 11, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "10 Minuten"):
            parse_scheduled_at("2030-01-01 12:05", now=now)
        with self.assertRaisesRegex(ValueError, "90 Tage"):
            parse_scheduled_at("2030-04-10 12:00", now=now)


if __name__ == "__main__":
    unittest.main()
