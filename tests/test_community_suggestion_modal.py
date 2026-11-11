"""Discord command tests for the community suggestion modal."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from cogs.pulse_suite import CommunitySuggestionModal, PulseSuite


class CommunitySuggestionModalTests(unittest.TestCase):
    def test_command_opens_modal_with_selected_category_and_anonymity(self):
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=100),
            response=SimpleNamespace(send_modal=AsyncMock()),
        )
        choice = discord.app_commands.Choice(name="Gameplay / Roleplay", value="gameplay")
        asyncio.run(
            PulseSuite.vorschlag.callback(
                None, interaction, choice, True
            )
        )
        interaction.response.send_modal.assert_awaited_once()
        modal = interaction.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, CommunitySuggestionModal)
        self.assertEqual(modal.category, "gameplay")
        self.assertTrue(modal.anonymous)
        self.assertEqual(modal.suggestion_title.max_length, 120)
        self.assertEqual(modal.suggestion_content.max_length, 3000)

    def test_command_rejects_direct_messages(self):
        interaction = SimpleNamespace(
            guild=None,
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        asyncio.run(
            PulseSuite.vorschlag.callback(
                None,
                interaction,
                discord.app_commands.Choice(name="Allgemein", value="general"),
                False,
            )
        )
        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])


if __name__ == "__main__":
    unittest.main()
