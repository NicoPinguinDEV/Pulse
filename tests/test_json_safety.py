"""K-05: a corrupt JSON file is backed up and never silently overwritten with defaults."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import webserver as ws
from tests.test_pulse_admin_area import ADMIN, fake_auth_for
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401  (Phase 9: no ./pulse.db)


class JsonSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "config.json")
        self.printed = patch("builtins.print").start()
        ws._CORRUPT_JSON.clear()

    def tearDown(self):
        patch.stopall()
        ws._CORRUPT_JSON.clear()
        self.tmp.cleanup()

    def test_valid_roundtrip_and_no_tmp_left(self):
        ws.save_json(self.path, {"team_role_ids": [1, 2]})
        self.assertEqual(ws.load_json(self.path, {}), {"team_role_ids": [1, 2]})
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_corrupt_file_is_backed_up_and_not_overwritten(self):
        Path(self.path).write_text('{"team_role_ids": [1, 2', encoding="utf-8")
        self.assertEqual(ws.load_json(self.path, {}), {})
        backups = [n for n in os.listdir(self.tmp.name) if n.startswith("config.json.corrupt-")]
        self.assertEqual(len(backups), 1)
        self.assertIn(self.path, ws.corrupt_json_files())
        with self.assertRaises(ws.JsonWriteBlocked):
            ws.save_json(self.path, {"team_role_ids": []})
        self.assertEqual(Path(self.path).read_text(encoding="utf-8"), '{"team_role_ids": [1, 2')
        self.assertTrue(any("beschädigt" in str(c) for c in self.printed.call_args_list))
        # a second read does not create another backup
        ws.load_json(self.path, {})
        self.assertEqual(len([n for n in os.listdir(self.tmp.name) if ".corrupt-" in n]), 1)

    def test_repaired_file_unblocks_writes(self):
        Path(self.path).write_text("{kaputt", encoding="utf-8")
        ws.load_json(self.path, {})
        Path(self.path).write_text(json.dumps({"ok": True}), encoding="utf-8")
        self.assertEqual(ws.load_json(self.path, {}), {"ok": True})
        ws.save_json(self.path, {"ok": False})
        self.assertEqual(ws.load_json(self.path, {}), {"ok": False})

    def test_dashboard_shows_understandable_message(self):
        Path(self.path).write_text("{kaputt", encoding="utf-8")
        ws.load_json(self.path, {})
        client = TestClient(ws.app)
        with patch.object(ws, "auth", fake_auth_for(ADMIN)):
            html = client.get("/system").text
        self.assertIn("config.json", html)
        self.assertIn("beschädigt", html)

        @ws.app.get("/__test_json_blocked")
        async def blocked():
            ws.save_json(self.path, {})
        try:
            response = client.get("/__test_json_blocked")
        finally:
            ws.app.router.routes = [r for r in ws.app.router.routes if getattr(r, "path", "") != "/__test_json_blocked"]
        self.assertEqual(response.status_code, 503)
        self.assertIn("beschädigt", response.text)
        self.assertIn("nicht gespeichert", response.text)


if __name__ == "__main__":
    unittest.main()
