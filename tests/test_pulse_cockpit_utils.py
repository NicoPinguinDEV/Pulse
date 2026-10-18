import unittest
from datetime import date

from pulse_cockpit_utils import (
    inactive_days,
    is_inactive,
    last_shift_dates,
    seconds_per_member,
    trend,
    week_to_date_windows,
)


class CockpitHelperTests(unittest.TestCase):
    HISTORY = [
        {"mod_id": "1", "date": "2026-10-13", "duration_seconds": 3600},
        {"mod_id": "1", "date": "2026-10-15", "duration_seconds": 1800},
        {"mod_id": "2", "date": "2026-10-06", "duration_seconds": 7200},
        {"mod_id": "2", "date": "kaputt", "duration_seconds": 999},
        "kein dict",
    ]

    def test_seconds_per_member_respects_window(self):
        totals = seconds_per_member(self.HISTORY, date(2026, 10, 12), date(2026, 10, 18))
        self.assertEqual(totals, {"1": 5400})

    def test_last_shift_dates(self):
        self.assertEqual(last_shift_dates(self.HISTORY), {"1": date(2026, 10, 15), "2": date(2026, 10, 6)})

    def test_week_to_date_windows_compare_same_span(self):
        this, prev = week_to_date_windows(date(2026, 10, 15))  # Thursday
        self.assertEqual(this, (date(2026, 10, 12), date(2026, 10, 15)))
        self.assertEqual(prev, (date(2026, 10, 5), date(2026, 10, 8)))

    def test_trend_labels(self):
        self.assertEqual(trend(150, 100), ("↑", "+50 %"))
        self.assertEqual(trend(50, 100), ("↓", "-50 %"))
        self.assertEqual(trend(100, 100), ("→", "±0 %"))
        self.assertEqual(trend(5, 0), ("↑", "neu"))
        self.assertEqual(trend(0, 0), ("→", "unverändert"))

    def test_inactivity_excludes_leave_and_flags_never_active(self):
        today = date(2026, 10, 18)
        self.assertIsNone(inactive_days(None, today))
        self.assertTrue(is_inactive(None, today, on_leave=False))
        self.assertFalse(is_inactive(None, today, on_leave=True))
        self.assertTrue(is_inactive(date(2026, 10, 10), today, on_leave=False))
        self.assertFalse(is_inactive(date(2026, 10, 11), today, on_leave=False))


if __name__ == "__main__":
    unittest.main()
