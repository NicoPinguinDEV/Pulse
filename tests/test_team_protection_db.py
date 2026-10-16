"""Integration tests for the persistent warning-abuse guard."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db


class TeamProtectionDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.temp_dir.name) / "pulse-test.db"))
        self.db_patch.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _trigger_multi_target_burst(self, actor_id=900, start=10000):
        results = []
        for offset, target_id in enumerate((11, 12, 13, 14)):
            results.append(
                pulse_db.check_team_warn_safety(
                    guild_id=1,
                    actor_id=actor_id,
                    target_id=target_id,
                    now_ts=start + offset,
                )
            )
        return results

    def test_four_warnings_to_three_or_more_targets_trigger_lockout(self):
        results = self._trigger_multi_target_burst()
        self.assertTrue(all(item["allowed"] for item in results[:3]))
        self.assertFalse(results[3]["allowed"])
        self.assertEqual(results[3]["action"], "lockout")
        state = pulse_db.get_team_safety_state(1, 900, now_ts=10003)
        self.assertTrue(state["locked"])
        self.assertEqual(state["strike_count"], 1)

    def test_repeat_attempts_during_lockout_escalate_to_team_kick(self):
        self._trigger_multi_target_burst()
        first = pulse_db.check_team_warn_safety(1, 900, 15, now_ts=10004)
        second = pulse_db.check_team_warn_safety(1, 900, 16, now_ts=10005)
        self.assertEqual(first["action"], "blocked")
        self.assertEqual(second["action"], "team_kick")

    def test_second_burst_within_24_hours_escalates_to_team_kick(self):
        first = self._trigger_multi_target_burst(start=10000)
        self.assertEqual(first[-1]["action"], "lockout")
        start_again = 10000 + 1804
        results = self._trigger_multi_target_burst(start=start_again)
        self.assertTrue(all(item["allowed"] for item in results[:3]))
        self.assertEqual(results[-1]["action"], "team_kick")


if __name__ == "__main__":
    unittest.main()
