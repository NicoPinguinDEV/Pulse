"""Audit #96/#97: reviewer assignment, load balancing and independent (blind) multi-reviewer evaluation."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote

from fastapi.testclient import TestClient

import pulse_application_review as ar
import pulse_db
import webserver as ws

RATINGS = {"a": 3, "b": 4}


def review(who, rec=None):
    item = {"reviewer_id": str(who), "reviewer_name": f"R{who}", "ratings": dict(RATINGS), "notes": "", "updated_at": "x"}
    if rec:
        item["recommendation"] = rec
    return item


class LogicTests(unittest.TestCase):
    def test_assign_validates_dedupes_and_keeps_existing_reviews(self):
        item = {"status": "pending", "reviews": {"101": review(101)}}
        assigned = ar.assign(item, [("101", "A"), ("102", "B"), ("102", "B"), ("x", "bad")], "Leitung", "13.11.2026")
        self.assertEqual([a["reviewer_id"] for a in assigned], ["101", "102"])
        self.assertIn("101", item["reviews"])
        again = ar.assign(item, [("102", "B2"), ("103", "C")], "Zweite", "14.11.2026")
        self.assertEqual(again[0]["assigned_by"], "Leitung")  # kept for people who stay assigned
        self.assertIn("101", item["reviews"])  # review of a removed reviewer is never deleted
        with self.assertRaises(ValueError):
            ar.assign(item, [], "L", "t")
        with self.assertRaises(ValueError):
            ar.assign(item, [(str(100 + i), "x") for i in range(ar.MAX_ASSIGNED + 1)], "L", "t")

    def test_workload_counts_only_open_unreviewed_assignments_and_suggestion_is_stable(self):
        apps = {
            "a": {"status": "pending", "assignments": [{"reviewer_id": "1"}, {"reviewer_id": "2"}]},
            "b": {"status": "interview", "assignments": [{"reviewer_id": "1"}], "reviews": {"1": review(1)}},  # done
            "c": {"status": "hired", "assignments": [{"reviewer_id": "2"}]},  # closed
            "d": {"status": "pending", "assignments": [{"reviewer_id": "1"}]},
            "e": "kaputt",
        }
        load = ar.workload(apps, ["1", "2", "3"])
        self.assertEqual((load["1"], load["2"], load["3"]), (2, 1, 0))
        self.assertEqual(ar.suggest(apps, ["1", "2", "3"], 2), ["3", "2"])
        self.assertEqual(ar.suggest(apps, ["1", "2", "3"], 2, exclude=["3"]), ["2", "1"])
        self.assertEqual(ar.suggest({}, [], 3), [])

    def test_reviews_are_blind_until_the_viewer_has_reviewed_leadership_sees_all(self):
        item = {"reviews": {"1": review(1), "2": review(2)}}
        self.assertEqual(ar.visible_reviews(item, "3", False), {})
        self.assertEqual(set(ar.visible_reviews(item, "1", False)), {"1", "2"})
        self.assertEqual(set(ar.visible_reviews(item, "3", True)), {"1", "2"})
        incomplete = {"reviews": {"1": {"reviewer_id": "1", "ratings": {}}, "x": review(5)}}
        self.assertEqual(ar.completed_reviews(incomplete), {})

    def test_progress_tally_and_decision_gate(self):
        item = {"assignments": [{"reviewer_id": "1"}, {"reviewer_id": "2"}, {"reviewer_id": "3"}],
                "reviews": {"1": review(1, "accept"), "2": review(2, "bogus")}}
        progress = ar.progress(item)
        self.assertEqual((progress["assigned"], progress["completed"], progress["missing"], progress["tally"]),
                         (3, 2, ["3"], {"accept": 1}))
        self.assertEqual(ar.decision_gate(item, {"min_reviews": 0}), "")
        self.assertIn("2 von 3", ar.decision_gate(item, {"min_reviews": 3}))
        self.assertEqual(ar.decision_gate(item, {"min_reviews": 2}), "")
        for bad in ({"min_reviews": 99}, {"min_reviews": "x"}, None):
            self.assertEqual(ar.normalize_policy(bad)["min_reviews"], 0)
        self.assertIsNone(ar.normalize_recommendation("zzz"))


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.apps_file = root / "applications.json"
        self.apps = {"app1": {"status": "pending", "user_id": "900", "name": "Bewerber", "career_label": "Support", "reviews": {}}}
        self.save()
        self.patches = [patch.object(ws, "APPS_FILE", str(self.apps_file)), patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
                        patch.object(ws, "log_audit")]
        for item in self.patches:
            item.start()
        pulse_db.init_db()
        people = {101: SimpleNamespace(id=101, display_name="Alice", bot=False), 102: SimpleNamespace(id=102, display_name="Bob", bot=False),
                  103: SimpleNamespace(id=103, display_name="Cleo", bot=False), 900: SimpleNamespace(id=900, display_name="Bewerber", bot=False)}
        self.guild = SimpleNamespace(id=1, name="G", members=list(people.values()), roles=[], get_member=lambda i: people.get(int(i)),
                                 get_channel=lambda _i: None, get_role=lambda _i: None, text_channels=[], channels=[])
        self.perms = {101: {"can_manage_applications": True}, 102: {"can_manage_applications": True}, 103: {}, 900: {"can_manage_applications": True}}
        self.who = "5"
        self.lead = {"can_promote": True, "can_manage_applications": True, "can_view_dashboard": True}

        def fake_auth(request, session, perm="can_view_dashboard", admin=False):
            perms = self.current_perms
            if admin and not perms.get("is_admin"):
                raise ws.HTTPException(status_code=403, detail="nur Admin")
            return SimpleNamespace(user={"id": self.who, "global_name": "U", "username": "u"}, perms=perms, guild=self.guild,
                                   config={}, msg="", msg_ok=True, guild_name="G")
        self.current_perms = self.lead
        self.extra = [patch.object(ws, "auth", fake_auth),
                      patch.object(ws, "compute_perms", lambda guild, uid, cfg: (self.perms.get(int(uid), {}), None))]
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

    def post(self, path, **data):
        return self.client.post(path, data=data, follow_redirects=False)

    def test_only_leadership_assigns_and_only_eligible_non_applicants(self):
        self.current_perms = {"can_manage_applications": True, "can_view_dashboard": True}
        self.assertEqual(self.post("/applications/assign", app_id="app1", reviewer_ids=["101"]).status_code, 403)
        self.assertNotIn("assignments", self.stored()["app1"])
        self.current_perms = self.lead
        for ids, text in ((["103"], "darf keine"), (["900"], "eigene Bewerbung"), (["999"], "nicht auf dem Server"), ([], "mindestens")):
            r = self.post("/applications/assign", app_id="app1", reviewer_ids=ids)
            self.assertIn("t=err", r.headers["location"], ids)
            self.assertNotIn("assignments", self.stored()["app1"], ids)
        r = self.post("/applications/assign", app_id="app1", reviewer_ids=["101", "102"])
        self.assertIn("t=ok", r.headers["location"])
        self.assertEqual([a["reviewer_id"] for a in self.stored()["app1"]["assignments"]], ["101", "102"])
        self.assertEqual(len([n for n in pulse_db.notifications("101") if "zugewiesen" in n["title"]]), 1)
        self.apps["app1"]["status"] = "hired"
        self.save()
        self.assertIn("t=err", self.post("/applications/assign", app_id="app1", reviewer_ids=["101"]).headers["location"])

    def test_review_stores_recommendation_and_page_is_blind_until_own_review(self):
        self.apps["app1"]["reviews"] = {"102": review(102, "reject")}
        self.save()
        self.who, self.current_perms = "101", {"can_manage_applications": True, "can_view_dashboard": True}
        page = self.client.get("/applications").text
        self.assertIn("nach deiner eigenen Bewertung sichtbar", page)
        self.assertNotIn("Team-Score", page)
        form = {"app_id": "app1", "review_notes": "ok", "recommendation": "accept", "rating_x": "3"}
        from webserver import APPLICATION_REVIEW_CRITERIA
        form.update({f"rating_{key}": "4" for key, _ in APPLICATION_REVIEW_CRITERIA})
        self.assertIn("t=ok", self.post("/applications/review", **form).headers["location"])
        stored = self.stored()["app1"]["reviews"]["101"]
        self.assertEqual(stored["recommendation"], "accept")
        self.assertIn("Team-Score", self.client.get("/applications").text)
        bad = self.post("/applications/review", **{**form, "recommendation": "<script>"})
        self.assertIn("t=ok", bad.headers["location"])
        self.assertNotIn("recommendation", self.stored()["app1"]["reviews"]["101"])  # a junk value is dropped, never stored

    def test_policy_is_admin_only_validated_and_gates_decisions(self):
        self.current_perms = self.lead
        self.assertEqual(self.post("/applications/review-policy", min_reviews="2").status_code, 403)
        self.current_perms = {"is_admin": True, "can_promote": True, "can_view_dashboard": True}
        for bad in ("9", "-1", "abc"):
            self.assertIn("t=err", self.post("/applications/review-policy", min_reviews=bad).headers["location"])
        self.assertEqual(ar.normalize_policy(pulse_db.get_setting(ar.POLICY_KEY, {}))["min_reviews"], 0)
        self.assertIn("t=ok", self.post("/applications/review-policy", min_reviews="2").headers["location"])
        decide = dict(action="decide_app", app_id="app1", decision="reject", action_reason="Begründung")
        blocked = self.post("/action", **decide)
        self.assertIn("t=err", blocked.headers["location"])
        self.assertIn("fehlen unabh", unquote(blocked.headers["location"]))
        self.assertEqual(self.stored()["app1"]["status"], "pending")
        self.apps["app1"]["reviews"] = {"101": review(101), "102": review(102)}
        self.save()
        passed = self.post("/action", **decide)
        self.assertNotIn("fehlen unabh", unquote(passed.headers.get("location", "")))

    def test_leadership_page_offers_assignment_with_load_and_suggestion(self):
        page = self.client.get("/applications").text
        self.assertIn("Prüfer zuweisen", page)
        self.assertIn("Alice · Last 0 · empfohlen", page)
        self.assertNotIn("Cleo", page)  # no review right
        self.assertNotIn("Bewerber · Last", page)  # the applicant is not offered as a reviewer for their own application
        self.current_perms = {"can_manage_applications": True, "can_view_dashboard": True}
        self.assertNotIn("Prüfer zuweisen", self.client.get("/applications").text)


if __name__ == "__main__":
    unittest.main()
