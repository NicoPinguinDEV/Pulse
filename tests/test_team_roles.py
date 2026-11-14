"""Canonical BOCHUM RP team roles: ordering, aliases and retired-role rejection."""
import unittest

from pulse_team_roles import (
    TEAM_ROLE_GROUPS,
    TEAM_ROLE_ORDER,
    canonical_team_role_name,
    team_role_group,
    team_role_priority,
)


class CurrentTeamRoleTests(unittest.TestCase):
    def test_exact_three_groups_and_24_current_roles(self):
        self.assertEqual(
            [(key, label, len(names)) for key, label, names in TEAM_ROLE_GROUPS],
            [
                ("fuehrungsebene", "Führungsebene", 8),
                ("highteam", "HighTeam", 7),
                ("lowteam", "LowTeam", 9),
            ],
        )
        self.assertEqual(len(TEAM_ROLE_ORDER), 24)
        self.assertEqual(len(set(TEAM_ROLE_ORDER)), 24)

    def test_canonical_names_and_decorated_aliases(self):
        cases = {
            "》𝐅𝐨𝐮𝐧𝐝𝐞𝐫": "Founder",
            "》BORP ✘ Rechte Hand": "Rechte Hand",
            "》BORP メ Co.Owner": "Co.Owner",
            "》BORP メ Stv.-Management": "Stv.Management",
            "》BORP メ Stv.MA": "Stv.Management",
            "》BORP メ Stv.Teamleitung": "Stv.Teamleitung",
            "》BORP メ Sr.Moderator": "Sr.Moderator",
            "》BORP メ Test Supporter": "Test Supporter",
        }
        for raw, expected in cases.items():
            with self.subTest(role=raw):
                self.assertEqual(canonical_team_role_name(raw), expected)

    def test_groups_and_rank_order_are_consistent(self):
        self.assertEqual(team_role_group("》BORP メ Projektleitung"), "fuehrungsebene")
        self.assertEqual(team_role_group("》BORP メ Eventleitung"), "highteam")
        self.assertEqual(team_role_group("》BORP メ Jr.Admin"), "lowteam")
        self.assertLess(team_role_priority("Founder"), team_role_priority("Management"))
        self.assertLess(team_role_priority("Management"), team_role_priority("Teamleitung"))
        self.assertLess(team_role_priority("Teamleitung"), team_role_priority("Sr.Admin"))
        self.assertLess(team_role_priority("Sr.Admin"), team_role_priority("Test Supporter"))

    def test_retired_role_names_are_not_current_aliases(self):
        for raw in (
            "Inhaber", "Stv.Inhaber", "Dev-Leitung", "Projektkoordination",
            "Direktor", "Teamaufsicht", "Supervisor", "Community-Management",
            "Aushilfe", "Bauteamler", "Leitende Projektleitung",
        ):
            with self.subTest(role=raw):
                self.assertIsNone(canonical_team_role_name(raw))
                self.assertIsNone(team_role_group(raw))
                self.assertIsNone(team_role_priority(raw))


if __name__ == "__main__":
    unittest.main()
