"""U-14: server-side sessions (list, sign out everywhere, idle timeout, team exit) and API key scopes/expiry/rate limit."""
import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import pulse_sessions
import pulse_ultimate as pu
import webserver as ws
from tests.test_pulse_admin_area import TEAMLER, fake_auth_for


class TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()
        pu._INIT = False
        ws._rate_hits.clear()

    def tearDown(self):
        self.db.stop()
        pu._INIT = False
        self.tmp.cleanup()


def token(uid="7", sid=None, iat=None):
    data = {"id": uid, "exp": int(time.time() + 600)}
    if sid:
        data["sid"] = sid
    signed = ws.sign_payload(data, typ=ws.TOKEN_DASHBOARD)
    payload = ws.verify_session_token(signed, ws.TOKEN_DASHBOARD)
    if iat is not None:
        payload["iat"] = iat
    return signed, payload


class SessionTests(TempDb):
    def test_device_label(self):
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
        self.assertEqual(pulse_sessions.device_label(ua), "Chrome · Windows")
        self.assertEqual(pulse_sessions.device_label(""), "Unbekanntes Gerät")

    def test_lifecycle_and_old_cookies(self):
        sid = pulse_sessions.create("7", "Firefox Linux")
        self.assertTrue(pulse_sessions.is_valid(token(sid=sid)[1]))
        self.assertTrue(pulse_sessions.is_valid(token()[1]))  # cookie from before U-14 stays valid
        self.assertFalse(pulse_sessions.is_valid(token(sid="unknown")[1]))
        self.assertFalse(pulse_sessions.is_valid(token(uid="8", sid=sid)[1]))  # sid belongs to someone else
        self.assertEqual([s["device"] for s in pulse_sessions.list_for("7")], ["Firefox · Linux"])
        self.assertTrue(pulse_sessions.revoke("7", sid))
        self.assertFalse(pulse_sessions.is_valid(token(sid=sid)[1]))

    def test_idle_timeout(self):
        sid = pulse_sessions.create("7", now=time.time() - pulse_sessions.IDLE_SECONDS - 10)
        self.assertFalse(pulse_sessions.is_valid(token(sid=sid)[1]))
        self.assertEqual(pulse_sessions.list_for("7"), [])

    def test_revoke_all_also_ends_old_cookies_keep_current(self):
        a, b = pulse_sessions.create("7"), pulse_sessions.create("7")
        self.assertEqual(pulse_sessions.revoke_all("7", keep_sid=a), 1)
        self.assertTrue(pulse_sessions.is_valid(token(sid=a)[1]))
        self.assertFalse(pulse_sessions.is_valid(token(sid=b)[1]))
        old = token(iat=int(time.time()) - 5)[1]
        pulse_sessions.revoke_all("7")
        self.assertFalse(pulse_sessions.is_valid(old))
        self.assertFalse(pulse_sessions.is_valid(token(sid=a)[1]))

    def test_logout_and_cookie_check(self):
        sid = pulse_sessions.create("7")
        signed, _ = token(sid=sid)
        self.assertEqual(ws.get_current_user(signed)["id"], "7")
        client = TestClient(ws.app, raise_server_exceptions=False)
        client.cookies.set("user_session", signed)
        client.post("/logout", follow_redirects=False)
        with self.assertRaises(ws.HTTPException):
            ws.get_current_user(signed)  # the copied cookie is useless after logout

    def test_team_exit_revokes(self):
        sid = pulse_sessions.create("7")
        signed, _ = token(sid=sid)
        member = SimpleNamespace(id=7, roles=[])
        guild = SimpleNamespace(id=1, name="G", chunked=True, get_member=lambda _: member)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=SimpleNamespace(get_guild=lambda _: guild))),
                                  query_params={})
        with patch.object(ws, "compute_perms", lambda *a: ({"can_view_dashboard": False, "is_admin": False}, member)):
            with self.assertRaises(ws.HTTPException) as denied:
                ws.auth(request, signed, perm=None)
        self.assertEqual(denied.exception.status_code, 403)
        self.assertFalse(pulse_sessions.is_valid(token(sid=sid)[1]))

    def test_missing_member_in_unchunked_cache_does_not_revoke(self):
        sid = pulse_sessions.create("7")
        signed, _ = token(sid=sid)
        guild = SimpleNamespace(id=1, name="G", chunked=False)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=SimpleNamespace(get_guild=lambda _: guild))),
                                  query_params={})
        with patch.object(ws, "compute_perms", lambda *a: ({"can_view_dashboard": False, "is_admin": False}, None)):
            with self.assertRaises(ws.HTTPException):
                ws.auth(request, signed, perm=None)
        self.assertTrue(pulse_sessions.is_valid(token(sid=sid)[1]))

    def test_sessions_page(self):
        sid = pulse_sessions.create("123", "Chrome Windows")
        pulse_sessions.create("123", "Safari iPhone")
        client = TestClient(ws.app, raise_server_exceptions=False)
        client.cookies.set("user_session", token(uid="123", sid=sid)[0])
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            page = client.get("/me/sessions")
            self.assertEqual(page.status_code, 200)
            self.assertIn("dieses Gerät", page.text)
            self.assertIn("Safari · iPhone", page.text)
            done = client.post("/me/sessions/revoke-others", follow_redirects=False)
            self.assertIn("t=ok", done.headers["location"])
        self.assertEqual(len(pulse_sessions.list_for("123")), 1)


class ApiKeyTests(TempDb):
    def add_key(self, raw, scopes=None, expires=None):
        pu.setup()
        with pu.cx() as c:
            c.execute("INSERT INTO ultimate_api_keys(id,name,token_hash,created_by_id,created_at,revoked_at,scopes,expires_at)"
                      " VALUES(?,?,?,?,?,?,?,?)", (pu.uid("key"), "t", hashlib.sha256(raw.encode()).hexdigest(), "1", pu.iso(), None,
                                                   scopes, expires))

    def test_old_keys_keep_full_access(self):
        self.add_key("old")
        self.assertEqual(pu.check_api_key("old", "team"), 200)
        self.assertTrue(pu.validate_api_key("old"))

    def test_scope_expiry_and_last_used(self):
        self.add_key("scoped", scopes="overview")
        self.assertEqual(pu.check_api_key("scoped", "overview"), 200)
        self.assertEqual(pu.check_api_key("scoped", "team"), 403)
        with pu.cx() as c:
            self.assertIsNotNone(c.execute("SELECT last_used_at FROM ultimate_api_keys").fetchone()[0])
        self.add_key("expired", expires="2000-01-01T00:00:00+00:00")
        self.assertEqual(pu.check_api_key("expired"), 401)
        self.assertEqual(pu.check_api_key("nope"), 401)

    def test_rate_limit(self):
        self.add_key("busy")
        limit = pu.API_RATE_LIMIT[0]
        results = [pu.check_api_key("busy") for _ in range(limit + 1)]
        self.assertEqual(results[-1], 429)
        self.assertEqual(results[0], 200)

    def test_endpoint_reports_scope_error(self):
        self.add_key("scoped", scopes="overview")
        response = TestClient(ws.app, raise_server_exceptions=False).get("/ultimate/api/v2/team", headers={"X-Pulse-API-Key": "scoped"})
        self.assertEqual(response.status_code, 403)

    def test_existing_table_gets_new_columns(self):
        with pu.cx() as c:
            c.execute("CREATE TABLE ultimate_api_keys(id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,"
                      " created_by_id TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT)")
            c.execute("INSERT INTO ultimate_api_keys VALUES('k','alt',?,'1','2026-01-01',NULL)", (hashlib.sha256(b"legacy").hexdigest(),))
        pu.setup()
        self.assertEqual(pu.check_api_key("legacy", "team"), 200)  # data kept, old key still works


if __name__ == "__main__":
    unittest.main()
