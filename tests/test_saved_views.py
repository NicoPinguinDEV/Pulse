"""Personal saved filters for dashboard tables (#61): storage rules, routes, rights, backup and page integration."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_db
import pulse_views as pv
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, GUILD, TEAMLER


def auth_as(perms, user_id="123"):
    def fake_auth(request, session, perm="can_view_dashboard", admin=False):
        if admin and not perms.get("is_admin"):
            raise HTTPException(status_code=403, detail="Dieser Bereich ist nur für Administratoren.")
        if perm and not perms.get(perm):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        return SimpleNamespace(user={"id": user_id, "global_name": "Tester"}, guild=GUILD, guild_name=GUILD.name,
                               perms=perms, config={"team_role_ids": []}, member=None, msg="", msg_ok=True)
    return fake_auth


class SavedViewsBase(unittest.TestCase):
    def setUp(self):
        import pulse_ultimate
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
                        patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json")),
                        patch.object(ws, "APPS_FILE", str(root / "applications.json")),
                        patch.object(ws, "CONFIG_FILE", str(root / "config.json")),
                        patch.object(ws, "auth", auth_as(ADMIN)),
                        patch.object(pulse_ultimate, "_INIT", False),
                        patch("builtins.print")]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        pulse_ultimate.setup()
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def rows(self):
        with pulse_db.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM dashboard_saved_views ORDER BY name").fetchall()]

    def post_save(self, **data):
        body = {"page": "/tickets", "name": "Dringend", "query": "status=open&category=bug"}
        body.update(data)
        return self.client.post("/me/views/save", data=body, follow_redirects=False)


class StorageTests(SavedViewsBase):
    def test_query_is_reduced_to_safe_filter_pairs(self):
        pairs = pv.clean_query([("status", "open"), ("msg", "Hallo"), ("t", "ok"), ("page", "3"), ("Bad-Key", "x"),
                                ("q", "  Max  "), ("q", "Max"), ("empty", " "), ("long", "x" * 101), ("ctl", "a\nb"), ("edit", "auto_1")])
        self.assertEqual(pairs, [("status", "open"), ("q", "Max")])
        self.assertEqual(len(pv.clean_query([(f"k{i}", "v") for i in range(40)])), pv.MAX_PAIRS)
        self.assertEqual(pv.href("/tickets", "status=open&msg=x&next=//evil.example"), "/tickets?status=open&next=%2F%2Fevil.example")
        self.assertEqual(pv.href("https://evil.example", "a=b"), "/me")   # the path is never taken from input
        self.assertEqual(pv.href("//evil.example", ""), "/me")
        self.assertEqual(pv.href("/tickets", ""), "/tickets")

    def test_save_replace_limit_and_validation(self):
        first = pv.save_view("123", "/tickets", "  Dringend   offen ", [("status", "open")])
        self.assertEqual((first["name"], first["query"], first["replaced"]), ("Dringend offen", "status=open", False))
        again = pv.save_view("123", "/tickets", "dringend OFFEN", [("status", "closed")])   # same name → replaced
        self.assertTrue(again["replaced"])
        self.assertEqual([(r["name"], r["query"]) for r in self.rows()], [("dringend OFFEN", "status=closed")])
        for bad in (("/nope", "x", [("a", "b")]), ("/tickets", "", [("a", "b")]), ("/tickets", "x" * 61, [("a", "b")]),
                    ("/tickets", "ok", []), ("/tickets", "ok", [("msg", "nur Meldung")]), ("/tickets", "a\x00b", [("a", "b")])):
            with self.assertRaises(ValueError):
                pv.save_view("123", *bad)
        for i in range(pv.MAX_VIEWS_PER_PAGE - 1):
            pv.save_view("123", "/tickets", f"Ansicht {i}", [("q", str(i))])
        with self.assertRaises(ValueError):
            pv.save_view("123", "/tickets", "eine zu viel", [("q", "x")])
        pv.save_view("123", "/applications", "andere Seite", [("view", "history")])   # the limit is per page
        pv.save_view("456", "/tickets", "andere Person", [("q", "x")])              # … and per person
        self.assertEqual(len(self.rows()), pv.MAX_VIEWS_PER_PAGE + 2)

    def test_views_belong_to_one_person(self):
        mine = pv.save_view("123", "/tickets", "Meine", [("status", "open")])
        pv.save_view("456", "/tickets", "Fremde", [("status", "closed")])
        self.assertEqual([v["name"] for v in pv.list_views("123", "/tickets")], ["Meine"])
        self.assertIsNone(pv.delete_view("456", mine["id"]))   # someone else's view is not found
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(pv.delete_view("123", mine["id"])["name"], "Meine")
        self.assertEqual([r["name"] for r in self.rows()], ["Fremde"])
        self.assertEqual(pv.list_views("123", "/unbekannt"), [])

    def test_views_are_part_of_backup_restore_and_personal_export(self):
        import pulse_retention
        pv.save_view("100000000000000001", "/tickets", "Export", [("status", "open")])
        state = pulse_db.export_state()
        self.assertEqual([r["name"] for r in state["dashboard_saved_views"]], ["Export"])
        with pulse_db.connect() as db:
            db.execute("DELETE FROM dashboard_saved_views")
        pulse_db.import_state(state)
        self.assertEqual([r["name"] for r in self.rows()], ["Export"])
        exported, _ = pulse_retention._rows_for(pulse_db.DB_PATH, "100000000000000001")
        self.assertEqual([r["name"] for r in exported["dashboard_saved_views"]], ["Export"])
        other, _ = pulse_retention._rows_for(pulse_db.DB_PATH, "100000000000000002")
        self.assertNotIn("dashboard_saved_views", other)


class RouteTests(SavedViewsBase):
    def test_save_and_delete_through_the_routes(self):
        r = self.post_save()
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/tickets?status=open&category=bug&msg="))
        self.assertIn("t=ok", r.headers["location"])
        [row] = self.rows()
        self.assertEqual((row["user_id"], row["page"], row["name"], row["query"]), ("123", "/tickets", "Dringend", "status=open&category=bug"))
        r = self.client.post(f"/me/views/{row['id']}/delete", data={"query": "status=open"}, follow_redirects=False)
        self.assertTrue(r.headers["location"].startswith("/tickets?status=open&msg="))
        self.assertEqual(self.rows(), [])

    def test_bad_requests_change_nothing(self):
        for change in ({"page": "/admin/settings"}, {"page": "https://evil.example/x"}, {"name": ""}, {"query": ""},
                       {"query": "msg=x&t=ok"}, {"name": "x" * 200}):
            r = self.post_save(**change)
            self.assertEqual(r.status_code, 303, change)
            self.assertIn("t=err", r.headers["location"], change)
            self.assertFalse(r.headers["location"].startswith(("http", "//")), change)
        self.assertEqual(self.rows(), [])
        self.post_save()
        [row] = self.rows()
        with patch.object(ws, "auth", auth_as(ADMIN, "999")):   # even an administrator cannot delete another person's view
            r = self.client.post(f"/me/views/{row['id']}/delete", follow_redirects=False)
            self.assertIn("t=err", r.headers["location"])
        no_dashboard = dict(TEAMLER, can_view_dashboard=False)
        with patch.object(ws, "auth", auth_as(no_dashboard, "777")):
            self.assertEqual(self.post_save().status_code, 403)
            self.assertEqual(self.client.post(f"/me/views/{row['id']}/delete").status_code, 403)
        self.assertEqual(len(self.rows()), 1)

    def test_bar_shows_only_own_views_marks_the_active_one_and_escapes(self):
        pv.save_view("123", "/tickets", "<b>Offen</b>", [("status", "open")])
        pv.save_view("123", "/tickets", "Geschlossen", [("status", "closed")])
        pv.save_view("456", "/tickets", "FremdeAnsicht", [("status", "open")])
        out = pv.bar("/tickets", "123", [("status", "open"), ("msg", "gespeichert")])
        self.assertIn("&lt;b&gt;Offen&lt;/b&gt;", out)
        self.assertNotIn("<b>Offen</b>", out)
        self.assertNotIn("FremdeAnsicht", out)
        self.assertEqual(out.count('aria-current="true"'), 1)
        self.assertIn("href='/tickets?status=closed'", out)
        self.assertIn("Aktuellen Filter speichern", out)
        self.assertNotIn("Aktuellen Filter speichern", pv.bar("/tickets", "123", []))   # nothing to save without a filter
        self.assertEqual(pv.bar("/unbekannt", "123", [("a", "b")]), "")
        self.assertIn("class='btn", pv.bar("/ultimate/team", "123", [("status", "break")], style="ultimate"))

    def test_list_pages_show_the_bar(self):
        pv.save_view("123", "/tickets", "MeineTicketAnsicht", [("status", "closed")])
        pv.save_view("123", "/mod-cases", "MeineFallAnsicht", [("q", "spam")])
        pv.save_view("123", "/applications", "MeinVerlauf", [("view", "history")])
        pv.save_view("123", "/ultimate/automations", "MeineFehler", [("status", "error")])
        for path, name in (("/tickets?status=closed", "MeineTicketAnsicht"), ("/mod-cases?q=spam", "MeineFallAnsicht"),
                           ("/applications?view=history", "MeinVerlauf"), ("/ultimate/automations?status=error", "MeineFehler")):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200, (path, page.text[-300:]))
            self.assertIn("Meine Ansichten", page.text, path)
            self.assertIn(name, page.text, path)
            self.assertIn('aria-current="true"', page.text, path)


class TeamFilterTests(SavedViewsBase):
    def page(self, query=""):
        import pulse_ultimate as pu
        role = SimpleNamespace(id=100)
        members = [SimpleNamespace(id=1, display_name="Anna Admin", name="anna", bot=False, roles=[role], status="online"),
                   SimpleNamespace(id=2, display_name="Bernd Builder", name="bernd", bot=False, roles=[role], status="offline")]

        def stats(member, shared=None):
            return {"profile": {"internal_status": "break" if member.id == 2 else "available"}, "warnings": 0, "score": 50,
                    "weekly_seconds": 0, "activity_pct": 10}
        with patch.object(pu, "team_members", lambda guild: members), patch.object(pu, "member_stats", stats), \
                patch.object(pu, "stats_context", lambda: {}), patch.object(pu, "get_profile", lambda uid: {}):
            response = self.client.get("/ultimate/team" + query)
        self.assertEqual(response.status_code, 200, response.text[-400:])
        return response.text

    def test_team_list_filters_by_name_and_status_and_offers_views(self):
        everything = self.page()
        self.assertIn("Anna Admin", everything)
        self.assertIn("Bernd Builder", everything)
        self.assertIn("Meine Ansichten", everything)
        by_name = self.page("?q=bernd")
        self.assertIn("Bernd Builder", by_name)
        self.assertNotIn("Anna Admin", by_name)
        by_status = self.page("?status=available")
        self.assertIn("Anna Admin", by_status)
        self.assertNotIn("Bernd Builder", by_status)
        self.assertIn("Kein Teammitglied passt zum Filter.", self.page("?q=niemand"))
        self.assertIn("Anna Admin", self.page("?status=<script>"))   # unknown status is ignored, nothing is echoed
        self.assertNotIn("<script>alert(1)", self.page("?q=<script>alert(1)</script>"))


if __name__ == "__main__":
    unittest.main()
