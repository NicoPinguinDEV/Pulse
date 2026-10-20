"""Regression tests for role-gated public recruiting and staff notifications."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_web() -> str:
    return (ROOT / "webserver.py").read_text(encoding="utf-8")


def function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function {name!r} was not found")


IDS = (Path(__file__).resolve().parents[1] / "pulse_ids.py").read_text(encoding="utf-8")


class ApplicationEligibilityTests(unittest.TestCase):
    def test_requested_role_and_notification_targets_are_configured(self):
        source = read_web()
        self.assertIn("APPLICATION_REQUIRED_ROLE_ID = 1531303733311307997", IDS)  # A-09: value in pulse_ids.py
        self.assertIn("APPLICATION_REQUIRED_ROLE_ID = _pulse_ids.APPLICATION_REQUIRED_ROLE_ID", source)
        self.assertIn("APPLICATION_ALERT_CHANNEL_ID = 1558776676731719720", IDS)  # A-09: value in pulse_ids.py
        self.assertIn("APPLICATION_ALERT_CHANNEL_ID = _pulse_ids.APPLICATION_ALERT_CHANNEL_ID", source)
        self.assertIn("APPLICATION_ALERT_ROLE_ID = 1531300183088824340", IDS)  # A-09: value in pulse_ids.py
        self.assertIn("APPLICATION_ALERT_ROLE_ID = _pulse_ids.APPLICATION_ALERT_ROLE_ID", source)

    def test_form_page_requires_live_member_role_check(self):
        source = read_web()
        helper = function_source(source, "verify_application_member")
        public_page = function_source(source, "public_apply_page")
        self.assertIn("guild.get_member(uid)", helper)
        self.assertIn("await guild.fetch_member(uid)", helper)
        self.assertIn("APPLICATION_REQUIRED_ROLE_ID", helper)
        self.assertIn("not any(role.id == APPLICATION_REQUIRED_ROLE_ID for role in member.roles)", helper)
        self.assertIn("await verify_application_member(", public_page)

    def test_submit_handler_rechecks_role_server_side_before_persisting(self):
        source = read_web()
        handler = function_source(source, "handle_action")
        submit_start = handler.index('if action == "submit_application":')
        submit_end = handler.index("# ---------- Ab hier: Login + Recht", submit_start)
        submit = handler[submit_start:submit_end]
        self.assertIn("await verify_application_member(", submit)
        self.assertLess(
            submit.index("await verify_application_member("),
            submit.index("save_json(APPS_FILE, apps)"),
        )
        self.assertIn("str(apply_user.get(\"id\")) != str(applicant_id)", submit)
        self.assertIn("submitted_form = await request.form()", submit)
        self.assertIn('answer_count = len(career_definition["questions"]) if career_definition else 0', submit)
        self.assertIn('str(submitted_form.get(f"answer_{index}", ""))', submit)
        self.assertIn('"test_version": 3', submit)
        self.assertNotIn('[answer_1, answer_2, answer_3, answer_4, answer_5, answer_6]', submit)

    def test_application_form_is_a_guided_candidate_experience(self):
        source = read_web()
        public_page = function_source(source, "public_apply_page")
        self.assertIn('data-apply-wizard novalidate', public_page)
        self.assertIn('data-apply-step="0"', public_page)
        self.assertIn('data-apply-step="1"', public_page)
        self.assertIn('applicationProgressBar', public_page)
        self.assertIn('applicationNext', public_page)
        self.assertIn('validatePanel(panels[active])', public_page)
        self.assertIn('Wird sicher übermittelt', public_page)

    def test_application_review_is_permission_gated_and_validated(self):
        source = read_web()
        review = function_source(source, "save_application_review")
        page = function_source(source, "applications_page")
        self.assertIn('can_manage_applications', review)
        self.assertIn('item.get("status") not in {"pending", "interview"}', review)
        self.assertIn('if score not in range(1, 6)', review)
        self.assertIn('reviews[reviewer_id] =', review)
        self.assertIn('save_json(APPS_FILE, apps)', review)
        self.assertIn('APPLICATION_REVIEW_CRITERIA', page)
        self.assertIn('action="/applications/review"', page)
        self.assertIn('★ Team-Score:', page)

    def test_notification_pings_only_the_configured_role(self):
        source = read_web()
        notifier = function_source(source, "notify_new_application")
        self.assertIn("APPLICATION_ALERT_CHANNEL_ID", notifier)
        self.assertIn("APPLICATION_ALERT_ROLE_ID", notifier)
        self.assertIn("await send_styled(", notifier)
        self.assertIn("discord.AllowedMentions(", notifier)
        self.assertIn("roles=[alert_role] if alert_role else []", notifier)
        self.assertIn("users=False", notifier)
        self.assertIn("everyone=False", notifier)
        self.assertIn("await notify_new_application(request, app_id_new, apps[app_id_new])", function_source(source, "handle_action"))

    def test_application_managers_can_see_decision_controls(self):
        source = read_web()
        applications = function_source(source, "applications_page")
        self.assertIn('can_decide = ctx.perms["can_manage_applications"] or ctx.perms["can_promote"] or ctx.perms["is_admin"]', applications)

    def test_accepting_application_only_invites_to_mandatory_interview(self):
        source = read_web()
        self.assertIn("APPLICATION_ROLE_ID = 1496070682126188604", IDS)  # A-09: value in pulse_ids.py
        self.assertIn("APPLICATION_ROLE_ID = _pulse_ids.APPLICATION_ROLE_ID", source)
        handler = function_source(source, "handle_action")
        self.assertIn('"pending": {"accept", "reject"}', handler)
        self.assertIn('"interview": {"hire_after_interview", "reject_after_interview"}', handler)
        invite_start = handler.index('if decision == "accept":')
        hire_start = handler.index('if decision == "hire_after_interview":', invite_start)
        invite = handler[invite_start:hire_start]
        hire_end = handler.index('if decision == "reject_after_interview"', hire_start)
        hire = handler[hire_start:hire_end]
        self.assertIn("application_role = guild.get_role(APPLICATION_ROLE_ID)", invite)
        self.assertIn("await applicant.add_roles(", invite)
        self.assertIn('item["status"] = "interview"', invite)
        self.assertNotIn("hired_role_id", invite)
        self.assertNotIn('team_entry["team_since"]', invite)
        self.assertIn("Vor einer Einstellung muss verpflichtend ein Bewerbungsgespräch stattfinden.", invite)
        self.assertIn("role.id == APPLICATION_ROLE_ID", hire)
        self.assertIn("await applicant.edit(", hire)
        self.assertIn('item["status"] = "hired"', hire)
        self.assertIn("set(team_role_ids) | {APPLICATION_ROLE_ID}", hire)
        self.assertNotIn('await applicant.kick(', handler)

    def test_applications_page_has_two_stage_interview_workflow(self):
        source = read_web()
        applications = function_source(source, "applications_page")
        self.assertIn('active_application_statuses = {"pending", "interview"}', applications)
        self.assertIn("Zum Bewerbungsgespräch einladen", applications)
        self.assertIn("Bewerbungsgespräch erforderlich", applications)
        self.assertIn("hire_after_interview", applications)
        self.assertIn("reject_after_interview", applications)

    def test_application_oauth_reuses_registered_dashboard_callback(self):
        source = read_web()
        redirect_helper = function_source(source, "oauth_redirect_uri")
        callback = function_source(source, "callback")
        apply_login = function_source(source, "apply_login")

        # A normal dashboard callback is reused by default, avoiding an unregistered
        # /apply/callback URI when only /callback exists in the Discord portal.
        self.assertIn("(REDIRECT_URI or APPLICATION_REDIRECT_URI) if application else REDIRECT_URI", redirect_helper)
        self.assertIn('return f"{request_origin(request)}/callback"', redirect_helper)
        self.assertNotIn("apply/callback' if application", redirect_helper)
        self.assertIn("redirect_apply = oauth_redirect_uri(request, application=True)", apply_login)
        self.assertIn("apply_oauth_state: str = Cookie(None)", callback)
        self.assertIn("application=application_flow", callback)
        self.assertIn('RedirectResponse(url="/apply", status_code=303)', callback)


if __name__ == "__main__":
    unittest.main()
