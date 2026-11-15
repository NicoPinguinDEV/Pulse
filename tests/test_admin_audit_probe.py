"""Audit #165: jede erfolgreiche Admin-POST-Aktion hinterlässt einen Audit-Eintrag (Fallback, wenn der Handler keinen schreibt)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import webserver as ws

ADMIN = {"id": "7", "global_name": "Ann"}


class ProbeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(ws, "AUDIT_FILE", str(Path(tmp.name) / "audit.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def entries(self):
        return ws.load_json(ws.AUDIT_FILE, [])

    def test_writes_a_generic_entry_when_the_handler_audited_nothing(self):
        probe = {"n": 0, "user": ADMIN}
        self.assertTrue(ws.audit_probe_finish(probe, "POST", "/admin/etwas", "/admin/etwas", 303))
        entry = self.entries()[0]
        self.assertEqual((entry["actor"], entry["actor_id"], entry["action"], entry["details"]), ("Ann", "7", "Admin-Aktion", "POST /admin/etwas"))

    def test_no_duplicate_when_the_handler_already_audited(self):
        probe = {"n": 0, "user": ADMIN}
        token = ws._AUDIT_PROBE.set(probe)
        try:
            ws.log_audit("Ann", "7", "Einstellung geändert", "x")
        finally:
            ws._AUDIT_PROBE.reset(token)
        self.assertEqual(probe["n"], 1)
        self.assertFalse(ws.audit_probe_finish(probe, "POST", "/admin/etwas", "", 303))
        self.assertEqual(len(self.entries()), 1)

    def test_failures_gets_other_prefixes_and_unknown_actors_write_nothing(self):
        cases = [({"n": 0, "user": ADMIN}, "POST", "/admin/x", 403), ({"n": 0, "user": ADMIN}, "GET", "/admin/x", 200),
                 ({"n": 0, "user": ADMIN}, "POST", "/tickets/action", 303), ({"n": 0, "user": None}, "POST", "/admin/x", 303), (None, "POST", "/admin/x", 303)]
        for probe, method, path, status in cases:
            self.assertFalse(ws.audit_probe_finish(probe, method, path, "", status), (method, path, status))
        self.assertEqual(self.entries(), [])

    def test_log_audit_works_without_a_probe(self):
        ws.log_audit("Ann", "7", "A", "d")
        self.assertEqual(len(self.entries()), 1)


class MiddlewareTests(unittest.TestCase):
    """Echte Middleware mit einer vorübergehenden Testroute (wird danach wieder entfernt)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(ws, "AUDIT_FILE", str(Path(tmp.name) / "audit.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

        def fake_auth():
            ws._AUDIT_PROBE.get()["user"] = ADMIN  # wie ws.auth() für Administratoren

        async def silent():
            fake_auth()
            return {"ok": True}

        async def audited():
            fake_auth()
            ws.log_audit("Ann", "7", "Eigene Aktion", "x")
            return {"ok": True}

        async def failing():
            fake_auth()
            raise ws.HTTPException(status_code=403, detail="nein")

        before = len(ws.app.router.routes)
        ws.app.add_api_route("/admin/zz-probe-silent", silent, methods=["POST"])
        ws.app.add_api_route("/admin/zz-probe-audited", audited, methods=["POST"])
        ws.app.add_api_route("/admin/zz-probe-failing", failing, methods=["POST"])
        self.addCleanup(lambda: ws.app.router.routes.__delitem__(slice(before, None)))
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def post(self, path):
        return self.client.post(path, headers={"Origin": "http://testserver"})

    def test_silent_admin_post_gets_a_fallback_entry_but_audited_and_failed_ones_do_not(self):
        self.assertEqual(self.post("/admin/zz-probe-silent").status_code, 200)
        entries = ws.load_json(ws.AUDIT_FILE, [])
        self.assertEqual([e["action"] for e in entries], ["Admin-Aktion"])
        self.assertEqual(entries[0]["details"], "POST /admin/zz-probe-silent")
        self.assertEqual(self.post("/admin/zz-probe-audited").status_code, 200)
        self.assertEqual(self.post("/admin/zz-probe-failing").status_code, 403)
        self.assertEqual([e["action"] for e in ws.load_json(ws.AUDIT_FILE, [])], ["Admin-Aktion", "Eigene Aktion"])


if __name__ == "__main__":
    unittest.main()
