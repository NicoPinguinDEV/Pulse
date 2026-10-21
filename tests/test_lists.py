"""U-05: shared pagination/search/CSV and their use in the audit center and the warning center."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import pulse_lists
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, GUILD


class HelperTests(unittest.TestCase):
    def test_paginate_and_clamp(self):
        rows, meta = pulse_lists.paginate(list(range(120)), 3, 50)
        self.assertEqual(rows, list(range(100, 120)))
        self.assertEqual((meta["page"], meta["pages"], meta["first"], meta["last"]), (3, 3, 101, 120))
        self.assertEqual(pulse_lists.paginate(list(range(5)), 99, 50)[1]["page"], 1)
        self.assertEqual(pulse_lists.paginate([], 1, 50)[1]["first"], 0)

    def test_params_reject_odd_values(self):
        p = pulse_lists.params({"page": "-4", "per": "100000", "q": " x "})
        self.assertEqual((p["page"], p["per"], p["q"]), (1, pulse_lists.DEFAULT_PER_PAGE, "x"))
        self.assertEqual(pulse_lists.params({"lpage": "2"}, "l")["page"], 2)

    def test_pager_keeps_query_and_escapes(self):
        _rows, meta = pulse_lists.paginate(list(range(120)), 2, 50)
        html = pulse_lists.pager("/warns", meta, {"q": '<b>"', "view": "history"})
        self.assertIn("page=3", html)
        self.assertIn("view=history", html)
        self.assertNotIn("<b>", html)
        self.assertIn("51–100 von 120", html)

    def test_csv_is_formula_safe(self):
        body = pulse_lists.csv_response("x.csv", ["A"], [["=HYPERLINK(1)"], ["@cmd"], ["ok"]]).body.decode("utf-8")
        self.assertIn("'=HYPERLINK(1)", body)
        self.assertIn("'@cmd", body)
        self.assertTrue(body.startswith("﻿"))
        self.assertEqual(pulse_lists.csv_safe("-1"), ws.csv_safe("-1"))


def member(i, name):
    return SimpleNamespace(id=i, bot=False, display_name=name, name=name.lower(), roles=[SimpleNamespace(id=5)])


class PageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()
        self.members = [member(1, "Anna"), member(2, "Ben & Co")]
        guild = SimpleNamespace(**{**vars(GUILD), "members": self.members, "get_role": lambda _: None})
        self.ctx = SimpleNamespace(user={"id": "123", "global_name": "Tester"}, guild=guild, guild_name="G", perms=ADMIN,
                                   config={"team_role_ids": [5]}, member=None, msg="", msg_ok=True)
        warns = [{"id": f"W{i}", "reason": f"Grund {i}", "by": "Leitung", "date": "01.10.2026", "active": True} for i in range(60)]
        self.teamdb = {"1": {"warns_list": warns}, "2": {"warns_list": [{"id": "WX", "reason": "=SUMME(A1)", "by": "L", "date": "02.10.2026",
                                                                          "active": True}]}}

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    def client(self):
        return TestClient(ws.app, raise_server_exceptions=False)

    def run_with_fakes(self, fn):
        with patch.object(ws, "auth", lambda *a, **k: self.ctx), \
                patch.object(ws, "load_json", lambda path, default=None: self.teamdb if path == ws.DATA_FILE else default), \
                patch.object(ws, "save_json", lambda *a, **k: None), \
                patch.object(ws, "active_warns", lambda entry: [w for w in entry.get("warns_list", []) if w.get("active", True)]), \
                patch.object(ws, "warning_role_health", lambda *a: []):
            return fn(self.client())

    def test_warns_paginated_search_unescaped_and_csv(self):
        def go(client):
            first = client.get("/warns")
            self.assertEqual(first.status_code, 200)
            self.assertIn("1–50 von 61", first.text)
            self.assertIn("page=2", first.text)
            found = client.get("/warns", params={"q": "ben & co"})
            self.assertIn("1 Einträge", found.text)  # before: the query was HTML-escaped and never matched "&"
            csv = client.get("/warns.csv", params={"q": "summe"})
            self.assertEqual(csv.status_code, 200)
            self.assertIn("'=SUMME(A1)", csv.content.decode("utf-8"))
            self.assertNotIn("Grund 1;", csv.content.decode("utf-8"))
        self.run_with_fakes(go)

    def test_audit_page_and_csv(self):
        import cogs.pulse_suite as suite
        db_patch = patch.object(suite, "DB_PATH", str(Path(self.tmp.name) / "pulse_suite.db"))  # never the real suite DB
        db_patch.start()
        self.addCleanup(db_patch.stop)
        suite.init_db()
        suite.register_routes()
        for i in range(60):
            suite.audit(1, "9", "Leitung", f"t{i}", f"Ziel {i}", "test_action", f"Detail {i}")

        def go(client):
            page = client.get("/suite/audit")
            self.assertEqual(page.status_code, 200)
            self.assertIn("1–50 von 60", page.text)
            self.assertIn("/suite/audit.csv", page.text)
            csv = client.get("/suite/audit.csv", params={"q": "ziel 59"})
            self.assertEqual(csv.status_code, 200)
            body = csv.content.decode("utf-8")
            self.assertIn("Ziel 59", body)
            self.assertNotIn("Ziel 58", body)
        with patch.object(ws, "auth", lambda *a, **k: self.ctx), patch.object(ws, "load_json", lambda path, default=None: default):
            go(self.client())


if __name__ == "__main__":
    unittest.main()
