"""Audit #103: versioned applicant message templates (history, restore, limits, admin-only route)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_app_comms as ac
import pulse_db
import webserver as ws
from tests.test_interview_calendar import make_auth


class VersionCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = str(Path(self.tmp.name) / "pulse.db")
        self.patches = [patch.object(pulse_db, "DB_PATH", self.db), patch.object(ws, "AUDIT_FILE", str(Path(self.tmp.name) / "a.json")),
                        patch.object(ws, "compute_perms", lambda g, u, c: ({}, None))]
        for p in self.patches:
            p.start()
        pulse_db._SCHEMA_READY.discard(self.db)
        pulse_db.init_db()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        pulse_db._SCHEMA_READY.discard(self.db)
        self.tmp.cleanup()

    def edit(self, text, key="rejected", actor="Lead"):
        values = dict(ac.load())
        values[key] = text
        return ac.save(values, actor)

    def test_unchanged_save_creates_no_version(self):
        ac.save(ac.load(), "Lead")
        self.assertEqual(ac.history("rejected"), [])

    def test_each_change_is_a_version_newest_first_with_actor(self):
        self.edit("Text A")
        self.edit("Text B", actor="Admin")
        rows = ac.history("rejected")
        self.assertEqual([(r["version"], r["text"], r["actor"]) for r in rows], [(2, "Text B", "Admin"), (1, "Text A", "Lead")])
        self.assertEqual(ac.history("hired"), [])

    def test_restore_activates_old_text_and_is_itself_versioned(self):
        self.edit("Text A")
        self.edit("Text B")
        self.assertEqual(ac.restore("rejected", 1, "Admin"), "Text A")
        self.assertEqual(ac.render("rejected"), "Text A")
        self.assertEqual(ac.history("rejected")[0]["version"], 3)

    def test_restore_unknown_version_or_key_is_rejected(self):
        self.edit("Text A")
        with self.assertRaises(ValueError):
            ac.restore("rejected", 9)
        with self.assertRaises(ValueError):
            ac.restore("nope", 1)

    def test_reset_to_default_is_a_version_too(self):
        self.edit("Text A")
        self.edit(ac.DEFAULTS["rejected"])
        self.assertEqual(ac.history("rejected")[0]["text"], ac.DEFAULTS["rejected"])

    def test_versions_are_bounded(self):
        for i in range(ac.MAX_VERSIONS + 5):
            self.edit(f"Text {i}")
        rows = ac.history("rejected")
        self.assertEqual(len(rows), ac.MAX_VERSIONS)
        self.assertEqual(rows[0]["text"], f"Text {ac.MAX_VERSIONS + 4}")

    def test_restore_route_is_admin_only(self):
        self.edit("Text A")
        self.edit("Text B")
        client = TestClient(ws.app, raise_server_exceptions=False)
        data = {"restore": "rejected:1"}
        with patch.object(ws, "auth", make_auth("1")), patch.object(ws, "log_audit"):
            from fastapi import HTTPException

            def deny(request, session, perm="x", admin=False):
                raise HTTPException(status_code=403, detail="nein")
            with patch.object(ws, "auth", deny):
                self.assertEqual(client.post("/applications/templates/restore", data=data, follow_redirects=False).status_code, 403)
            self.assertEqual(ac.render("rejected"), "Text B")
            with patch.object(ws, "auth", make_auth("1")):
                resp = client.post("/applications/templates/restore", data=data, follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303))
        self.assertEqual(ac.render("rejected"), "Text A")


if __name__ == "__main__":
    unittest.main()
