"""Phase 18.1: /profil exposes only the invoking team member's own summary."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.pulse_pro import PulsePro


class ProfileCommandTests(unittest.TestCase):
    def test_profile_is_self_only_ephemeral_and_uses_existing_data(self):
        interaction = SimpleNamespace(
            guild=object(),
            user=SimpleNamespace(id=42, display_name="Musterteamler"),
            response=SimpleNamespace(send_message=AsyncMock(), is_done=lambda: False),
        )
        shifts = {"active_shifts": {"42": {"status": "online"}}, "history": []}
        with (
            patch("webserver.load_config", return_value={"weekly_goal_hours": 4, "team_role_ids": [9]}),
            patch("webserver.compute_perms", return_value=({"can_view_dashboard": True}, SimpleNamespace(display_name="Musterteamler", roles=[SimpleNamespace(id=9, name="Supporter", position=5)]))),
            patch("webserver.load_shift_state", return_value=shifts),
            patch("webserver.calculate_weekly_seconds", return_value=7200),
            patch("webserver.fmt_duration", return_value="2 h"),
            patch("cogs.pulse_pro.db.list_tasks", return_value=[{"status": "open"}]),
            patch("cogs.pulse_pro.db.list_tickets", return_value=[{"status": "in_progress"}]),
            patch("cogs.pulse_pro.db.attempts", return_value=[
                {"training_id": "training-1", "passed": 1},
                {"training_id": "training-1", "passed": 1},
                {"training_id": "training-2", "passed": 0},
            ]),
        ):
            cog = PulsePro.__new__(PulsePro)
            asyncio.run(PulsePro.profile.callback(cog, interaction))

        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        embed = kwargs["embed"]
        rendered = " ".join(field.value for field in embed.fields)
        self.assertIn("Supporter", rendered)
        self.assertIn("Im Dienst", rendered)
        self.assertIn("1", rendered)  # one open task, one ticket and one distinct passed training
        self.assertIn("/me", rendered)

    def test_profile_denies_members_without_dashboard_access(self):
        interaction = SimpleNamespace(
            guild=object(),
            user=SimpleNamespace(id=42, display_name="Gast"),
            response=SimpleNamespace(send_message=AsyncMock(), is_done=lambda: False),
        )
        with (
            patch("webserver.load_config", return_value={}),
            patch("webserver.compute_perms", return_value=({}, None)),
            patch("cogs.pulse_pro.db.list_tasks") as list_tasks,
        ):
            cog = PulsePro.__new__(PulsePro)
            asyncio.run(PulsePro.profile.callback(cog, interaction))

        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        list_tasks.assert_not_called()


if __name__ == "__main__":
    unittest.main()
