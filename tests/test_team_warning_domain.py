"""BOT-05: shared warning record creation and revocation logic."""
import unittest
from datetime import datetime, timedelta, timezone

from pulse_team_warning import build_warning_record, revoke_warning_record


class TeamWarningDomainTests(unittest.TestCase):
    def test_build_record_preserves_schema_and_calculates_expiry_from_one_timestamp(self):
        issued = datetime(2026, 10, 21, 17, 30, tzinfo=timezone.utc)
        record = build_warning_record(
            warning_id="warn_test001",
            reason=" Nachvollziehbarer Grund ",
            proof="https://example.test/beweis",
            by="Leitung",
            issued_at=issued,
            duration_days=14,
        )
        self.assertEqual(record["id"], "warn_test001")
        self.assertEqual(record["reason"], "Nachvollziehbarer Grund")
        self.assertEqual(record["proof"], "https://example.test/beweis")
        self.assertEqual(record["by"], "Leitung")
        self.assertEqual(record["date"], "21.10.2026 17:30")
        self.assertEqual(record["duration_days"], 14)
        self.assertEqual(record["expires_at"], (issued + timedelta(days=14)).isoformat(timespec="seconds"))
        self.assertTrue(record["active"])
        self.assertIsNone(record["revoked_at"])
        self.assertIsNone(record["revoked_by"])
        self.assertEqual(record["revoked_reason"], "")

    def test_build_record_rejects_invalid_duration_or_empty_reason(self):
        issued = datetime(2026, 10, 21, tzinfo=timezone.utc)
        common = dict(warning_id="warn_test", reason="Grund", proof="", by="Leitung", issued_at=issued)
        with self.assertRaises(ValueError):
            build_warning_record(**common, duration_days=0)
        with self.assertRaises(ValueError):
            build_warning_record(**common, duration_days=366)
        with self.assertRaises(ValueError):
            build_warning_record(**{**common, "reason": "   "}, duration_days=7)

    def test_revoke_is_one_way_and_preserves_history(self):
        entry = {"warns_list": [
            {"id": "warn_active", "active": True, "reason": "Original", "revoked_at": None},
            {"id": "warn_other", "active": True, "reason": "Other", "revoked_at": None},
        ]}
        revoked = revoke_warning_record(
            entry, warning_id="warn_active", revoked_at="2026-10-21T17:30:00+00:00",
            revoked_by="Leitung", reason="Fehlentscheidung",
        )
        self.assertIs(revoked, entry["warns_list"][0])
        self.assertFalse(revoked["active"])
        self.assertEqual(revoked["revoked_reason"], "Fehlentscheidung")
        self.assertEqual(entry["warns_list"][1]["active"], True)
        self.assertIsNone(revoke_warning_record(
            entry, warning_id="warn_active", revoked_at="again",
            revoked_by="Leitung", reason="Zweiter Versuch",
        ))

    def test_revoke_requires_a_reason_and_unknown_ids_do_not_mutate(self):
        entry = {"warns_list": [{"id": "warn_active", "active": True}]}
        with self.assertRaises(ValueError):
            revoke_warning_record(
                entry, warning_id="warn_active", revoked_at="now",
                revoked_by="Leitung", reason=" ",
            )
        self.assertIsNone(revoke_warning_record(
            entry, warning_id="missing", revoked_at="now",
            revoked_by="Leitung", reason="Grund",
        ))
        self.assertTrue(entry["warns_list"][0]["active"])


if __name__ == "__main__":
    unittest.main()
