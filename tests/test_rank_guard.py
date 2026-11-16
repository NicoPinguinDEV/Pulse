"""DISC-1 regression tests: actor-side hierarchy for /promote and /demote (shared with the Dashboard)."""

import functools
import unittest
from types import SimpleNamespace as NS

from pulse_rank_guard import actor_rank_change_problem, team_rank_index

# Team ranks lowest -> highest: 10 (Aushilfe), 20 (Supporter), 30 (Moderator), 40 (Leitung)
ORDERED = [10, 20, 30, 40]


@functools.total_ordering
class FakeRole:
    """Minimal discord.Role stand-in: roles compare by position like the real ones."""

    def __init__(self, rid, position, admin, managed, default):
        self.id, self.position, self.managed, self._default = rid, position, managed, default
        self.permissions = NS(administrator=admin)

    def is_default(self):
        return self._default

    def __eq__(self, other):
        return self.position == other.position

    def __lt__(self, other):
        return self.position < other.position

    __hash__ = object.__hash__


def role(rid, position, *, admin=False, managed=False, default=False):
    return FakeRole(rid, position, admin, managed, default)


def member(mid, role_ids, *, admin=False, bot=False):
    roles = [role(r, r) for r in role_ids]
    top = max(roles, key=lambda r: r.position) if roles else role(0, 0)
    return NS(id=mid, roles=roles, top_role=top, bot=bot, guild_permissions=NS(administrator=admin))


class ActorRankChangeTests(unittest.TestCase):
    def check(self, actor, target, new_role, action="promote", admin=False):
        return actor_rank_change_problem(
            actor=actor, actor_is_admin=admin, target=target, role=new_role,
            ordered_team_role_ids=ORDERED, action=action,
        )

    def test_promote_above_own_rank_is_blocked(self):
        actor, target = member(1, [30]), member(2, [10])
        self.assertTrue(self.check(actor, target, role(40, 40)))
        self.assertTrue(self.check(actor, target, role(30, 30)))  # equal to own rank

    def test_promote_below_own_rank_passes(self):
        actor, target = member(1, [30]), member(2, [10])
        self.assertEqual(self.check(actor, target, role(20, 20)), "")

    def test_self_promotion_blocked(self):
        actor = member(1, [30])
        self.assertTrue(self.check(actor, actor, role(20, 20)))

    def test_target_not_below_actor_is_blocked(self):
        actor = member(1, [30])
        self.assertTrue(self.check(actor, member(2, [30]), role(20, 20), "demote"))
        self.assertTrue(self.check(actor, member(2, [40]), role(20, 20), "demote"))

    def test_actor_without_team_rank_is_blocked(self):
        self.assertTrue(self.check(member(1, [5]), member(2, [10]), role(20, 20)))

    def test_administrator_target_and_role_protected_for_non_admin(self):
        actor = member(1, [30])
        self.assertTrue(self.check(actor, member(2, [10], admin=True), role(20, 20)))
        self.assertTrue(self.check(actor, member(2, [10]), role(20, 20, admin=True)))

    def test_bot_target_and_managed_role_blocked_even_for_admin(self):
        actor = member(1, [40], admin=True)
        self.assertTrue(self.check(actor, member(2, [], bot=True), role(20, 20), admin=True))
        self.assertTrue(self.check(actor, member(2, [10]), role(20, 20, managed=True), admin=True))

    def test_admin_actor_not_bound_by_team_rank(self):
        actor = member(1, [], admin=True)
        self.assertEqual(self.check(actor, member(2, [10]), role(40, 40), admin=True), "")

    def test_role_outside_team_ranks_blocked(self):
        self.assertTrue(self.check(member(1, [40]), member(2, [10]), role(99, 5)))

    def test_unknown_action_denied_by_default(self):
        self.assertTrue(self.check(member(1, [40]), member(2, [10]), role(20, 20), action="kick"))

    def test_rank_index(self):
        self.assertEqual(team_rank_index(member(1, [10, 30]), ORDERED), 2)
        self.assertEqual(team_rank_index(member(1, [5]), ORDERED), -1)




class SlashCommandWiringTests(unittest.TestCase):
    """The /promote path must really use the shared guard (DISC-1) and stay deny-by-default."""

    def _run(self, actor, target, new_role, is_admin=False, action="promote"):
        from unittest import mock

        import webserver
        from cogs.pulse_suite import PulseSuite

        guild = NS(me=NS(top_role=role(999, 999), guild_permissions=NS(manage_roles=True)))
        interaction = NS(guild=guild, user=actor)
        suite = NS(safe_target=lambda i, m: True)
        with mock.patch.object(webserver, "compute_perms", return_value=({"is_admin": is_admin}, actor)), \
                mock.patch.object(webserver, "load_config", return_value={}), \
                mock.patch.object(webserver, "ordered_team_role_ids", return_value=ORDERED):
            return PulseSuite._rank_change_error(suite, interaction, target, new_role, set(ORDERED), action)

    def test_promote_above_actor_blocked_via_slash_path(self):
        actor, target = member(1, [30]), member(2, [10])
        self.assertTrue(self._run(actor, target, role(40, 40)))

    def test_promote_below_actor_allowed_via_slash_path(self):
        actor, target = member(1, [30]), member(2, [10])
        self.assertEqual(self._run(actor, target, role(20, 20)), "")

if __name__ == "__main__":
    unittest.main()
