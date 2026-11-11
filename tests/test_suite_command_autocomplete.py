"""Tests for role-aware Discord category and priority autocomplete."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cogs.pulse_suite import PulseSuite


class SuiteAutocompleteTests(unittest.TestCase):
    def test_ticket_category_autocomplete_offers_active_configured_categories_only(self):
        definitions = {
            "support": {"name": "Support", "description": "Allgemeine Hilfe", "active": True},
            "custom_technik": {"name": "Technik", "description": "Technische Fragen", "active": True},
            "hidden": {"name": "Intern", "description": "Nicht anbieten", "active": False},
        }
        interaction = SimpleNamespace(guild=SimpleNamespace(id=1))
        with (
            patch("cogs.pulse_suite.allowed", return_value=True),
            patch("cogs.tickets.ticket_category_definitions", return_value=definitions),
        ):
            choices = asyncio.run(
                PulseSuite.pulse_ticket_category_autocomplete(None, interaction, "tech")
            )
        self.assertEqual([choice.value for choice in choices], ["custom_technik"])
        self.assertIn("Technik", choices[0].name)
        self.assertNotIn("hidden", [choice.value for choice in choices])

    def test_ticket_category_autocomplete_requires_ticket_permission(self):
        interaction = SimpleNamespace(guild=SimpleNamespace(id=1))
        with patch("cogs.pulse_suite.allowed", return_value=False):
            choices = asyncio.run(
                PulseSuite.pulse_ticket_category_autocomplete(None, interaction, "")
            )
        self.assertEqual(choices, [])

    def test_member_report_category_autocomplete_uses_the_same_active_definitions(self):
        definitions = {
            "support": {"name": "Support", "description": "Hilfe", "active": True},
            "closed": {"name": "Archiviert", "description": "", "active": False},
        }
        interaction = SimpleNamespace(guild=SimpleNamespace(id=1))
        with patch("cogs.tickets.ticket_category_definitions", return_value=definitions):
            choices = asyncio.run(
                PulseSuite.melden_category_autocomplete(None, interaction, "")
            )
        self.assertEqual([choice.value for choice in choices], ["support"])

    def test_priority_autocomplete_returns_only_matching_supported_values(self):
        interaction = SimpleNamespace(guild=SimpleNamespace(id=1))
        with patch("cogs.pulse_suite.allowed", return_value=True):
            choices = asyncio.run(
                PulseSuite.pulse_ticket_priority_autocomplete(None, interaction, "dring")
            )
        self.assertEqual([(choice.name, choice.value) for choice in choices], [("Dringend", "urgent")])

        with patch("cogs.pulse_suite.allowed", return_value=False):
            denied = asyncio.run(
                PulseSuite.pulse_ticket_priority_autocomplete(None, interaction, "")
            )
        self.assertEqual(denied, [])

    def test_autocomplete_returns_nothing_outside_a_guild(self):
        interaction = SimpleNamespace(guild=None)
        with patch("cogs.pulse_suite.allowed", return_value=True):
            categories = asyncio.run(
                PulseSuite.pulse_ticket_category_autocomplete(None, interaction, "")
            )
            priorities = asyncio.run(
                PulseSuite.melden_priority_autocomplete(None, interaction, "")
            )
        self.assertEqual(categories, [])
        self.assertEqual(priorities, [])


if __name__ == "__main__":
    unittest.main()
