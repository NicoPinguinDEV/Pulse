"""DISC-6: per-moderator kick/ban limit for Pulse-executed sanctions (Anti-Nuke trusts the bot itself)."""
import unittest
from unittest.mock import patch

import pulse_sanction_guard as sg


class SanctionGuardTests(unittest.TestCase):
    def setUp(self):
        sg.reset()
        self.addCleanup(sg.reset)

    def test_limit_blocks_the_next_attempt_and_audits(self):
        with patch("webserver.log_audit") as audit:
            results = [sg.reserve(1, 10, "ban", now=float(i)) for i in range(sg.LIMIT + 1)]
        self.assertTrue(all(ok for ok, _ in results[:sg.LIMIT]))
        self.assertFalse(results[-1][0])
        self.assertIn("Sicherheitsgrenze", results[-1][1])
        self.assertEqual(audit.call_count, 1)

    def test_window_expiry_frees_the_moderator(self):
        for i in range(sg.LIMIT):
            sg.reserve(1, 10, "ban", now=float(i))
        self.assertTrue(sg.reserve(1, 10, "ban", now=sg.WINDOW_SECONDS + 100.0)[0])

    def test_moderators_and_guilds_are_counted_separately(self):
        for i in range(sg.LIMIT):
            sg.reserve(1, 10, "ban", now=float(i))
        self.assertTrue(sg.reserve(1, 11, "ban", now=6.0)[0])
        self.assertTrue(sg.reserve(2, 10, "ban", now=6.0)[0])

    def test_owner_is_exempt(self):
        for i in range(sg.LIMIT + 3):
            self.assertTrue(sg.reserve(1, 99, "ban", owner_id=99, now=float(i))[0])

    def test_release_returns_the_reservation(self):
        for i in range(sg.LIMIT):
            sg.reserve(1, 10, "kick", now=float(i))
        sg.release(1, 10)
        self.assertTrue(sg.reserve(1, 10, "kick", now=10.0)[0])

    def test_tracking_is_bounded(self):
        for actor in range(sg.MAX_TRACKED + 50):
            sg.reserve(1, actor, "ban", now=float(actor) * 1000)  # each older than the window
        self.assertLessEqual(len(sg._events), sg.MAX_TRACKED + 1)

    def test_audit_failure_does_not_change_the_refusal(self):
        for i in range(sg.LIMIT):
            sg.reserve(1, 10, "ban", now=float(i))
        with patch("webserver.log_audit", side_effect=RuntimeError("x")):
            self.assertFalse(sg.reserve(1, 10, "ban", now=7.0)[0])


class ModCasesWiringTests(unittest.IsolatedAsyncioTestCase):
    """A refused ban creates no case, sends no DM and performs no Discord action."""

    async def test_blocked_ban_has_no_side_effects(self):
        from types import SimpleNamespace as NS
        from unittest.mock import AsyncMock
        from cogs.mod_cases import ModCases
        sg.reset()
        self.addCleanup(sg.reset)
        for i in range(sg.LIMIT):
            sg.reserve(1, 10, "ban", now=0.0)
        guild = NS(id=1, owner_id=0, name="G", ban=AsyncMock())
        interaction = NS(guild=guild, user=NS(id=10, display_name="Mod"))
        target = NS(id=5, display_name="Ziel", send=AsyncMock(), kick=AsyncMock())
        respond = AsyncMock()
        cog = ModCases.__new__(ModCases)
        with patch("cogs.mod_cases.mc.create_case") as create, patch("webserver.log_audit"), \
                patch.object(sg.time, "monotonic", return_value=1.0):
            await cog._execute(interaction, "ban", target, "Grund", "", None, respond)
        create.assert_not_called()
        target.send.assert_not_awaited()
        guild.ban.assert_not_awaited()
        self.assertIn("Sicherheitsgrenze", respond.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
