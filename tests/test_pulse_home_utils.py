import unittest
from datetime import date, datetime, timedelta

from pulse_home_utils import daily_seconds, due_state, format_hm, goal_progress, greeting, shift_streak


class MyPulseHelperTests(unittest.TestCase):
    def test_greeting_follows_time_of_day(self):
        self.assertEqual(greeting(7), "Guten Morgen")
        self.assertEqual(greeting(14), "Guten Tag")
        self.assertEqual(greeting(20), "Guten Abend")
        self.assertEqual(greeting(2), "Gute Nacht")

    def test_format_hm_pads_minutes(self):
        self.assertEqual(format_hm(3600 + 5 * 60), "1h 05m")
        self.assertEqual(format_hm(-10), "0h 00m")

    def test_goal_progress_caps_and_reports_remaining(self):
        self.assertEqual(goal_progress(1.5 * 3600, 3), (50, int(1.5 * 3600)))
        self.assertEqual(goal_progress(10 * 3600, 3), (100, 0))
        self.assertEqual(goal_progress(0, 0), (100, 0))

    def test_daily_seconds_counts_only_own_recent_shifts_and_running_shift(self):
        today = date(2026, 10, 18)
        history = [
            {"mod_id": "1", "date": "2026-10-18", "duration_seconds": 600},
            {"mod_id": "1", "date": "2026-10-12", "duration_seconds": 300},
            {"mod_id": "1", "date": "2026-10-11", "duration_seconds": 999},  # outside 7 days
            {"mod_id": "2", "date": "2026-10-18", "duration_seconds": 999},  # someone else
            {"mod_id": "1", "date": "kaputt", "duration_seconds": 999},
        ]
        result = dict(daily_seconds(history, "1", today, 7, active_date=today, active_seconds=60))
        self.assertEqual(len(result), 7)
        self.assertEqual(result[today], 660)
        self.assertEqual(result[date(2026, 10, 12)], 300)
        self.assertNotIn(date(2026, 10, 11), result)

    def test_streak_counts_consecutive_days(self):
        today = date(2026, 10, 18)
        history = [{"mod_id": "1", "date": (today - timedelta(days=d)).isoformat(), "duration_seconds": 60} for d in (1, 2, 3, 5)]
        self.assertEqual(shift_streak(history, "1", today), 3)
        self.assertEqual(shift_streak(history, "1", today, active_today=True), 4)
        self.assertEqual(shift_streak([], "1", today), 0)

    def test_due_state(self):
        now = datetime(2026, 10, 18, 12, 0)
        self.assertEqual(due_state(now - timedelta(minutes=1), now), "overdue")
        self.assertEqual(due_state(now + timedelta(hours=2), now), "today")
        self.assertEqual(due_state(now + timedelta(days=2), now), "soon")
        self.assertEqual(due_state(now + timedelta(days=9), now), "later")
        self.assertIsNone(due_state(None, now))


if __name__ == "__main__":
    unittest.main()
