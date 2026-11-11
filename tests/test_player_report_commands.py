"""Tests for the public player-report slash commands."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.pulse_suite import PulseSuite


class PlayerReportCommandTests(unittest.TestCase):
    def test_player_report_command_rejects_direct_messages(self):
        interaction = SimpleNamespace(
            guild=None,
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        asyncio.run(PulseSuite.spieler_meldung.callback(
            None,
            interaction,
            "RP Player",
            discord.app_commands.Choice(name="Regelverstoß", value="rule_violation"),
            "Eine Beschreibung, die ausreichend lang für die Prüfung ist.",
            discord.app_commands.Choice(name="Normal", value="normal"),
            None,
        ))
        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    def test_my_reports_command_is_private_and_uses_current_user_only(self):
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=100),
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        rows = [{
            "id": "RPT-ABCDEF123456",
            "target_name": "RP Player",
            "status": "in_progress",
            "priority": "normal",
            "description": "Eine nachvollziehbare Meldung zur Prüfung durch die Teamleitung.",
        }]
        with patch("pulse_player_reports.list_user_reports", return_value=rows) as listing:
            asyncio.run(PulseSuite.meine_meldungen.callback(None, interaction))
        listing.assert_called_once_with(100, 42, limit=10)
        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        self.assertIn("RPT-ABCDEF123456", interaction.response.send_message.await_args.kwargs["embed"].fields[0].name)


if __name__ == "__main__":
    unittest.main()
