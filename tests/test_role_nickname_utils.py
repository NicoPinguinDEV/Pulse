"""Unit tests for automatic role nickname prefix formatting."""
from __future__ import annotations

import unittest

from pulse_nickname_utils import (
    MAX_DISCORD_NICKNAME_LENGTH,
    format_prefixed_nickname,
    normalize_role_prefix,
    strip_known_prefix,
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
