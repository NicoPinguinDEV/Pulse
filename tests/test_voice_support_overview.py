import unittest

from pulse_cockpit_utils import voice_support_overview


class VoiceSupportOverviewTests(unittest.TestCase):
    def test_waiting_and_assigned_cases_are_separated(self):
        cases = {
            "waiting-1": {
                "case_id": "A1", "user_id": "11", "created_at": "2026-11-10T10:00:00",
                "waiting_in_shared_room": True, "assigned_staff_id": "",
            },
            "waiting-2": {
                "case_id": "A2", "user_id": "12", "created_at": "2026-11-10T10:05:00",
                "waiting_in_shared_room": True, "assigned_staff_id": "0",
            },
            "active-1": {
                "case_id": "A3", "user_id": "13", "created_at": "2026-11-10T09:00:00",
                "waiting_in_shared_room": False, "assigned_staff_id": "99",
                "assigned_staff_name": "Supporter", "support_started_at": "2026-11-10T09:01:00",
            },
            "closed": {
                "case_id": "A4", "user_id": "14", "created_at": "2026-11-10T08:00:00",
                "waiting_in_shared_room": False, "assigned_staff_id": "98", "closed_at": "2026-11-10T08:30:00",
            },
        }
        result = voice_support_overview(cases)
        self.assertEqual([item["case_id"] for item in result["waiting"]], ["A1", "A2"])
        self.assertEqual([item["case_id"] for item in result["active"]], ["A3"])

    def test_malformed_or_missing_case_collection_is_safe(self):
        self.assertEqual(voice_support_overview(None), {"waiting": [], "active": []})
        self.assertEqual(voice_support_overview(["bad", None]), {"waiting": [], "active": []})


if __name__ == "__main__":
    unittest.main()
