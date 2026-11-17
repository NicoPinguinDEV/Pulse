"""AUTH-6 / AUTH-7 regression tests: OAuth codes never reach the access log, the session secret file is created
exclusively with owner-only permissions."""
import logging
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_logging
import webserver as ws


def access_record(path, status=303):
    return logging.LogRecord("uvicorn.access", 20, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                             ("203.0.113.5:1234", "GET", path, "1.1", status), None)


class AccessLogMaskingTests(unittest.TestCase):
    def test_oauth_code_and_state_are_masked_in_the_request_line(self):
        record = access_record("/callback?code=SECRETCODE123&state=STATEVALUE456")
        self.assertTrue(pulse_logging.SensitiveQueryAccessFilter().filter(record))
        rendered = record.getMessage()
        self.assertNotIn("SECRETCODE123", rendered)
        self.assertNotIn("STATEVALUE456", rendered)
        self.assertIn("/callback?code=***&state=***", rendered)

    def test_apply_callback_error_and_other_parameters_stay_readable(self):
        record = access_record("/apply/callback?error=access_denied&state=ABC&page=2")
        pulse_logging.SensitiveQueryAccessFilter().filter(record)
        rendered = record.getMessage()
        self.assertIn("error=access_denied", rendered)
        self.assertIn("page=2", rendered)
        self.assertNotIn("state=ABC", rendered)

    def test_token_like_parameters_and_case_variants_are_masked(self):
        record = access_record("/x?Access_Token=abc&REFRESH_TOKEN=def&ok=1")
        pulse_logging.SensitiveQueryAccessFilter().filter(record)
        rendered = record.getMessage()
        self.assertNotIn("abc", rendered)
        self.assertNotIn("def", rendered)
        self.assertIn("ok=1", rendered)

    def test_lines_without_query_are_untouched_and_never_dropped(self):
        record = access_record("/dashboard", 200)
        self.assertTrue(pulse_logging.SensitiveQueryAccessFilter().filter(record))
        self.assertIn("/dashboard", record.getMessage())

    def test_filter_is_installed_once_alongside_the_healthcheck_filter(self):
        access = logging.getLogger("uvicorn.access")
        before = list(access.filters)
        try:
            pulse_logging.quiet_healthcheck_access_log()
            pulse_logging.quiet_healthcheck_access_log()
            self.assertEqual(sum(isinstance(f, pulse_logging.SensitiveQueryAccessFilter) for f in access.filters), 1)
        finally:
            access.filters[:] = before


class SessionSecretFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.secret_file = str(Path(self.tmp.name) / ".session_secret")
        patches = [patch.object(ws, "SECRET_FILE", self.secret_file), patch("builtins.print"),
                   patch.dict(os.environ, {"SESSION_SECRET": ""})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    @unittest.skipIf(sys.platform == "win32", "POSIX file modes")
    def test_file_is_created_owner_only_from_the_start(self):
        seen = {}
        real_open = os.open

        def spying_open(path, flags, mode=0o777, *a, **k):
            if str(path) == self.secret_file:
                seen["flags"], seen["mode"] = flags, mode
            return real_open(path, flags, mode, *a, **k)

        with patch.object(os, "open", spying_open):
            key = ws._load_secret()
        self.assertEqual(seen["mode"], 0o600)
        self.assertTrue(seen["flags"] & os.O_EXCL)
        self.assertEqual(stat.S_IMODE(os.stat(self.secret_file).st_mode), 0o600)
        self.assertEqual(Path(self.secret_file).read_bytes().strip(), key)

    def test_creation_is_exclusive_and_second_load_reuses_the_key(self):
        first = ws._load_secret()
        second = ws._load_secret()
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 32)

    def test_existing_file_created_in_the_meantime_is_used_not_overwritten(self):
        """Another process created the file between the exists-check and the exclusive create."""
        real_exists = os.path.exists
        Path(self.secret_file).write_bytes(b"k" * 64 + b"\n")
        with patch.object(os.path, "exists", lambda p: False if str(p) == self.secret_file else real_exists(p)):
            key = ws._load_secret()
        self.assertEqual(key, b"k" * 64)
        self.assertEqual(Path(self.secret_file).read_bytes().strip(), b"k" * 64)



class ApplyLogoutTests(unittest.TestCase):
    """AUTH-5: applicants can end the (stateless) application session; the dashboard logout ends it too."""

    def setUp(self):
        from fastapi.testclient import TestClient
        self.client = TestClient(ws.app, raise_server_exceptions=False, headers={"Origin": "http://testserver"},
                                 follow_redirects=False)

    def deleted(self, response, name):
        cookies = response.headers.get_list("set-cookie")
        return any(c.startswith(f"{name}=") and ("Max-Age=0" in c or "expires=" in c.lower()) for c in cookies)

    def test_apply_logout_deletes_the_application_cookie(self):
        self.client.cookies.set("apply_session", "irgendein-wert")
        response = self.client.post("/apply/logout")
        self.assertEqual(response.status_code, 303)
        self.assertTrue(self.deleted(response, "apply_session"))

    def test_apply_logout_needs_a_same_origin_post(self):
        self.assertEqual(self.client.get("/apply/logout").status_code, 405)
        cross = self.client.post("/apply/logout", headers={"Origin": "https://evil.example"})
        self.assertEqual(cross.status_code, 403)

    def test_dashboard_logout_also_ends_a_shared_device_application_session(self):
        response = self.client.post("/logout")
        self.assertTrue(self.deleted(response, "user_session"))
        self.assertTrue(self.deleted(response, "apply_session"))


if __name__ == "__main__":
    unittest.main()
