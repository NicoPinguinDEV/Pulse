"""K-03: session secret validation and typed tokens (dashboard vs. application)."""
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import webserver as ws

PLACEHOLDER = "change-me-to-a-long-random-secret"
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401  (Phase 9: no ./pulse.db)


class SecretTests(unittest.TestCase):
    def load(self, env_value):
        with tempfile.TemporaryDirectory() as tmp:
            secret_file = os.path.join(tmp, ".session_secret")
            with patch.dict(os.environ, {"SESSION_SECRET": env_value} if env_value is not None else {}, clear=False), \
                    patch.object(ws, "SECRET_FILE", secret_file), patch("builtins.print") as printed:
                if env_value is None:
                    os.environ.pop("SESSION_SECRET", None)
                key = ws._load_secret()
                problem = ws.SESSION_SECRET_PROBLEM
            return key, problem, printed

    def test_placeholder_is_never_used(self):
        key, problem, printed = self.load(PLACEHOLDER)
        self.assertNotEqual(key, PLACEHOLDER.encode())
        self.assertGreaterEqual(len(key), 32)
        self.assertIn("Platzhalter", problem)
        self.assertTrue(printed.called)

    def test_short_secret_is_rejected(self):
        key, problem, _ = self.load("kurz123")
        self.assertNotEqual(key, b"kurz123")
        self.assertIn("zu kurz", problem)

    def test_strong_secret_is_used(self):
        strong = "x" * 48
        key, problem, _ = self.load(strong)
        self.assertEqual(key, strong.encode())
        self.assertEqual(problem, "")


class SystemPageTests(unittest.TestCase):
    def test_active_system_page_shows_secret_problem(self):
        # /system is served by pulse_pro.system_v5 (the pulse_features handler is displaced).
        from fastapi.testclient import TestClient
        from tests.test_pulse_admin_area import ADMIN, fake_auth_for
        with patch.object(ws, "auth", fake_auth_for(ADMIN)), \
                patch.object(ws, "SESSION_SECRET_PROBLEM", "SESSION_SECRET ist ein Platzhalter"):
            html = TestClient(ws.app).get("/system").text
        self.assertIn("SESSION_SECRET ist ein Platzhalter", html)


class TokenTypeTests(unittest.TestCase):
    def test_dashboard_and_apply_tokens_are_not_interchangeable(self):
        now = time.time()
        dash = ws.sign_payload({"id": "1", "exp": int(now + 60), "apply_exp": int(now + 60)}, typ=ws.TOKEN_DASHBOARD)
        apply = ws.sign_payload({"id": "1", "exp": int(now + 60), "apply_exp": int(now + 60)}, typ=ws.TOKEN_APPLY)
        self.assertIsNotNone(ws.verify_session_token(dash, ws.TOKEN_DASHBOARD))
        self.assertIsNone(ws.verify_session_token(dash, ws.TOKEN_APPLY))
        self.assertIsNotNone(ws.verify_session_token(apply, ws.TOKEN_APPLY))
        self.assertIsNone(ws.verify_session_token(apply, ws.TOKEN_DASHBOARD))
        self.assertIn("iat", ws.verify_payload(dash))

    def test_existing_untyped_sessions_keep_working_until_expiry(self):
        now = time.time()
        legacy_dash = ws.sign_payload({"id": "1", "exp": int(now + 60)})
        legacy_apply = ws.sign_payload({"id": "1", "apply_exp": int(now + 60)})
        self.assertIsNotNone(ws.verify_session_token(legacy_dash, ws.TOKEN_DASHBOARD))
        self.assertIsNotNone(ws.verify_session_token(legacy_apply, ws.TOKEN_APPLY))
        self.assertIsNone(ws.verify_session_token(legacy_dash, ws.TOKEN_APPLY))
        self.assertIsNone(ws.verify_session_token(legacy_apply, ws.TOKEN_DASHBOARD))
        expired = ws.sign_payload({"id": "1", "exp": int(now - 1)}, typ=ws.TOKEN_DASHBOARD)
        self.assertIsNone(ws.verify_session_token(expired, ws.TOKEN_DASHBOARD))

    def test_get_current_user_rejects_application_token(self):
        apply = ws.sign_payload({"id": "1", "apply_exp": int(time.time() + 60), "exp": int(time.time() + 60)}, typ=ws.TOKEN_APPLY)
        with self.assertRaises(ws.HTTPException):
            ws.get_current_user(apply)


if __name__ == "__main__":
    unittest.main()
