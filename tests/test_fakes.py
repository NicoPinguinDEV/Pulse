"""A-08: the shared fakes work with real Pulse code paths."""
import asyncio
import unittest
from unittest.mock import patch

import pulse_command_errors
import pulse_permissions
from discord import app_commands
from tests.fakes import FakeChannel, FakeGuild, FakeInteraction, FakeMember, FakeRole


class FakeTests(unittest.TestCase):
    def test_permission_model_with_fakes(self):
        team = FakeRole(10, "Supporter")
        member = FakeMember(42, roles=[team])
        guild = FakeGuild(members=[member], roles=[team])
        perms, found = pulse_permissions.compute(guild, 42, {"team_role_ids": [10]})
        self.assertIs(found, member)
        self.assertTrue(perms["can_view_dashboard"])

    def test_interaction_records_answers(self):
        interaction = FakeInteraction(FakeMember(1), FakeGuild())
        with patch("builtins.print"), patch("traceback.print_exception"):
            asyncio.run(pulse_command_errors.handle_app_command_error(interaction, app_commands.CheckFailure()))
        self.assertEqual(interaction.log[0][0], "response")
        self.assertTrue(interaction.log[0][2])

    def test_channel_send_and_fetch(self):
        channel = FakeChannel()
        message = asyncio.run(channel.send("hallo"))
        self.assertIs(asyncio.run(channel.fetch_message(message.id)), message)


if __name__ == "__main__":
    unittest.main()
