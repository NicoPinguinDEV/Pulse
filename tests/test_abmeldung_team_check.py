"""DISC-3: /abmeldung and its button/modal are team-only; a refusal must not touch roles, nicknames or storage."""
import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from cogs.abmeldung import AbmeldungCog

TEAM_IDS = {500}


def user(role_ids, admin=False):
    return NS(id=7, roles=[NS(id=r) for r in role_ids], guild_permissions=NS(administrator=admin),
              add_roles=AsyncMock(), edit=AsyncMock(), display_name="U")


def interaction(u):
    return NS(guild=NS(id=1), user=u,
              response=NS(send_message=AsyncMock(), defer=AsyncMock(), is_done=lambda: False),
              followup=NS(send=AsyncMock()))


class AbmeldungTeamCheckTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(AbmeldungCog, "_configured_team_role_ids", staticmethod(lambda: set(TEAM_IDS)))
        p.start()
        self.addCleanup(p.stop)
        self.cog = AbmeldungCog.__new__(AbmeldungCog)

    def test_membership_rules(self):
        self.assertTrue(AbmeldungCog.is_team_member(user([500])))
        self.assertTrue(AbmeldungCog.is_team_member(user([], admin=True)))
        self.assertFalse(AbmeldungCog.is_team_member(user([1, 2])))
        self.assertFalse(AbmeldungCog.is_team_member(user([])))

    def test_membership_fails_closed_without_configured_team_roles(self):
        with patch.object(AbmeldungCog, "_configured_team_role_ids", staticmethod(lambda: set())):
            self.assertFalse(AbmeldungCog.is_team_member(user([500])))

    def test_citizen_is_refused_before_any_side_effect(self):
        citizen = user([1])
        i = interaction(citizen)
        with patch.object(AbmeldungCog, "parse_date", MagicMock()) as parse:
            asyncio.run(self.cog.abmeldung_eintragen(i, "@everyone Urlaub", "01.01.2030", "02.01.2030"))
        i.response.send_message.assert_awaited_once()
        self.assertIn("Teammitglieder", i.response.send_message.await_args.args[0])
        i.response.defer.assert_not_awaited()
        parse.assert_not_called()  # nothing after the check ran
        citizen.add_roles.assert_not_awaited()
        citizen.edit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
