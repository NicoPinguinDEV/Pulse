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


class WorkflowHardeningTests(unittest.TestCase):
    ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]

    def test_every_checkout_drops_the_git_credentials(self):
        import re
        for path in sorted((self.ROOT / ".github" / "workflows").glob("*.yml")):
            lines = path.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                if "actions/checkout@" not in line:
                    continue
                indent = len(line) - len(line.lstrip()) + (2 if line.lstrip().startswith("- ") else 0)
                block = []
                for nxt in lines[i + 1:]:
                    if nxt.strip() and len(nxt) - len(nxt.lstrip()) < indent:
                        break
                    if re.match(r"^\s*- ", nxt) and len(nxt) - len(nxt.lstrip()) <= indent - (0 if not line.lstrip().startswith("- ") else 2):
                        break
                    block.append(nxt)
                self.assertTrue(any("persist-credentials: false" in b for b in block[:6]), f"{path.name}:{i + 1}")

    def test_remote_deploy_validates_the_commit_sha_itself(self):
        text = (self.ROOT / "deploy" / "remote_deploy.sh").read_text(encoding="utf-8")
        self.assertIn("^[0-9a-f]{40}$", text)
        self.assertLess(text.index("^[0-9a-f]{40}$"), text.index("cd \"$REPO_DIR\""))

    def test_dependabot_covers_compose_images(self):
        text = (self.ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertIn("package-ecosystem: docker-compose", text)

    def test_caddy_caps_request_bodies(self):
        self.assertIn("max_size", (self.ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8"))
