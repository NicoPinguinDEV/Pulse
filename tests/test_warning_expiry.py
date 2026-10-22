"""Regression tests for legacy team-warning expiry migration."""
import unittest
from datetime import datetime
from unittest.mock import patch

import webserver
from pulse_time import BERLIN


class WarningExpiryMigrationTests(unittest.TestCase):
    def test_legacy_warning_from_sixteenth_uses_seven_calendar_days(self):
        now = datetime(2026, 10, 22, 18, 0, tzinfo=BERLIN)
        entry = {
            "warns_list": [
                {
                    "id": "warn_legacy",
                    "reason": "Alte Warnung",
                    "date": "16.10.2026 17:30",
                    "active": True,
                    "expires_at": None,
                    "duration_days": None,
                }
            ]
        }

        with patch("webserver.now_de", return_value=now):
            changed = webserver.normalize_warns(entry)
            warn = entry["warns_list"][0]

            self.assertTrue(changed)
            self.assertEqual(warn["duration_days"], 7)
            self.assertEqual(warn["expires_at"], "2026-10-22T17:30:00+02:00")
            self.assertEqual(warn["expiry_migration_source"], "legacy_default_7_calendar_days")
            self.assertTrue(webserver.warning_is_expired(warn, now))
            self.assertEqual(webserver.active_warns(entry), [])

    def test_existing_expiry_is_not_changed(self):
        expires_at = "2026-10-25T12:00:00+02:00"
        entry = {
            "warns_list": [
                {
                    "id": "warn_timed",
                    "date": "16.10.2026 12:00",
                    "duration_days": 7,
                    "expires_at": expires_at,
                    "active": True,
                }
            ]
        }
        with patch("webserver.now_de", return_value=datetime(2026, 10, 22, 18, 0, tzinfo=BERLIN)):
            webserver.normalize_warns(entry)
        warn = entry["warns_list"][0]

        self.assertEqual(warn["expires_at"], expires_at)
        self.assertNotIn("expiry_migration_source", warn)

    def test_existing_duration_without_expiry_uses_full_duration(self):
        now = datetime(2026, 10, 22, 18, 0, tzinfo=BERLIN)
        entry = {
            "warns_list": [
                {
                    "id": "warn_duration",
                    "date": "16.10.2026 17:30",
                    "duration_days": 3,
                    "active": True,
                }
            ]
        }

        with patch("webserver.now_de", return_value=now):
            webserver.normalize_warns(entry)
        warn = entry["warns_list"][0]

        self.assertEqual(warn["expires_at"], "2026-10-19T17:30:00+02:00")
        self.assertTrue(webserver.warning_is_expired(warn, now))

    def test_undated_legacy_warning_gets_one_stable_seven_day_grace_period(self):
        first_seen = datetime(2026, 10, 22, 18, 0, tzinfo=BERLIN)
        entry = {
            "warns_list": [
                {
                    "id": "warn_undated",
                    "date": "N/A",
                    "active": True,
                }
            ]
        }

        with patch("webserver.now_de", return_value=first_seen):
            webserver.normalize_warns(entry)
        first_expiry = entry["warns_list"][0]["expires_at"]
        self.assertEqual(first_expiry, "2026-10-29T18:00:00+01:00")

        later = datetime(2026, 10, 25, 18, 0, tzinfo=BERLIN)
        with patch("webserver.now_de", return_value=later):
            webserver.normalize_warns(entry)
        self.assertEqual(entry["warns_list"][0]["expires_at"], first_expiry)


if __name__ == "__main__":
    unittest.main()
