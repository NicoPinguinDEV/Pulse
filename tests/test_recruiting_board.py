"""Audit #95: recruiting Kanban board – columns, workflow transitions, permissions, guild isolation, conflicts, audit."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote

from fastapi.testclient import TestClient

import pulse_application_review as ar
import pulse_board as pb
import pulse_db
import pulse_flags
import webserver as ws

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
RATINGS = {"a": 3, "b": 4}


def done_review(who):
    return {"reviewer_id": str(who), "reviewer_name": f"R{who}", "ratings": dict(RATINGS), "updated_at": "x"}


class LogicTests(unittest.TestCase):
    def apps(self):
        return {
            "in": {"status": "pending", "user_id": "1", "name": "Neu", "created_at": "2026-11-10T10:00:00+00:00"},
            "rev": {"status": "pending", "user_id": "2", "name": "Geprüft", "created_at": "2026-11-01T10:00:00+00:00",
                    "reviews": {"101": done_review(101)}, "assignments": [{"reviewer_id": "101", "reviewer_name": "Alice"}]},
            "iv": {"status": "interview", "user_id": "3", "name": "Gespräch", "interview_invited_at": "2026-11-08T10:00:00+00:00"},
            "pro": {"status": "hired", "user_id": "4", "name": "Probe", "decided_at": "2026-11-01T10:00:00+00:00"},
            "team": {"status": "accepted", "user_id": "5", "name": "Team", "decided_at": "2026-11-12T10:00:00+00:00"},
            "no": {"status": "rejected", "user_id": "6", "name": "Nein", "decided_at": "2026-11-13T10:00:00+00:00"},
            "old": {"status": "rejected", "user_id": "7", "name": "Alt", "decided_at": "2026-01-01T10:00:00+00:00"},
            "junk": "kaputt", "weird": {"status": "???", "user_id": "8"},
        }

    def columns(self, **kw):
        team = {"4": {"probation": {"active": True, "started_at": "2026-11-01T10:00:00+00:00"}}}
        return pb.board(self.apps(), team, pb.DEFAULT_DEADLINES, NOW, **kw)

    def test_every_column_is_filled_from_the_real_status(self):
        cols = self.columns()
        self.assertEqual({k: [c["id"] for c in v] for k, v in cols.items()},
                         {"eingang": ["in"], "pruefung": ["rev"], "gespraech": ["iv"], "probezeit": ["pro"], "team": ["team"], "abgelehnt": ["no"]})
        self.assertEqual([k for k, _ in pb.STAGES], list(cols))  # no new status values, same six columns
        rev = cols["pruefung"][0]
        self.assertEqual((rev["reviewers"], rev["assigned"], rev["completed"], rev["overdue"]), (["Alice"], 1, 1, True))  # 13 days > 5

    def test_transition_table_matches_what_a_card_offers(self):
        self.assertEqual(pb.allowed_decisions("pending"), {"accept", "reject"})
        self.assertEqual(pb.allowed_decisions("interview"), {"hire_after_interview", "reject_after_interview"})
        for closed in ("hired", "accepted", "rejected", "", None, "bogus"):
            self.assertEqual(pb.allowed_decisions(closed), set(), closed)
        cols = self.columns(can_decide=True)
        offered = {c["id"]: {d["decision"] for d in c["actions"]["decisions"]} for v in cols.values() for c in v}
        self.assertEqual(offered["in"], {"accept", "reject"})
        self.assertEqual(offered["iv"], {"hire_after_interview", "reject_after_interview"})
        for closed in ("pro", "team", "no"):
            self.assertEqual(offered[closed], set())
        self.assertTrue(self.columns(can_decide=True)["gespraech"][0]["actions"]["needs_role"])
        self.assertEqual(self.columns(can_decide=False)["eingang"][0]["actions"]["decisions"], [])  # no right, no actions

    def test_review_gate_blocks_actions_and_invitation_cannot_skip_to_hire(self):
        policy = {"min_reviews": 2}
        card = self.columns(can_decide=True, policy=policy)["pruefung"][0]
        self.assertIn("fehlen unabh", card["actions"]["blocked"])
        enough = {"status": "pending", "reviews": {"1": done_review(1), "2": done_review(2)}}
        self.assertEqual(pb.card_actions(enough, policy, True)["blocked"], "")
        pending_targets = {t for _, t in pb.DECISIONS["pending"].values()}
        self.assertNotIn("team", pending_targets)  # Eingang/Prüfung can never jump to Team without the interview step
        self.assertNotIn("probezeit", pending_targets)

    def test_guild_isolation_and_filters(self):
        apps = self.apps()
        apps["in"]["guild_id"] = "999"
        apps["rev"]["guild_id"] = "1"
        cols = pb.board(apps, {}, pb.DEFAULT_DEADLINES, NOW, guild_id=1)
        ids = {c["id"] for v in cols.values() for c in v}
        self.assertNotIn("in", ids)
        self.assertIn("rev", ids)
        self.assertIn("in", {c["id"] for v in pb.board(apps, {}, pb.DEFAULT_DEADLINES, NOW).values() for c in v})  # legacy: no guild given
        cols = self.columns()
        flat = lambda c: sorted(x["id"] for v in c.values() for x in v)  # noqa: E731
        self.assertEqual(flat(pb.filter_columns(cols, "geprüf")), ["rev"])
        self.assertEqual(flat(pb.filter_columns(cols, "alice")), ["rev"])
        self.assertEqual(flat(pb.filter_columns(cols, "", "overdue")), ["in", "rev"])  # 4 d > 3 d and 13 d > 5 d; the interview (6 d of 7 d) is on time
        self.assertEqual(flat(pb.filter_columns(cols, "", "mine", "101")), ["rev"])
        self.assertEqual(flat(pb.filter_columns(cols, "", "unassigned")), ["in", "iv"])
        self.assertEqual(flat(pb.filter_columns(cols, "", "bogus")), flat(cols))

    def test_stale_reason_and_safe_return(self):
        self.assertEqual(pb.stale_reason({"status": "pending"}, ""), "")
        self.assertEqual(pb.stale_reason({"status": "pending"}, "pending"), "")
        self.assertIn("inzwischen bearbeitet", pb.stale_reason({"status": "interview"}, "pending"))
        self.assertEqual(pb.safe_return("/applications/board"), "/applications/board")
        for evil in ("https://evil.example", "//evil.example", "/admin", "", None):
            self.assertEqual(pb.safe_return(evil), "/applications")


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.apps_file = root / "applications.json"
        self.apps = {
            "app1": {"status": "pending", "user_id": "900", "name": "Bewerber", "career_label": "Support", "reviews": {},
                     "created_at": "2026-11-12T10:00:00+00:00"},
            "app2": {"status": "interview", "user_id": "901", "name": "Gespräch", "career_label": "Support", "reviews": {},
                     "interview_invited_at": "13.11.2026 10:00", "created_at": "2026-11-01T10:00:00+00:00"},
        }
        self.save()
        self.audit = []
        self.patches = [patch.object(ws, "APPS_FILE", str(self.apps_file)), patch.object(ws, "DATA_FILE", str(root / "team.json")),
                        patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
                        patch.object(ws, "log_audit", side_effect=lambda *a, **k: self.audit.append(a) or "ref"),
                        patch.object(ws, "send_dm_notification", AsyncMock()), patch.object(ws, "send_team_update_embed", AsyncMock()),
                        patch.object(pulse_flags, "enabled", lambda key: True)]
        for item in self.patches:
            item.start()
        pulse_db.init_db()
        people = {i: SimpleNamespace(id=i, display_name=n, bot=False, roles=[]) for i, n in
                  ((101, "Alice"), (102, "Bob"), (900, "Bewerber"), (901, "Gespräch"))}
        self.guild = SimpleNamespace(id=1, name="G", members=list(people.values()), roles=[], get_member=lambda i: people.get(int(i)),
                                     get_channel=lambda _i: None, get_role=lambda _i: None, text_channels=[], channels=[], me=None)
        self.who = "5"
        self.lead = {"can_promote": True, "is_admin": False, "can_manage_applications": True, "can_view_dashboard": True}
        self.current_perms = self.lead
        self.member_perms = {101: {"can_manage_applications": True}, 102: {"can_manage_applications": True}}

        def fake_auth(request, session, perm="can_view_dashboard", admin=False):
            return SimpleNamespace(user={"id": self.who, "global_name": "U", "username": "u"}, perms=self.current_perms, guild=self.guild,
                                   config={}, msg="", msg_ok=True, guild_name="G", member=None)
        self.extra = [patch.object(ws, "auth", fake_auth),
                      patch.object(ws, "compute_perms", lambda guild, uid, cfg: (self.member_perms.get(int(uid), {}), None))]
        for item in self.extra:
            item.start()
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        for item in reversed(self.extra + self.patches):
            item.stop()
        self.tmp.cleanup()

    def save(self):
        self.apps_file.write_text(json.dumps(self.apps), encoding="utf-8")

    def stored(self):
        return json.loads(self.apps_file.read_text(encoding="utf-8"))

    def post(self, path="/action", **data):
        return self.client.post(path, data=data, follow_redirects=False)

    def reject(self, app_id="app1", **extra):
        return self.post(action="decide_app", app_id=app_id, decision="reject", action_reason="Kein Fit",
                         return_to="/applications/board", **extra)

    def test_board_page_renders_all_columns_cards_and_real_forms(self):
        page = self.client.get("/applications/board").text
        for label in ("Eingang", "Prüfung", "Gespräch", "Probezeit", "Team", "Abgelehnt"):
            self.assertIn(label, page)
        self.assertIn("Bewerber", page)
        self.assertIn('name="expected_status" value="pending"', page)
        self.assertIn('value="reject"', page)
        self.assertIn("Einstiegsrolle", page)  # the interview card needs a hire role
        self.assertIn("Prüfer zuweisen", page)
        self.assertIn('role="search"', page)
        self.assertIn("Keine Bewerbungen in dieser Spalte.", page)
        self.assertIn("Keine Bewerbung entspricht", self.client.get("/applications/board?q=zzzz").text)

    def test_permissions_are_enforced_by_the_server_not_the_page(self):
        self.current_perms = {"can_view_dashboard": True, "can_promote": False, "is_admin": False}
        self.assertEqual(self.client.get("/applications/board").status_code, 403)
        self.assertEqual(self.post(action="decide_app", app_id="app1", decision="reject", action_reason="x").status_code, 403)
        self.assertEqual(self.stored()["app1"]["status"], "pending")
        self.current_perms = {"can_manage_applications": True, "can_view_dashboard": True, "can_promote": False, "is_admin": False}  # recruiting staff, not leadership
        page = self.client.get("/applications/board").text
        self.assertNotIn("Prüfer zuweisen", page)
        self.assertEqual(self.post("/applications/assign", app_id="app1", reviewer_ids=["101"]).status_code, 403)

    def test_reject_from_board_goes_through_the_existing_flow_and_returns_to_the_board(self):
        response = self.reject(expected_status="pending")
        location = unquote(response.headers["location"])
        self.assertTrue(location.startswith("/applications/board?"), location)
        self.assertIn("t=ok", location)
        saved = self.stored()["app1"]
        self.assertEqual((saved["status"], saved["decision_reason"]), ("rejected", "Kein Fit"))
        self.assertTrue(saved["decided_by"])
        self.assertTrue(any(entry[2] == "Bewerbung Entschieden" for entry in self.audit))  # revisable: audit entry with reason
        self.assertIn("Kein Fit", [entry[3] for entry in self.audit if entry[2] == "Bewerbung Entschieden"][0])

    def test_old_form_without_return_to_still_goes_to_applications(self):
        response = self.post(action="decide_app", app_id="app1", decision="reject", action_reason="Alt")
        self.assertTrue(response.headers["location"].startswith("/applications?"))
        evil = self.post(action="decide_app", app_id="app2", decision="reject_after_interview", action_reason="x", return_to="https://evil.example")
        self.assertTrue(evil.headers["location"].startswith("/applications?"))

    def test_invalid_transitions_and_ids_change_nothing(self):
        cases = [("app1", "hire_after_interview"), ("app1", "reject_after_interview"), ("app2", "accept"), ("app2", "reject"),
                 ("app1", "bogus"), ("nope", "reject"), ("", "reject"), ("../x", "reject")]
        for app_id, decision in cases:
            response = self.post(action="decide_app", app_id=app_id, decision=decision, action_reason="x", return_to="/applications/board")
            self.assertIn("t=err", response.headers["location"], (app_id, decision))
            self.assertTrue(response.headers["location"].startswith("/applications/board?"))
        self.assertEqual(self.stored(), self.apps)  # nothing was written
        self.assertEqual(self.audit, [])
        missing_reason = self.post(action="decide_app", app_id="app1", decision="reject", action_reason="   ", return_to="/applications/board")
        self.assertIn("t=err", missing_reason.headers["location"])
        self.assertEqual(self.stored()["app1"]["status"], "pending")

    def test_hire_needs_a_valid_role_and_cannot_skip_the_interview(self):
        no_role = self.post(action="decide_app", app_id="app2", decision="hire_after_interview", action_reason="gut", return_to="/applications/board")
        self.assertIn("t=err", no_role.headers["location"])
        self.assertEqual(self.stored()["app2"]["status"], "interview")
        pending_hire = self.post(action="decide_app", app_id="app1", decision="hire_after_interview", action_reason="x", hire_role_id="5")
        self.assertIn("t=err", pending_hire.headers["location"])
        self.assertEqual(self.stored()["app1"]["status"], "pending")

    def test_review_gate_still_blocks_decisions_from_the_board(self):
        pulse_db.set_setting(ar.POLICY_KEY, {"min_reviews": 2})
        self.assertIn("fehlen unabh", self.client.get("/applications/board").text)
        blocked = self.reject()
        self.assertIn("fehlen unabh", unquote(blocked.headers["location"]))
        self.assertEqual(self.stored()["app1"]["status"], "pending")
        self.apps["app1"]["reviews"] = {"101": done_review(101), "102": done_review(102)}
        self.save()
        self.assertIn("t=ok", self.reject().headers["location"])

    def test_double_submit_and_concurrent_change_are_rejected_safely(self):
        first = self.reject(expected_status="pending")
        self.assertIn("t=ok", first.headers["location"])
        second = self.reject(expected_status="pending")  # the same form submitted again
        self.assertIn("t=err", second.headers["location"])
        self.assertIn("inzwischen bearbeitet", unquote(second.headers["location"]))
        self.assertEqual(len([e for e in self.audit if e[2] == "Bewerbung Entschieden"]), 1)  # decided exactly once
        # somebody else moved app2 to the interview stage while another user still saw "pending"
        stale = self.post(action="decide_app", app_id="app2", decision="reject_after_interview", action_reason="x",
                          expected_status="pending", return_to="/applications/board")
        self.assertIn("inzwischen bearbeitet", unquote(stale.headers["location"]))
        self.assertEqual(self.stored()["app2"]["status"], "interview")

    def test_write_conflict_from_a_concurrent_change_is_reported_and_nothing_is_stored(self):
        from pulse_applications_repository import ApplicationRepositoryConflict
        repository = ws.get_applications_repository()
        with patch.object(repository, "update_entry", side_effect=ApplicationRepositoryConflict("geändert")):
            response = self.reject(expected_status="pending")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.stored()["app1"]["status"], "pending")
        self.assertEqual([e for e in self.audit if e[2] == "Bewerbung Entschieden"], [])
        self.assertIn("t=ok", self.reject(expected_status="pending").headers["location"])  # and a clean retry works

    def test_guild_isolation_for_page_and_actions(self):
        self.apps["app1"]["guild_id"] = "999"
        self.save()
        self.assertNotIn("Bewerber</div>", self.client.get("/applications/board").text)
        response = self.reject()
        self.assertIn("nicht gefunden", unquote(response.headers["location"]))
        self.assertEqual(self.stored()["app1"]["status"], "pending")
        assign = self.post("/applications/assign", app_id="app1", reviewer_ids=["101"], return_to="/applications/board")
        self.assertIn("nicht gefunden", unquote(assign.headers["location"]))
        self.assertNotIn("assignments", self.stored()["app1"])

    def test_assignment_from_board_keeps_reviews_and_returns_to_board(self):
        self.apps["app1"]["reviews"] = {"102": done_review(102)}
        self.save()
        response = self.post("/applications/assign", app_id="app1", reviewer_ids=["101", "102"], return_to="/applications/board", expected_status="pending")
        self.assertTrue(response.headers["location"].startswith("/applications/board?"))
        saved = self.stored()["app1"]
        self.assertEqual([a["reviewer_id"] for a in saved["assignments"]], ["101", "102"])
        self.assertIn("102", saved["reviews"])  # independent review untouched
        self.assertTrue(any(entry[2] == "Bewerbung zugewiesen" for entry in self.audit))
        stale = self.post("/applications/assign", app_id="app1", reviewer_ids=["101"], return_to="/applications/board", expected_status="interview")
        self.assertIn("inzwischen bearbeitet", unquote(stale.headers["location"]))
        self.assertEqual(len(self.stored()["app1"]["assignments"]), 2)

    def test_board_shows_only_counts_of_reviews_never_ratings_of_others(self):
        self.apps["app1"]["reviews"] = {"102": {**done_review(102), "recommendation": "reject", "notes": "GEHEIMNOTIZ"}}
        self.save()
        page = self.client.get("/applications/board").text
        self.assertIn("Bewertungen 1/", page)
        self.assertNotIn("GEHEIMNOTIZ", page)
        self.assertNotIn("Ablehnen 1", page)

    def test_unreadable_data_shows_an_error_state_not_a_crash(self):
        with patch.object(ws, "load_json", side_effect=OSError("kaputt")):
            response = self.client.get("/applications/board")
        self.assertEqual(response.status_code, 200)
        self.assertIn("nicht lesbar", response.text)

    def test_existing_applications_page_and_forms_still_work(self):
        page = self.client.get("/applications")
        self.assertEqual(page.status_code, 200)
        self.assertIn('name="decision" value="accept"', page.text)
        self.assertIn("hire_after_interview", page.text)


if __name__ == "__main__":
    unittest.main()
