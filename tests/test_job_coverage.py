"""P-06 / O-07: every @tasks.loop is visible in the job registry (system status, /healthz jobs_failing)."""
import ast
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_jobs

ROOT = Path(__file__).resolve().parents[1]


class LoopCoverageTests(unittest.TestCase):
    def test_every_tasks_loop_is_tracked(self):
        missing = []
        for path in [*ROOT.glob("*.py"), *(ROOT / "cogs").glob("*.py")]:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                    decorators = [ast.unparse(d) for d in node.decorator_list]
                    if any("tasks.loop" in d for d in decorators) and not any("tracked" in d for d in decorators):
                        missing.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name}")
        self.assertEqual(missing, [], "Hintergrundschleife ohne @tracked – Fehler wären im Systemstatus unsichtbar")


class CaughtErrorsAreReportedTests(unittest.TestCase):
    def setUp(self):
        pulse_jobs._JOBS.clear()

    def test_failed_ticket_panel_refresh_shows_up_but_loop_keeps_going(self):
        from cogs import tickets
        cog = object.__new__(tickets.Tickets) if hasattr(tickets, "Tickets") else None
        if cog is None:
            self.skipTest("Ticket-Cog-Klasse nicht gefunden")
        cog.bot = SimpleNamespace(wait_until_ready=AsyncMock())
        cog.refresh_panels = AsyncMock(side_effect=RuntimeError("Discord nicht erreichbar"))
        cog.run_automation = AsyncMock()
        with patch("pulse_alerts.raise_alert"), patch("builtins.print"):  # print: emoji on a cp1252 Windows console
            asyncio.run(tickets.Tickets.panel_refresh.coro(cog))  # must not raise
        cog.run_automation.assert_awaited_once()  # the second step still ran
        self.assertIn("Tickets Panels & Automatik (5 min)", pulse_jobs.health_summary()["jobs_failing"])

    def test_tracked_loops_are_not_paused_by_maintenance_mode(self):
        """These six loops ran during maintenance mode before; tracking them must not change that."""
        from cogs import verification
        cog = object.__new__(verification.Verification) if hasattr(verification, "Verification") else None
        if cog is None:
            self.skipTest("Verifikations-Cog-Klasse nicht gefunden")
        cog.guild_id = 1
        cog.bot = SimpleNamespace(get_guild=lambda _: object())
        cog.run_reminder = AsyncMock(return_value="ok")
        with patch("pulse_flags.automations_paused", return_value=True):
            asyncio.run(verification.Verification.reminder_loop.coro(cog))
        cog.run_reminder.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
