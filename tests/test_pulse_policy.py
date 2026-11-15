"""Audit #120: central action policy – fail-closed, and equal to the checks that exist today."""
import itertools
import unittest
from types import SimpleNamespace

import pulse_case_registry as registry
import pulse_permissions as pp
import pulse_policy as policy

KEYS_IN_PLAY = ("can_moderate_kicks", "can_moderate_bans", "can_warn", "can_promote", "can_manage_tickets")


def perms_with(*granted, admin=False):
    base = {key: False for key in pp.ALL_KEYS}
    base["is_admin"] = admin
    for key in granted:
        base[key] = True
    return base


class PolicyBasicsTests(unittest.TestCase):
    def test_every_referenced_right_exists(self):
        for action, (label, keys) in policy.ACTIONS.items():
            self.assertTrue(label, action)
            for key in keys:
                self.assertIn(key, policy.KNOWN_KEYS, f"{action}: {key}")

    def test_any_one_right_is_enough(self):
        self.assertTrue(policy.can(perms_with("can_moderate_bans"), "case.view.BM"))
        self.assertTrue(policy.can(perms_with("can_moderate_kicks"), "case.view.BM"))
        self.assertFalse(policy.can(perms_with("can_warn"), "case.view.BM"))

    def test_admin_passes_everything_including_admin_area(self):
        admin = perms_with(admin=True)
        for action in policy.ACTIONS:
            self.assertTrue(policy.can(admin, action), action)
        self.assertFalse(policy.can(perms_with(*pp.ALL_KEYS), "admin.area"))  # all rights != admin

    def test_fail_closed(self):
        ok = perms_with("can_manage_tickets")
        self.assertTrue(policy.can(ok, "ticket.manage"))
        for bad_perms in (None, {}, [], "is_admin", 1):
            self.assertFalse(policy.can(bad_perms, "ticket.manage"), bad_perms)
        for bad_action in ("nope", "", None, 5, ["ticket.manage"]):
            self.assertFalse(policy.can(ok, bad_action), bad_action)
        # only a literal True counts, not a truthy value
        self.assertFalse(policy.can({"can_manage_tickets": "yes"}, "ticket.manage"))
        self.assertFalse(policy.can({"can_manage_tickets": 1}, "ticket.manage"))
        self.assertFalse(policy.can({"is_admin": "true"}, "admin.area"))

    def test_require_raises_without_leaking_rights(self):
        policy.require(perms_with("can_manage_tickets"), "ticket.manage")
        with self.assertRaises(policy.PermissionDenied) as ctx:
            policy.require(perms_with(), "ticket.manage")
        self.assertIsInstance(ctx.exception, PermissionError)
        self.assertIn("Tickets verwalten", str(ctx.exception))
        self.assertNotIn("can_manage_tickets", str(ctx.exception))
        with self.assertRaises(policy.PermissionDenied):
            policy.require(perms_with(admin=True), "gibt-es-nicht")

    def test_explain_and_allowed_actions(self):
        info = policy.explain("case.view.BM")
        self.assertEqual(info["any_of"], ["can_moderate_kicks", "can_moderate_bans"])
        with self.assertRaises(KeyError):
            policy.explain("nope")
        self.assertEqual(policy.allowed_actions(perms_with("can_warn")), ["case.view.TW", "moderation.warn"])
        self.assertEqual(policy.allowed_actions(None), [])


class ParityWithExistingChecksTests(unittest.TestCase):
    """For every combination of the relevant rights (with/without admin) the policy equals today's checks."""

    def combos(self):
        for admin in (False, True):
            for n in range(len(KEYS_IN_PLAY) + 1):
                for subset in itertools.combinations(KEYS_IN_PLAY, n):
                    yield perms_with(*subset, admin=admin)

    def test_case_kinds_equal_registry_allowed_kinds(self):
        for perms in self.combos():
            self.assertEqual(policy.allowed_case_kinds(perms), registry.allowed_kinds(perms), perms)

    def test_moderation_view_equals_inline_route_check(self):
        # webserver.py /mod-cases/verlauf-style check: is_admin or can_moderate_kicks or can_moderate_bans
        for perms in self.combos():
            inline = bool(perms.get("is_admin") or perms.get("can_moderate_kicks") or perms.get("can_moderate_bans"))
            self.assertEqual(policy.can(perms, "case.view.BM"), inline, perms)

    def test_automod_review_equals_inline_check(self):
        # cogs/automod.py: is_admin or can_moderate_kicks
        for perms in self.combos():
            inline = bool(perms.get("is_admin") or perms.get("can_moderate_kicks"))
            self.assertEqual(policy.can(perms, "moderation.review_automod"), inline, perms)

    def test_computed_permissions_of_a_real_guild_member(self):
        # End to end with the real permission computation (fake guild): a leader sees TF cases, not BM cases.
        role = SimpleNamespace(id=11, name="Leitung")
        member = SimpleNamespace(id=5, roles=[role], guild_permissions=SimpleNamespace(administrator=False))
        guild = SimpleNamespace(owner_id=1, get_member=lambda uid: member if int(uid) == 5 else None)
        config = {"team_role_ids": [11], "permissions": {"11": {"can_promote": True}}}
        perms, _ = pp.compute(guild, 5, config)
        self.assertTrue(policy.can(perms, "case.view.TF"))
        self.assertTrue(policy.can(perms, "application.manage"))  # inherited from can_promote
        self.assertFalse(policy.can(perms, "case.view.BM"))
        self.assertFalse(policy.can(perms, "server.manage"))
        stranger, _ = pp.compute(guild, 99, config)
        self.assertEqual(policy.allowed_actions(stranger), [])


if __name__ == "__main__":
    unittest.main()
