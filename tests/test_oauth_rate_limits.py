"""Regression tests for bounded, concurrent OAuth and application login routes."""
import unittest
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

import webserver as ws


class OAuthRateLimitTests(unittest.TestCase):
    def setUp(self):
        with ws._rate_lock:
            ws._rate_hits.clear()
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        with ws._rate_lock:
            ws._rate_hits.clear()

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


if __name__ == "__main__":
    unittest.main()
