"""Regression tests for the skippable shared-shell team dashboard introduction."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pulse_pro
import pulse_tutorial
import webserver as ws


class TutorialAssetTests(unittest.TestCase):
    def setUp(self):
        self.assets = pulse_tutorial.assets()

    def test_launcher_and_dialog_are_keyboard_accessible(self):
        for marker in (
            'id="pulseTutorialLauncher"',
            'aria-haspopup="dialog"',
            'id="pulseTutorialCard"',
            'role="dialog"',
            'aria-labelledby="pulseTutorialTitle"',
            'aria-describedby="pulseTutorialCopy"',
            'id="pulseTutorialPrevious"',
            'id="pulseTutorialNext"',
            'id="pulseTutorialSkip"',
            'id="pulseTutorialClose"',
            'role="progressbar"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.assets)

    def test_first_run_auto_opens_but_skip_and_completion_are_persisted(self):
        for marker in (
            "pulse.team-intro.v1:",
            "pulse.team-intro.snoozed.v1:",
            "localStorage.setItem(key,'1')",
            "sessionStorage.setItem(key,'1')",
            "if(!readFlag(storageKey)&&!readSessionFlag(snoozeKey))",
            "writeFlag(storageKey,true)",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.assets)
        self.assertIn("window.PulseTeamTutorial", self.assets)

    def test_tour_targets_main_dashboard_functions_and_only_uses_local_links(self):
        for path in (
            "/me", "/dashboard", "/pulse-inbox", "/team", "/tickets",
            "/applications", "/calendar", "/wiki", "/activity-check",
            "/melonly", "/stats", "/ultimate",
        ):
            with self.subTest(path=path):
                self.assertIn(path, self.assets)
        self.assertIn("steps.filter(step=>!step.selector||document.querySelector(step.selector))", self.assets)
        self.assertIn("href.startsWith('/')&&!href.startsWith('//')", self.assets)
        self.assertIn('target="_blank" rel="noopener noreferrer"', self.assets)

    def test_tour_respects_reduced_motion_mobile_and_keyboard_controls(self):
        for marker in (
            "prefers-reduced-motion:reduce",
            "window.innerWidth<768",
            "window.pulseOpenNav",
            "event.key==='Escape'",
            "event.key==='ArrowRight'",
            "event.key==='ArrowLeft'",
            "event.key==='Tab'",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.assets)

    def test_dashboard_user_id_is_escaped_and_scopes_the_completion_flag(self):
        ctx = SimpleNamespace(user={"id": '42" onmouseover="alert(1)'}, perms={})
        with (
            patch("pulse_testmode.banner_html", return_value=""),
            patch("pulse_flags.banner_html", return_value=""),
            patch("pulse_keys.button_html", return_value=""),
            patch("pulse_keys.dialog_html", return_value=""),
            patch.object(pulse_pro, "build_command_modal", return_value=""),
            patch.object(ws, "render_page", side_effect=lambda _title, _ctx, _page, body, _extra: body),
        ):
            rendered = pulse_pro.render_pro_page(ws, "Test", ctx, "me", "<p>Body</p>")
        self.assertIn('data-pulse-user="42&quot; onmouseover=&quot;alert(1)"', rendered)
        self.assertIn("data-pulse-user", rendered)
        self.assertIn("pulse.team-intro.v1:", self.assets)

    def test_shared_shell_hook_injects_the_tutorial(self):
        shared = pulse_pro.pro_body_extra()
        self.assertIn('id="pulseTutorialLauncher"', shared)
        self.assertIn('id="pulseTutorialCard"', shared)
        self.assertIn("PulseTeamTutorial", shared)


if __name__ == "__main__":
    unittest.main()
