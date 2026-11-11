"""Discord command tests for the community suggestion modal."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

    def test_member_can_vote_and_receives_private_tally(self):
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=100),
            user=SimpleNamespace(id=42),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        proposal = discord.app_commands.Choice(name="Besseres Menü", value="dcf_abc12345")
        vote = discord.app_commands.Choice(name="👍 Dafür", value="for")
        with patch("pulse_community_feedback.cast_vote", return_value={
            "vote": "for", "for": 4, "against": 2,
        }):
            asyncio.run(PulseSuite.vorschlag_abstimmen.callback(
                None, interaction, proposal, vote
            ))
        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertIn("4", kwargs["embed"].description)
        self.assertIn("2", kwargs["embed"].description)

    def test_open_poll_list_is_visible_to_the_server(self):
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=100),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        rows = [{
            "id": "dcf_abc12345",
            "title": "Besseres Menü",
            "category": "gameplay",
            "content": "Ein Vorschlag mit ausreichend langer Beschreibung.",
            "votes_for": 4,
            "votes_against": 2,
        }]
        with patch("pulse_community_feedback.list_voting_feedback", return_value=rows):
            asyncio.run(PulseSuite.vorschlaege.callback(None, interaction))
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertFalse(kwargs["ephemeral"])
        self.assertIn("Besseres Menü", kwargs["embed"].fields[0].name)
        self.assertIn("Dafür", kwargs["embed"].fields[0].value)

    def test_feedback_command_opens_modal_with_feedback_type(self):
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=100),
            response=SimpleNamespace(send_modal=AsyncMock()),
        )
        choice = discord.app_commands.Choice(name="Support", value="support")
        asyncio.run(PulseSuite.community_feedback.callback(None, interaction, choice, False))
        modal = interaction.response.send_modal.await_args.args[0]
        self.assertEqual(modal.kind, "feedback")
        self.assertFalse(modal.anonymous)
        self.assertEqual(modal.title, "BOCHUM RP · Community-Feedback")

    def test_vote_autocomplete_is_guild_scoped(self):
        interaction = SimpleNamespace(guild=SimpleNamespace(id=100))
        rows = [{
            "id": "dcf_abc12345",
            "title": "Besseres Menü",
            "category": "gameplay",
            "votes_for": 4,
            "votes_against": 2,
        }]
        with patch("pulse_community_feedback.list_voting_feedback", return_value=rows) as listing:
            choices = asyncio.run(
                PulseSuite.vorschlag_vote_autocomplete(None, interaction, "menü")
            )
        listing.assert_called_once_with(100, limit=25)
        self.assertEqual([item.value for item in choices], ["dcf_abc12345"])

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
