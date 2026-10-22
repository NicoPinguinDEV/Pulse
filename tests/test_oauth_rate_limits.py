"""Regression tests for bounded, concurrent OAuth and application login routes."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient

import webserver as ws


class OAuthRateLimitTests(unittest.TestCase):
    def setUp(self):
        with ws._rate_lock:
            ws._rate_hits.clear()
            ws._rate_last_full_sweep = 0.0
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        with ws._rate_lock:
            ws._rate_hits.clear()
            ws._rate_last_full_sweep = 0.0

    def test_dashboard_login_is_rate_limited_with_retry_after(self):
        for _ in range(15):
            self.assertEqual(self.client.get("/login", follow_redirects=False).status_code, 303)
        blocked = self.client.get("/login", follow_redirects=False)
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers["Retry-After"], "60")

    def test_application_login_and_callback_are_rate_limited(self):
        for _ in range(15):
            self.assertEqual(self.client.get("/apply/login", follow_redirects=False).status_code, 303)
        self.assertEqual(self.client.get("/apply/login", follow_redirects=False).status_code, 429)

        for _ in range(30):
            self.assertEqual(self.client.get("/apply/callback", follow_redirects=False).status_code, 400)
        blocked = self.client.get("/apply/callback", follow_redirects=False)
        self.assertEqual(blocked.status_code, 429)

    def test_rate_limit_is_thread_safe(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: ws.rate_limited("concurrent-test", limit=3, window=60), range(24)))
        self.assertEqual(results.count(False), 3)
        self.assertEqual(results.count(True), 21)

    def test_rate_limiter_rejects_new_keys_at_capacity_without_growing(self):
        now = ws.time.time()
        with ws._rate_lock:
            ws._rate_hits.update({
                f"client-{index}": [now]
                for index in range(ws._RATE_LIMIT_MAX_KEYS)
            })
            ws._rate_last_full_sweep = now

        self.assertTrue(ws.rate_limited("unseen-client", limit=3, window=60))
        self.assertEqual(len(ws._rate_hits), ws._RATE_LIMIT_MAX_KEYS)
        self.assertNotIn("unseen-client", ws._rate_hits)

    def test_rate_limiter_reclaims_expired_keys_at_capacity(self):
        with ws._rate_lock:
            ws._rate_hits.update({
                f"expired-{index}": [1.0]
                for index in range(ws._RATE_LIMIT_MAX_KEYS)
            })

        with patch.object(ws.time, "time", return_value=5000.0):
            self.assertFalse(ws.rate_limited("fresh-client", limit=3, window=60))

        self.assertEqual(len(ws._rate_hits), 1)
        self.assertIn("fresh-client", ws._rate_hits)


if __name__ == "__main__":
    unittest.main()
