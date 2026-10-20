"""Phase 1 security regressions (K-17 public API docs, K-14 CSV formula injection)."""
import csv
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import webserver as ws


class PublicApiDocsTests(unittest.TestCase):
    def test_api_docs_are_not_public(self):
        client = TestClient(ws.app)
        for path in ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"):
            self.assertEqual(client.get(path, follow_redirects=False).status_code, 404, path)


class CsvInjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    def export(self, url):
        role = SimpleNamespace(id=7, name="=cmd|' /C calc'!A0", position=1)
        member = SimpleNamespace(id=123, display_name='=HYPERLINK("http://evil","x")', roles=[role], bot=False,
                                 status="online", name="evil")
        guild = SimpleNamespace(id=1, name="G", members=[member], roles=[role], get_member=lambda _: member,
                                get_role=lambda _: role, owner_id=0, icon=None)
        perms = dict({k: True for k in ws.PERM_KEYS}, is_admin=True)
        ctx = SimpleNamespace(user={"id": "1", "global_name": "A"}, guild=guild, guild_name="G", perms=perms,
                              config={"team_role_ids": [7]}, member=member, msg="", msg_ok=True)
        import pulse_next
        stats = {"score": 1, "weekly_seconds": 0, "total_seconds": 0, "closed_tickets": 0, "warnings": 0, "training_passed": 0}
        with patch.object(ws, "auth", lambda *a, **k: ctx), \
                patch.object(pulse_next, "all_stats", lambda guild: [(member, stats)]):
            response = TestClient(ws.app).get(url)
        return response

    def assert_no_formula(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertIn("HYPERLINK", response.text)  # the dangerous name is exported – but neutralized
        for row in csv.reader(io.StringIO(response.text)):
            for cell in row:
                self.assertFalse(cell[:1] in ("=", "+", "-", "@"), f"unsicher: {cell!r}")

    def test_team_export_neutralizes_formulas(self):
        self.assert_no_formula(self.export("/export/team.csv"))

    def test_ultimate_report_neutralizes_formulas(self):
        self.assert_no_formula(self.export("/ultimate/reports.csv"))


if __name__ == "__main__":
    unittest.main()
