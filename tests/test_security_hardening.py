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
