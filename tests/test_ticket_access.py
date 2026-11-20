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
UNBOUND_GUILD = dict(NORMAL, id="T-UNBOUND", guild_id="")
NULL_GUILD = dict(NORMAL, id="T-NULL-GUILD", guild_id=None)

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

    def test_unbound_tickets_are_denied_even_for_administrators(self):
        # A missing guild ID must never behave like a wildcard, including for normal
        # categories and administrators. Only explicitly guild-bound rows are visible.
        with CategoryPatch():
            for ticket in (UNBOUND_GUILD, NULL_GUILD):
                self.assertFalse(pta.can_access_ticket(ctx_for(SUPPORTER), ticket))
                self.assertFalse(pta.can_access_ticket(ctx_for(ADMIN), ticket))

    def test_confidential_category_without_roles_is_admin_only(self):
        leadership = dict(HIGH, id="T-LEAD", category="leadership")
        with CategoryPatch():
            self.assertFalse(pta.can_access_ticket(ctx_for(SUPPORTER, [777]), leadership))
            self.assertTrue(pta.can_access_ticket(ctx_for(ADMIN), leadership))

    def test_filter_removes_forbidden_tickets(self):
        with CategoryPatch():
            visible = pta.filter_tickets(ctx_for(SUPPORTER), [HIGH, NORMAL, OTHER_GUILD, UNBOUND_GUILD, NULL_GUILD])
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



class TicketButtonAuthorizationTests(unittest.TestCase):
    def setUp(self):
        import cogs.pulse_suite as suite
        import cogs.tickets as ticket_module
        self.suite = suite
        self.ticket_module = ticket_module

    def _discord_member(self, user_id=123, role_ids=(), *, admin=False):
        return NS(
            id=user_id,
            display_name=f"Member {user_id}",
            guild=GUILD,
            roles=[NS(id=role_id) for role_id in role_ids],
            guild_permissions=NS(administrator=admin),
        )

    def test_normal_ticket_buttons_require_category_responsibility(self):
        with CategoryPatch():
            outsider = self._discord_member()
            responsible = self._discord_member(role_ids=[777])
            admin = self._discord_member(admin=True)

            self.assertFalse(self.ticket_module.can_manage_ticket(outsider, HIGH))
            self.assertTrue(self.ticket_module.can_manage_ticket(responsible, HIGH))
            self.assertTrue(self.ticket_module.can_manage_ticket(admin, HIGH))
            self.assertFalse(self.ticket_module.can_manage_ticket(admin, OTHER_GUILD))

    def test_ticket_creator_can_close_only_own_ticket_in_same_guild(self):
        with CategoryPatch():
            owner = self._discord_member(user_id=9)
            other_user = self._discord_member(user_id=10)
            wrong_guild = dict(HIGH, guild_id="2")

            self.assertTrue(self.ticket_module.can_control_ticket(owner, HIGH, allow_owner=True))
            self.assertFalse(self.ticket_module.can_control_ticket(other_user, HIGH, allow_owner=True))
            self.assertFalse(self.ticket_module.can_control_ticket(owner, wrong_guild, allow_owner=True))

    def test_suite_buttons_use_the_shared_category_and_guild_acl(self):
        web = NS(
            compute_perms=lambda guild, user_id, config: (ADMIN if str(user_id) == "124" else SUPPORTER, {}),
            load_config=lambda: {},
        )
        with CategoryPatch():
            outsider = ctx_for(SUPPORTER).member
            responsible = ctx_for(SUPPORTER, [777]).member
            admin = NS(id=124, roles=[], display_name="Administrator")

            self.assertFalse(self.suite.can_access_suite_ticket(GUILD, outsider, HIGH, web))
            self.assertTrue(self.suite.can_access_suite_ticket(GUILD, responsible, HIGH, web))
            self.assertTrue(self.suite.can_access_suite_ticket(GUILD, admin, HIGH, web))
            self.assertFalse(self.suite.can_access_suite_ticket(GUILD, admin, OTHER_GUILD, web))

    def test_all_standard_discord_controls_and_close_modal_recheck_category_acl(self):
        import inspect

        source = inspect.getsource(self.ticket_module.TicketControlView)
        self.assertEqual(source.count("if not can_manage_ticket(interaction.user, t):"), 4)
        self.assertIn("can_control_ticket(interaction.user, t, allow_owner=True)", source)
        modal_source = inspect.getsource(self.ticket_module.TicketCloseModal.on_submit)
        self.assertIn("can_control_ticket(interaction.user, ticket, allow_owner=True)", modal_source)
        self.assertIn("str(getattr(self.actor, \"id\", \"\")) != str(interaction.user.id)", modal_source)

    def test_suite_claim_and_close_do_not_report_success_when_database_rejects(self):
        import asyncio
        from unittest.mock import AsyncMock

        interaction = NS(
            guild=GUILD,
            user=NS(id=123, display_name="Tester"),
            response=NS(send_message=AsyncMock()),
        )
        with mock.patch.object(self.suite, "allowed", return_value=True), \
                mock.patch.object(self.suite.db, "get_ticket", return_value=HIGH), \
                mock.patch.object(self.suite, "can_access_suite_ticket", return_value=True), \
                mock.patch.object(self.suite.db, "claim_ticket", return_value=False) as claim, \
                mock.patch.object(self.suite.db, "close_ticket", return_value=False) as close, \
                mock.patch.object(self.suite, "audit") as audit:
            asyncio.run(self.suite.suite_ticket_action(interaction, "claim", "T-HIGH"))
            claim.assert_called_once()
            self.assertIn("bereits übernommen", interaction.response.send_message.await_args.args[0])
            interaction.response.send_message.reset_mock()

            asyncio.run(self.suite.suite_ticket_action(interaction, "close", "T-HIGH"))
            close.assert_called_once()
            self.assertIn("konnte nicht geschlossen", interaction.response.send_message.await_args.args[0])
            audit.assert_not_called()

    def test_suite_transfer_uses_atomic_transfer_and_category_eligible_manager(self):
        import asyncio
        from unittest.mock import AsyncMock

        next_manager = self._discord_member(user_id=456, role_ids=[777])
        interaction = NS(
            guild=GUILD,
            user=NS(id=123, display_name="Current"),
            response=NS(send_message=AsyncMock()),
        )
        with CategoryPatch(), \
                mock.patch.object(self.suite, "allowed", return_value=True), \
                mock.patch.object(self.suite, "managers", return_value=[next_manager]), \
                mock.patch.object(self.suite.db, "get_ticket", return_value=HIGH), \
                mock.patch.object(self.suite, "can_access_suite_ticket", return_value=True), \
                mock.patch.object(self.suite.db, "list_tickets", return_value=[]), \
                mock.patch.object(self.suite.db, "transfer_ticket", return_value=True) as transfer, \
                mock.patch.object(self.suite.db, "claim_ticket") as claim, \
                mock.patch.object(self.suite.db, "notify") as notify, \
                mock.patch.object(self.suite, "audit") as audit:
            asyncio.run(self.suite.suite_ticket_action(interaction, "transfer", "T-HIGH"))

        transfer.assert_called_once_with("T-HIGH", 456, "Member 456", actor_id=123, actor_name="Current")
        claim.assert_not_called()
        notify.assert_called_once()
        audit.assert_called_once()
        self.assertIn("Ticket übergeben", interaction.response.send_message.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
