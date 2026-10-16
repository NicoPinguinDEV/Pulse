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
