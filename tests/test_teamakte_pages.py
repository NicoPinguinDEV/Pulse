"""Audit #71/#75/#76/#81/#87/#88/#89/#92/#94: team planning page, 360° block and goal routes
(fake login, fake Discord objects, temp database - no network, no Discord actions)."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_db
import pulse_player_reports
import pulse_teamakte as ta
import pulse_teamakte_pages as pages
import webserver as ws
from tests.fakes import FakeGuild, FakeMember, FakeRole

UTC = timezone.utc
LEAD = {"can_view_dashboard": True, "can_promote": True}
TEAM = {"can_view_dashboard": True}
ROLE = FakeRole(77, "Support")


def make_ctx(perms, uid="1", members=()):
    guild = FakeGuild(members=list(members))
    return SimpleNamespace(user={"id": uid, "global_name": f"M{uid}"}, guild=guild, guild_name="T", perms=perms,
                           config={"team_role_ids": [77]}, member=None, msg="", msg_ok=True)


def fake_auth(perms, uid="1", members=()):
    def auth(request, session, perm="can_view_dashboard", admin=False):
        if admin and not perms.get("is_admin"):
            raise HTTPException(status_code=403, detail="nur Admin")
        if perm and not perms.get(perm):
            raise HTTPException(status_code=403, detail="nein")
        return make_ctx(perms, uid, members)
    return auth


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        now = datetime.now(UTC)
        self.members = [FakeMember(1, "Ann", roles=[ROLE]), FakeMember(2, "Bo", roles=[ROLE])]
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")), patch.object(ws, "AUDIT_FILE", str(root / "audit.json")),
                        patch.object(ws, "DATA_FILE", str(root / "team.json")), patch.object(ws, "SHIFTS_FILE", str(root / "shifts.json")),
                        patch.object(ws, "CONFIG_FILE", str(root / "config.json")),
                        patch.object(ws, "DB_ABMELDUNGEN", str(root / "none.db")),
                        patch.object(pulse_player_reports, "DB_PATH", str(root / "pulse_suite.db"))]  # Moderationsquellen der Hinweise (#129)
        for p in self.patches:
            p.start()
        pulse_db._SCHEMA_READY.discard(str(root / "pulse.db"))
        pulse_db.init_db()
        history = [{"mod_id": "1", "mod_name": "Ann", "started_at_iso": (now - timedelta(days=d)).isoformat(), "duration_seconds": 7200} for d in range(1, 6)]
        ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {}, "history": history})
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def as_user(self, perms, uid="1"):
        return patch.object(ws, "auth", fake_auth(perms, uid, self.members))


class PlanningPageTests(Base):
    def test_every_view_renders_for_leadership(self):
        with self.as_user(LEAD):
            for key, _label in pages.VIEWS:
                resp = self.client.get(f"/team/planung?ansicht={key}")
                self.assertEqual(resp.status_code, 200, key)
                self.assertIn("Teamplanung", resp.text)
            self.assertEqual(self.client.get("/team/planung?ansicht=<script>").status_code, 200)

    def test_normal_team_member_is_denied(self):
        with self.as_user(TEAM):
            self.assertEqual(self.client.get("/team/planung").status_code, 403)

    def test_views_show_expected_content(self):
        ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {}, "history": [
            {"mod_id": "1", "mod_name": "Ann", "started_at_iso": (datetime.now(UTC) - timedelta(days=2)).isoformat(), "duration_seconds": 14 * 3600}]})
        with self.as_user(LEAD):
            self.assertIn("länger als das Maximum", self.client.get("/team/planung?ansicht=anomalien").text)
            self.assertIn("Ann", self.client.get("/team/planung?ansicht=auslastung").text)
            self.assertIn("Definitionen", self.client.get("/team/planung?ansicht=bericht").text)


class MemberSectionTests(Base):
    def render(self, perms, viewer="1", target=2):
        ctx = make_ctx(perms, viewer, self.members)
        member = ctx.guild.get_member(target)
        member.roles = [SimpleNamespace(id=77, position=5, name="Support")]
        return pages.member_section(ws, ctx, member, {}, [])

    def test_lead_sees_overview_goals_form_and_package(self):
        html = self.render(LEAD)
        for part in ("360°-Übersicht", "Entwicklung", "Beförderungspaket", "keine automatische Beförderung", "/team/2/entwicklung/neu"):
            self.assertIn(part, html)

    def test_person_sees_own_overview_without_package_or_form(self):
        html = self.render(TEAM, viewer="2", target=2)
        self.assertIn("360°-Übersicht", html)
        self.assertNotIn("Beförderungspaket", html)
        self.assertNotIn("/entwicklung/neu", html)

    def test_other_member_sees_nothing(self):
        self.assertEqual(self.render(TEAM, viewer="1", target=2), "")

    def test_warnings_stay_hidden_without_permission(self):
        html = self.render(LEAD)  # LEAD cannot warn and is not the target -> 🔒
        self.assertIn("🔒", html)

    def test_failure_never_breaks_the_profile(self):
        with patch.object(pages, "collect", side_effect=RuntimeError("x")):
            self.assertEqual(self.render(LEAD), "")


class GoalRouteTests(Base):
    def test_lead_creates_and_closes_goal_with_audit(self):
        with self.as_user(LEAD):
            resp = self.client.post("/team/2/entwicklung/neu", data={"title": "Moderationsschulung", "due": "15.12.2026"}, follow_redirects=False)
            self.assertEqual(resp.status_code, 303)
            goal = ta.goals("2")[0]
            self.assertEqual(goal["due"], "2026-12-15")
            resp = self.client.post(f"/team/2/entwicklung/{goal['id']}/status", data={"status": "done"}, follow_redirects=False)
            self.assertEqual(resp.status_code, 303)
        self.assertEqual(ta.goals("2")[0]["status"], "done")
        audit = ws.load_json(ws.AUDIT_FILE, [])
        self.assertEqual(sum(1 for e in audit if "Entwicklungsziel" in str(e)), 2)

    def test_non_lead_cannot_write_and_nothing_is_stored(self):
        with self.as_user(TEAM):
            self.assertEqual(self.client.post("/team/2/entwicklung/neu", data={"title": "x"}).status_code, 403)
        self.assertEqual(ta.goals("2"), [])
        self.assertEqual(ws.load_json(ws.AUDIT_FILE, []), [])

    def test_invalid_input_unknown_member_and_foreign_goal(self):
        ta.add_goal("1", "Anns Ziel")
        gid = ta.goals("1")[0]["id"]
        with self.as_user(LEAD):
            self.assertEqual(self.client.post("/team/2/entwicklung/neu", data={"title": ""}, follow_redirects=False).status_code, 303)
            self.assertEqual(ta.goals("2"), [])
            self.assertEqual(self.client.post("/team/999/entwicklung/neu", data={"title": "x"}).status_code, 404)
            self.assertEqual(self.client.post(f"/team/2/entwicklung/{gid}/status", data={"status": "done"}).status_code, 404)
            self.assertEqual(self.client.post(f"/team/1/entwicklung/{gid}/status", data={"status": "bogus"}).status_code, 400)
        self.assertEqual(ta.goals("1")[0]["status"], "open")


if __name__ == "__main__":
    unittest.main()


class IntelligencePageTests(Base):
    def test_every_view_renders_for_leadership_and_escapes_input(self):
        import pulse_teamakte_pages as pg
        with self.as_user(LEAD):
            for key, _label in pg.INTEL_VIEWS:
                self.assertEqual(self.client.get(f"/team/intelligenz?ansicht={key}").status_code, 200, key)
            resp = self.client.get("/team/intelligenz?ansicht=fragen&q=<script>alert(1)</script>")
            self.assertEqual(resp.status_code, 200)
            self.assertNotIn("<script>alert(1)</script>", resp.text)
            self.assertIn("kann ich nicht beantworten", resp.text)
            self.assertIn("Entwurf", self.client.get("/team/intelligenz?ansicht=tagesbericht").text)
            self.assertEqual(self.client.get("/team/intelligenz?ansicht=nope").status_code, 200)

    def test_normal_member_is_denied(self):
        with self.as_user(TEAM):
            self.assertEqual(self.client.get("/team/intelligenz").status_code, 403)
