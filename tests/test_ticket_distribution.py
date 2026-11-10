import unittest

from pulse_ticket_distribution import choose_assignee, distribution_settings


class DistributionSettingsTests(unittest.TestCase):
    def test_disabled_by_default_and_capacity_is_bounded(self):
        self.assertEqual(
            distribution_settings(None),
            {"enabled": False, "max_open_per_staff": 5},
        )
        self.assertEqual(
            distribution_settings({"enabled": "yes", "max_open_per_staff": 999}),
            {"enabled": True, "max_open_per_staff": 50},
        )
        self.assertEqual(
            distribution_settings({"enabled": "off", "max_open_per_staff": 0}),
            {"enabled": False, "max_open_per_staff": 1},
        )
        self.assertEqual(
            distribution_settings({"enabled": True, "max_open_per_staff": "bad"}),
            {"enabled": True, "max_open_per_staff": 5},
        )


class ChooseAssigneeTests(unittest.TestCase):
    def test_selects_lowest_priority_weighted_workload(self):
        candidates = [
            {"id": 30, "name": "Lena"},
            {"id": 20, "name": "Max"},
            {"id": 10, "name": "Nico"},
        ]
        tickets = [
            {"guild_id": "1", "status": "open", "claimed_by_id": "30", "priority": "urgent"},
            {"guild_id": "1", "status": "open", "claimed_by_id": "20", "priority": "normal"},
            {"guild_id": "1", "status": "open", "claimed_by_id": "20", "priority": "low"},
            {"guild_id": "1", "status": "closed", "claimed_by_id": "10", "priority": "urgent"},
            {"guild_id": "2", "status": "open", "claimed_by_id": "10", "priority": "urgent"},
        ]
        result = choose_assignee(candidates, tickets, "1", max_open_per_staff=4)
        self.assertEqual(result["id"], "10")
        self.assertEqual(result["open_ticket_count"], 0)
        self.assertEqual(result["workload_points"], 0)

    def test_ties_are_deterministic_and_use_member_id(self):
        result = choose_assignee(
            [{"id": 9, "name": "Neun"}, {"id": 3, "name": "Drei"}],
            [],
            1,
            max_open_per_staff=2,
        )
        self.assertEqual(result["id"], "3")
        self.assertEqual(result["name"], "Drei")

    def test_capacity_limit_leaves_ticket_unassigned(self):
        result = choose_assignee(
            [{"id": 9, "name": "Neun"}],
            [
                {"guild_id": "1", "status": "open", "claimed_by_id": "9", "priority": "normal"},
                {"guild_id": "1", "status": "in_progress", "claimed_by_id": "9", "priority": "high"},
            ],
            1,
            max_open_per_staff=2,
        )
        self.assertIsNone(result)

    def test_bots_invalid_members_and_blank_guild_are_ignored(self):
        self.assertIsNone(
            choose_assignee([{"id": 1, "bot": True}, {"id": "invalid"}], [], "1")
        )
        self.assertIsNone(choose_assignee([{"id": 1}], [], ""))


if __name__ == "__main__":
    unittest.main()
