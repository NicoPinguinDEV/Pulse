"""Audit #70/#117: global event notification on the shared dashboard shell and polling fallback."""
import json
import re
import unittest
from types import SimpleNamespace

import pulse_live_refresh as lr
import pulse_pro


class ScriptTests(unittest.TestCase):
    def test_every_shared_shell_page_gets_the_global_listener(self):
        for page in ("me", "wiki", "tickets", "tasks", "dashboard", "settings", "admin", "unknown", ""):
            with self.subTest(page=page):
                html = lr.script_for(page)
                self.assertIn("pulseLiveBanner", html)
                self.assertIn("/api/events/stream", html)
                self.assertIn("/api/events/poll", html)

    def test_script_uses_all_non_admin_event_domains(self):
        import pulse_eventbus
        html = lr.script_for("wiki")
        match = re.search(r"var domains=(\[[^\]]*\])", html)
        self.assertIsNotNone(match)
        self.assertEqual(tuple(json.loads(match.group(1))), lr.LIVE_DOMAINS)
        self.assertEqual(set(lr.LIVE_DOMAINS), set(pulse_eventbus.DOMAIN_ACTION))

    def test_sse_cursor_and_poll_fallback_are_present(self):
        html = lr.script_for("*")
        for marker in ("ready", "pulse", "reset", "expired", "setInterval(poll,15000)"):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
        self.assertIn("document.hidden", html)
        self.assertIn("credentials:'same-origin'", html)
        self.assertIn("errors>=2", html)

    def test_refresh_is_never_automatic(self):
        html = lr.script_for("tickets")
        self.assertEqual(html.count("location.reload()"), 1)
        self.assertIn("addEventListener('click'", html)

    def test_global_listener_is_injected_by_the_shared_shell_hook(self):
        html = pulse_pro.pro_body_extra()
        self.assertIn("pulseLiveBanner", html)
        self.assertIn("/api/events/poll", html)


class IntegrationTests(unittest.TestCase):
    def render(self, page):
        ws = SimpleNamespace(esc=lambda v: str(v), render_page=lambda title, ctx, active, body, extra="": body)
        ctx = SimpleNamespace(perms={})
        return pulse_pro.render_pro_page(ws, "T", ctx, page, "<p>Inhalt</p>")

    def test_render_pro_page_does_not_add_a_second_page_specific_listener(self):
        html = self.render("tickets")
        self.assertNotIn("pulseLiveBanner", html)
        self.assertIn("<p>Inhalt</p>", html)


if __name__ == "__main__":
    unittest.main()
