"""Unit tests for automatic role nickname prefix formatting."""
from __future__ import annotations

import unittest

from pulse_nickname_utils import (
    MAX_DISCORD_NICKNAME_LENGTH,
    automatic_role_prefix,
    format_prefixed_nickname,
    normalize_role_prefix,
    select_highest_role_prefix,
    strip_known_prefix,
    strip_legacy_rank_prefix,
)


class RoleNicknamePrefixTests(unittest.TestCase):
    def test_normalizes_prefix_and_formats_requested_name(self):
        self.assertEqual(normalize_role_prefix("  Bürger ✗  "), "Bürger ✗")
        self.assertEqual(format_prefixed_nickname("Bürger ✗", "Nico"), "Bürger ✗ Nico")
        self.assertEqual(format_prefixed_nickname("VIP ✗", "Mitglied"), "VIP ✗ Mitglied")

    def test_nickname_respects_discord_length_limit(self):
        result = format_prefixed_nickname("Bürger ✗", "N" * 100)
        self.assertEqual(len(result), MAX_DISCORD_NICKNAME_LENGTH)
        self.assertTrue(result.startswith("Bürger ✗ "))

    def test_automatic_role_prefix_cleans_server_decorations(self):
        self.assertEqual(automatic_role_prefix("》BORP メ Owner"), "Owner ✗")
        self.assertEqual(automatic_role_prefix("》 𝐁𝐎 ║ 𝐁ü𝐫𝐠𝐞𝐫"), "Bürger ✗")
        self.assertEqual(automatic_role_prefix("》 VIP"), "VIP ✗")

    def test_bochum_team_roles_use_compact_abbreviations(self):
        cases = [
            ("》𝐅𝐨𝐮𝐧𝐝𝐞𝐫", "Founder"),
            ("》BORP メ Inhaber", "Inh."),
            ("》BORP メ Stv.Inhaber", "Stv.Inh."),
            ("》BORP ✘ Rechte Hand", "Rechte Hand"),
            ("》BORP メ Owner", "Owner"),
            ("》BORP メ Co.Owner", "Co.Owner"),
            ("〘⭐〙𝐃𝐂 𝐑𝐄𝐂𝐇𝐓𝐄", "DC"),
            ("》BORP メ Dev-Leitung", "L.Dev"),
            ("》BORP メ Developer", "Dev"),
            ("》BORP メ Leitende Projektleitung", "L.PL"),
            ("》BORP メ Projektleitung", "PL"),
            ("》BORP メ Stv.Projektleitung", "Stv.PL"),
            ("》BORP メ Leitende Projektkoordination", "L.PK"),
            ("》BORP メ Projektkoordination", "PK"),
            ("》BORP メ Stv.Projektkoordination", "Stv.PK"),
            ("》BORP メ Leitender Direktor", "L.Dir"),
            ("》BORP メ Direktor", "Dir"),
            ("》BORP メ Stv.Direktor", "Stv.Dir"),
            ("》BORP メ Leitende Serverleitung", "L.SL"),
            ("》BORP メ Serverleitung", "SL"),
            ("》BORP メ Stv.Serverleitung", "Stv.SL"),
            ("》BORP メ Leitender Manager", "L.Mgmt"),
            ("》BORP メ Management", "Mgmt"),
            ("》BORP メ Stv.Management", "Stv.Mgmt"),
            ("》BORP メ Leitende Teamleitung", "L.TL"),
            ("》BORP メ Teamleitung", "TL"),
            ("》BORP メ Stv.Teamleitung", "Stv.TL"),
            ("》BORP メ Leitende Teamaufsicht", "L.TA"),
            ("》BORP メ Teamaufsicht", "TA"),
            ("》BORP メ Stv Teamaufsicht", "Stv.TA"),
            ("》BORP メ Leitender-Supervisor", "L.Sup"),
            ("》BORP メ Supervisor", "Sup"),
            ("》BORP メ Stv.Supervisor", "Stv.Sup"),
            ("》BORP メ Ausbilder-Leitung", "L.AB"),
            ("》BORP メ Ausbilder", "AB"),
            ("》BORP メ Highteamanwärter", "HTA"),
            ("》BORP メ Community-Management", "CM"),
            ("》BORP メ Stv.Community-Management", "Stv.CM"),
            ("》BORP メ Fraktionsverwaltung", "FV"),
            ("》BORP メ Stv.Fraktionsverwaltung", "Stv.FV"),
            ("》BORP メ Eventleitung", "EL"),
            ("》BORP メ Admin-Leitung", "L.Admin"),
            ("》BORP メ Sr.Admin", "Sr.Admin"),
            ("》BORP メ Admin", "Admin"),
            ("》BORP メ Jr.Admin", "Jr.Admin"),
            ("》BORP メ Mod-Leitung", "L.Mod"),
            ("》BORP メ Sr.Moderator", "Sr.Mod"),
            ("》BORP メ Moderator", "Mod"),
            ("》BORP メ Jr.Moderator", "Jr.Mod"),
            ("》BORP メ Sup-Leitung", "L.Sup"),
            ("》BORP メ Sr.Supporter", "Sr.Sup"),
            ("》BORP メ Supporter", "Sup"),
            ("》BORP メ Test Supporter", "T.Sup"),
            ("》BORP メ Aushilfe", "Aushilfe"),
            ("》𝐁𝐎𝐑𝐏 メ 𝐁𝐚𝐮𝐭𝐞𝐚𝐦𝐥𝐞𝐫 𝐋𝐞𝐢𝐭𝐮𝐧𝐠", "L.Bau"),
            ("》𝐁𝐎𝐑𝐏 メ 𝐒𝐭𝐯 𝐁𝐚𝐮𝐭𝐞𝐚𝐦𝐥𝐞𝐫 𝐋𝐞𝐢𝐭𝐮𝐧𝐠", "Stv.L.Bau"),
            ("》𝐁𝐎𝐑𝐏 メ 𝐒𝐞𝐧𝐢𝐨𝐫 𝐁𝐚𝐮𝐭𝐞𝐚𝐦𝐥𝐞𝐫", "Sr.Bau"),
            ("》𝐁𝐎𝐑𝐏 メ 𝐁𝐚𝐮𝐭𝐞𝐚𝐦𝐥𝐞𝐫", "Bau"),
            ("》𝐁𝐎𝐑𝐏 メ 𝐓𝐞𝐬𝐭 𝐁𝐚𝐮𝐭𝐞𝐚𝐦𝐥𝐞𝐫", "T.Bau"),
            ("》𝐁𝐎 ║ 𝐓𝐞𝐚𝐦𝐥𝐞𝐫", "Team"),
        ]
        for role_name, expected_label in cases:
            with self.subTest(role=role_name):
                self.assertEqual(automatic_role_prefix(role_name), f"{expected_label} ✗")

    def test_old_prefix_settings_migrate_to_canonical_labels_but_custom_overrides_survive(self):
        cases = [
            ("》𝐅𝐨𝐮𝐧𝐝𝐞𝐫", "FND ✗", "Founder ✗"),
            ("》BORP メ Dev-Leitung", "DevL ✗", "L.Dev ✗"),
            ("》BORP メ Stv.Management", "Stv.Management ✗", "Stv.Mgmt ✗"),
            ("》BORP メ Ausbilder", "Ausb ✗", "AB ✗"),
            ("》BORP メ Sup-Leitung", "SupL ✗", "L.Sup ✗"),
            ("》𝐁𝐎𝐑𝐏 メ 𝐒𝐭𝐯 𝐁𝐚𝐮𝐭𝐞𝐚𝐦𝐥𝐞𝐫 𝐋𝐞𝐢𝐭𝐮𝐧𝐠", "Stv.BauL ✗", "Stv.L.Bau ✗"),
        ]
        for role_name, old_value, expected in cases:
            with self.subTest(role=role_name):
                self.assertEqual(
                    select_highest_role_prefix([(101, 10, role_name, False)], {"101": old_value}),
                    (101, expected),
                )

        self.assertEqual(
            select_highest_role_prefix([(102, 10, "》BORP メ Owner", False)], {"102": "Boss ✗"}),
            (102, "Boss ✗"),
        )

    def test_section_divider_roles_do_not_override_actual_team_rank(self):
        roles = [
            (100, 0, "@everyone", True),
            (101, 10, "》BORP メ Projektleitung", False),
            (102, 100, "「━━━━ Projektleitung-Ebene ━━━━」", False),
        ]
        self.assertEqual(
            select_highest_role_prefix(roles, {}),
            (101, "PL ✗"),
        )

    def test_highest_discord_role_wins_even_when_only_a_lower_role_is_configured(self):
        roles = [
            (100, 0, "@everyone", True),
            (101, 5, "》 𝐁𝐎 ║ Bürger", False),
            (102, 15, "》BORP メ Moderator", False),
            (103, 30, "》BORP メ Owner", False),
        ]
        self.assertEqual(
            select_highest_role_prefix(roles, {"101": "Bürger ✗", "102": "Moderator ✗"}),
            (103, "Owner ✗"),
        )
        self.assertEqual(
            select_highest_role_prefix(roles, {"103": "Owner-Team ✗"}),
            (103, "Owner-Team ✗"),
        )
        self.assertIsNone(select_highest_role_prefix(roles[:1], {}))

    def test_ex_teamler_role_overrides_higher_remaining_roles(self):
        roles = [
            (100, 0, "@everyone", True),
            (101, 5, "》 𝐁𝐎 ║ Bürger", False),
            (1489912887949394082, 10, "》⛔ ‖ 𝐄𝐗 𝐓𝐄𝐀𝐌𝐋𝐄𝐑", False),
            (103, 100, "》BORP メ Owner", False),
        ]
        self.assertEqual(
            select_highest_role_prefix(roles, {}),
            (1489912887949394082, "𝐄𝐗 𝐓𝐄𝐀𝐌𝐋𝐄𝐑 ✗"),
        )

    def test_legacy_rank_tags_are_removed_from_existing_nicknames(self):
        self.assertEqual(strip_legacy_rank_prefix("Sr.-Sup メ Dawo"), "Dawo")
        self.assertEqual(strip_legacy_rank_prefix("Owner メ Phillip | Abgem"), "Phillip | Abgem")
        self.assertEqual(strip_legacy_rank_prefix("TeamleitungメMrJoela | A"), "MrJoela | A")
        self.assertEqual(strip_legacy_rank_prefix("LTA ✗ Max"), "Max")
        self.assertEqual(strip_legacy_rank_prefix("DK メ Lomlom"), "Lomlom")
        self.assertEqual(strip_legacy_rank_prefix("MA メ Zaziki"), "Zaziki")
        self.assertEqual(strip_legacy_rank_prefix("Jules ist der Beste"), "Jules ist der Beste")

    def test_invalid_prefixes_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_role_prefix("")
        with self.assertRaises(ValueError):
            normalize_role_prefix("x" * 25)
        with self.assertRaises(ValueError):
            normalize_role_prefix("VIP\nModerator")

    def test_known_prefix_is_removed_only_at_name_start(self):
        self.assertEqual(
            strip_known_prefix("Bürger ✗ Nico", ["Bürger ✗", "VIP ✗"]),
            "Nico",
        )
        self.assertIsNone(strip_known_prefix("Mein VIP ✗ Name", ["VIP ✗"]))
        self.assertEqual(
            strip_known_prefix("VIP ✗ Bürger ✗ Nico", ["VIP ✗", "Bürger ✗"]),
            "Bürger ✗ Nico",
        )


if __name__ == "__main__":
    unittest.main()
