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
        self.public_gate = patch.object(ws.pulse_public_site, "public_access_enabled", return_value=True)
        self.public_gate.start()
        self.addCleanup(self.public_gate.stop)
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

    def test_rate_limiter_evicts_the_oldest_key_at_capacity_instead_of_locking_out_new_clients(self):
        # AUTH-4: filling the table with fake sources must not block every NEW client (login DoS); memory stays bounded.
        now = ws.time.time()
        with ws._rate_lock:
            ws._rate_hits.update({
                f"client-{index}": [now + index / 1000.0]
                for index in range(ws._RATE_LIMIT_MAX_KEYS)
            })
            ws._rate_last_full_sweep = now

        self.assertFalse(ws.rate_limited("unseen-client", limit=3, window=60))
        self.assertEqual(len(ws._rate_hits), ws._RATE_LIMIT_MAX_KEYS)
        self.assertIn("unseen-client", ws._rate_hits)
        self.assertNotIn("client-0", ws._rate_hits)  # the least recently active key made room
        self.assertIn(f"client-{ws._RATE_LIMIT_MAX_KEYS - 1}", ws._rate_hits)

    def test_new_client_is_still_limited_after_its_own_attempts_at_capacity(self):
        now = ws.time.time()
        with ws._rate_lock:
            ws._rate_hits.update({f"client-{i}": [now] for i in range(ws._RATE_LIMIT_MAX_KEYS)})
            ws._rate_last_full_sweep = now
        results = [ws.rate_limited("newcomer", limit=3, window=60) for _ in range(5)]
        self.assertEqual(results, [False, False, False, True, True])

    def test_ipv6_clients_share_one_bucket_per_64_prefix(self):
        from types import SimpleNamespace as NS
        same_64 = [NS(client=NS(host=f"2001:db8:1:2::{i:x}")) for i in (1, 2, 0xffff)]
        keys = {ws.request_rate_key(r, "login") for r in same_64}
        self.assertEqual(len(keys), 1)
        other = ws.request_rate_key(NS(client=NS(host="2001:db8:1:3::1")), "login")
        self.assertNotIn(other, keys)

    def test_ipv4_and_mapped_ipv4_and_unknown_hosts(self):
        from types import SimpleNamespace as NS
        self.assertEqual(ws.request_rate_key(NS(client=NS(host="203.0.113.9")), "x"), "x:203.0.113.9")
        self.assertEqual(ws.request_rate_key(NS(client=NS(host="::ffff:203.0.113.9")), "x"), "x:203.0.113.9")
        self.assertEqual(ws.request_rate_key(NS(client=None), "x"), "x:unknown")
        self.assertEqual(ws.request_rate_key(NS(client=NS(host="testclient")), "x"), "x:testclient")

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
