"""K-08: every slash-command error gets an answer (response or followup)."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord
from discord import app_commands

import pulse_command_errors as errors


class FakeResponse:
    def __init__(self, done):
        self.done, self.sent = done, []

    def is_done(self):
        return self.done

    async def send_message(self, text, ephemeral=False):
        self.sent.append(("response", text, ephemeral))


class FakeInteraction:
    def __init__(self, done=False):
        self.response = FakeResponse(done)
        self.command = SimpleNamespace(qualified_name="warn")
        self.followup = SimpleNamespace(send=self._followup)

    async def _followup(self, text, ephemeral=False):
        self.response.sent.append(("followup", text, ephemeral))


def run(error, done=False):
    interaction = FakeInteraction(done)
    with patch("builtins.print"), patch("traceback.print_exception"):
        asyncio.run(errors.handle_app_command_error(interaction, error))
    return interaction.response.sent


class ErrorHandlerTests(unittest.TestCase):
    def test_missing_permissions(self):
        sent = run(app_commands.MissingPermissions(["ban_members"]))
        self.assertEqual(sent[0][0], "response")
        self.assertTrue(sent[0][2])
        self.assertIn("ban members", sent[0][1])

    def test_cooldown_and_check_failure_and_dm(self):
        self.assertIn("warte", run(app_commands.CommandOnCooldown(app_commands.Cooldown(1, 30), 12.4))[0][1])
        self.assertIn("nicht verwenden", run(app_commands.CheckFailure())[0][1])
        self.assertIn("nur auf dem Server", run(app_commands.NoPrivateMessage())[0][1])

    def test_transformer_error(self):
        error = app_commands.TransformerError("abc", discord.AppCommandOptionType.integer, app_commands.Transformer())
        self.assertIn("Ungültige Eingabe", run(error)[0][1])

    def test_unexpected_error_gets_id_and_followup_when_deferred(self):
        cause = RuntimeError("boom")
        error = app_commands.CommandInvokeError(SimpleNamespace(qualified_name="warn", name="warn"), cause)
        sent = run(error, done=True)
        self.assertEqual(sent[0][0], "followup")
        self.assertIn("Fehler-ID", sent[0][1])
        self.assertNotIn("boom", sent[0][1])  # no raw exception text for users

    def test_tree_routes_errors_to_handler(self):
        self.assertTrue(issubclass(errors.PulseCommandTree, app_commands.CommandTree))
        import main
        self.assertIs(main.CustomBot.TREE_CLASS, errors.PulseCommandTree)


if __name__ == "__main__":
    unittest.main()
