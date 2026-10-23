"""K-01: long applications are not lost (3 h sliding session, browser draft, error pages keep the answers)."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

from fastapi.testclient import TestClient

import webserver as ws

# Browsers always send Origin on POST; cookie POSTs without it are refused (pulse_security, audit H1).
BROWSER = {"Origin": "http://testserver"}

CAREER = next(iter(ws.APPLICATION_CAREERS))


def apply_token(seconds_left=600, uid="4242"):
    return ws.sign_payload({"id": uid, "username": "max", "global_name": "Max", "avatar": None,
                            "apply_exp": int(time.time() + seconds_left)}, typ=ws.TOKEN_APPLY)


async def eligible(request, user_id):
    return True, 200, None, ""


class ApplySessionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(ws.app, headers=BROWSER)
        self.patches = [patch.object(ws, "verify_application_member", eligible), patch("builtins.print")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_session_lasts_three_hours_and_slides_on_form_load(self):
        self.assertEqual(ws.APPLY_SESSION_SECONDS, 3 * 3600)
        self.client.cookies.set("apply_session", apply_token(seconds_left=60))
        response = self.client.get(f"/apply?career={CAREER}")
        self.assertEqual(response.status_code, 200)
        renewed = ws.verify_session_token(response.cookies.get("apply_session"), ws.TOKEN_APPLY)
        self.assertGreater(renewed["apply_exp"], time.time() + 3 * 3600 - 60)
        self.assertEqual(renewed["typ"], ws.TOKEN_APPLY)

    def test_renewal_never_exceeds_twelve_hours_after_login(self):
        login_at = int(time.time()) - 11 * 3600  # logged in 11 h ago
        token = ws.sign_payload({"id": "4242", "apply_exp": int(time.time() + 600), "login_at": login_at}, typ=ws.TOKEN_APPLY)
        self.client.cookies.set("apply_session", token)
        renewed = ws.verify_session_token(self.client.post("/apply/keepalive").cookies.get("apply_session"), ws.TOKEN_APPLY)
        self.assertEqual(renewed["apply_exp"], login_at + 12 * 3600)
        self.assertEqual(renewed["login_at"], login_at)
        # after 12 h the session is over and the draft page asks for a new login
        self.client.cookies.clear()
        self.client.cookies.set("apply_session", ws.sign_payload(
            {"id": "4242", "apply_exp": login_at + 12 * 3600 - 13 * 3600, "login_at": login_at - 3600}, typ=ws.TOKEN_APPLY))
        self.assertEqual(self.client.post("/apply/keepalive").status_code, 401)

    def test_form_has_browser_draft_and_keepalive(self):
        self.client.cookies.set("apply_session", apply_token())
        html = self.client.get(f"/apply?career={CAREER}").text
        self.assertIn(f'"pulse:apply-draft:v1:4242:{CAREER}"', html)
        self.assertIn("localStorage.setItem", html)
        self.assertIn("/apply/keepalive", html)
        self.assertIn(f"/apply/login?career={CAREER}", html)

    def test_keepalive_renews_or_reports_expiry(self):
        self.client.cookies.set("apply_session", apply_token(seconds_left=30))
        ok = self.client.post("/apply/keepalive")
        self.assertEqual(ok.status_code, 200)
        self.assertIsNotNone(ws.verify_session_token(ok.cookies.get("apply_session"), ws.TOKEN_APPLY))
        self.client.cookies.clear()
        self.client.cookies.set("apply_session", apply_token(seconds_left=-5))
        self.assertEqual(self.client.post("/apply/keepalive").status_code, 401)
        # a dashboard token is no application session
        self.client.cookies.clear()
        self.client.cookies.set("apply_session", ws.sign_payload({"id": "1", "exp": int(time.time() + 60)}, typ=ws.TOKEN_DASHBOARD))
        self.assertEqual(self.client.post("/apply/keepalive").status_code, 401)

    def test_expired_session_on_submit_leads_to_login_that_returns_to_the_form(self):
        self.client.cookies.set("apply_session", apply_token(seconds_left=-5))
        with patch.object(ws, "application_module_states", lambda *_: {CAREER: True}):
            response = self.client.post("/action", data={"action": "submit_application", "applicant_id": "4242",
                                                         "career_path": CAREER, "applicant_text": "Motivation"})
        self.assertEqual(response.status_code, 403)
        self.assertIn(f"/apply/login?career={CAREER}", response.text)
        self.assertIn("Antworten", response.text)
        login = self.client.get(f"/apply/login?career={CAREER}", follow_redirects=False)
        self.assertEqual(login.cookies.get(ws.APPLY_RETURN_COOKIE), CAREER)
        evil = self.client.get("/apply/login?career=https://evil.example", follow_redirects=False)
        self.assertIsNone(evil.cookies.get(ws.APPLY_RETURN_COOKIE))

    def test_return_path_only_for_known_careers(self):
        request = type("R", (), {"cookies": {ws.APPLY_RETURN_COOKIE: CAREER}})()
        self.assertEqual(ws.apply_return_path(request), f"/apply?career={CAREER}")
        request.cookies = {ws.APPLY_RETURN_COOKIE: "//evil.example"}
        self.assertEqual(ws.apply_return_path(request), "/apply")

    def test_validation_error_links_back_to_the_filled_form(self):
        self.client.cookies.set("apply_session", apply_token())
        with patch.object(ws, "application_module_states", lambda *_: {CAREER: True}):
            response = self.client.post("/action", data={"action": "submit_application", "applicant_id": "4242",
                                                         "career_path": CAREER, "applicant_text": ""})
        self.assertEqual(response.status_code, 400)
        self.assertIn(f'href="/apply?career={CAREER}"', response.text)
        self.assertIn("deine Antworten sind noch da", response.text)

    def test_application_submission_then_staff_review_persists_a_complete_workflow(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(ws, "CONFIG_FILE", str(Path(tmp) / "config.json")), \
                patch.object(ws, "APPS_FILE", str(Path(tmp) / "applications.json")), \
                patch.object(ws, "application_module_states", lambda *_: {CAREER: True}), \
                patch.object(ws, "verify_application_member", eligible), \
                patch.object(ws, "rate_limited", return_value=False), \
                patch.object(ws, "grade_career_test", return_value={
                    "career_path": CAREER, "career_label": "Testlaufbahn", "score": 1, "total": 1,
                    "percent": 100, "pass_percent": 75, "passed": True, "details": [],
                }), \
                patch.object(ws, "grade_practical_tasks", return_value={"answers": []}), \
                patch.object(ws, "notify_new_application", new_callable=AsyncMock):
            self.client.cookies.set("apply_session", apply_token(uid="4242"))
            answers = {
                f"answer_{index}": "Antwort"
                for index in range(1, len(ws.APPLICATION_CAREERS[CAREER]["questions"]) + 1)
            }
            response = self.client.post("/action", data={
                "action": "submit_application",
                "applicant_id": "4242",
                "applicant_text": "Ich möchte das Team unterstützen.",
                "career_path": CAREER,
                **answers,
            })
            self.assertEqual(response.status_code, 200)
            self.assertIn("Bewerbung erfolgreich abgesendet", response.text)

            applications_path = Path(ws.APPS_FILE)
            applications = json.loads(applications_path.read_text(encoding="utf-8"))
            self.assertEqual(len(applications), 1)
            app_id, application = next(iter(applications.items()))
            self.assertEqual(application["status"], "pending")
            self.assertEqual(application["user_id"], "4242")

            reviewer = SimpleNamespace(
                perms={"can_manage_applications": True},
                user={"id": "900", "global_name": "Leitung"},
            )
            ratings = {
                f"rating_{key}": "4"
                for key, _label in ws.APPLICATION_REVIEW_CRITERIA
            }
            with patch.object(ws, "auth", return_value=reviewer):
                reviewed = self.client.post("/applications/review", data={
                    "app_id": app_id,
                    "review_notes": "Strukturierte Antworten.",
                    **ratings,
                }, follow_redirects=False)
            self.assertEqual(reviewed.status_code, 303)

            saved = json.loads(applications_path.read_text(encoding="utf-8"))[app_id]
            self.assertEqual(saved["reviews"]["900"]["reviewer_name"], "Leitung")
            self.assertEqual(set(saved["reviews"]["900"]["ratings"].values()), {4})


if __name__ == "__main__":
    unittest.main()
