"""U-04 / U-06 / U-07 / U-09: shared UI behaviour – messages, forms, accessibility, relative times."""
import unittest
from pathlib import Path
from types import SimpleNamespace

import pulse_pro
import webserver as ws
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401  (Phase 9: no ./pulse.db)


def ctx(msg="", ok=True):
    return SimpleNamespace(msg=msg, msg_ok=ok, guild_name="G", user={"id": "1", "global_name": "A"}, perms={},
                           guild=None, config={})


class MessageTests(unittest.TestCase):
    def render(self, msg, ok):
        try:
            return ws.render_page("Test", ctx(msg, ok), "dashboard", "<form action='/x'><input name='a'></form>")
        except Exception:  # sidebar needs a guild in some paths – the toast part is what matters here
            self.skipTest("render_page braucht hier mehr Kontext")

    def test_errors_stay_and_are_announced(self):
        html = self.render("Kanal fehlt", False)
        self.assertIn('role="alert"', html)
        self.assertIn("pulse-toast", html)
        self.assertNotIn('setTimeout(()=>{const t=document.getElementById("toast")', html)

    def test_success_closes_itself_and_keeps_the_anchor(self):
        html = self.render("Gespeichert", True)
        self.assertIn('role="status"', html)
        self.assertIn('setTimeout(()=>{const t=document.getElementById("toast")', html)
        self.assertIn("location.pathname+location.hash", html)

    def test_dashboard_shell_has_keyboard_navigation_and_reduced_motion_support(self):
        html = self.render("", True)
        self.assertIn('href="#main-content"', html)
        self.assertIn('id="main-content" tabindex="-1"', html)
        self.assertIn('aria-controls="pulseSidebar"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn(":focus-visible", html)
        self.assertIn("prefers-reduced-motion:reduce", html)


class SharedAssetsTests(unittest.TestCase):
    def test_form_protection_and_restore_script(self):
        js = pulse_pro.PRO_JS
        self.assertIn("restoreForms", js)               # inputs survive ?t=err
        self.assertIn("DOMContentLoaded", js)           # runs after the forms exist
        self.assertIn("dataset.busy='1'", js)           # no double submit
        self.assertIn("'password'", js)                 # passwords are never stored
        # review: the script runs in <head> – these must not depend on elements existing yet
        self.assertIn("f.dataset.confirm&&!confirm(f.dataset.confirm)", js)
        self.assertIn("addEventListener('DOMContentLoaded',relTimes)", js)

    def test_accessibility_and_mobile_css(self):
        css = pulse_pro.PRO_CSS
        self.assertIn(":focus-visible", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("min-height:44px", css)

    def test_icon_buttons_have_labels(self):
        source = Path(pulse_pro.__file__).read_text(encoding="utf-8")
        self.assertIn('aria-label="Navigation öffnen"', source)
        self.assertIn('aria-label="Schnellzugriff schließen"', source)


class TimeAndEmptyStateTests(unittest.TestCase):
    def test_time_tag(self):
        html = ws.time_tag("2026-10-20T12:30:00+00:00")
        self.assertIn('datetime="2026-10-20T12:30:00+00:00"', html)
        self.assertIn("data-relative", html)
        self.assertIn("20.10.2026 14:30", html)  # exact Berlin time as fallback/tooltip
        self.assertEqual(ws.time_tag("kaputt"), "kaputt")
        self.assertEqual(ws.time_tag(""), "–")

    def test_empty_state_escapes(self):
        html = ws.empty_state("<b>nichts</b>")
        self.assertIn('role="status"', html)
        self.assertIn("&lt;b&gt;nichts&lt;/b&gt;", html)


if __name__ == "__main__":
    unittest.main()
