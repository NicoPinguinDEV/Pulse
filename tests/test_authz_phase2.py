"""AUTHZ-7 / 8 / 9 / 10 regression tests (remediation phase 2).

* AUTHZ-7: warning counters in analytics exports only for viewers who may see warnings.
* AUTHZ-8: /export/logs needs the Melonly read gate, not just analytics.
* AUTHZ-9: searches are no oracle for leadership-only data (cases, Roblox profiles, audit trail).
* AUTHZ-10: the suite application routes cannot bypass the decision process.
Negative cases also assert that nothing was written.
"""

import contextlib
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_next
import pulse_ultimate
import webserver as ws
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401

GUILD = NS(id=1, name="T", members=[], roles=[], owner_id=0, get_member=lambda _: None)
ANALYTICS = {"can_view_dashboard": True, "can_view_analytics": True}
ANALYTICS_WARN = dict(ANALYTICS, can_warn=True)
LEAD = dict(ANALYTICS, can_promote=True)


def ctx_for(perms, uid="123"):
    return NS(user={"id": uid, "global_name": "Tester"}, guild=GUILD, guild_name="T", perms=perms,
              config={"team_role_ids": []}, member=NS(id=int(uid), roles=[], top_role=NS(position=1)), msg="", msg_ok=True)


def fake_auth(perms):
    def _auth(request, session, perm="can_view_dashboard", admin=False):
        if perm and not perms.get(perm):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        return ctx_for(perms)
    return _auth


class MelonlyExportTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(ws.app, raise_server_exceptions=False, headers={"Origin": "http://testserver"})
        self.repo = NS(iter_all=mock.Mock(return_value=[{"id": "1", "type": "Notiz", "target_user": "x", "reason": "geheim"}]))
        p = mock.patch.object(ws, "get_melonly_repository", return_value=self.repo)
        p.start()
        self.addCleanup(p.stop)

    def test_analytics_only_is_refused_without_reading_records(self):
        with mock.patch.object(ws, "auth", fake_auth(ANALYTICS)):
            response = self.client.get("/export/logs")
        self.assertEqual(response.status_code, 403)
        self.repo.iter_all.assert_not_called()

    def test_melonly_reader_may_export(self):
        with mock.patch.object(ws, "auth", fake_auth(dict(ANALYTICS, can_warn=True))):
            response = self.client.get("/export/logs")
        self.assertEqual(response.status_code, 200)
        self.assertIn("geheim", response.text)


class WarningCounterTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(ws.app, raise_server_exceptions=False, headers={"Origin": "http://testserver"})
        self.member = NS(id=77, display_name="Mia", status="online", bot=False)
        stats = {"score": 80, "weekly_seconds": 3600, "total_seconds": 7200, "closed_tickets": 2, "warnings": 3,
                 "training_passed": 1}
        patches = [mock.patch.object(pulse_ultimate, "team_members", lambda g: [self.member]),
                   mock.patch.object(pulse_ultimate, "member_stats", lambda m, shared=None: stats),
                   mock.patch.object(pulse_ultimate, "stats_context", lambda: {})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_metrics_hide_warning_counts_without_permission(self):
        with mock.patch.object(pulse_ultimate, "ctx_auth", lambda *a, **k: ctx_for(ANALYTICS)):
            data = self.client.get("/ultimate/metrics").json()
        self.assertIsNone(data["members"][0]["warnings"])

    def test_metrics_show_warning_counts_to_warners(self):
        with mock.patch.object(pulse_ultimate, "ctx_auth", lambda *a, **k: ctx_for(ANALYTICS_WARN)):
            data = self.client.get("/ultimate/metrics").json()
        self.assertEqual(data["members"][0]["warnings"], 3)


class SearchOracleTests(unittest.TestCase):
    def test_next_search_hides_cases_and_roblox_from_non_leads(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE ultimate_roblox_profiles(roblox_id,username,display_name,status);
            INSERT INTO ultimate_roblox_profiles VALUES('1','geheimname','x','ok');
            CREATE TABLE next_ideas(id,title,status,category,body,updated_at);
            CREATE TABLE next_announcements(id,title,priority,created_at,body);""")
        cases = mock.Mock(return_value=[{"case_no": "TF-1", "title": "geheimname"}])
        with mock.patch.object(pulse_next, "get_cases_for_search", cases), \
                mock.patch.object(pulse_next, "members", lambda g: []), \
                mock.patch.object(pulse_next.u, "cx", lambda: contextlib.nullcontext(conn)), \
                mock.patch.object(pulse_next.db, "list_tickets", lambda **k: []), \
                mock.patch.object(pulse_next.db, "list_tasks", lambda **k: []):
            plain = pulse_next.get_search_results(ctx_for(ANALYTICS), "geheimname")
            lead = pulse_next.get_search_results(ctx_for(LEAD), "geheimname")
        self.assertEqual((plain["cases"], plain["roblox"]), ([], []))
        self.assertTrue(lead["cases"] and lead["roblox"])
        self.assertEqual(cases.call_count, 1, "case search must not even run for non-leads")

    def test_suite_search_skips_audit_and_cases_for_non_leads(self):
        from cogs import pulse_suite as suite
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(suite, "DB_PATH", str(Path(tmp.name) / "s.db")):
            suite.init_db()
            with suite.cx() as c:
                c.execute("INSERT INTO suite_cases(id) VALUES('FALL-geheimwort')")
            with mock.patch.object(suite, "members", lambda g, w: []), \
                    mock.patch.object(ws, "load_json", lambda *a, **k: {}), \
                    mock.patch.object(ws, "can_review_applications", lambda c: False), \
                    mock.patch.object(suite.db, "list_tickets", lambda **k: []), \
                    mock.patch.object(suite.db, "list_tasks", lambda **k: []):
                plain = suite.search_all("geheimwort", ctx_for(ANALYTICS))
                lead = suite.search_all("geheimwort", ctx_for(LEAD))
        self.assertFalse([r for r in plain if r["type"] == "Fall"])
        self.assertTrue([r for r in lead if r["type"] == "Fall"])


class SuiteApplicationBypassTests(unittest.TestCase):
    def setUp(self):
        from cogs import pulse_suite as suite
        self.suite = suite
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.apps = {"A1": {"status": "rejected", "name": "x"}, "A2": {"status": "pending", "name": "y"}}
        self.writes = []
        repo = NS(update_entry=lambda app_id, **k: self.writes.append((app_id, k["updated"]["status"])))
        for p in (mock.patch.object(suite, "DB_PATH", str(Path(self.tmp.name) / "s.db")),
                  mock.patch.object(suite, "audit", lambda *a, **k: None),
                  mock.patch.object(ws, "load_json", lambda path, default=None: copy.deepcopy(self.apps)),
                  mock.patch.object(ws, "get_applications_repository", lambda: repo),
                  mock.patch.object(suite, "appname", lambda i, a: "x")):
            p.start()
            self.addCleanup(p.stop)
        suite.init_db()
        suite.register_routes()
        self.client = TestClient(ws.app, raise_server_exceptions=False, headers={"Origin": "http://testserver"},
                                 follow_redirects=False)

    def update(self, app_id, status):
        with mock.patch.object(self.suite, "auth", lambda *a, **k: ctx_for(LEAD)):
            return self.client.post(f"/suite/applications/{app_id}/update", data={"status": status})

    def test_decided_application_cannot_be_revived(self):
        self.assertEqual(self.update("A1", "pending").status_code, 409)
        self.assertEqual(self.writes, [])

    def test_accept_and_reject_must_go_through_the_board(self):
        self.assertEqual(self.update("A2", "accepted").status_code, 400)
        self.assertEqual(self.update("A2", "rejected").status_code, 400)
        self.assertEqual(self.writes, [])

    def test_workflow_status_still_allowed(self):
        self.assertEqual(self.update("A2", "in_review").status_code, 303)
        self.assertEqual(self.writes, [("A2", "in_review")])

    def test_interview_cannot_be_created_for_a_decided_application(self):
        with mock.patch.object(self.suite, "auth", lambda *a, **k: ctx_for(LEAD)):
            response = self.client.post("/suite/interview/create", data={
                "application_id": "A1", "auftreten": 80, "kommunikation": 80, "regeln": 80, "aktivitaet": 80, "team": 80})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.writes, [])


if __name__ == "__main__":
    unittest.main()
