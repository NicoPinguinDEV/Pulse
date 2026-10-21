"""Regression tests for the Pulse voice support scheduling helpers."""
from datetime import datetime
from zoneinfo import ZoneInfo
import unittest

from pulse_voice_support_utils import (DEFAULT_HOURS, estimate_wait_seconds, format_wait_estimate, queue_position, shift_is_online, support_open_now)


class VoiceSupportHoursTests(unittest.TestCase):
    def test_weekday_opening_boundary(self):
        cfg = {"hours": DEFAULT_HOURS}
        tz = ZoneInfo("Europe/Berlin")
        self.assertFalse(support_open_now(cfg, datetime(2026, 10, 19, 9, 59, tzinfo=tz)))
        self.assertTrue(support_open_now(cfg, datetime(2026, 10, 19, 10, 0, tzinfo=tz)))
        self.assertTrue(support_open_now(cfg, datetime(2026, 10, 19, 21, 59, tzinfo=tz)))
        self.assertFalse(support_open_now(cfg, datetime(2026, 10, 19, 22, 0, tzinfo=tz)))

    def test_weekend_hours_and_disabled_day(self):
        cfg = {"hours": {**DEFAULT_HOURS, "sunday": {"enabled": False, "start": "10:00", "end": "22:00"}}}
        tz = ZoneInfo("Europe/Berlin")
        self.assertTrue(support_open_now(cfg, datetime(2026, 10, 23, 23, 58, tzinfo=tz)))
        self.assertFalse(support_open_now(cfg, datetime(2026, 10, 23, 23, 59, tzinfo=tz)))
        self.assertFalse(support_open_now(cfg, datetime(2026, 10, 25, 12, 0, tzinfo=tz)))

    def test_invalid_or_equal_time_range_is_closed(self):
        tz = ZoneInfo("Europe/Berlin")
        self.assertFalse(support_open_now({"hours": {"monday": {"enabled": True, "start": "bad", "end": "22:00"}}}, datetime(2026, 10, 19, 12, 0, tzinfo=tz)))
        self.assertFalse(support_open_now({"hours": {"monday": {"enabled": True, "start": "10:00", "end": "10:00"}}}, datetime(2026, 10, 19, 10, 0, tzinfo=tz)))


class VoiceSupportDutyTests(unittest.TestCase):
    def test_only_online_dashboard_shifts_count_as_on_duty(self):
        shifts = {"active_shifts": {"10": {"status": "online"}, "11": {"status": "break"}, "12": {"status": "offline"}}}
        self.assertTrue(shift_is_online(shifts, 10))
        self.assertFalse(shift_is_online(shifts, 11))
        self.assertFalse(shift_is_online(shifts, 12))
        self.assertFalse(shift_is_online(shifts, 13))


class VoiceSupportQueueTests(unittest.TestCase):
    def setUp(self):
        self.cases = {
            "waiting:2": {"case_id": "second", "guild_id": "1", "waiting_in_shared_room": True,
                          "assigned_staff_id": 0, "created_at": "2026-10-21T10:02:00+02:00"},
            "waiting:1": {"case_id": "first", "guild_id": "1", "waiting_in_shared_room": True,
                          "assigned_staff_id": 0, "created_at": "2026-10-21T10:00:00+02:00"},
            "active:3": {"case_id": "active", "guild_id": "1", "waiting_in_shared_room": False,
                         "assigned_staff_id": 42, "created_at": "2026-10-21T09:00:00+02:00"},
            "other:4": {"case_id": "other-guild", "guild_id": "2", "waiting_in_shared_room": True,
                        "assigned_staff_id": 0, "created_at": "2026-10-21T09:59:00+02:00"},
        }

    def test_queue_position_ignores_active_cases_and_other_guilds(self):
        self.assertEqual(queue_position(self.cases, "first", 1), 1)
        self.assertEqual(queue_position(self.cases, "second", 1), 2)
        self.assertIsNone(queue_position(self.cases, "active", 1))
        self.assertEqual(queue_position(self.cases, "other-guild", 2), 1)
        self.assertIsNone(queue_position({}, "missing", 1))

    def test_invalid_timestamp_sorts_after_valid_waiters(self):
        cases = {
            "bad": {"case_id": "bad", "guild_id": "1", "waiting_in_shared_room": True,
                    "assigned_staff_id": 0, "created_at": "unknown"},
            "good": {"case_id": "good", "guild_id": "1", "waiting_in_shared_room": True,
                     "assigned_staff_id": 0, "created_at": "2026-10-21T08:00:00+00:00"},
        }
        self.assertEqual(queue_position(cases, "good", 1), 1)
        self.assertEqual(queue_position(cases, "bad", 1), 2)

    def test_wait_estimate_is_optional_and_accounts_for_parallel_support(self):
        self.assertEqual(estimate_wait_seconds(2, 600, 1), 1200)
        self.assertEqual(estimate_wait_seconds(2, 600, 2), 600)
        self.assertIsNone(estimate_wait_seconds(1, None))
        self.assertIsNone(estimate_wait_seconds(0, 600))

    def test_wait_estimate_formats_minutes_and_hours(self):
        self.assertEqual(format_wait_estimate(None), "Noch keine Vergleichsdaten")
        self.assertEqual(format_wait_estimate(1), "ca. 1 Min.")
        self.assertEqual(format_wait_estimate(3600), "ca. 1 Std.")
        self.assertEqual(format_wait_estimate(3660), "ca. 1 Std. 1 Min.")


if __name__ == "__main__":
    unittest.main()
