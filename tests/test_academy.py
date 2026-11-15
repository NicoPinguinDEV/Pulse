"""Audit #106/#176: Academy overview - own progress, certificates with refresher status, must-read, team table, page."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_academy as academy
import pulse_certificates as certs
import pulse_db
import pulse_wiki_plus as wiki
import webserver as ws
from tests.fakes import FakeGuild, FakeMember, FakeRole

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
RID = "123456789012345678"  # role IDs must look like Discord snowflakes
OTHER = "999999999999999999"


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
        self.t1 = pulse_db.create_training("Grundlagen", "", 80, 10, "9", "Lead", self.questions())
        self.t2 = pulse_db.create_training("Moderation", "", 80, 10, "9", "Lead", self.questions())
        self.path = pulse_db.save_training_path(RID, "Support", "Support-Pfad", "", "9", "Lead", [self.t1, self.t2])

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        pulse_db._SCHEMA_READY.discard(self.db)
        self.tmp.cleanup()

    @staticmethod
    def questions():
        return [{"question": "F?", "options": ["a", "b"], "answer_index": 0}]

    def pass_training(self, uid, training_id, name="X", when=None):
        attempt = pulse_db.save_attempt(training_id, uid, name, 90, True)
        return certs.issue_for_attempt(attempt, when or NOW)


class OverviewCase(Base):
    def test_progress_next_step_and_percent(self):
        view = academy.overview("1", [RID], NOW)
        path = view["paths"][0]
        self.assertEqual((path["done"], path["total"], path["percent"], path["next"]["title"]), (0, 2, 0, "Grundlagen"))
        self.pass_training("1", self.t1)
        view = academy.overview("1", [RID], NOW)
        self.assertEqual((view["done"], view["total"], view["percent"]), (1, 2, 50))
        self.assertEqual(view["paths"][0]["next"]["title"], "Moderation")
        self.pass_training("1", self.t2)
        done = academy.overview("1", [RID], NOW)
        self.assertEqual((done["percent"], done["paths"][0]["next"]), (100, None))

    def test_other_roles_have_no_paths_and_no_percent(self):
        view = academy.overview("1", [OTHER], NOW)
        self.assertEqual((view["paths"], view["percent"]), ([], None))
        self.assertEqual(academy.overview("1", [], NOW)["total"], 0)

    def test_certificates_and_refresher_attention(self):
        certs.set_rule(self.t1, 30, 10, "9")
        self.pass_training("1", self.t1, when=NOW - timedelta(days=25))  # valid until in 5 days -> expiring
        self.pass_training("2", self.t1, when=NOW - timedelta(days=40))  # expired
        a, b = academy.overview("1", [RID], NOW), academy.overview("2", [RID], NOW)
        self.assertEqual([c["status"] for c in a["attention"]], [certs.STATUS_EXPIRING])
        self.assertEqual([c["status"] for c in b["attention"]], [certs.STATUS_EXPIRED])
        newer = self.pass_training("2", self.t1, when=NOW)  # re-certification supersedes the expired one
        self.assertIsNotNone(newer)
        self.assertEqual(academy.overview("2", [RID], NOW)["attention"], [])
        self.assertEqual(len(academy.overview("2", [RID], NOW)["certificates"]), 1)

    def test_must_read_is_part_of_the_overview(self):
        page = pulse_db.save_wiki("Funk", "Regeln", "Text", "9", "Lead")
        page_id = page["id"] if isinstance(page, dict) else page
        wiki.set_required(page_id, True)
        self.assertEqual([p["title"] for p in academy.overview("1", [RID], NOW)["must_read"]], ["Funk"])

    def test_team_overview_sorts_lowest_progress_first(self):
        self.pass_training("1", self.t1)
        rows = academy.team_overview([("1", "Ann", [RID]), ("2", "Bo", [RID]), ("3", "Cy", [OTHER])], NOW)
        self.assertEqual([r["name"] for r in rows], ["Bo", "Ann", "Cy"])  # no path (None) sorts last
        self.assertEqual([r["percent"] for r in rows], [0, 50, None])


class PageCase(Base):
    def setUp(self):
        super().setUp()
        self.role = FakeRole(int(RID), "Support")
        self.members = [FakeMember(1, "Ann", roles=[self.role]), FakeMember(2, "Bo", roles=[self.role])]
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def as_user(self, perms, uid="1"):
        member = next(m for m in self.members if str(m.id) == uid)
        guild = FakeGuild(members=self.members)

        def auth(request, session, perm="can_view_dashboard", admin=False):
            if perm and not perms.get(perm):
                raise HTTPException(status_code=403, detail="nein")
            return SimpleNamespace(user={"id": uid, "global_name": f"M{uid}"}, guild=guild, guild_name="G", perms=perms,
                                   config={"team_role_ids": [int(RID)]}, member=member, msg="", msg_ok=True)
        return patch.object(ws, "auth", auth)

    def test_member_sees_own_progress_but_no_team_table(self):
        self.pass_training("1", self.t1)
        with self.as_user({"can_view_dashboard": True}):
            html = self.client.get("/academy").text
        self.assertIn("Support-Pfad", html)
        self.assertIn("50 %", html)
        self.assertIn("Nächste Schulung: Moderation", html)
        self.assertNotIn("Team-Fortschritt", html)

    def test_leadership_sees_team_table_and_escaped_names(self):
        self.members[1].display_name = "<script>x</script>"
        with self.as_user({"can_view_dashboard": True, "can_promote": True}):
            html = self.client.get("/academy").text
        self.assertIn("Team-Fortschritt", html)
        self.assertNotIn("<script>x</script>", html)

    def test_page_needs_dashboard_access(self):
        with self.as_user({}):
            self.assertEqual(self.client.get("/academy").status_code, 403)


if __name__ == "__main__":
    unittest.main()
