"""BOT-06: temporary ticket-creation locks expire and preserve existing ticket data."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_ticket_locks


class TicketLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()
        self.now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def lock(self, hours=3, reason="Spam in Tickets"):
        return pulse_ticket_locks.create_lock(
            100, 200, hours, reason, actor_id=1, actor_name="Admin", now=self.now,
        )

    def test_lock_is_guild_scoped_and_expires_automatically_on_check(self):
        record = self.lock(2)
        self.assertEqual(record["expires_at"], (self.now + timedelta(hours=2)).isoformat(timespec="seconds"))
        self.assertIsNotNone(pulse_ticket_locks.get_lock(100, 200, now=self.now + timedelta(hours=1)))
        self.assertIsNone(pulse_ticket_locks.get_lock(101, 200, now=self.now))
        self.assertIsNone(pulse_ticket_locks.get_lock(100, 200, now=self.now + timedelta(hours=2)))
        self.assertIsNone(pulse_db.get_setting("ticket_lock:100:200"))

    def test_lock_rejects_invalid_duration_and_short_reason(self):
        with self.assertRaises(ValueError):
            self.lock(0)
        with self.assertRaises(ValueError):
            self.lock(169)
        with self.assertRaises(ValueError):
            self.lock(2, "no")
        with self.assertRaises(ValueError):
            self.lock(2, "x" * 501)

    def test_admin_release_removes_lock_and_records_history(self):
        self.lock()
        record = pulse_ticket_locks.release_lock(100, 200, actor_id=3, actor_name="Leitung")
        self.assertEqual(record["user_id"], "200")
        self.assertIsNone(pulse_ticket_locks.get_lock(100, 200, now=self.now))
        self.assertIsNone(pulse_db.get_setting("ticket_lock:100:200"))

    def test_invalid_expiry_fails_closed_and_admin_can_clear(self):
        pulse_db.set_setting("ticket_lock:100:200", {
            "active": True, "reason": "Beschädigte Sperre", "expires_at": "kein Datum",
        })
        self.assertTrue(pulse_ticket_locks.get_lock(100, 200, now=self.now)["invalid"])
        self.assertIsNotNone(pulse_ticket_locks.release_lock(100, 200, actor_id=3, actor_name="Leitung"))
        self.assertIsNone(pulse_ticket_locks.get_lock(100, 200, now=self.now))


if __name__ == "__main__":
    unittest.main()
