"""Audit #65: Designsystem-Wächter - keine neuen festen Farbwerte in Inline-Stilen (Grundlinie darf nur sinken)."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("design_lint", ROOT / "tools" / "design_lint.py")
lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint)

NEW_MODULES = ("pulse_ack.py", "pulse_ack_pages.py", "pulse_meetings.py", "pulse_meetings_pages.py", "pulse_quiz_admin.py", "pulse_rules_lookup.py",
               "pulse_wiki_review.py", "pulse_event_cards.py", "pulse_poll_cards.py", "pulse_realm.py", "pulse_menu.py", "pulse_live_refresh.py",
               "pulse_home_widgets.py", "pulse_social_log.py", "pulse_testmode.py", "pulse_workflow_flow.py")


class DesignLintTests(unittest.TestCase):
    def test_no_regression_against_the_baseline(self):
        self.assertEqual(lint.main([]), 0, "Neue feste Farben in Inline-Stilen: Tokens/Klassen aus dem Designsystem verwenden (tools/design_lint.py)")

    def test_all_dashboard_pages_are_free_of_inline_colors(self):
        self.assertEqual(lint.measure(), {}, "Feste Farben gehören in Klassen/Tokens des Designsystems (pulse_pro.PRO_CSS, static/pulse.css)")
        baseline = __import__("json").loads((ROOT / "docs" / "design_lint_baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(baseline["total"], 0)

    def test_standalone_pages_are_the_only_exceptions(self):
        self.assertEqual(sorted(lint.STANDALONE_FILES), ["pulse_phase7.py", "pulse_ticket_builder.py"])
        marked = [name for name in ("pulse_ultimate.py", "webserver.py") if lint.MARKER in (ROOT / name).read_text(encoding="utf-8")]
        self.assertEqual(marked, ["pulse_ultimate.py", "webserver.py"])

    def test_new_modules_use_no_inline_colors(self):
        for name in NEW_MODULES:
            self.assertEqual(lint.count(ROOT / name), 0, name)

    def test_the_pattern_finds_what_it_should(self):
        sample = ROOT / "tests" / "__pycache__" / "x.txt"
        self.assertTrue(lint._STYLE.search('<div style="color:#fff">'))
        self.assertTrue(lint._STYLE.search("<div style='margin:0;background:#4338ca'>"))
        self.assertFalse(lint._STYLE.search('<div style="margin:0" class="pulse-card">'))
        self.assertFalse(lint._STYLE.search('<div class="x" data-color="#fff">'))
        del sample


if __name__ == "__main__":
    unittest.main()
