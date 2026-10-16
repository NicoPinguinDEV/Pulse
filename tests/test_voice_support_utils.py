"""Regression tests for the Pulse voice support scheduling helpers."""
from datetime import datetime
from zoneinfo import ZoneInfo
import unittest

from pulse_voice_support_utils import DEFAULT_HOURS, shift_is_online, support_open_now


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


if __name__ == "__main__":
    unittest.main()
