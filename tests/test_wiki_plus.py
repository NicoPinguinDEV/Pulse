"""Audit #111/#113: wiki search, must-read acknowledgements per version, routes and page integration (temp db, fake login)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_db
import pulse_wiki_plus as plus
import webserver as ws
from tests.fakes import FakeGuild, FakeMember, FakeRole

MANAGER = {"can_view_dashboard": True, "can_manage_wiki": True}
TEAM = {"can_view_dashboard": True}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.db = str(root / "pulse.db")
        self.patches = [patch.object(pulse_db, "DB_PATH", self.db), patch.object(ws, "AUDIT_FILE", str(root / "audit.json"))]
        for p in self.patches:
            p.start()
        pulse_db._SCHEMA_READY.discard(self.db)
        pulse_db.init_db()
        self.page = self.save("Dienstanweisung Funk", "Regeln", "Funkdisziplin: Nur kurze Meldungen, Rufzeichen nennen. Zebrastreifen beachten.")
        self.other = self.save("Ticket-Ablauf", "Support", "Tickets innerhalb von 24 Stunden beantworten.")
        self.role = FakeRole(77, "Support")
        self.members = [FakeMember(1, "Ann", roles=[self.role]), FakeMember(2, "Bo", roles=[self.role]), FakeMember(3, "Cy", roles=[self.role])]
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        pulse_db._SCHEMA_READY.discard(self.db)
        self.tmp.cleanup()

    @staticmethod
    def save(title, category, content, page_id=None):
        result = pulse_db.save_wiki(title, category, content, "9", "Lead", page_id=page_id)
        return result["id"] if isinstance(result, dict) else result

    def as_user(self, perms, uid="1"):
        members = self.members

        def auth(request, session, perm="can_view_dashboard", admin=False):
            if perm and not perms.get(perm):
                raise HTTPException(status_code=403, detail="nein")
            return SimpleNamespace(user={"id": uid, "global_name": f"M{uid}"}, guild=FakeGuild(members=members), guild_name="G", perms=perms,
                                   config={"team_role_ids": [77]}, member=None, msg="", msg_ok=True)
        return patch.object(ws, "auth", auth)


class SearchCase(Base):
    def test_all_terms_must_match_and_title_wins(self):
        self.assertEqual([r["id"] for r in plus.search("funk")], [self.page])
        self.assertEqual([r["id"] for r in plus.search("tickets 24")], [self.other])
        self.assertEqual(plus.search("funk tickets"), [])
        hits = plus.search("ticket")
        self.assertEqual(hits[0]["id"], self.other)

    def test_snippet_surrounds_the_match_and_short_or_empty_queries_return_nothing(self):
        snippet = plus.search("zebrastreifen")[0]["snippet"]
        self.assertIn("Zebrastreifen", snippet)
        for q in ("", " ", "a", None):
            self.assertEqual(plus.search(q), [])

    def test_results_are_bounded(self):
        for i in range(40):
            self.save(f"Seite {i}", "X", "Gemeinsamer Begriff")
        self.assertEqual(len(plus.search("gemeinsamer")), plus.MAX_RESULTS)


class MustReadCase(Base):
    def test_flag_confirm_and_new_version_makes_it_pending_again(self):
        self.assertEqual(plus.acknowledge(self.page, "1"), 0)  # not binding yet
        self.assertTrue(plus.set_required(self.page, True, "9", "Lead"))
        self.assertEqual([p["id"] for p in plus.pending_for("1")], [self.page])
        v1 = plus.acknowledge(self.page, "1", "Ann")
        self.assertEqual(plus.pending_for("1"), [])
        self.assertEqual([p["id"] for p in plus.pending_for("2")], [self.page])
        self.save("Dienstanweisung Funk", "Regeln", "Neuer Text, bitte neu lesen.", page_id=self.page)
        self.assertEqual([p["id"] for p in plus.pending_for("1")], [self.page])  # confirmation belonged to the old version
        v2 = plus.acknowledge(self.page, "1", "Ann")
        self.assertGreater(v2, v1)
        self.assertEqual(plus.acknowledge(self.page, "1"), v2)  # idempotent
        self.assertEqual(plus.pending_for("1"), [])

    def test_unknown_page_and_unflagging(self):
        self.assertFalse(plus.set_required("nope", True))
        plus.set_required(self.page, True)
        plus.set_required(self.page, False)
        self.assertEqual(plus.pending_for("1"), [])
        self.assertFalse(plus.is_required(self.page))

    def test_coverage_lists_confirmed_and_missing(self):
        plus.set_required(self.page, True)
        plus.acknowledge(self.page, "1", "Ann")
        cov = plus.coverage(self.page, [("1", "Ann"), ("2", "Bo")])
        self.assertEqual([c[1] for c in cov["confirmed"]], ["Ann"])
        self.assertEqual(cov["missing"], [("2", "Bo")])

    def test_tables_are_in_the_backup(self):
        plus.set_required(self.page, True)
        plus.acknowledge(self.page, "1")
        state = pulse_db.export_state()
        self.assertEqual((len(state["wiki_required_reads"]), len(state["wiki_read_acks"])), (1, 1))


class RouteCase(Base):
    def test_search_page_finds_and_escapes(self):
        with self.as_user(TEAM):
            self.assertIn("Dienstanweisung Funk", self.client.get("/wissen?q=funk").text)
            evil = self.client.get("/wissen?q=<script>alert(1)</script>").text
            self.assertNotIn("<script>alert(1)</script>", evil)
            self.assertIn("Keine Treffer", evil)

    def test_manager_flags_notifies_team_and_users_confirm(self):
        with self.as_user(TEAM):
            self.assertEqual(self.client.post(f"/wissen/{self.page}/pflicht", data={"on": "1"}).status_code, 403)
        self.assertFalse(plus.is_required(self.page))
        with self.as_user(MANAGER, "1"):
            resp = self.client.post(f"/wissen/{self.page}/pflicht", data={"on": "1"}, follow_redirects=False)
            self.assertEqual(resp.status_code, 303)
            self.assertEqual(self.client.post("/wissen/nope/pflicht", data={"on": "1"}).status_code, 404)
        self.assertTrue(plus.is_required(self.page))
        self.assertEqual(len(pulse_db.notifications("2")), 1)
        self.assertEqual(pulse_db.notifications("1"), [])  # the actor is not notified
        with self.as_user(TEAM, "2"):
            self.assertIn("Gelesen und verstanden", self.client.get(f"/wiki/{self.page}").text)
            self.assertEqual(self.client.post(f"/wissen/{self.page}/gelesen", follow_redirects=False).status_code, 303)
            self.assertIn("hast du bestätigt", self.client.get(f"/wiki/{self.page}").text)
            self.assertEqual(self.client.post(f"/wissen/{self.other}/gelesen").status_code, 404)  # not binding
        self.assertEqual(plus.pending_for("2"), [])

    def test_wiki_pages_show_search_and_pending_banner_and_manager_coverage(self):
        plus.set_required(self.page, True)
        with self.as_user(TEAM, "3"):
            listing = self.client.get("/wiki").text
            self.assertIn('action="/wissen"', listing)
            self.assertIn("1 Pflichtlektüre offen", listing)
        with self.as_user(MANAGER, "1"):
            detail = self.client.get(f"/wiki/{self.page}").text
            self.assertIn("Pflichtlektüre aufheben", detail)
            self.assertIn("noch offen:", detail)
            self.assertNotIn("Als Pflichtlektüre festlegen", detail)
            self.assertIn("Als Pflichtlektüre festlegen", self.client.get(f"/wiki/{self.other}").text)

    def test_wiki_keeps_working_if_the_extension_fails(self):
        with self.as_user(TEAM), patch.object(plus, "pending_for", side_effect=RuntimeError("db")):
            self.assertEqual(self.client.get("/wiki").status_code, 200)
            self.assertEqual(self.client.get(f"/wiki/{self.page}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
