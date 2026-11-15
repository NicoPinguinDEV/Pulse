"""Audit #70: Echtzeit-Hinweis auf weiteren Dashboard-Seiten (Skript, Zuordnung, Einbindung in render_pro_page)."""
import json
import re
import unittest
from types import SimpleNamespace

import pulse_live_refresh as lr
import pulse_pro


class ScriptTests(unittest.TestCase):
    def test_pages_without_domains_get_nothing(self):
        for page in ("me", "wiki", "", None, "unbekannt"):
            self.assertEqual(lr.script_for(page), "")

    def test_script_contains_only_the_pages_own_domains_and_no_auto_reload(self):
        html = lr.script_for("tickets")
        self.assertIn("/api/events/stream", html)
        self.assertIn('var domains=["ticket"]', html)
        self.assertIn('role="status"', html)
        self.assertIn("hidden", html)
        self.assertNotIn("location.reload()", html.split("pulseLiveReload")[0])        # nur auf Klick, nie automatisch
        self.assertEqual(html.count("location.reload()"), 1)
        self.assertNotIn("__DOMAINS__", html)

    def test_every_mapped_domain_is_a_valid_event_domain(self):
        import pulse_events
        for page, domains in lr.PAGE_DOMAINS.items():
            self.assertTrue(domains, page)
            for domain in domains:
                self.assertIn(domain, pulse_events.DOMAINS, page)
            self.assertEqual(json.loads(re.search(r"var domains=(\[[^\]]*\])", lr.script_for(page)).group(1)), list(domains))


class IntegrationTests(unittest.TestCase):
    def render(self, page):
        ws = SimpleNamespace(esc=lambda v: str(v), render_page=lambda title, ctx, active, body, extra="": body)
        ctx = SimpleNamespace(perms={})
        return pulse_pro.render_pro_page(ws, "T", ctx, page, "<p>Inhalt</p>")

    def test_live_pages_include_the_banner_and_others_do_not(self):
        self.assertIn("pulseLiveBanner", self.render("tickets"))
        self.assertIn("pulseLiveBanner", self.render("tasks"))
        self.assertNotIn("pulseLiveBanner", self.render("wiki"))
        self.assertIn("<p>Inhalt</p>", self.render("tickets"))


if __name__ == "__main__":
    unittest.main()
