"""AUTHZ-3 / AUTHZ-4 / AUTHZ-5 regression tests for the alternative promotion paths.

* pulse_next.promote_now (second promotion path): shared actor-rank rules, `old_role` only removed when the
  target holds it and it is below the approver.
* Suite promotions (/suite/promotions/*): requester never approves, compare-and-set state changes, the Teamler-Schutz
  lockout and the hierarchy are re-checked at approval time.
Every refusal must happen before any Discord role change.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from fastapi.testclient import TestClient

import pulse_next
import webserver as ws
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_rank_guard import ORDERED, member, role

CFG = {"team_role_ids": ORDERED}


class FakeMember:
    def __init__(self, mid, role_ids):
        base = member(mid, role_ids)
        self.id, self.roles, self.top_role = base.id, base.roles, base.top_role
        self.bot, self.guild_permissions, self.display_name = False, base.guild_permissions, f"M{mid}"
        self.added, self.removed = [], []

    async def add_roles(self, *roles, reason=None):
        self.added.extend(r.id for r in roles)

    async def remove_roles(self, *roles, reason=None):
        self.removed.extend(r.id for r in roles)


def make_guild(members, roles):
    return NS(id=1, owner_id=0, me=NS(top_role=role(999, 999)),
              get_member=lambda i: members.get(int(i)), get_role=lambda i: roles.get(int(i)))


class PromoteNowTests(unittest.TestCase):
    def run_promote(self, approver_roles, target_roles, new_role, old_role=None, admin=False):
        roles = {r: role(r, r) for r in ORDERED}
        approver = FakeMember(1, approver_roles)
        target = FakeMember(2, target_roles)
        guild = make_guild({1: approver, 2: target}, roles)
        ctx = NS(guild=guild, member=approver, perms={"is_admin": admin}, user={"id": "1", "global_name": "A"})
        row = {"id": "P1", "user_id": "2", "target_role_id": str(new_role), "old_role_id": str(old_role or ""),
               "from_rank": "a", "to_rank": "b"}
        with mock.patch.object(ws, "load_config", return_value=CFG), \
                mock.patch.object(ws, "ordered_team_role_ids", return_value=ORDERED), \
                mock.patch.object(pulse_next, "event"), mock.patch.object(pulse_next.db, "notify"), \
                mock.patch.object(pulse_next, "actor_id", lambda c: "1"), mock.patch.object(pulse_next, "actor_name", lambda c: "A"):
            result = asyncio.run(pulse_next.promote_now(ctx, row))
        return result, target

    def test_promotion_above_approver_changes_nothing(self):
        result, target = self.run_promote([30], [10], 40, old_role=10)
        self.assertIn("nichts vergeben", result)
        self.assertEqual((target.added, target.removed), ([], []))

    def test_target_not_below_approver_changes_nothing(self):
        result, target = self.run_promote([30], [30], 20, old_role=30)
        self.assertIn("nichts vergeben", result)
        self.assertEqual((target.added, target.removed), ([], []))

    def test_old_role_above_approver_is_not_removed(self):
        # target holds a higher team role (40) that the request names as old_role: it must survive
        result, target = self.run_promote([30], [10, 40], 20, old_role=40)
        self.assertEqual(target.removed, [])

    def test_valid_promotion_adds_role_and_removes_lower_old_role(self):
        result, target = self.run_promote([30], [10], 20, old_role=10)
        self.assertEqual(target.added, [20])
        self.assertEqual(target.removed, [10])

    def test_old_role_not_held_by_target_is_ignored(self):
        _, target = self.run_promote([30], [10], 20, old_role=20 + 0)  # equals new role -> ignored
        self.assertEqual(target.removed, [])


class SuitePromotionApprovalTests(unittest.TestCase):
    def setUp(self):
        from cogs import pulse_suite as suite
        self.suite = suite
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.patches = [mock.patch.object(suite, "DB_PATH", str(Path(self.tmp.name) / "suite.db")),
                        mock.patch.object(ws, "load_config", return_value=CFG),
                        mock.patch.object(ws, "ordered_team_role_ids", return_value=ORDERED),
                        mock.patch.object(suite, "audit", lambda *a, **k: None)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        suite.init_db()
        suite.register_routes()
        roles = {r: role(r, r) for r in ORDERED}
        self.target = FakeMember(2, [10])
        self.actor = FakeMember(1, [40])
        self.guild = make_guild({1: self.actor, 2: self.target}, roles)
        self.user_id = "1"
        self.client = TestClient(ws.app, raise_server_exceptions=False, headers={"Origin": "http://testserver"})

    def ctx(self):
        return NS(guild=self.guild, member=self.actor, perms={"can_promote": True, "is_admin": False},
                  user={"id": self.user_id, "global_name": "Leitung"})

    def insert(self, created_by="9", status="pending", required=1, approvals="[]"):
        with self.suite.cx() as c:
            c.execute("INSERT INTO suite_promotions(id,target_id,target_name,current_role,current_role_id,target_role,target_role_id,"
                      "reason,status,required_approvals,approvals_json,created_by_id,created_by_name,created_at,updated_at) "
                      "VALUES('prom_t','2','Ziel','a','10','b','20','r',?,?,?,?,'x','t','t')",
                      (status, required, approvals, created_by))

    def status(self):
        with self.suite.cx() as c:
            return c.execute("SELECT status,approvals_json FROM suite_promotions WHERE id='prom_t'").fetchone()

    def approve(self, safety=(True, "")):
        async def lockout(*a, **k):
            return safety
        with mock.patch.object(self.suite, "auth", lambda *a, **k: self.ctx()), \
                mock.patch.object(ws, "enforce_team_safety_action_lockout", lockout), \
                mock.patch.object(self.suite, "locked", lambda: False):
            return self.client.post("/suite/promotions/prom_t/action", data={"decision": "approve"}, follow_redirects=False)

    def test_requester_cannot_approve_own_request(self):
        self.insert(created_by=self.user_id)
        response = self.approve()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.target.added, [])
        self.assertEqual(self.status()[0], "pending")

    def test_second_person_approves_and_role_is_granted_once(self):
        self.insert()
        self.assertEqual(self.approve().status_code, 303)
        self.assertEqual(self.target.added, [20])
        self.assertEqual(self.status()[0], "approved")
        self.approve()  # replay: no longer pending, nothing happens again
        self.assertEqual(self.target.added, [20])

    def test_lockout_blocks_approval_without_role_change(self):
        self.insert()
        response = self.approve(safety=(False, "Teamler-Schutz"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.target.added, [])
        self.assertEqual(self.status()[0], "pending")

    def test_stale_state_is_not_overwritten(self):
        # the approvals changed between reading the request and storing the approval -> 409, no role change
        self.insert(required=2)
        original = self.suite.json.loads

        def racing(text, *a, **k):
            with self.suite.cx() as c:
                c.execute("UPDATE suite_promotions SET approvals_json=? WHERE id='prom_t'",
                          (json.dumps([{"id": "5", "name": "B", "at": "t"}]),))
            return original(text, *a, **k)

        with mock.patch.object(self.suite.json, "loads", racing):
            response = self.approve()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.target.added, [])

    def test_hierarchy_rechecked_at_approval_time(self):
        self.insert()
        self.actor = FakeMember(1, [20])  # approver has meanwhile been demoted below the requested rank
        self.guild = make_guild({1: self.actor, 2: self.target}, {r: role(r, r) for r in ORDERED})
        self.assertEqual(self.approve().status_code, 403)
        self.assertEqual(self.target.added, [])


if __name__ == "__main__":
    unittest.main()
