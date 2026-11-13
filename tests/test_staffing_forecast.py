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
        result = forecast.build_forecast([], shifts, now=self.now.replace(hour=8), days_ahead=1)
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

    def test_forecast_does_not_show_elapsed_hours_today(self):
        today = self.now.date().isoformat()
        shifts = [
            {"date": today, "start_time": "09:00", "end_time": "11:00", "assignee_id": "A"},
            {"date": today, "start_time": "14:00", "end_time": "16:00", "assignee_id": "B"},
        ]
        result = forecast.build_forecast([], shifts, now=self.now, days_ahead=1)
        shown_hours = {row["hour"] for row in result["rows"]}
        self.assertNotIn(9, shown_hours)
        self.assertNotIn(10, shown_hours)
        self.assertIn(14, shown_hours)

    def test_total_gap_count_includes_slots_beyond_display_limit(self):
        tickets = []
        for age in range(56, 0, -1):
            day = self.now.date() - timedelta(days=age)
            for hour in range(24):
                local = datetime.combine(day, datetime.min.time(), tzinfo=BERLIN).replace(hour=hour, minute=15)
                tickets.append({"opened_at": local.isoformat()})
        result = forecast.build_forecast(
            tickets, [], now=self.now, history_days=56, days_ahead=7,
            tickets_per_person_hour=0.5, limit=56,
        )
        self.assertEqual(len(result["rows"]), forecast.MAX_ROWS)
        self.assertGreater(result["gap_slots_count"], len(result["rows"]))
        self.assertEqual(result["visible_gap_slots_count"], forecast.MAX_ROWS)
        self.assertTrue(result["truncated"])

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


class LoadForecastTests(unittest.TestCase):
    now = datetime(2026, 11, 11, 12, 0, tzinfo=BERLIN)

    def tickets_on(self, weekday, counts):
        days = [d for d in (self.now.date() - timedelta(days=n) for n in range(56, 0, -1)) if d.weekday() == weekday]
        out = []
        for day, count in zip(days, counts):
            at = datetime.combine(day, datetime.min.time(), tzinfo=BERLIN).replace(hour=10)
            out.extend({"opened_at": at.astimezone(timezone.utc).isoformat()} for _ in range(count))
        return out

    def day(self, result, weekday):
        return next(d for d in result["days"] if d["weekday"] == weekday)

    def test_mean_and_interval_come_from_same_weekday_samples(self):
        result = forecast.build_load_forecast(self.tickets_on(0, [10] * 8), now=self.now)
        monday = self.day(result, 0)
        self.assertEqual((monday["expected"], monday["low"], monday["high"], monday["samples"]), (10.0, 10.0, 10.0, 8))
        self.assertEqual(monday["confidence"], "hoch")
        noisy = forecast.build_load_forecast(self.tickets_on(0, [0, 20, 0, 20, 0, 20, 0, 20]), now=self.now)
        monday = self.day(noisy, 0)
        self.assertEqual(monday["expected"], 10.0)
        self.assertEqual(monday["low"], 0.0)
        self.assertGreater(monday["high"], 20.0)

    def test_no_data_means_no_numbers(self):
        result = forecast.build_load_forecast([], now=self.now)
        self.assertTrue(all(d["expected"] is None and d["low"] is None for d in result["days"]))
        self.assertEqual(result["expected_total"], 0)
        self.assertIsNone(result["trend_percent"])

    def test_other_weekdays_forecast_zero_and_bad_rows_are_ignored(self):
        tickets = self.tickets_on(0, [4] * 8) + [{"opened_at": "kaputt"}, {}, "x", {"opened_at": None}]
        result = forecast.build_load_forecast(tickets, now=self.now)
        self.assertEqual(self.day(result, 1)["expected"], 0.0)
        self.assertEqual(result["history_tickets"], 32)

    def test_trend_compares_recent_four_weeks_with_previous_four(self):
        result = forecast.build_load_forecast(self.tickets_on(0, [2, 2, 2, 2, 4, 4, 4, 4]), now=self.now)
        self.assertEqual(result["trend_percent"], 100)

    def test_bounds_and_future_tickets_are_ignored(self):
        future = [{"opened_at": (self.now + timedelta(days=1)).isoformat()}]
        result = forecast.build_load_forecast(future, now=self.now, days_ahead=999, history_days=1)
        self.assertEqual(len(result["days"]), forecast.MAX_DAYS_AHEAD)
        self.assertEqual(result["history_days"], 28)
        self.assertEqual(result["history_tickets"], 0)


if __name__ == "__main__":
    unittest.main()


class ForecastPageTests(unittest.TestCase):
    def test_page_renders_load_table_without_data_and_requires_manager_auth(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        import pulse_db
        import webserver as ws
        from cogs import pulse_suite

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, \
                patch.object(pulse_db, "DB_PATH", str(Path(tmp) / "pulse.db")), \
                patch.object(pulse_suite, "DB_PATH", str(Path(tmp) / "suite.db")):
            pulse_db.init_db()
            pulse_suite.register_routes()
            pulse_suite.init_db()  # register_routes() returns early when another test already registered the routes
            guild = SimpleNamespace(id=1, members=[], roles=[], get_role=lambda _: None, get_member=lambda _: None)
            ctx = SimpleNamespace(user={"id": "1", "global_name": "L"}, guild=guild, guild_name="G", member=None,
                                  perms={"is_admin": True, "can_view_dashboard": True}, config={}, msg="", msg_ok=True)
            with patch.object(ws, "auth", lambda *a, **k: ctx):
                response = TestClient(ws.app, raise_server_exceptions=False).get("/suite/forecast")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Ticketlast pro Tag", response.text)
        self.assertIn("Ohne ausreichende Daten wird keine Zahl erfunden", response.text)
