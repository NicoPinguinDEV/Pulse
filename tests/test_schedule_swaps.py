"""Two-party shift swap requests with manager approval and overlap protection."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

import cogs.pulse_suite as suite


GUILD = "155555555555555555"
REQUESTER = "100000000000000001"
TARGET = "100000000000000002"
MANAGER = "100000000000000003"


class ScheduleSwapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path_patch = patch.object(suite, "DB_PATH", str(Path(self.tmp.name) / "pulse_suite.db"))
        self.path_patch.start()
        suite.init_db()
        self.future = (datetime.now(ZoneInfo("Europe/Berlin")).date() + timedelta(days=3)).isoformat()

    def tearDown(self):
        self.path_patch.stop()
        self.tmp.cleanup()

    def add_shift(self, shift_id, date, start, end, member_id, member_name):
        with suite.cx() as connection:
            connection.execute(
                """INSERT INTO suite_schedule
                   (id,guild_id,date,shift_name,start_time,end_time,assignee_id,assignee_name,
                    created_by_id,created_by_name,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (shift_id, GUILD, date, shift_id, start, end, member_id, member_name, MANAGER, "Leitung", suite.iso()),
            )

    def make_request(self, own_date=None, target_date=None):
        self.add_shift("own", own_date or self.future, "09:00", "12:00", REQUESTER, "Alice")
        self.add_shift("target", target_date or self.future, "14:00", "17:00", TARGET, "Bob")
        return suite.create_schedule_swap_request(GUILD, "own", "target", REQUESTER, "Alice")

    def test_peer_acceptance_and_manager_approval_atomically_swap_owners(self):
        request_id = self.make_request()
        [request] = suite.schedule_swap_requests(GUILD, REQUESTER)
        self.assertEqual(request["id"], request_id)
        self.assertEqual(request["status"], "pending_peer")
        self.assertEqual(request["target_name"], "Bob")
        with self.assertRaises(PermissionError):
            suite.respond_schedule_swap_request(request_id, GUILD, REQUESTER, True)

        self.assertEqual(suite.respond_schedule_swap_request(request_id, GUILD, TARGET, True), "pending_manager")
        result = suite.decide_schedule_swap_request(request_id, GUILD, MANAGER, "Leitung", True)
        self.assertEqual(result["status"], "approved")
        with suite.cx() as connection:
            own = dict(connection.execute("SELECT * FROM suite_schedule WHERE id='own'").fetchone())
            target = dict(connection.execute("SELECT * FROM suite_schedule WHERE id='target'").fetchone())
        self.assertEqual((own["assignee_id"], own["assignee_name"]), (TARGET, "Bob"))
        self.assertEqual((target["assignee_id"], target["assignee_name"]), (REQUESTER, "Alice"))
        [saved] = suite.schedule_swap_requests(GUILD, REQUESTER)
        self.assertEqual(saved["status"], "approved")

    def test_peer_rejection_and_requester_cancellation_do_not_swap(self):
        request_id = self.make_request()
        self.assertEqual(suite.respond_schedule_swap_request(request_id, GUILD, TARGET, False), "rejected")
        with suite.cx() as connection:
            self.assertEqual(connection.execute("SELECT assignee_id FROM suite_schedule WHERE id='own'").fetchone()[0], REQUESTER)
        another = suite.create_schedule_swap_request(GUILD, "own", "target", REQUESTER, "Alice")
        self.assertTrue(suite.cancel_schedule_swap_request(another, GUILD, REQUESTER))
        self.assertFalse(suite.cancel_schedule_swap_request(another, GUILD, TARGET))
        with suite.cx() as connection:
            self.assertEqual(connection.execute("SELECT status FROM suite_schedule_swap_requests WHERE id=?", (another,)).fetchone()[0], "cancelled")

    def test_only_own_future_shift_and_another_members_shift_can_be_requested(self):
        self.add_shift("own", self.future, "09:00", "12:00", REQUESTER, "Alice")
        self.add_shift("target", self.future, "14:00", "17:00", TARGET, "Bob")
        with self.assertRaises(PermissionError):
            suite.create_schedule_swap_request(GUILD, "target", "own", REQUESTER, "Alice")
        with self.assertRaises(ValueError):
            suite.create_schedule_swap_request(GUILD, "own", "own", REQUESTER, "Alice")
        past = (datetime.now(ZoneInfo("Europe/Berlin")).date() - timedelta(days=2)).isoformat()
        self.add_shift("past", past, "09:00", "12:00", TARGET, "Bob")
        with self.assertRaises(ValueError):
            suite.create_schedule_swap_request(GUILD, "own", "past", REQUESTER, "Alice")
        with self.assertRaises(ValueError):
            suite.create_schedule_swap_request("999999999999999999", "own", "target", REQUESTER, "Alice")

    def test_overlapping_existing_shift_blocks_approval_without_partial_update(self):
        request_id = self.make_request()
        self.add_shift("target_conflict", self.future, "09:30", "10:30", TARGET, "Bob")
        suite.respond_schedule_swap_request(request_id, GUILD, TARGET, True)
        with self.assertRaisesRegex(ValueError, "überschneidet"):
            suite.decide_schedule_swap_request(request_id, GUILD, MANAGER, "Leitung", True)
        with suite.cx() as connection:
            own = connection.execute("SELECT assignee_id FROM suite_schedule WHERE id='own'").fetchone()[0]
            target = connection.execute("SELECT assignee_id FROM suite_schedule WHERE id='target'").fetchone()[0]
            status = connection.execute("SELECT status FROM suite_schedule_swap_requests WHERE id=?", (request_id,)).fetchone()[0]
        self.assertEqual((own, target, status), (REQUESTER, TARGET, "pending_manager"))

    def test_stale_assignment_and_duplicate_pending_requests_are_rejected(self):
        request_id = self.make_request()
        with self.assertRaises(ValueError):
            suite.create_schedule_swap_request(GUILD, "own", "target", REQUESTER, "Alice")
        suite.respond_schedule_swap_request(request_id, GUILD, TARGET, True)
        with suite.cx() as connection:
            connection.execute("UPDATE suite_schedule SET assignee_id=?,assignee_name=? WHERE id='target'", ("100000000000000004", "Cara"))
        with self.assertRaisesRegex(ValueError, "geändert"):
            suite.decide_schedule_swap_request(request_id, GUILD, MANAGER, "Leitung", True)

    def test_overnight_schedule_intervals_are_parsed_correctly(self):
        start, end = suite._schedule_interval({
            "date": self.future, "start_time": "22:00", "end_time": "06:00",
        })
        self.assertEqual(end - start, timedelta(hours=8))
        self.assertEqual(end.date(), start.date() + timedelta(days=1))


if __name__ == "__main__":
    unittest.main()
