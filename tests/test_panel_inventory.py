"""Tests for the panel inventory's non-guessing status and safe link formatting."""
import unittest

from pulse_panel_inventory import discord_message_url, panel_status


class PanelInventoryHelperTests(unittest.TestCase):
    def test_statuses_do_not_claim_missing_or_unchecked_panels_are_healthy(self):
        self.assertEqual(panel_status(False, "123", True), "Kanal nicht gefunden")
        self.assertEqual(panel_status(True, "", None), "Noch nicht veröffentlicht")
        self.assertEqual(panel_status(True, "123", True), "Veröffentlicht")
        self.assertEqual(panel_status(True, "123", False), "Discord-Nachricht fehlt")
        self.assertEqual(panel_status(True, "123", None), "Status nicht prüfbar")

    def test_discord_link_requires_valid_positive_ids(self):
        self.assertEqual(discord_message_url(1, 2, 3), "https://discord.com/channels/1/2/3")
        self.assertEqual(discord_message_url(1, "x", 3), "")
        self.assertEqual(discord_message_url(0, 2, 3), "")


if __name__ == "__main__":
    unittest.main()
