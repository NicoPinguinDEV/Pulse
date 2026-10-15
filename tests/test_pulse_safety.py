"""Behavior-level tests for critical Pulse safety rules (stdlib only)."""
from __future__ import annotations

import unittest

from pulse_safety import (
    approved_action_operation,
    memory_guard_level,
    rank_role_ids_after_change,
)


class MemoryGuardTests(unittest.TestCase):
    def test_escalation_thresholds(self):
        self.assertEqual(memory_guard_level(179.9), 0)
        self.assertEqual(memory_guard_level(180), 1)
        self.assertEqual(memory_guard_level(210), 2)
        self.assertEqual(memory_guard_level(230), 3)

    def test_warning_requires_recovery_below_160(self):
        self.assertEqual(memory_guard_level(170, current_level=1), 1)
        self.assertEqual(memory_guard_level(159.9, current_level=1), 0)

    def test_critical_steps_down_without_flapping(self):
        self.assertEqual(memory_guard_level(225, current_level=3), 3)
        self.assertEqual(memory_guard_level(219.9, current_level=3), 2)
        self.assertEqual(memory_guard_level(205, current_level=2), 2)
        self.assertEqual(memory_guard_level(199.9, current_level=2), 1)
        self.assertEqual(memory_guard_level(170, current_level=2), 1)
        self.assertEqual(memory_guard_level(150, current_level=2), 0)

    def test_missing_measurement_keeps_previous_state(self):
        self.assertEqual(memory_guard_level(None, current_level=2), 2)


class TeamActionTests(unittest.TestCase):
    def test_rank_change_keeps_non_team_roles_and_only_one_rank(self):
        self.assertEqual(
            rank_role_ids_after_change(
                current_role_ids=[1, 10, 11, 20, 30],
                team_role_ids=[10, 11, 12],
                target_role_id=12,
            ),
            (1, 20, 30, 12),
        )

    def test_server_kick_and_team_exit_are_not_interchangeable(self):
        self.assertEqual(approved_action_operation("server-exit"), "kick_member")
        self.assertEqual(approved_action_operation("team-exit"), "remove_team_roles")
        self.assertIsNone(approved_action_operation("unknown"))


if __name__ == "__main__":
    unittest.main()
