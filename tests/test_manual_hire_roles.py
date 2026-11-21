"""Regression tests for automatic role bundles during manual team hiring."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import pulse_ids
import webserver as ws
from pulse_hire_roles import manual_hire_role_ids
from pulse_team_roles import TEAM_ROLE_ORDER
from tests.fakes import FakeGuild, FakeMember


EXPECTED = {
    "Test Supporter": ("supporter", "low_team"),
    "Supporter": ("supporter", "low_team"),
    "Sr.Supporter": ("supporter", "low_team"),
    "Jr.Moderator": ("moderation", "low_team"),
    "Moderator": ("moderation", "low_team"),
    "Sr.Moderator": ("moderation", "low_team"),
    "Jr.Admin": ("admin", "low_team"),
    "Admin": ("admin", "low_team"),
    "Sr.Admin": ("admin", "low_team"),
    "Eventleitung": ("eventleitung", "high_team"),
    "Fraktionsverwaltung": ("fraktionsverwaltung", "high_team"),
    "Ausbilder": ("ausbilder", "high_team"),
    "Teamleitung": ("teamleitung", "high_team"),
    "Stv.Teamleitung": ("teamleitung", "high_team"),
    "Management": ("management", "high_team"),
    "Stv.Management": ("management", "high_team"),
    "Serverleitung": ("serverleitung", "leitungsebene"),
    "Stv.Serverleitung": ("serverleitung", "leitungsebene"),
    "Projektleitung": ("projektleitung", "leitungsebene"),
    "Stv.Projektleitung": ("projektleitung", "leitungsebene"),
    "Founder": ("owner_team", "fuehrungsebene"),
    "Rechte Hand": ("rechte_hand", "fuehrungsebene"),
    "Owner": ("owner_team", "fuehrungsebene"),
    "Co.Owner": ("owner_team", "fuehrungsebene"),
}


class Role:
    def __init__(self, role_id, name, position, *, administrator=False, managed=False):
        self.id = int(role_id)
        self.name = name
        self.position = int(position)
        self.managed = bool(managed)
        self.mention = f"<@&{self.id}>"
        self.permissions = SimpleNamespace(administrator=administrator)

    def is_default(self):
        return self.id == 0


class ManualHireRoleMappingTests(unittest.TestCase):
    def test_every_current_rank_maps_to_its_position_and_group(self):
        self.assertEqual(set(EXPECTED), set(TEAM_ROLE_ORDER))
        for rank_name, (level_key, group_key) in EXPECTED.items():
            with self.subTest(rank=rank_name):
                self.assertEqual(
                    manual_hire_role_ids(rank_name),
                    (
                        pulse_ids.TEAM_MEMBER_ROLE_ID,
                        pulse_ids.TEAM_HIRE_LEVEL_ROLE_IDS[level_key],
                        pulse_ids.TEAM_HIRE_GROUP_ROLE_IDS[group_key],
                    ),
                )

    def test_decorated_rank_names_are_normalized(self):
        self.assertEqual(
            manual_hire_role_ids("》BORP メ Supporter"),
            manual_hire_role_ids("Supporter"),
        )
        self.assertEqual(
            manual_hire_role_ids("》BORP ✘ Rechte Hand"),
            manual_hire_role_ids("Rechte Hand"),
        )

    def test_unknown_or_retired_roles_fail_closed(self):
        self.assertEqual(manual_hire_role_ids("Inhaber"), ())
        self.assertEqual(manual_hire_role_ids("Team"), ())
        self.assertEqual(manual_hire_role_ids(""), ())


class ManualHireRouteTests(unittest.TestCase):
    def setUp(self):
        self.support_role = Role(10, "》BORP メ Supporter", 5)
        self.lead_role = Role(11, "》BORP メ Teamleitung", 15)
        self.actor = FakeMember(100, "Leitung", roles=[self.lead_role])
        self.target = FakeMember(200, "Neues Mitglied")
        self.actor.top_role = self.lead_role
        self.target.top_role = Role(0, "@everyone", 0)
        self.roles = [self.support_role, self.lead_role]
        for index, role_id in enumerate(manual_hire_role_ids("Supporter"), start=20):
            self.roles.append(Role(role_id, f"Zusatzrolle {index}", index))
        self.guild = FakeGuild(members=[self.actor, self.target], roles=self.roles)
        self.guild.me.top_role = Role(999, "Pulse", 100)
        self.guild.me.guild_permissions = SimpleNamespace(manage_roles=True)
        self.config = {"team_role_ids": [self.support_role.id, self.lead_role.id]}
        self.ctx = SimpleNamespace(
            user={"id": str(self.actor.id), "global_name": "Leitung"},
            guild=self.guild,
            guild_name=self.guild.name,
            perms={"can_promote": True, "is_admin": False},
            config=self.config,
            member=self.actor,
            msg="",
            msg_ok=True,
        )
        patches = (
            patch.object(ws, "auth", lambda *args, **kwargs: self.ctx),
            patch.object(ws, "ordered_team_role_ids", return_value=[self.support_role.id, self.lead_role.id]),
            patch.object(ws, "enforce_team_safety_action_lockout", new_callable=AsyncMock, return_value=(True, "")),
            patch.object(ws, "load_json", return_value={}),
            patch.object(ws, "save_json"),
            patch.object(ws, "store_employment_event"),
            patch.object(ws, "log_audit", return_value="audit-1"),
            patch.object(ws, "send_dm_notification", new_callable=AsyncMock),
            patch.object(ws, "send_team_update_embed", new_callable=AsyncMock),
        )
        self.mocks = [p.start() for p in patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(patches)])
        self.client = TestClient(ws.app, raise_server_exceptions=False, headers={"Origin": "http://testserver"})

    def post_hire(self):
        return self.client.post(
            "/action",
            data={
                "action": "hire",
                "target_user_id": str(self.target.id),
                "hire_role_id": str(self.support_role.id),
                "action_reason": "Regressionstest manuelle Einstellung",
            },
            follow_redirects=False,
        )

    def test_hiring_assigns_rank_membership_position_and_low_team_roles(self):
        response = self.post_hire()
        self.assertEqual(response.status_code, 303)
        expected_ids = {
            self.support_role.id,
            pulse_ids.TEAM_MEMBER_ROLE_ID,
            pulse_ids.TEAM_HIRE_LEVEL_ROLE_IDS["supporter"],
            pulse_ids.TEAM_HIRE_GROUP_ROLE_IDS["low_team"],
        }
        self.assertEqual({role.id for role in self.target.roles}, expected_ids)
        self.assertEqual({role.id for role in self.target.added_roles}, expected_ids)
        self.mocks[-1].assert_awaited_once()

    def test_missing_required_role_refuses_hire_before_any_role_is_added(self):
        missing_id = pulse_ids.TEAM_HIRE_GROUP_ROLE_IDS["low_team"]
        self.guild.roles = [role for role in self.guild.roles if role.id != missing_id]
        response = self.post_hire()
        self.assertEqual(response.status_code, 303)
        from urllib.parse import unquote
        self.assertIn("Es wurde nichts geändert", unquote(response.headers.get("location", "")))
        self.assertEqual(self.target.added_roles, [])

    def test_additional_administrator_role_is_rejected_for_non_admin_actor(self):
        target_level_id = pulse_ids.TEAM_HIRE_LEVEL_ROLE_IDS["supporter"]
        for role in self.guild.roles:
            if role.id == target_level_id:
                role.permissions.administrator = True
        response = self.post_hire()
        self.assertEqual(response.status_code, 303)
        from urllib.parse import unquote
        self.assertIn("Administratorrechte", unquote(response.headers.get("location", "")))
        self.assertEqual(self.target.added_roles, [])


if __name__ == "__main__":
    unittest.main()
