import unittest

from pulse_role_utils import group_roles, is_separator, rank_change, separator_label


class RoleGroupingTests(unittest.TestCase):
    def test_separator_detection(self):
        self.assertTrue(is_separator("「━━━━ Supporter-Ebene ━━━━」"))
        self.assertTrue(is_separator("「━━━━Teamaufsichts-Ebene ━━━━」"))
        self.assertFalse(is_separator("》BORP メ Supporter"))
        self.assertEqual(separator_label("「━━━━ Supporter-Ebene ━━━━」"), "Supporter-Ebene")

    def test_roles_belong_to_the_divider_below_them(self):
        roles = [
            {"name": "》BORP メ Sup-Leitung", "position": 104},
            {"name": "》BORP メ Supporter", "position": 102},
            {"name": "「━━━━ Supporter-Ebene ━━━━」", "position": 100},
            {"name": "》BORP メ Aushilfe", "position": 99},
            {"name": "「━━━━ Low-Team ━━━━」", "position": 98},
            {"name": "18+", "position": 14},
        ]
        groups = group_roles(roles)
        self.assertEqual([g["label"] for g in groups], ["Supporter-Ebene", "Low-Team", "Weitere Rollen"])
        self.assertEqual([r["name"] for r in groups[0]["roles"]], ["》BORP メ Sup-Leitung", "》BORP メ Supporter"])
        self.assertEqual(groups[0]["separator"]["position"], 100)
        self.assertIsNone(groups[2]["separator"])

    def test_rank_change_sets_exactly_one_rank(self):
        add, remove = rank_change({1, 2, 50}, [1, 2, 3], 3)
        self.assertEqual(add, {3})
        self.assertEqual(remove, {1, 2})
        add, remove = rank_change({3, 50}, [1, 2, 3], 3)
        self.assertEqual((add, remove), (set(), set()))


if __name__ == "__main__":
    unittest.main()
