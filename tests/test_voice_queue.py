"""BOT-07: voice-support queue position, wait estimate, history and supporter statistics."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pulse_db
import pulse_voice_queue as vq


def case(uid, created, staff=0, started=None, waiting=True):
    return {"case_id": f"c{uid}", "user_id": str(uid), "created_at": created, "assigned_staff_id": staff,
            "assigned_staff_name": f"S{staff}" if staff else "", "support_started_at": started, "waiting_in_shared_room": waiting}


class QueueTests(unittest.TestCase):
    def test_position_counts_only_unclaimed_waiting_cases(self):
        cases = {"waiting:1": case(1, "2026-10-21T10:00:00"), "waiting:2": case(2, "2026-10-21T10:05:00"),
                 "room": case(3, "2026-10-21T09:00:00", staff=7, waiting=False)}
        self.assertEqual(vq.queue_position(cases, 1), 1)
        self.assertEqual(vq.queue_position(cases, 2), 2)
        self.assertIsNone(vq.queue_position(cases, 3))

    def test_estimate_from_history(self):
        history = [case(n, "2026-10-21T10:00:00", staff=7, started="2026-10-21T10:04:00") for n in range(5)]
        self.assertEqual(vq.typical_wait_seconds(history), 240)
        self.assertIn("etwa **8 Min.**", vq.estimate_text(2, history, 1))
        self.assertIn("etwa **4 Min.**", vq.estimate_text(2, history, 2))   # two supporters on duty
        self.assertNotIn("Wartezeit", vq.estimate_text(1, [], 1))          # no data → only the position
        self.assertEqual(vq.estimate_text(None, history, 1), "")

    def test_history_is_bounded(self):
        history = []
        for n in range(vq.HISTORY_MAX + 5):
            history = vq.append_history(history, {"case_id": n})
        self.assertEqual(len(history), vq.HISTORY_MAX)
        self.assertEqual(history[0]["case_id"], 5)

    def test_supporter_stats(self):
        history = [vq.history_entry(case(1, "2026-10-21T10:00:00", staff=7, started="2026-10-21T10:02:00"), "fertig", "2026-10-21T10:12:00"),
                   vq.history_entry(case(2, "2026-10-21T11:00:00", staff=7, started="2026-10-21T11:04:00"), "fertig", "2026-10-21T11:10:00"),
                   vq.history_entry(case(3, "2026-10-21T11:00:00"), "verlassen", "2026-10-21T11:01:00")]
        stats = vq.supporter_stats(history, [{"case_id": "c1", "rating": 5}, {"case_id": "c2", "rating": 4}])
        self.assertEqual(len(stats), 1)
        row = stats[0]
        self.assertEqual((row["cases"], row["median_wait_min"], row["median_duration_min"], row["rating"]), (2, 3.0, 8.0, 4.5))


class CogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    def test_citizen_gets_position_dm(self):
        from cogs import voice_support
        pulse_db.set_setting(voice_support.CASES_KEY, {"waiting:5": case(5, "2026-10-21T10:00:00")})
        sent = []

        async def send(text):
            sent.append(text)
        member = SimpleNamespace(id=5, send=send)
        cog = object.__new__(voice_support.VoiceSupport)
        asyncio.run(cog._tell_queue_position(member, 1))
        self.assertIn("Nr. 1", sent[0])

    def test_after_hours_offers_a_ticket(self):
        from cogs import voice_support
        pulse_db.set_setting("ticket_system_config", {"ticket_panels": [{"id": "p", "channel_id": 4242, "message_id": 1}]})
        with patch("pulse_panels.BASE_DIR", Path(self.tmp.name)):
            self.assertIn("<#4242>", voice_support.ticket_instead_text(1))
        pulse_db.set_setting("ticket_system_config", {})
        with patch("pulse_panels.BASE_DIR", Path(self.tmp.name)):
            self.assertEqual(voice_support.ticket_instead_text(1), "")


if __name__ == "__main__":
    unittest.main()
