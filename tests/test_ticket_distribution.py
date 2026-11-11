import unittest

from pulse_ticket_distribution import choose_assignee, distribution_settings, workload_snapshot


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



class WorkloadSnapshotTests(unittest.TestCase):
    def test_counts_only_open_assigned_tickets_from_requested_guild(self):
        result = workload_snapshot(
            [
                {"guild_id": "1", "status": "open", "claimed_by_id": "10", "claimed_by_name": "Alice", "priority": "urgent"},
                {"guild_id": "1", "status": "in_progress", "claimed_by_id": "10", "claimed_by_name": "Alice", "priority": "normal"},
                {"guild_id": "1", "status": "closed", "claimed_by_id": "10", "claimed_by_name": "Alice", "priority": "urgent"},
                {"guild_id": "2", "status": "open", "claimed_by_id": "10", "claimed_by_name": "Alice", "priority": "urgent"},
                {"guild_id": "1", "status": "open", "claimed_by_id": None, "priority": "high"},
            ],
            guild_id="1",
            max_open_per_staff=2,
        )
        self.assertEqual(result["assigned_open_tickets"], 2)
        self.assertEqual(result["staff_count"], 1)
        self.assertEqual(result["overloaded_count"], 1)
        self.assertEqual(result["staff"][0]["name"], "Alice")
        self.assertEqual(result["staff"][0]["workload_points"], 6)
        self.assertTrue(result["staff"][0]["at_capacity"])

    def test_priority_weighting_and_sorting_make_heavier_loads_visible_first(self):
        result = workload_snapshot(
            [
                {"guild_id": "1", "status": "open", "claimed_by_id": "10", "claimed_by_name": "Normal", "priority": "low"},
                {"guild_id": "1", "status": "open", "claimed_by_id": "20", "claimed_by_name": "Urgent", "priority": "urgent"},
            ],
            guild_id="1",
            max_open_per_staff=3,
        )
        self.assertEqual([row["member_id"] for row in result["staff"]], ["20", "10"])
        self.assertEqual(result["staff"][0]["workload_points"], 4)
        self.assertEqual(result["staff"][1]["workload_points"], 1)
        self.assertEqual(result["overloaded_count"], 0)

    def test_capacity_and_output_limits_are_bounded(self):
        result = workload_snapshot(
            [
                {"guild_id": "1", "status": "open", "claimed_by_id": str(index), "claimed_by_name": f"Member {index}"}
                for index in range(1, 12)
            ],
            guild_id="1",
            max_open_per_staff=999,
            limit=3,
        )
        self.assertEqual(result["max_open_per_staff"], 50)
        self.assertEqual(result["staff_count"], 11)
        self.assertEqual(len(result["staff"]), 3)
        self.assertEqual(result["overloaded_count"], 0)

    def test_blank_guild_is_safe_and_returns_empty_summary(self):
        self.assertEqual(
            workload_snapshot([{"guild_id": "1", "status": "open", "claimed_by_id": "10"}], ""),
            {
                "max_open_per_staff": 5,
                "staff_count": 0,
                "assigned_open_tickets": 0,
                "overloaded_count": 0,
                "staff": [],
            },
        )


if __name__ == "__main__":
    unittest.main()
