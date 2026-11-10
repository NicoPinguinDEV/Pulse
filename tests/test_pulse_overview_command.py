"""Feature #3: /pulse combines team health with the invoking member's own status."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.pulse_pro import PulsePro


class PulseOverviewCommandTests(unittest.TestCase):
    def test_pulse_shows_role_duty_tasks_tickets_and_training_progress(self):
        member = SimpleNamespace(
            display_name="Musterteamler",
            roles=[
                SimpleNamespace(id=9, name="Supporter", position=5),
                SimpleNamespace(id=10, name="Bürger", position=1),
            ],
        )
        interaction = SimpleNamespace(
            guild=object(),
            user=SimpleNamespace(id=42, display_name="Musterteamler"),
            response=SimpleNamespace(send_message=AsyncMock(), is_done=lambda: False),
        )
        shifts = {
            "active_shifts": {"42": {"status": "online"}},
            "history": [],
        }

        def list_tasks(*, assignee_id=None, limit=1000):
            if assignee_id is not None:
                return [{"status": "open"}]
            return [{"status": "open"}, {"status": "done"}]

        def list_tickets(*, claimed_by_id=None, limit=1000, open_only=False):
            if claimed_by_id is not None:
                return [{"status": "open"}, {"status": "closed"}]
            return [{"status": "open"}, {"status": "closed"}]

        with (
            patch("webserver.load_config", return_value={"weekly_goal_hours": 4, "team_role_ids": [9, 10]}),
            patch("webserver.compute_perms", return_value=({"can_view_dashboard": True}, member)),
            patch("webserver.load_shift_state", return_value=shifts),
            patch("webserver.calculate_weekly_seconds", return_value=7200),
            patch("webserver.fmt_duration", return_value="2 h"),
            patch("cogs.pulse_pro.db.list_tasks", side_effect=list_tasks),
            patch("cogs.pulse_pro.db.list_tickets", side_effect=list_tickets),
            patch("cogs.pulse_pro.db.promotion_requests", return_value=[{"id": "pending"}]),
            patch(
                "cogs.pulse_pro.db.attempts",
                return_value=[
                    {"training_id": "training-1", "passed": 1},
                    {"training_id": "training-1", "passed": 1},
                    {"training_id": "training-2", "passed": 0},
                ],
            ),
            patch("cogs.pulse_pro.panel_url", side_effect=lambda path="": f"https://pulse.example{path}"),
        ):
            cog = PulsePro.__new__(PulsePro)
            asyncio.run(PulsePro.pulse.callback(cog, interaction))

        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        rendered = " ".join(field.value for field in kwargs["embed"].fields)
        self.assertIn("Supporter", rendered)
        self.assertIn("Im Dienst", rendered)
        self.assertIn("2 h von 4 h (50 %)", rendered)
        self.assertIn("1 offene Aufgaben", rendered)
        self.assertIn("1 übernommene Tickets", rendered)
        self.assertIn("1 bestandene Schulungen", rendered)

    def test_pulse_denies_users_without_team_dashboard_access(self):
        interaction = SimpleNamespace(
            guild=object(),
            user=SimpleNamespace(id=42, display_name="Gast"),
            response=SimpleNamespace(send_message=AsyncMock(), is_done=lambda: False),
        )
        with (
            patch("webserver.load_config", return_value={}),
            patch("webserver.compute_perms", return_value=({}, None)),
            patch("cogs.pulse_pro.db.list_tasks") as list_tasks,
            patch("cogs.pulse_pro.db.list_tickets") as list_tickets,
        ):
            cog = PulsePro.__new__(PulsePro)
            asyncio.run(PulsePro.pulse.callback(cog, interaction))

        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        list_tasks.assert_not_called()
        list_tickets.assert_not_called()


if __name__ == "__main__":
    unittest.main()
