"""Bounded staffing forecast tests: historical ticket volume vs. scheduled coverage."""
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pulse_staffing_forecast as forecast


BERLIN = ZoneInfo("Europe/Berlin")


class StaffingForecastTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 11, 12, 0, tzinfo=BERLIN)
        self.next_monday = (self.now.date() + timedelta(days=5)).isoformat()

    def monday_history(self, count=8):
        tickets = []
        day = self.now.date() - timedelta(days=56)
        while day < self.now.date():
            if day.weekday() == 0:
                local = datetime.combine(day, datetime.min.time(), tzinfo=BERLIN).replace(hour=18, minute=15)
                tickets.extend({"opened_at": local.astimezone(timezone.utc).isoformat()} for _ in range(count // 8))
            day += timedelta(days=1)
        return tickets

    def find_slot(self, result, day, hour):
        return next(row for row in result["rows"] if row["date"] == day and row["hour"] == hour)

    def test_weekday_hour_average_compares_required_and_planned_staff(self):
        tickets = self.monday_history(8)
        shifts = [{
            "date": self.next_monday, "start_time": "17:00", "end_time": "20:00",
            "assignee_id": "100000000000000001", "assignee_name": "Alice",
        }]
        result = forecast.build_forecast(
            tickets, shifts, now=self.now, days_ahead=7, tickets_per_person_hour=1.0,
        )
        slot = self.find_slot(result, self.next_monday, 18)
        self.assertEqual(result["history_tickets"], 8)
        self.assertEqual(slot["sample_occurrences"], 8)
        self.assertEqual(slot["expected_tickets"], 1.0)
        self.assertEqual(slot["required_staff"], 1)
        self.assertEqual(slot["planned_staff"], 1)
        self.assertEqual(slot["gap"], 0)

    def test_capacity_assumption_changes_estimated_required_headcount(self):
        tickets = self.monday_history(8)
        shifts = [{
            "date": self.next_monday, "start_time": "17:00", "end_time": "20:00",
            "assignee_id": "100000000000000001",
        }]
        result = forecast.build_forecast(tickets, shifts, now=self.now, tickets_per_person_hour=0.5)
        slot = self.find_slot(result, self.next_monday, 18)
        self.assertEqual(slot["required_staff"], 2)
        self.assertEqual(slot["planned_staff"], 1)
        self.assertEqual(slot["gap"], 1)

    def test_overlapping_shifts_of_same_member_are_not_double_counted(self):
        shifts = [
            {"date": self.now.date().isoformat(), "start_time": "09:00", "end_time": "12:00", "assignee_id": "A"},
            {"date": self.now.date().isoformat(), "start_time": "10:00", "end_time": "13:00", "assignee_id": "A"},
            {"date": self.now.date().isoformat(), "start_time": "10:00", "end_time": "13:00", "assignee_id": "B"},
        ]
        result = forecast.build_forecast([], shifts, now=self.now, days_ahead=1)
        slot = self.find_slot(result, self.now.date().isoformat(), 10)
        self.assertEqual(slot["planned_staff"], 2)
        self.assertEqual(result["planned_shifts_count"], 3)

    def test_overnight_shift_covers_both_calendar_days(self):
        shifts = [{
            "date": self.now.date().isoformat(), "start_time": "22:00", "end_time": "06:00",
            "assignee_id": "100000000000000001",
        }]
        result = forecast.build_forecast([], shifts, now=self.now, days_ahead=2)
        today = self.now.date().isoformat()
        tomorrow = (self.now.date() + timedelta(days=1)).isoformat()
        self.assertEqual(self.find_slot(result, today, 23)["planned_staff"], 1)
        self.assertEqual(self.find_slot(result, tomorrow, 1)["planned_staff"], 1)
        self.assertNotIn((tomorrow, 6), {(row["date"], row["hour"]) for row in result["rows"]})

    def test_empty_history_does_not_invent_demand_and_empty_slots_stay_empty(self):
        result = forecast.build_forecast([], [], now=self.now)
        self.assertEqual(result["history_tickets"], 0)
        self.assertEqual(result["rows"], [])

    def test_invalid_rows_are_ignored_and_inputs_are_bounded(self):
        result = forecast.build_forecast(
            [{"opened_at": "not-a-date"}, None, {"opened_at": datetime(2026, 11, 9, 18, tzinfo=BERLIN).isoformat()}],
            [{"date": "invalid", "start_time": "n/a", "end_time": "n/a", "assignee_id": "A"},
             {"date": self.now.date().isoformat(), "start_time": "10:00", "end_time": "11:00"}],
            now=self.now, history_days=999, days_ahead=999, tickets_per_person_hour=99, limit=999,
        )
        self.assertEqual(result["history_days"], forecast.MAX_HISTORY_DAYS)
        self.assertEqual(result["days_ahead"], forecast.MAX_DAYS_AHEAD)
        self.assertEqual(result["tickets_per_person_hour"], forecast.DEFAULT_CAPACITY)
        self.assertLessEqual(len(result["rows"]), forecast.MAX_ROWS)
        self.assertEqual(result["history_tickets"], 1)

    def test_capacity_validation_uses_safe_default(self):
        self.assertEqual(forecast.normalize_capacity(0), forecast.DEFAULT_CAPACITY)
        self.assertEqual(forecast.normalize_capacity(100), forecast.DEFAULT_CAPACITY)
        self.assertEqual(forecast.normalize_capacity("invalid"), forecast.DEFAULT_CAPACITY)
        self.assertEqual(forecast.normalize_capacity(1.25), 1.2)


if __name__ == "__main__":
    unittest.main()