"""U-13: one consistent set of security headers, same-origin check with Referer fallback, logout via POST."""
import unittest

from fastapi.testclient import TestClient

import pulse_security
import webserver as ws


class HeaderTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def test_headers_are_set_once_and_consistently(self):
        response = self.client.get("/", follow_redirects=False)
        headers = response.headers
        self.assertEqual(headers.get("Content-Security-Policy"), pulse_security.CSP)
        self.assertIn("frame-ancestors 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers.get("Referrer-Policy"), "same-origin")
        self.assertEqual(headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(headers.get("X-Robots-Tag"), "noindex, nofollow")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(len(headers.get_list("Referrer-Policy")), 1)

    def test_static_like_files_may_be_cached(self):
        self.assertNotEqual(self.client.get("/sw.js").headers.get("Cache-Control"), "no-store")

    def test_foreign_origin_and_foreign_referer_are_refused(self):
        evil = self.client.post("/logout", headers={"Origin": "https://evil.example"}, follow_redirects=False)
        self.assertEqual(evil.status_code, 403)
        referer_only = self.client.post("/logout", headers={"Referer": "https://evil.example/page"}, follow_redirects=False)
        self.assertEqual(referer_only.status_code, 403)  # before: no Origin header → no check at all
        same = self.client.post("/logout", headers={"Referer": "http://testserver/me"}, follow_redirects=False)
        self.assertEqual(same.status_code, 303)

    def test_same_origin_rules(self):
        h = {"host": "pulse.example"}
        self.assertTrue(pulse_security.same_origin_ok("GET", {**h, "origin": "https://evil.example"}))
        self.assertTrue(pulse_security.same_origin_ok("POST", h))  # API clients without browser headers
        self.assertTrue(pulse_security.same_origin_ok("POST", {**h, "origin": "https://pulse.example"}))
        self.assertFalse(pulse_security.same_origin_ok("POST", {**h, "origin": "null", "referer": "https://x.example/"}))


class LogoutTests(unittest.TestCase):
    def test_get_only_asks_post_logs_out(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        client.cookies.set("user_session", "abc")
        page = client.get("/logout")
        self.assertEqual(page.status_code, 200)
        self.assertIn('method="post" action="/logout"', page.text)
        self.assertNotIn("user_session=", page.headers.get("set-cookie", ""))
        done = client.post("/logout", follow_redirects=False)
        self.assertEqual(done.status_code, 303)
        self.assertIn('user_session=""', done.headers.get("set-cookie", ""))


if __name__ == "__main__":
    unittest.main()
