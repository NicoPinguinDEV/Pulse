"""DATA-3: dashboard promote/demote are serialised per member and a repeated POST is not executed twice."""
import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest import mock
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import webserver as ws
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_rank_guard import ORDERED, member, role


class FakeTarget:
    def __init__(self, mid, role_ids):
        base = member(mid, role_ids)
        self.id, self.roles, self.top_role, self.bot = base.id, base.roles, base.top_role, False
        self.guild_permissions = base.guild_permissions
        self.display_name = f"M{mid}"
        self.mention = f"<@{mid}>"
        self.add_roles = AsyncMock()
        self.remove_roles = AsyncMock()


class HelperTests(unittest.TestCase):
    def test_same_member_gets_the_same_lock_and_others_do_not(self):
        async def run():
            a1, a2, b = ws._rank_target_lock(1, 5), ws._rank_target_lock(1, 5), ws._rank_target_lock(1, 6)
            return a1 is a2, a1 is b
        self.assertEqual(asyncio.run(run()), (True, False))

    def test_lock_is_released_and_garbage_collected(self):
        async def run():
            lock = ws._rank_target_lock(9, 9)
            await lock.acquire()
            locked = ws._rank_target_lock(9, 9).locked()
            lock.release()
            return locked, ws._rank_target_lock(9, 9).locked()
        self.assertEqual(asyncio.run(run()), (True, False))

    def test_replay_window(self):
        ws._rank_recent.clear()
        self.addCleanup(ws._rank_recent.clear)
        self.assertFalse(ws._rank_action_replayed(1, 5, "promote", now=100.0))
        self.assertTrue(ws._rank_action_replayed(1, 5, "promote", now=101.0))
        self.assertFalse(ws._rank_action_replayed(1, 5, "demote", now=101.0))   # other action
        self.assertFalse(ws._rank_action_replayed(1, 6, "promote", now=101.0))  # other member
        self.assertFalse(ws._rank_action_replayed(1, 5, "promote", now=100.0 + ws._RANK_REPLAY_SECONDS + 1))
        self.assertLessEqual(len(ws._rank_recent), 4)


class PromoteRouteTests(unittest.TestCase):
    def setUp(self):
        ws._rank_recent.clear()
        self.addCleanup(ws._rank_recent.clear)
        roles = {r: role(r, r) for r in ORDERED}
        self.target = FakeTarget(55, [10])
        self.actor = FakeTarget(1, [40])
        self.guild = NS(id=1, owner_id=0, name="G", me=NS(top_role=role(999, 999), guild_permissions=NS(manage_roles=True)),
                        get_member=lambda i: {55: self.target, 1: self.actor}.get(int(i)), get_role=lambda i: roles.get(int(i)))
        perms = {"is_admin": True, "can_promote": True, "can_view_dashboard": True}
        ctx = NS(user={"id": "1", "global_name": "Admin"}, guild=self.guild, perms=perms, member=self.actor,
                 config={"team_role_ids": ORDERED}, msg="", msg_ok=True)
        self.finish = AsyncMock()

        async def lockout_ok(*a, **k):
            return True, ""

        for p in (mock.patch.object(ws, "auth", lambda *a, **k: ctx),
                  mock.patch.object(ws, "load_config", return_value={"team_role_ids": ORDERED}),
                  mock.patch.object(ws, "ordered_team_role_ids", return_value=ORDERED),
                  mock.patch.object(ws, "enforce_team_safety_action_lockout", lockout_ok),
                  mock.patch.object(ws, "finish_promotion", self.finish),
                  mock.patch.object(ws, "log_audit")):
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(ws.app, raise_server_exceptions=False, follow_redirects=False,
                                 headers={"Origin": "http://testserver"})

    def promote(self):
        return self.client.post("/action", data={"action": "promote", "user_id": "55", "action_reason": "Gute Arbeit",
                                                  "probation_days": "3"})

    def test_repeated_post_promotes_only_once(self):
        first = self.promote()
        self.assertEqual(first.status_code, 303)
        self.assertEqual(self.target.add_roles.await_count, 1)
        second = self.promote()  # double click / replay of the same request
        self.assertEqual(second.status_code, 303)
        self.assertEqual(self.target.add_roles.await_count, 1, "the replay must not promote a second time")
        self.assertEqual(self.finish.await_count, 1, "follow-up (DM, embed, audit) only once")
        self.assertIn("gerade", second.headers["location"].lower() + second.text.lower())


if __name__ == "__main__":
    unittest.main()
