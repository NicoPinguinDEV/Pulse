"""AUTHZ-1 / AUTHZ-2 regression tests: ticket category confidentiality on every Dashboard ticket path.

A supporter with the general ticket permission must not read, summarise, download or change tickets of
confidential categories (Highteam / Führungsebene) without the responsible role. A refused request must
answer like a missing ticket and must not write anything.
"""

import unittest
from types import SimpleNamespace as NS
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_db
import pulse_ticket_access as pta
import webserver as ws
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401  (no ./pulse.db, no .session_secret)

GUILD = NS(id=1, name="Test", members=[], roles=[], channels=[], text_channels=[], member_count=3, owner_id=0,
           icon=None, get_member=lambda _: None, get_role=lambda _: None, get_channel=lambda _: None)
HIGH = {"id": "T-HIGH", "guild_id": "1", "category": "highteam", "user_id": "9", "user_name": "Opfer",
        "status": "closed", "transcript_path": "T-HIGH.txt", "channel_id": "5"}
NORMAL = {"id": "T-NORM", "guild_id": "1", "category": "support", "user_id": "9", "user_name": "Bürger",
          "status": "closed", "transcript_path": "T-NORM.txt", "channel_id": "6"}
OTHER_GUILD = dict(NORMAL, id="T-OTHER", guild_id="2")

SUPPORTER = {"can_view_dashboard": True, "can_manage_tickets": True, "is_admin": False}
MEMBER_ONLY = {"can_view_dashboard": True, "can_manage_tickets": False, "is_admin": False}
ADMIN = {"can_view_dashboard": True, "can_manage_tickets": True, "is_admin": True}

DEFS = {"highteam": {"role_ids": []}, "leadership": {"role_ids": []}, "support": {"role_ids": []}}
GROUPS = {"highteam": "highteam", "leadership": "leadership", "support": "team"}


def ctx_for(perms, role_ids=()):
    member = NS(id=123, display_name="Tester", roles=[NS(id=r) for r in role_ids])
    return NS(user={"id": "123", "global_name": "Tester"}, guild=GUILD, guild_name=GUILD.name, perms=perms,
              config={"team_role_ids": []}, member=member, msg="", msg_ok=True)


def fake_auth(perms, role_ids=()):
    def _auth(request, session, perm="can_view_dashboard", admin=False):
        if admin and not perms.get("is_admin"):
            raise HTTPException(status_code=403, detail="admin")
        if perm and not perms.get(perm):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        return ctx_for(perms, role_ids)
    return _auth


class CategoryPatch:
    """Stand-in for the ticket cog's category configuration: highteam is answered by role 777 only."""

    def __enter__(self):
        import cogs.tickets as tk
        self._ps = [
            mock.patch.object(tk, "ticket_category_definitions", lambda: DEFS),
            mock.patch.object(tk, "category_meta", lambda key: ("n", "d", "e", 1, GROUPS.get(str(key), "team"))),
            mock.patch.object(tk, "category_roles",
                              lambda guild, key: [NS(id=777)] if key == "highteam" else []),
        ]
        for p in self._ps:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._ps:
            p.stop()


class CanAccessTicketTests(unittest.TestCase):
    def test_rules(self):
        with CategoryPatch():
            self.assertFalse(pta.can_access_ticket(ctx_for(SUPPORTER), HIGH))                # no category role
            self.assertTrue(pta.can_access_ticket(ctx_for(SUPPORTER, [777]), HIGH))          # responsible role
            self.assertTrue(pta.can_access_ticket(ctx_for(ADMIN), HIGH))                     # administrators
            self.assertTrue(pta.can_access_ticket(ctx_for(SUPPORTER), NORMAL))               # unchanged behaviour
            self.assertFalse(pta.can_access_ticket(ctx_for(ADMIN), OTHER_GUILD))             # guild binding
            self.assertFalse(pta.can_access_ticket(ctx_for(ADMIN), None))

    def test_confidential_category_without_roles_is_admin_only(self):
        leadership = dict(HIGH, id="T-LEAD", category="leadership")
        with CategoryPatch():
            self.assertFalse(pta.can_access_ticket(ctx_for(SUPPORTER, [777]), leadership))
            self.assertTrue(pta.can_access_ticket(ctx_for(ADMIN), leadership))

    def test_filter_removes_forbidden_tickets(self):
        with CategoryPatch():
            visible = pta.filter_tickets(ctx_for(SUPPORTER), [HIGH, NORMAL, OTHER_GUILD])
        self.assertEqual([t["id"] for t in visible], ["T-NORM"])


class TicketRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(ws.app, base_url="https://testserver", follow_redirects=False)
        self.tickets = {t["id"]: t for t in (HIGH, NORMAL, OTHER_GUILD)}
        self._cat = CategoryPatch().__enter__()
        self.addCleanup(self._cat.__exit__)
        self._get = mock.patch.object(pulse_db, "get_ticket", lambda ticket_id=None, channel_id=None: self.tickets.get(ticket_id))
        self._get.start()
        self.addCleanup(self._get.stop)

    def as_user(self, perms, role_ids=()):
        p = mock.patch.object(ws, "auth", fake_auth(perms, role_ids))
        p.start()
        self.addCleanup(p.stop)

    def test_transcript_and_view_refuse_confidential_ticket_like_a_missing_one(self):
        self.as_user(SUPPORTER)
        for path in ("/tickets/transcript/T-HIGH", "/tickets/transcript/T-HIGH/view"):
            denied = self.client.get(path)
            missing = self.client.get(path.replace("T-HIGH", "T-NOPE"))
            self.assertEqual(denied.status_code, 404, path)
            self.assertEqual(denied.text.replace("T-HIGH", "X"), missing.text.replace("T-NOPE", "X"),
                             "refusal must not differ from a missing ticket")

    def test_transcript_allowed_for_responsible_role_reaches_the_file_check(self):
        self.as_user(SUPPORTER, [777])
        response = self.client.get("/tickets/transcript/T-HIGH")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Transkriptdatei ist nicht vorhanden", response.text)  # passed authorization, file simply absent

    def test_action_refused_without_write_or_discord_action(self):
        self.as_user(SUPPORTER)
        with mock.patch.object(pulse_db, "update_ticket_priority") as write, \
                mock.patch.object(ws, "log_audit") as audit:
            response = self.client.post("/tickets/action", data={"ticket_id": "T-HIGH", "action": "priority", "priority": "urgent"},
                                        headers={"origin": "https://testserver"})
        self.assertIn(response.status_code, (302, 303))
        write.assert_not_called()
        audit.assert_not_called()

    def test_priority_update_refused_without_write(self):
        self.as_user(SUPPORTER)
        with mock.patch.object(pulse_db, "update_ticket_priority") as write:
            self.client.post("/tickets/update", data={"ticket_id": "T-HIGH", "priority": "urgent"},
                             headers={"origin": "https://testserver"})
            self.client.post("/tickets/update", data={"ticket_id": "T-OTHER", "priority": "urgent"},
                             headers={"origin": "https://testserver"})
        write.assert_not_called()

    def test_detail_and_summary_hidden(self):
        self.as_user(SUPPORTER)
        for path in ("/ticket/T-HIGH", "/ticket/T-HIGH/summary", "/ticket/T-OTHER"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_download_by_filename_checks_the_ticket_category(self):
        self.as_user(SUPPORTER)
        self.assertEqual(self.client.get("/ticket/transcript/T-HIGH.txt").status_code, 404)

    def test_ticket_list_requires_ticket_management(self):  # AUTHZ-2
        self.as_user(MEMBER_ONLY)
        self.assertEqual(self.client.get("/tickets").status_code, 403)


if __name__ == "__main__":
    unittest.main()
