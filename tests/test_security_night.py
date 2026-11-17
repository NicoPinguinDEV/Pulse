"""Night audit regression tests (synthetic data only, no network, no Discord)."""
import unittest
from unittest.mock import patch

import webserver as ws
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401


class RequestBodyLimitTests(unittest.TestCase):
    def test_declared_sizes(self):
        f = ws.request_body_problem
        self.assertIsNone(f("GET", {"content-length": "999999999999"}))
        self.assertIsNone(f("POST", {}))
        self.assertIsNone(f("POST", {"content-length": "1000", "content-type": "application/x-www-form-urlencoded"}))
        self.assertEqual(f("POST", {"content-length": str(ws.MAX_BODY_BYTES + 1), "content-type": "application/json"})[0], 413)
        self.assertEqual(f("POST", {"content-length": str(ws.MAX_BODY_BYTES + 1)})[0], 413)
        self.assertIsNone(f("POST", {"content-length": str(50 * 2**20), "content-type": "multipart/form-data; boundary=x"}))
        self.assertEqual(f("POST", {"content-length": str(ws.MAX_UPLOAD_BODY_BYTES + 1), "content-type": "multipart/form-data; boundary=x"})[0], 413)
        self.assertEqual(f("POST", {"content-length": "-5"})[0], 400)
        self.assertEqual(f("POST", {"content-length": "abc"})[0], 400)
        self.assertEqual(f("POST", {"transfer-encoding": "chunked"})[0], 411)

    def test_middleware_rejects_oversized_anonymous_post_before_any_handler(self):
        from fastapi.testclient import TestClient
        client = TestClient(ws.app)
        big = "x=" + "a" * (ws.MAX_BODY_BYTES + 10)
        r = client.post("/logout", content=big, headers={"content-type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status_code, 413)
