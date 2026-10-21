import unittest
from datetime import datetime, timezone, timedelta

from pulse_shift_utils import (
    AUTO_SHIFT_END_SECONDS,
    shift_age_seconds,
    credited_shift_seconds,
    parse_max_shift_hours,
    shift_event_id,
    shift_is_overdue,
)


class ShiftTimeoutTests(unittest.TestCase):
    def test_warning_threshold_is_exactly_24_hours(self):
        self.assertEqual(AUTO_SHIFT_END_SECONDS, 24 * 60 * 60)
        now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        self.assertFalse(shift_is_overdue((now - timedelta(hours=23, minutes=59)).isoformat(), now))
        self.assertTrue(shift_is_overdue((now - timedelta(hours=24)).isoformat(), now))

    def test_timezone_offsets_are_compared_correctly(self):
        now = datetime.fromisoformat("2026-10-15T14:00:00+02:00")
        started = "2026-10-14T12:00:00+00:00"
        self.assertTrue(shift_is_overdue(started, now))

    def test_legacy_naive_timestamp_is_accepted(self):
        now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(
            shift_age_seconds("2026-10-14T12:00:00", now),
            float(24 * 60 * 60),
        )


    def test_unconfirmed_auto_closed_shift_has_zero_credit_even_for_legacy_records(self):
        self.assertEqual(credited_shift_seconds({
            "auto_closed": True,
            "duration_seconds": 24 * 3600,
        }), 0)
        self.assertEqual(credited_shift_seconds({
            "auto_closed": True,
            "duration_seconds": 12 * 3600,
            "duration_confirmed": False,
        }), 0)

    def test_lead_confirmed_duration_is_credited_but_normal_shifts_are_unchanged(self):
        self.assertEqual(credited_shift_seconds({
            "auto_closed": True,
            "duration_seconds": 90 * 60,
            "duration_confirmed": True,
        }), 90 * 60)
        self.assertEqual(credited_shift_seconds({
            "duration_seconds": 30 * 60,
        }), 30 * 60)

    def test_max_shift_hours_config_is_validated_and_defaults_safely(self):
        self.assertEqual(parse_max_shift_hours("12"), 12.0)
        self.assertEqual(parse_max_shift_hours("0.5"), 0.5)
        self.assertEqual(parse_max_shift_hours("25"), 12.0)
        self.assertEqual(parse_max_shift_hours("nan"), 12.0)
        self.assertEqual(parse_max_shift_hours("oops"), 12.0)

    def test_shift_event_id_is_stable_and_unique_per_start(self):
        first = shift_event_id("12345", "2026-10-14T12:00:00+00:00")
        again = shift_event_id("12345", "2026-10-14T12:00:00+00:00")
        second = shift_event_id("12345", "2026-10-14T13:00:00+00:00")
        self.assertEqual(first, again)
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
