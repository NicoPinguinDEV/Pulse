"""Verification via Discord OAuth (identify + guilds.join) and the hourly auto-rejoin job.

Every Discord call goes to an httpx.MockTransport or a fake guild – nothing reaches Discord.
"""
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import discord
import httpx
from fastapi.testclient import TestClient

import pulse_db
import pulse_rejoin as rj
import pulse_verification as v
import webserver as ws
from cogs import rejoin as rejoin_cog
from cogs import verification as verification_cog
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for

UID = "123456789012345678"
UID2 = "223456789012345678"
GUILD_ID = int(ws.GUILD_ID)
NOW = 2_000_000_000.0
TOKENS = {"access_token": "access-AAA", "refresh_token": "refresh-RRR", "expires_in": 604800, "scope": "identify guilds.join"}


def not_found():
    return discord.NotFound(SimpleNamespace(status=404, reason="x"), "Unknown Ban")


class FakeGuild:
    def __init__(self, members=(), bans=(), ban_check_fails=False):
        self.id = GUILD_ID
        self.members = {int(m.id): m for m in members}
        self.bans = {int(b) for b in bans}
        self.ban_check_fails = ban_check_fails
        self.ban_lookups = []

    def get_member(self, uid):
        return self.members.get(int(uid))

    async def fetch_member(self, uid):
        member = self.members.get(int(uid))
        if member is None:
            raise not_found()
        return member

    async def fetch_ban(self, user):
        self.ban_lookups.append(user.id)
        if self.ban_check_fails:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="x"), "Missing Permissions")
        if user.id in self.bans:
            return SimpleNamespace(user=user)
        raise not_found()


class FakeBot:
    def __init__(self, guild, cog=None):
        self.guild = guild
        self.cog = cog

    def get_guild(self, gid):
        return self.guild if self.guild and gid == self.guild.id else None

    def get_cog(self, name):
        return self.cog if name == "Verification" else None


class Discord:
    """Programmable fake of the Discord HTTP API; records every request."""

    def __init__(self, *, refresh=(200, TOKENS), put=(201, None), exchange=(200, TOKENS), me=(200, {"id": UID})):
        self.refresh, self.put, self.exchange, self.me = refresh, put, exchange, me
        self.requests = []

    def handler(self, request: httpx.Request):
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/oauth2/token/revoke"):
            return httpx.Response(200)
        if path.endswith("/oauth2/token"):
            form = parse_qs(request.content.decode())
            status, body = self.exchange if form["grant_type"] == ["authorization_code"] else self.refresh
            if isinstance(status, Exception):
                raise status
            return httpx.Response(status, json=body or {})
        if path.endswith("/users/@me"):
            return httpx.Response(self.me[0], json=self.me[1])
        if request.method == "PUT" and "/members/" in path:
            status, body = self.put
            if isinstance(status, Exception):
                raise status
            return httpx.Response(status, json=body) if body is not None else httpx.Response(status)
        return httpx.Response(404, json={})

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def puts(self):
        return [r for r in self.requests if r.method == "PUT"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        self.patches = [
            patch.object(pulse_db, "DB_PATH", str(base / "pulse.db")),
            patch.object(ws, "SESSION_KEY", b"k" * 32),
            patch.object(ws, "AUDIT_FILE", str(base / "audit_logs.json")),
            patch.object(ws, "CLIENT_ID", "999"),
            patch.object(ws, "CLIENT_SECRET", "test-secret"),
            patch.object(ws, "PUBLIC_BASE_URL", "https://pulse.test"),
            patch.dict("os.environ", {"DISCORD_TOKEN": "bot-token-for-tests"}),
            patch("builtins.print"),
        ]
        for p in self.patches:
            p.start()
        pulse_db.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def enable(self):
        rj.save_settings({"enabled": True})

    def run_job(self, fake, guild, now=NOW, limit=rj.MAX_PER_RUN):
        async def go():
            async with fake.client() as client:
                return await rj.run_rejoin(FakeBot(guild), client, now=now, limit=limit)
        return asyncio.run(go())

    def left(self, uid=UID, *, hours_ago=25, reason="leave", tokens=TOKENS):
        rj.store_tokens(uid, tokens, now=NOW - 30 * 86400, consent=True)
        rj.mark_left(uid, reason, now=NOW - hours_ago * 3600)


class SettingsAndStorageTests(Base):
    def test_feature_is_off_by_default_and_requires_no_email_scope(self):
        self.assertFalse(rj.load_settings()["enabled"])
        self.assertFalse(rj.is_enabled())
        self.assertEqual(rj.SCOPES, ("identify", "guilds.join"))
        self.assertTrue(rj.save_settings({"enabled": "1"})["enabled"])

    def test_tokens_are_encrypted_at_rest_and_never_exported(self):
        rj.store_tokens(UID, TOKENS, now=NOW, consent=True)
        with pulse_db.connect() as db:
            raw = db.execute("SELECT access_token_enc, refresh_token_enc FROM rejoin_oauth_tokens").fetchone()
        self.assertNotIn("access-AAA", raw[0])
        self.assertNotIn("refresh-RRR", raw[1])
        entry = rj.get_entry(UID)
        self.assertEqual((entry["access_token"], entry["refresh_token"]), ("access-AAA", "refresh-RRR"))
        self.assertEqual(entry["access_expires_at"], NOW + 604800)
        exported = json.dumps(rj.export_person(UID))
        self.assertNotIn("access-AAA", exported)
        self.assertNotIn("refresh-RRR", exported)

    def test_invalid_input_is_rejected(self):
        with self.assertRaises(ValueError):
            rj.store_tokens("123", TOKENS)
        with self.assertRaises(ValueError):
            rj.store_tokens(UID, {"access_token": "a"})

    def test_fresh_consent_resets_left_state_and_table_creation_is_idempotent(self):
        self.left(hours_ago=30)
        rj.store_tokens(UID, TOKENS, now=NOW, consent=True)
        rj.store_tokens(UID, TOKENS, now=NOW, consent=True)
        entry = rj.get_entry(UID)
        self.assertIsNone(entry["left_at"])
        self.assertEqual(rj.stats()["linked"], 1)

    def test_unreadable_key_does_not_crash(self):
        rj.store_tokens(UID, TOKENS, now=NOW, consent=True)
        with patch.object(ws, "SESSION_KEY", b"x" * 32):
            self.assertIsNone(rj.get_entry(UID)["refresh_token"])

    def test_forget_revokes_at_discord_and_deletes(self):
        rj.store_tokens(UID, TOKENS, now=NOW, consent=True)
        fake = Discord()

        async def go():
            async with fake.client() as client:
                return await rj.forget(UID, client)
        self.assertTrue(asyncio.run(go()))
        self.assertIsNone(rj.get_entry(UID))
        self.assertTrue(any(r.url.path.endswith("/oauth2/token/revoke") for r in fake.requests))
        self.assertFalse(asyncio.run(go()))


class OAuthVerificationTests(Base):
    def setUp(self):
        super().setUp()
        self.enable()
        self.member = SimpleNamespace(id=int(UID), roles=[])
        self.cog = SimpleNamespace(calls=[])

        async def apply_verification(guild, member):
            self.cog.calls.append(member.id)
            return "verified"
        self.cog.apply_verification = apply_verification

    def complete(self, fake, guild=None):
        guild = guild if guild is not None else FakeGuild([self.member])

        async def go():
            async with fake.client() as client:
                return await rj.complete_verification("code-1", "https://pulse.test/callback", FakeBot(guild, self.cog), client)
        return asyncio.run(go())

    def test_successful_authorization_stores_tokens_and_verifies(self):
        fake = Discord()
        ok, message = self.complete(fake)
        self.assertTrue(ok, message)
        self.assertEqual(self.cog.calls, [int(UID)])
        self.assertEqual(rj.get_entry(UID)["refresh_token"], "refresh-RRR")
        exchange = parse_qs(fake.requests[0].content.decode())
        self.assertEqual(exchange["grant_type"], ["authorization_code"])
        self.assertEqual(exchange["redirect_uri"], ["https://pulse.test/callback"])

    def test_missing_guilds_join_scope_is_rejected(self):
        ok, message = self.complete(Discord(exchange=(200, dict(TOKENS, scope="identify"))))
        self.assertFalse(ok)
        self.assertIn("Server für dich beitreten", message)
        self.assertIsNone(rj.get_entry(UID))
        self.assertEqual(self.cog.calls, [])

    def test_denied_code_and_non_members_get_no_role(self):
        ok, _ = self.complete(Discord(exchange=(400, {"error": "invalid_grant"})))
        self.assertFalse(ok)
        ok, message = self.complete(Discord(), guild=FakeGuild([]))
        self.assertFalse(ok)
        self.assertIn("nicht Mitglied", message)
        self.assertEqual(self.cog.calls, [])
        self.assertIsNone(rj.get_entry(UID))

    def test_disabled_feature_does_nothing(self):
        rj.save_settings({"enabled": False})
        fake = Discord()
        ok, _ = self.complete(fake)
        self.assertFalse(ok)
        self.assertEqual(fake.requests, [])


class RejoinJobTests(Base):
    def setUp(self):
        super().setUp()
        self.enable()

    def test_grace_period_of_24_hours(self):
        self.left(hours_ago=23)
        fake = Discord()
        report = self.run_job(fake, FakeGuild())
        self.assertEqual(fake.puts(), [])
        self.assertEqual(report["added"], 0)
        report = self.run_job(fake, FakeGuild(), now=NOW + 2 * 3600)
        self.assertEqual(report["added"], 1)

    def test_rejoin_uses_put_with_stored_access_token_and_citizen_role(self):
        self.left()
        fake = Discord()
        report = self.run_job(fake, FakeGuild())
        self.assertEqual(report["added"], 1)
        put = fake.puts()[0]
        self.assertEqual(put.url.path, f"/api/v10/guilds/{GUILD_ID}/members/{UID}")
        self.assertEqual(put.headers["Authorization"], "Bot bot-token-for-tests")
        self.assertEqual(json.loads(put.content), {"access_token": "access-AAA", "roles": [str(v.CITIZEN_ROLE_ID)]})
        entry = rj.get_entry(UID)
        self.assertIsNone(entry["left_at"])
        self.assertEqual(entry["rejoin_count"], 1)
        self.assertEqual(self.run_job(fake, FakeGuild())["added"], 0)  # nobody due any more

    def test_banned_members_are_never_re_added(self):
        self.left()
        fake = Discord()
        guild = FakeGuild(bans=[UID])
        report = self.run_job(fake, guild)
        self.assertEqual(report["banned"], 1)
        self.assertEqual(fake.puts(), [])
        self.assertEqual(rj.get_entry(UID)["left_reason"], "ban")
        self.run_job(fake, guild, now=NOW + 5 * 86400)
        self.assertEqual(fake.puts(), [])

    def test_unverifiable_ban_status_blocks_rejoin(self):
        self.left()
        fake = Discord()
        report = self.run_job(fake, FakeGuild(ban_check_fails=True))
        self.assertEqual(fake.puts(), [])
        self.assertEqual(report["failed"], 1)

    def test_kicked_or_unknown_leavers_are_not_due(self):
        self.left(UID, reason="kick")
        self.left(UID2, reason="unknown")
        fake = Discord()
        self.run_job(fake, FakeGuild())
        self.assertEqual(fake.requests, [])

    def test_revoked_authorization_deletes_tokens_and_is_not_retried(self):
        self.left(tokens=dict(TOKENS, expires_in=0))  # access token expired → refresh needed
        fake = Discord(refresh=(400, {"error": "invalid_grant"}))
        report = self.run_job(fake, FakeGuild())
        self.assertEqual(report["revoked"], [UID])
        self.assertIsNone(rj.get_entry(UID))
        self.assertEqual(fake.puts(), [])
        count = len(fake.requests)
        self.run_job(fake, FakeGuild(), now=NOW + 7200)
        self.assertEqual(len(fake.requests), count)

    def test_put_401_deletes_tokens(self):
        self.left()
        report = self.run_job(Discord(put=(401, {"message": "401: Unauthorized"})), FakeGuild())
        self.assertEqual(report["revoked"], [UID])
        self.assertIsNone(rj.get_entry(UID))

    def test_refresh_rotates_and_stores_new_tokens(self):
        self.left(tokens=dict(TOKENS, expires_in=0))
        rotated = dict(TOKENS, access_token="access-NEW", refresh_token="refresh-NEW")
        fake = Discord(refresh=(200, rotated))
        self.assertEqual(self.run_job(fake, FakeGuild())["added"], 1)
        self.assertEqual(json.loads(fake.puts()[0].content)["access_token"], "access-NEW")
        self.assertEqual(rj.get_entry(UID)["refresh_token"], "refresh-NEW")

    def test_429_stops_the_run_without_retry_spam(self):
        self.left(UID)
        self.left(UID2)
        fake = Discord(put=(429, {"retry_after": 30}))
        report = self.run_job(fake, FakeGuild())
        self.assertIn("429", report["stopped"])
        self.assertEqual(len(fake.puts()), 1)

    def test_403_and_404_count_failures_and_give_up(self):
        self.left()
        fake = Discord(put=(403, {"message": "Missing Permissions"}))
        for hour in range(rj.MAX_FAILURES + 2):
            self.run_job(fake, FakeGuild(), now=NOW + hour * 3600)
        self.assertEqual(len(fake.puts()), rj.MAX_FAILURES)
        self.assertEqual(rj.stats()["given_up"], 1)
        self.left(UID2)
        report = self.run_job(Discord(put=(404, {})), FakeGuild())
        self.assertEqual(report["failed"], 1)
        self.assertIn("404", rj.get_entry(UID2)["last_status"])

    def test_retry_waits_one_hour(self):
        self.left()
        fake = Discord(put=(500, {}))
        self.run_job(fake, FakeGuild())
        self.run_job(fake, FakeGuild(), now=NOW + 600)
        self.assertEqual(len(fake.puts()), 1)

    def test_timeouts_stop_the_run(self):
        for i in range(5):
            self.left(str(int(UID) + i))
        fake = Discord(put=(httpx.ReadTimeout("timeout"), None))
        report = self.run_job(fake, FakeGuild())
        self.assertEqual(report["stopped"], "Discord nicht erreichbar")
        self.assertEqual(len(fake.puts()), rj.MAX_TRANSPORT_ERRORS)

    def test_run_is_bounded(self):
        for i in range(rj.MAX_PER_RUN + 5):
            self.left(str(int(UID) + i))
        fake = Discord()
        self.assertEqual(self.run_job(fake, FakeGuild())["added"], rj.MAX_PER_RUN)

    def test_present_member_is_not_put(self):
        self.left()
        fake = Discord()
        report = self.run_job(fake, FakeGuild([SimpleNamespace(id=int(UID))]))
        self.assertEqual(report["present"], 1)
        self.assertEqual(fake.requests, [])

    def test_disabled_job_does_nothing(self):
        rj.save_settings({"enabled": False})
        self.left()
        fake = Discord()
        self.assertFalse(self.run_job(fake, FakeGuild())["ran"])
        self.assertEqual(fake.requests, [])


class CogTests(Base):
    def test_leave_reason_detects_kick_and_unknown(self):
        now = datetime.now(timezone.utc)

        def guild(entries, audit=True):
            async def audit_logs(limit, action):
                for entry in entries:
                    yield entry
            me = SimpleNamespace(guild_permissions=SimpleNamespace(view_audit_log=audit))
            return SimpleNamespace(me=me, audit_logs=audit_logs)
        kick = SimpleNamespace(target=SimpleNamespace(id=int(UID)), created_at=now - timedelta(seconds=5))
        old = SimpleNamespace(target=SimpleNamespace(id=int(UID)), created_at=now - timedelta(minutes=10))
        self.assertEqual(asyncio.run(rejoin_cog.leave_reason(guild([kick]), int(UID), now=now)), "kick")
        self.assertEqual(asyncio.run(rejoin_cog.leave_reason(guild([old]), int(UID), now=now)), "leave")
        self.assertEqual(asyncio.run(rejoin_cog.leave_reason(guild([], audit=False), int(UID), now=now)), "unknown")

    def test_job_run_writes_audit_entries(self):
        self.enable()
        self.left(tokens=dict(TOKENS, expires_in=0))
        cog = rejoin_cog.Rejoin(SimpleNamespace(get_guild=FakeBot(FakeGuild()).get_guild))
        fake = Discord(refresh=(400, {"error": "invalid_grant"}))

        async def go():
            async with fake.client() as client:
                return await cog.run_once(client)
        with patch.object(rj, "time") as fake_time:
            fake_time.time.return_value = NOW
            asyncio.run(go())
        actions = [e["action"] for e in ws.load_json(ws.AUDIT_FILE, [])]
        self.assertIn("Rejoin-Autorisierung entfernt", actions)


class VerifyButtonTests(Base):
    """With the feature on, the button sends the consent text and link instead of the citizen role."""

    def setUp(self):
        super().setUp()
        pulse_db.set_setting(v.SETTING_KEY, {"enabled": True, "channel_id": "111111111111111111"})
        with patch.object(verification_cog, "_guild_id", return_value=GUILD_ID):
            self.cog = verification_cog.Verification(SimpleNamespace(add_view=lambda *a, **k: None))
        roles = {v.UNVERIFIED_ROLE_ID: SimpleNamespace(id=v.UNVERIFIED_ROLE_ID, position=5),
                 v.CITIZEN_ROLE_ID: SimpleNamespace(id=v.CITIZEN_ROLE_ID, position=6)}
        self.guild = SimpleNamespace(id=GUILD_ID, get_role=roles.get,
                                     me=SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=True), top_role=SimpleNamespace(position=99)))
        self.added = []

        async def add_roles(*r, reason=None):
            self.added.extend(x.id for x in r)

        async def remove_roles(*r, reason=None):
            pass
        self.member = SimpleNamespace(id=int(UID), roles=[roles[v.UNVERIFIED_ROLE_ID]], add_roles=add_roles, remove_roles=remove_roles)
        self.sent = []

        async def send_message(text, ephemeral=False, view=None):
            self.sent.append((text, view))

        async def defer(ephemeral=False, thinking=False):
            pass
        self.interaction = SimpleNamespace(guild=self.guild, user=self.member,
                                           response=SimpleNamespace(is_done=lambda: False, send_message=send_message, defer=defer),
                                           followup=SimpleNamespace(send=send_message))

    def test_enabled_feature_requires_authorization_first(self):
        rj.save_settings({"enabled": True})
        status = asyncio.run(self.cog.handle_click(self.interaction))
        self.assertEqual(status, "authorize")
        self.assertEqual(self.added, [])
        text, view = self.sent[0]
        self.assertIn("Server für dich beitreten", text)
        self.assertIn("/rejoin-abmelden", text)
        self.assertEqual(view.children[0].url, "https://pulse.test/verify/authorize")

    def test_authorized_member_or_disabled_feature_verifies_directly(self):
        status = asyncio.run(self.cog.handle_click(self.interaction))  # feature off: unchanged behaviour
        self.assertEqual(status, "verified")
        self.assertEqual(self.added, [v.CITIZEN_ROLE_ID])


class RouteTests(Base):
    def setUp(self):
        super().setUp()
        self.client = TestClient(ws.app)

    def test_consent_page_and_start_only_when_enabled(self):
        self.assertEqual(self.client.get("/verify/authorize").status_code, 404)
        self.enable()
        page = self.client.get("/verify/authorize")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Server für dich beitreten", page.text)
        no_consent = self.client.post("/verify/authorize/start", data={}, follow_redirects=False)
        self.assertEqual(no_consent.headers["location"], "/verify/authorize")
        with patch.object(ws, "REDIRECT_URI", "https://pulse.test/callback"):
            started = self.client.post("/verify/authorize/start", data={"consent": "1"}, follow_redirects=False)
        self.assertEqual(started.status_code, 303)
        query = parse_qs(urlparse(started.headers["location"]).query)
        self.assertEqual(query["scope"], ["identify guilds.join"])
        self.assertNotIn("email", started.headers["location"])
        self.assertIn("verify_oauth_state", started.headers.get("set-cookie", ""))

    def test_csp_allows_the_redirect_to_discord_after_the_form_post(self):
        """Regression: form-action 'self' made browsers drop the 303 to Discord – the click did nothing."""
        self.enable()
        with patch.object(ws, "REDIRECT_URI", "https://pulse.test/callback"):
            started = self.client.post("/verify/authorize/start", data={"consent": "1"}, follow_redirects=False)
        target = urlparse(started.headers["location"])
        directives = dict(part.strip().split(" ", 1) for part in started.headers["content-security-policy"].split(";"))
        sources = directives["form-action"].split()
        self.assertIn(f"{target.scheme}://{target.netloc}", sources)
        self.assertEqual(sorted(sources), sorted(["'self'", "https://discord.com"]))  # nothing broader

    def test_callback_with_valid_state_runs_verification(self):
        self.enable()
        calls = []

        async def complete(code, redirect_uri, bot):
            calls.append(code)
            return True, "Verifizierung abgeschlossen"
        self.client.cookies.set("verify_oauth_state", "s1")
        with patch.object(rj, "complete_verification", complete):
            response = self.client.get("/callback?code=abc&state=s1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, ["abc"])
        self.client.cookies.set("verify_oauth_state", "s1")
        with patch.object(rj, "complete_verification", complete):
            self.client.get("/callback?code=abc&state=wrong")
        self.assertEqual(calls, ["abc"])

    def test_admin_settings_are_server_side_authorized_and_audited(self):
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            self.assertEqual(self.client.post("/admin/rejoin/save", data={"enabled": "1"}).status_code, 403)
            self.assertEqual(self.client.post("/admin/rejoin/forget", data={"user_id": UID}).status_code, 403)
        self.assertFalse(rj.is_enabled())
        with patch.object(ws, "auth", fake_auth_for(ADMIN)):
            self.client.post("/admin/rejoin/save", data={"enabled": "1"})
            self.assertTrue(rj.is_enabled())
            rj.store_tokens(UID, TOKENS, consent=True)
            page = self.client.get("/admin/privacy")
            self.assertIn(UID, page.text)
            self.assertNotIn("refresh-RRR", page.text)
            with patch.object(rj, "revoke_token", lambda *a, **k: asyncio.sleep(0)):
                self.client.post("/admin/rejoin/forget", data={"user_id": UID})
        self.assertIsNone(rj.get_entry(UID))
        actions = [e["action"] for e in ws.load_json(ws.AUDIT_FILE, [])]
        self.assertIn("Verifizierung per Autorisierung/Auto-Rejoin aktiviert", actions)
        self.assertIn("Rejoin-Autorisierung gelöscht", actions)

    def test_enabling_is_refused_without_https_base_url(self):
        with patch.object(ws, "PUBLIC_BASE_URL", "http://pulse.test"), patch.object(ws, "auth", fake_auth_for(ADMIN)):
            self.client.post("/admin/rejoin/save", data={"enabled": "1"})
        self.assertFalse(rj.is_enabled())


if __name__ == "__main__":
    unittest.main()
