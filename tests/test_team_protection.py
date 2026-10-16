"""Unit tests for moderator-abuse protection policy."""
from __future__ import annotations

import unittest

from pulse_team_protection import detect_warn_burst, lockout_action, next_incident_action


class TeamProtectionPolicyTests(unittest.TestCase):
    def test_two_warnings_to_same_target_are_not_yet_flagged(self):
        result = detect_warn_burst(
            [
                {"action": "warn_attempt", "target_id": "42", "created_at": 95},
            ],
            target_id=42,
            now_ts=100,
        )
        self.assertFalse(result["triggered"])
        self.assertEqual(result["same_target_count"], 2)

    def test_third_warning_to_same_target_is_flagged(self):
        result = detect_warn_burst(
            [
                {"action": "warn_attempt", "target_id": "42", "created_at": 95},
                {"action": "warn_attempt", "target_id": "42", "created_at": 96},
            ],
            target_id=42,
            now_ts=100,
        )
        self.assertTrue(result["triggered"])
        self.assertEqual(result["code"], "same_target_burst")

    def test_fourth_warning_across_three_targets_is_flagged(self):
        result = detect_warn_burst(
            [
                {"action": "warn_attempt", "target_id": "1", "created_at": 95},
                {"action": "warn_attempt", "target_id": "2", "created_at": 96},
                {"action": "warn_attempt", "target_id": "3", "created_at": 97},
            ],
            target_id=4,
            now_ts=100,
        )
        self.assertTrue(result["triggered"])
        self.assertEqual(result["code"], "multi_target_burst")
        self.assertEqual(result["distinct_targets_5m"], 4)

    def test_old_and_non_warning_events_do_not_trigger(self):
        result = detect_warn_burst(
            [
                {"action": "warn_attempt", "target_id": "1", "created_at": 1},
                {"action": "removed_warn", "target_id": "2", "created_at": 99},
            ],
            target_id=3,
            now_ts=1000,
        )
        self.assertFalse(result["triggered"])

    def test_second_incident_within_24_hours_causes_team_kick(self):
        self.assertEqual(next_incident_action(1, 100, 200), "team_kick")
        self.assertEqual(next_incident_action(1, 100, 100 + 86401), "lockout")
        self.assertEqual(next_incident_action(0, 0, 100), "lockout")

    def test_repeated_lockout_attempts_escalate(self):
        self.assertEqual(lockout_action(1), "blocked")
        self.assertEqual(lockout_action(2), "team_kick")


if __name__ == "__main__":
    unittest.main()
