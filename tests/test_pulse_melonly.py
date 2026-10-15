import unittest

from pulse_melonly import can_manage_melonly_type, rank_roblox_candidates, roblox_similarity_score


class RobloxSuggestionTests(unittest.TestCase):
    def test_exact_username_is_first(self):
        users = [
            {"id": "2", "name": "NICODOLA2"},
            {"id": "1", "name": "NICODOLA1"},
            {"id": "3", "name": "NICOBOCHUM"},
        ]
        ranked = rank_roblox_candidates("@nicodola1", users)
        self.assertEqual(ranked[0]["name"], "NICODOLA1")
        self.assertEqual(ranked[0]["match_score"], 100)

    def test_typo_suggests_closest_username_first(self):
        users = [
            {"id": "10", "name": "NICODOLA1"},
            {"id": "11", "name": "NICOBOCHUM"},
            {"id": "12", "name": "BOCHUMRP"},
        ]
        ranked = rank_roblox_candidates("nicodl", users)
        self.assertEqual(ranked[0]["name"], "NICODOLA1")
        self.assertGreater(roblox_similarity_score("nicodl", "NICODOLA1"), roblox_similarity_score("nicodl", "BOCHUMRP"))

    def test_duplicate_names_prefer_candidate_with_numeric_id(self):
        users = [
            {"id": "N/A", "name": "PlayerOne"},
            {"id": "12345", "name": "playerone", "displayName": "Player One"},
        ]
        ranked = rank_roblox_candidates("playerone", users)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["id"], "12345")
        self.assertEqual(ranked[0]["displayName"], "Player One")

    def test_invalid_query_returns_no_candidates(self):
        self.assertEqual(rank_roblox_candidates("@@", [{"name": "PlayerOne", "id": "1"}]), [])


class MelonlyPermissionTests(unittest.TestCase):
    def test_warn_role_can_add_warns_but_not_kicks_or_bans(self):
        self.assertTrue(can_manage_melonly_type("Warn", can_warn=True))
        self.assertFalse(can_manage_melonly_type("Kick", can_warn=True))
        self.assertFalse(can_manage_melonly_type("Ban", can_warn=True))
        self.assertFalse(can_manage_melonly_type("Ban BOLO", can_warn=True))
        self.assertFalse(can_manage_melonly_type("Notiz", can_warn=True))

    def test_kick_permission_is_separate_from_warn_and_ban(self):
        self.assertTrue(can_manage_melonly_type("Kick", can_moderate_kicks=True))
        self.assertFalse(can_manage_melonly_type("Warn", can_moderate_kicks=True))
        self.assertFalse(can_manage_melonly_type("Ban", can_moderate_kicks=True))

    def test_bans_and_bolo_need_individual_permissions(self):
        self.assertTrue(can_manage_melonly_type("Ban", can_moderate_bans=True))
        self.assertFalse(can_manage_melonly_type("Ban BOLO", can_moderate_bans=True))
        self.assertTrue(can_manage_melonly_type("Ban BOLO", can_moderate_bolo=True))
        self.assertFalse(can_manage_melonly_type("Ban", can_moderate_bolo=True))

    def test_notes_role_can_manage_notes_only(self):
        self.assertTrue(can_manage_melonly_type("Notiz", can_add_notes=True))
        self.assertFalse(can_manage_melonly_type("Ban", can_add_notes=True))

    def test_manager_can_manage_all_supported_entries(self):
        for log_type in ("Warn", "Kick", "Ban", "Ban BOLO", "Notiz"):
            self.assertTrue(can_manage_melonly_type(log_type, manager=True))
        self.assertFalse(can_manage_melonly_type("Unknown", manager=True))


if __name__ == "__main__":
    unittest.main()
