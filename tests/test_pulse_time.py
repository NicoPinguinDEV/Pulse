"""D-09: one tolerant parser for every timestamp format Pulse has stored."""
import unittest
from datetime import datetime, timezone

from pulse_time import BERLIN, age_days, display, now_iso, parse_timestamp


class PulseTimeTests(unittest.TestCase):
    def test_all_legacy_formats(self):
        expected = datetime(2026, 10, 20, 14, 30, tzinfo=BERLIN)
        for value in ("20.10.2026 14:30", "20.10.2026 14:30:00", "2026-10-20 14:30", "2026-10-20T14:30:00+02:00",
                      "2026-10-20T12:30:00Z", "2026-10-20T12:30:00+00:00"):
            with self.subTest(value=value):
                self.assertEqual(parse_timestamp(value), expected)
        self.assertEqual(parse_timestamp("2026-10-20").date().isoformat(), "2026-10-20")
        self.assertEqual(parse_timestamp("20.10.2026").date().isoformat(), "2026-10-20")
        self.assertEqual(parse_timestamp(0), datetime(1970, 1, 1, tzinfo=timezone.utc))

    def test_garbage_returns_none(self):
        for value in (None, "", "morgen", "32.13.2026", "N/A", True and "x"):
            self.assertIsNone(parse_timestamp(value))

    def test_display_in_berlin_and_fallback(self):
        self.assertEqual(display("2026-10-20T12:30:00Z"), "20.10.2026 14:30")
        self.assertEqual(display("2026-01-20T12:30:00Z"), "20.01.2026 13:30")  # winter time
        self.assertEqual(display("unbekannt"), "unbekannt")
        self.assertEqual(display("2026-10-20", with_time=False), "20.10.2026")

    def test_now_iso_has_offset_and_age(self):
        self.assertRegex(now_iso(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")
        self.assertAlmostEqual(age_days("2026-10-10T00:00:00+00:00", datetime(2026, 10, 20, tzinfo=timezone.utc)), 10)
        self.assertIsNone(age_days("kaputt"))


if __name__ == "__main__":
    unittest.main()
