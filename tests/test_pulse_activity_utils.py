from datetime import datetime, date
import unittest

from pulse_activity_utils import (
    activity_action_is_terminal,
    activity_warning_due,
    activity_warning_id,
    leave_covers_date,
    parse_activity_date,
)


class ActivityUtilsTests(unittest.TestCase):
    def test_warning_deadline_is_20_local_time(self):
        self.assertFalse(activity_warning_due(datetime(2026, 10, 15, 19, 59)))
        self.assertTrue(activity_warning_due(datetime(2026, 10, 15, 20, 0)))
        self.assertTrue(activity_warning_due(datetime(2026, 10, 15, 23, 59)))

    def test_abmeldung_inclusive_date_range(self):
        self.assertTrue(leave_covers_date("2026-10-14", "2026-10-16", "2026-10-15"))
        self.assertTrue(leave_covers_date("15.10.2026", "16.10.2026", "2026-10-15"))
        self.assertFalse(leave_covers_date("2026-10-16", "2026-10-18", "2026-10-15"))
        self.assertFalse(leave_covers_date("2026-10-10", "2026-10-14", "2026-10-15"))

    def test_malformed_leave_record_fails_safe(self):
        self.assertTrue(leave_covers_date("bad-date", "2026-10-17", "2026-10-15"))
        self.assertTrue(leave_covers_date("2026-10-14", "bad-date", "2026-10-15"))

    def test_warning_id_is_stable_per_check_and_user(self):
        first = activity_warning_id(42, 1234)
        self.assertEqual(first, activity_warning_id(42, 1234))
        self.assertNotEqual(first, activity_warning_id(43, 1234))
        self.assertNotEqual(first, activity_warning_id(42, 1235))
        self.assertTrue(first.startswith("warn_"))

    def test_terminal_statuses_are_not_retried(self):
        for status in ("issued", "confirmed", "on_leave", "not_team_member", "max_warns"):
            self.assertTrue(activity_action_is_terminal(status))
        for status in (None, "failed", "processing"):
            self.assertFalse(activity_action_is_terminal(status))


if __name__ == "__main__":
    unittest.main()
