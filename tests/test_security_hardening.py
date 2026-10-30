"""Security hardening 2026-10 (docs/SECURITY_HARDENING.md): regression tests for SEC-xx findings.

Only local fakes – no Discord, no OAuth, no production data.
"""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import pulse_db
import pulse_sessions
import webserver as ws

BROWSER = {"Origin": "http://testserver"}


class TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()
        ws._rate_hits.clear()

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()


def fake_request(headers: dict, scheme: str = "http"):
    lowered = {k.lower(): v for k, v in headers.items()}
    return SimpleNamespace(headers=lowered, url=SimpleNamespace(scheme=scheme),
                           base_url=f"{scheme}://fallback.invalid/")


class ForgedHostHeaderTests(unittest.TestCase):
    """SEC-02: X-Forwarded-Host came straight from the client and ended up in bot messages/OAuth links."""

    def test_forwarded_host_from_the_client_is_ignored(self):
        with patch.object(ws, "PUBLIC_BASE_URL", ""), patch.object(ws, "REDIRECT_URI", ""):
            origin = ws.request_origin(fake_request({"Host": "pulse.example", "X-Forwarded-Host": "evil.example",
                                                     "X-Forwarded-Proto": "javascript"}))
        self.assertEqual(origin, "http://pulse.example")

    def test_configured_callback_wins_over_any_header(self):
        with patch.object(ws, "PUBLIC_BASE_URL", ""), patch.object(ws, "REDIRECT_URI", "https://dash.example/callback"):
            origin = ws.request_origin(fake_request({"Host": "evil.example"}))
        self.assertEqual(origin, "https://dash.example")

    def test_malformed_host_falls_back(self):
        with patch.object(ws, "PUBLIC_BASE_URL", ""), patch.object(ws, "REDIRECT_URI", ""):
            for host in ("evil.example/x", "a@b", "evil.example)[x](y", "host name"):
                self.assertEqual(ws.request_origin(fake_request({"Host": host})), "http://fallback.invalid")
            self.assertEqual(ws.request_origin(fake_request({"Host": "127.0.0.1:25095"})), "http://127.0.0.1:25095")

    def test_public_base_url_still_first(self):
        with patch.object(ws, "PUBLIC_BASE_URL", "https://public.example"):
            self.assertEqual(ws.request_origin(fake_request({"Host": "evil.example"})), "https://public.example")


class ApplicationAlertMarkdownTests(unittest.TestCase):
    """SEC-04: the applicant name from the public form could put a masked phishing link into the team alert."""

    def test_discord_text_neutralises_links_and_mentions(self):
        text = ws.discord_text("[Hier klicken](https://evil.example) @everyone")
        self.assertIn("\\[Hier klicken]", text)  # escaped bracket: no masked link
        self.assertNotIn("@everyone", text)
        self.assertIn("evil.example", text)  # still readable, only not clickable as masked link

    def test_alert_embed_escapes_name(self):
        sent = {}

        async def capture(channel, *args, **kwargs):
            sent.update(kwargs)
            return True

        channel = SimpleNamespace(guild=None, send=lambda *a, **k: None)
        guild = SimpleNamespace(get_channel=lambda _i: channel, get_role=lambda _i: None, me=object())
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=SimpleNamespace(get_guild=lambda _i: guild))),
                                  headers={"host": "x"}, url=SimpleNamespace(scheme="http"), base_url="http://x/")
        application = {"user_id": "123<>", "name": "[Bewerbung prüfen](https://evil.example)", "career_label": "**X**"}
        with patch.object(ws, "send_styled", AsyncMock(side_effect=capture)):
            asyncio.run(ws.notify_new_application(request, "A1", application))
        embed = sent["embed"]
        bewerber = embed.fields[0].value
        self.assertIn("\\[Bewerbung prüfen]", bewerber)
        self.assertNotIn("&amp;", bewerber)
        self.assertIn("Discord-ID: `123`", bewerber)


class SessionWithoutSidTests(TempDb):
    """SEC-03: a login whose server-side session could not be stored received an unrevocable 7-day cookie."""

    def _callback(self, create_side_effect):
        member = SimpleNamespace(id=7, roles=[])
        guild = SimpleNamespace(id=1, name="G", get_member=lambda _i: member)
        ws.app.state.bot = SimpleNamespace(get_guild=lambda _i: guild)
        client = TestClient(ws.app, raise_server_exceptions=False)
        client.cookies.set("oauth_state", "abc")
        with patch.object(ws, "discord_oauth_identify", AsyncMock(return_value=({"id": "7", "username": "u"}, ""))), \
             patch.object(ws, "compute_perms", lambda *a: ({"can_view_dashboard": True, "is_admin": False}, member)), \
             patch.object(pulse_sessions, "create", side_effect=create_side_effect):
            return client.get("/callback?code=c&state=abc", follow_redirects=False)

    def tearDown(self):
        ws.app.state.bot = None
        super().tearDown()

    def test_login_fails_closed_when_session_cannot_be_stored(self):
        response = self._callback(RuntimeError("database is locked"))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("user_session=", response.headers.get("set-cookie", ""))

    def test_login_still_works_normally(self):
        response = self._callback(lambda *a, **k: "sid-1")
        self.assertEqual(response.status_code, 303)
        self.assertIn("user_session=", response.headers.get("set-cookie", ""))

    def test_logout_ends_a_copied_cookie_without_sid(self):
        signed = ws.sign_payload({"id": "7", "exp": int(time.time() + 600)}, typ=ws.TOKEN_DASHBOARD)
        time.sleep(1.05)  # revoked_before has second resolution; the cookie must be strictly older
        self.assertEqual(ws.get_current_user(signed)["id"], "7")
        other = pulse_sessions.create("7")  # a separate device with its own session stays signed in
        client = TestClient(ws.app, raise_server_exceptions=False, headers=BROWSER)
        client.cookies.set("user_session", signed)
        client.post("/logout", follow_redirects=False)
        with self.assertRaises(ws.HTTPException):
            ws.get_current_user(signed)
        self.assertTrue(pulse_sessions.is_valid({"id": "7", "sid": other}))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------------------------------------
# Role escalation simulations (SEC-01, SEC-07, SEC-08, SEC-09) – fake guild, nothing leaves the process.
# ---------------------------------------------------------------------------------------------------------
import functools  # noqa: E402

import discord  # noqa: E402

from tests.fakes import FakeGuild, FakeMember  # noqa: E402


@functools.total_ordering
class Role:
    def __init__(self, rid, name, position, admin=False, **perms):
        self.id, self.name, self.position, self.managed, self.mention = rid, name, position, False, f"<@&{rid}>"
        self.permissions = discord.Permissions(administrator=admin, **perms)
        self.members = []
        self.color = SimpleNamespace(value=0)

    def is_default(self):
        return False

    def __eq__(self, other):
        return isinstance(other, Role) and other.id == self.id

    def __hash__(self):
        return hash(self.id)

    def __lt__(self, other):
        return self.position < other.position


def member(uid, name, *roles):
    m = FakeMember(uid, name, roles=list(roles))
    m.top_role = max(roles, key=lambda r: r.position) if roles else Role(0, "@everyone", 0)
    return m


SUPPORT, MOD, LEAD = Role(10, "Support", 5), Role(11, "Moderator", 6, kick_members=True), Role(12, "Leitung", 7)
ADMINROLE = Role(20, "Server-Admin", 4, admin=True)        # forgotten admin role BELOW the lead role
HELPER_MOD = Role(21, "Extra-Mod", 3, ban_members=True)    # non-team role with server-wide rights
COSMETIC = Role(22, "Event", 2)
TEAM_IDS = [10, 11, 12]
LEAD_PERMS = {k: False for k in ws.PERM_KEYS} | {"can_view_dashboard": True, "can_promote": True,
                                                  "can_manage_promotions": True, "can_warn": True,
                                                  "can_manage_applications": True, "is_admin": False}


class RoleEscalationTests(TempDb):
    def setUp(self):
        super().setUp()
        self.lead = member(100, "Lena", LEAD)
        self.alt = member(200, "Zweitaccount")
        self.support = member(300, "Sam", SUPPORT)
        self.guild = FakeGuild(members=[self.lead, self.alt, self.support],
                               roles=[SUPPORT, MOD, LEAD, ADMINROLE, HELPER_MOD, COSMETIC])
        self.guild.me.top_role = Role(99, "Pulse", 50)
        self.guild.me.guild_permissions = discord.Permissions(manage_roles=True)
        self.config = {"team_role_ids": TEAM_IDS}
        ws.app.state.bot = SimpleNamespace(get_guild=lambda _i: self.guild)
        self.patches = [patch.object(ws, "load_config", lambda: self.config),
                        patch.object(ws, "auth", self.fake_auth)]
        for p in self.patches:
            p.start()
        self.client = TestClient(ws.app, raise_server_exceptions=False, headers=BROWSER)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        ws.app.state.bot = None
        super().tearDown()

    def fake_auth(self, request, session, perm="can_view_dashboard", admin=False):
        if perm and not LEAD_PERMS.get(perm):
            raise ws.HTTPException(status_code=403)
        return SimpleNamespace(user={"id": str(self.lead.id), "global_name": "Lena"}, guild=self.guild,
                               guild_name="G", perms=dict(LEAD_PERMS), config=self.config, member=self.lead,
                               msg="", msg_ok=True)

    def _msg(self, response):
        from urllib.parse import parse_qs, urlparse
        return parse_qs(urlparse(response.headers.get("location", "")).query).get("msg", [""])[0]

    # SEC-01 ------------------------------------------------------------------------------------------
    def test_promotion_request_cannot_target_a_non_team_admin_role(self):
        r = self.client.post("/promotions/create", data={"target_user_id": self.alt.id, "requested_role_id": ADMINROLE.id,
                                                         "reason": "x"}, follow_redirects=False)
        self.assertIn("keine Teamrolle", self._msg(r))
        self.assertEqual(pulse_db.promotion_requests("pending"), [])

    def test_promotion_request_cannot_reach_own_rank(self):
        r = self.client.post("/promotions/create", data={"target_user_id": self.support.id, "requested_role_id": LEAD.id,
                                                         "reason": "x"}, follow_redirects=False)
        self.assertIn("eigenen Rang", self._msg(r))
        self.assertEqual(pulse_db.promotion_requests("pending"), [])

    def test_own_request_cannot_be_self_approved_and_stored_bad_role_is_refused(self):
        # A request created before the fix (or directly in the DB) with an admin role and the lead as author.
        pid = pulse_db.create_promotion_request(self.alt.id, "Zweitaccount", "", ADMINROLE.name, "x", self.lead.id,
                                                "Lena", ADMINROLE.id, None)
        r = self.client.post("/promotions/decide", data={"promotion_id": pid, "status": "approved"}, follow_redirects=False)
        self.assertIn("Vier-Augen", self._msg(r))
        other = pulse_db.create_promotion_request(self.alt.id, "Zweitaccount", "", ADMINROLE.name, "x", 555, "Jemand",
                                                  ADMINROLE.id, None)
        r = self.client.post("/promotions/decide", data={"promotion_id": other, "status": "approved"}, follow_redirects=False)
        self.assertIn("keine Teamrolle", self._msg(r))
        self.assertEqual(self.alt.added_roles, [])
        self.assertEqual(len(pulse_db.promotion_requests("pending")), 2)  # nothing decided

    def test_valid_promotion_still_works(self):
        pid = pulse_db.create_promotion_request(self.support.id, "Sam", "Support", MOD.name, "gut", 555, "Jemand",
                                                MOD.id, SUPPORT.id)
        r = self.client.post("/promotions/decide", data={"promotion_id": pid, "status": "approved"}, follow_redirects=False)
        self.assertIn("befördert", self._msg(r))
        self.assertEqual(self.support.added_roles, [MOD])

    # SEC-08 ------------------------------------------------------------------------------------------
    def test_role_manager_refuses_roles_with_server_rights(self):
        for role in (ADMINROLE, HELPER_MOD):
            with self.subTest(role=role.name):
                r = self.client.post(f"/team/{self.alt.id}/roles", data={"add_role_ids": [role.id], "action_reason": "x"},
                                     follow_redirects=False)
                self.assertIn("nur Administratoren", self._msg(r))
        self.assertEqual(self.alt.added_roles, [])

    def test_role_manager_still_assigns_plain_and_team_roles(self):
        r = self.client.post(f"/team/{self.alt.id}/roles", data={"add_role_ids": [COSMETIC.id, MOD.id], "action_reason": "ok"},
                             follow_redirects=False)
        self.assertNotIn("nur Administratoren", self._msg(r))
        self.assertEqual({x.id for x in self.alt.added_roles}, {COSMETIC.id, MOD.id})

    # SEC-09 ------------------------------------------------------------------------------------------
    def test_warning_of_higher_or_own_rank_cannot_be_withdrawn(self):
        boss = member(400, "Chef", LEAD)
        team = {str(boss.id): {"warns_list": [{"id": "w1", "reason": "r", "active": True}]},
                str(self.lead.id): {"warns_list": [{"id": "w2", "reason": "r", "active": True}]}}
        with patch.object(ws, "load_json", lambda *_a, **_k: team), patch.object(ws, "save_json") as saved:
            for target, wid in ((boss, "w1"), (self.lead, "w2")):
                result = asyncio.run(ws.revoke_team_warning(self.guild, member=target, warn_id=wid, revoke_reason="weg",
                                                            actor_name="Lena", actor_id=str(self.lead.id), config=self.config,
                                                            actor_member=self.lead))
                self.assertFalse(result["stored"])
            saved.assert_not_called()
        self.assertTrue(all(w["active"] for e in team.values() for w in e["warns_list"]))

    # SEC-07 ------------------------------------------------------------------------------------------
    def test_hire_after_interview_respects_own_rank(self):
        apps = {"A1": {"status": "interview", "user_id": str(self.alt.id), "name": "Alt", "interview_invited_at": "01.10.2026 10:00"}}
        edits = []

        async def edit(**kwargs):
            edits.append(kwargs)
        self.alt.edit = edit
        with patch.object(ws, "load_json", lambda path, default=None: apps if path == ws.APPS_FILE else (default or {})), \
             patch.object(ws, "save_json"):
            r = self.client.post("/action", data={"action": "decide_app", "app_id": "A1", "decision": "hire_after_interview",
                                                  "hire_role_id": LEAD.id, "action_reason": "Gespräch gut"},
                                 follow_redirects=False)
        self.assertIn("eigenen Rang", self._msg(r))
        self.assertEqual(edits, [])
        self.assertEqual(apps["A1"]["status"], "interview")


# ---------------------------------------------------------------------------------------------------------
# SEC-11 – recovery consents survive wrong client credentials; only invalid_grant unlinks.
# ---------------------------------------------------------------------------------------------------------
import pulse_recovery  # noqa: E402


class FakeHttpResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class RecoveryConsentTests(TempDb):
    UID = "123456789012345678"

    def setUp(self):
        super().setUp()
        self.key = patch.object(ws, "SESSION_KEY", b"p" * 32)
        self.key.start()
        pulse_recovery.store_refresh_token(self.UID, "refresh-token-for-tests", now=100)
        guild = SimpleNamespace(id=1)
        self.patches = [patch.object(pulse_recovery, "target_status", lambda *_a: (guild, SimpleNamespace(id=2), [])),
                        patch.dict("os.environ", {"DISCORD_TOKEN": "x"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.key.stop()
        super().tearDown()

    def _rejoin(self, status, body):
        client = SimpleNamespace(post=AsyncMock(return_value=FakeHttpResponse(status, body)), put=AsyncMock())
        return asyncio.run(pulse_recovery.rejoin_user(self.UID, "refresh-token-for-tests", None, client))

    def test_wrong_client_secret_keeps_every_consent(self):
        for status, body in ((401, {"error": "invalid_client"}), (400, {"error": "invalid_client"}), (400, {})):
            ok, message = self._rejoin(status, body)
            self.assertFalse(ok)
            self.assertEqual(pulse_recovery.get_refresh_token(self.UID), "refresh-token-for-tests", (status, body))
        self.assertEqual(self._rejoin(401, {})[1], pulse_recovery.CONFIG_ERROR_MESSAGE)

    def test_revoked_consent_is_still_unlinked(self):
        ok, _ = self._rejoin(400, {"error": "invalid_grant"})
        self.assertFalse(ok)
        self.assertIsNone(pulse_recovery.get_refresh_token(self.UID))


# ---------------------------------------------------------------------------------------------------------
# SEC-06 – AI moderation review buttons: no sanction without the matching right.
# ---------------------------------------------------------------------------------------------------------
class AiReviewRightsTests(unittest.TestCase):
    def _cog_and_interaction(self, perms, discord_perms):
        from cogs import ai_moderation
        cog = ai_moderation.AIModeration.__new__(ai_moderation.AIModeration)
        reviewer = member(1, "Prüfer", SUPPORT)
        reviewer.guild_permissions = discord.Permissions(**discord_perms)
        guild = FakeGuild(members=[reviewer], roles=[SUPPORT, LEAD])
        guild.me.top_role = Role(99, "Pulse", 50)
        interaction = SimpleNamespace(user=reviewer, guild=guild)
        base = {k: False for k in ws.PERM_KEYS} | {"can_view_dashboard": True, "is_admin": False}
        return cog, interaction, base | perms

    def test_warn_right_alone_cannot_ban_kick_or_timeout(self):
        cog, interaction, perms = self._cog_and_interaction({"can_warn": True, "can_promote": True}, {})
        citizen = member(5, "Bürger", COSMETIC)
        with patch.object(ws, "compute_perms", lambda *a: (perms, interaction.user)), \
             patch.object(ws, "load_config", lambda: {"team_role_ids": TEAM_IDS}):
            for action in ("ban", "kick", "timeout_10m", "warn"):
                self.assertTrue(cog._action_problem(interaction, citizen, action), action)
            self.assertEqual(cog._action_problem(interaction, citizen, "team_warn"), "")

    def test_matching_rights_and_hierarchy_allow_the_sanction(self):
        cog, interaction, perms = self._cog_and_interaction({"can_moderate_bans": True}, {"ban_members": True})
        citizen = member(5, "Bürger", COSMETIC)
        higher = member(6, "Hoch", Role(30, "VIP", 40))
        with patch.object(ws, "compute_perms", lambda *a: (perms, interaction.user)), \
             patch.object(ws, "load_config", lambda: {"team_role_ids": TEAM_IDS}):
            self.assertEqual(cog._action_problem(interaction, citizen, "ban"), "")
            self.assertIn("Rollenhierarchie", cog._action_problem(interaction, higher, "ban"))
            self.assertTrue(cog._action_problem(interaction, citizen, "team_warn"))  # can_warn missing

    def test_team_warn_goes_through_issue_team_warning(self):
        cog, interaction, perms = self._cog_and_interaction({"can_warn": True}, {})
        target = member(7, "Kollege", SUPPORT)
        refused = {"ok": False, "stored": False, "message": "Du darfst keine Team-Warnung an ein Mitglied mit gleichem"}
        with patch.object(ws, "compute_perms", lambda *a: (perms, interaction.user)), \
             patch.object(ws, "load_config", lambda: {"team_role_ids": TEAM_IDS}), \
             patch.object(ws, "issue_team_warning", AsyncMock(return_value=refused)) as issued, \
             patch.object(ws, "save_json") as saved:
            interaction.user.guild = interaction.guild
            result = asyncio.run(cog._issue_team_warn(interaction.guild, target, interaction.user,
                                                      {"id": "K1", "rule": "R", "reason": "x", "message_url": ""}))
        self.assertFalse(result["ok"])
        self.assertEqual(issued.await_args.kwargs["actor_member"], interaction.user)
        saved.assert_not_called()


# ---------------------------------------------------------------------------------------------------------
# SEC-10 – /pulse-approve honours the emergency lock and requires kick_members for a real server kick.
# ---------------------------------------------------------------------------------------------------------
class SecondApprovalTests(TempDb):
    def setUp(self):
        super().setUp()
        from cogs import pulse_suite
        self.suite = pulse_suite
        self.dbp = patch.object(pulse_suite, "DB_PATH", str(Path(self.tmp.name) / "suite.db"))
        self.dbp.start()
        pulse_suite.init_db()
        self.approver = member(2, "Freigeber", LEAD)
        self.approver.guild_permissions = discord.Permissions()  # no kick_members
        self.target = member(3, "Ziel", SUPPORT)
        self.target.kick = AsyncMock()
        self.guild = FakeGuild(members=[self.approver, self.target], roles=[SUPPORT, LEAD])
        self.guild.me.top_role = Role(99, "Pulse", 50)
        self.guild.me.guild_permissions = discord.Permissions(kick_members=True, manage_roles=True)
        self.sent = []
        self.interaction = SimpleNamespace(
            guild=self.guild, user=self.approver,
            response=SimpleNamespace(send_message=AsyncMock(side_effect=lambda m, **k: self.sent.append(m)),
                                     defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()))
        creator = SimpleNamespace(id=1, display_name="Ersteller")
        self.aid = pulse_suite.action_record("server-exit", {"target_id": self.target.id, "reason": "Grund"}, creator)
        perms = {k: False for k in ws.PERM_KEYS} | {"can_view_dashboard": True, "can_promote": True, "is_admin": False}
        self.patches = [patch.object(ws, "compute_perms", lambda *a: (perms, self.approver)),
                        patch.object(ws, "load_config", lambda: {"team_role_ids": TEAM_IDS}),
                        patch.object(pulse_suite.discord, "Member", FakeMember)]
        for p in self.patches:
            p.start()
        ws.pulse_db.init_db()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.dbp.stop()
        super().tearDown()

    def _approve(self):
        cog = self.suite.PulseSuite.__new__(self.suite.PulseSuite)
        asyncio.run(self.suite.PulseSuite.approve_cmd.callback(cog, self.interaction, self.aid))
        with self.suite.cx() as c:
            return c.execute("SELECT status FROM suite_actions WHERE id=?", (self.aid,)).fetchone()[0]

    def test_server_kick_needs_discord_kick_permission(self):
        self.assertEqual(self._approve(), "awaiting_second")
        self.target.kick.assert_not_awaited()
        self.assertIn("Mitglieder kicken", self.sent[-1])

    def test_emergency_lock_blocks_the_second_approval(self):
        self.approver.guild_permissions = discord.Permissions(kick_members=True)
        self.suite.set_setting("emergency_lock", True)
        self.assertEqual(self._approve(), "awaiting_second")
        self.target.kick.assert_not_awaited()
        self.assertIn("Emergency Lock", self.sent[-1])
