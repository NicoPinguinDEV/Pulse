"""Regression guards for the Pulse dashboard navigation and visual refresh.

These source-level checks complement the browser tests: they protect existing route
entries and permission gates while the shared shell is restyled.
"""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "pulse_pro.py").read_text(encoding="utf-8")


class DashboardRedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sidebar = SOURCE.split("def patched_sidebar(", 1)[1].split("user_name =", 1)[0]
        cls.dashboard = SOURCE.split("async def dashboard_v5(", 1)[1].split("return render_pro_page(ws,'Übersicht'", 1)[0]

    def test_primary_navigation_has_six_clear_groups(self):
        for group in ("Mein Bereich", "Team", "Support", "Recruiting", "Planung", "Auswertung"):
            with self.subTest(group=group):
                self.assertIn(f'("{group}", [', self.sidebar)

    def test_existing_main_routes_remain_in_navigation(self):
        for path in (
            "/me", "/dashboard", "/pulse-inbox", "/team", "/team-status",
            "/activity-check", "/melonly", "/warns", "/wiki", "/tickets",
            "/voice-support", "/applications", "/meetings", "/meetings-history",
            "/calendar", "/loa", "/training", "/stats", "/achievements", "/factions",
        ):
            with self.subTest(path=path):
                self.assertIn(f'"{path}"', self.sidebar)

    def test_extended_navigation_remains_permission_filtered(self):
        self.assertIn("from pulse_design import SUITE_NAV, ULTIMATE_NAV, visible_nav", self.sidebar)
        self.assertIn("entries = visible_nav(items, leader)", self.sidebar)
        self.assertIn("pulse-advanced-group", self.sidebar)
        self.assertIn('str(current_page).startswith(("u:", "s:"))', self.sidebar)

    def test_removed_workflow_shortcuts_stay_out_of_sidebar(self):
        self.assertIn('hidden_keys = {"handover", "approvals", "tasks2"} if prefix == "u" else {"tasks"}', self.sidebar)
        self.assertIn("item for item in visible_nav(items, leader) if item[0] not in hidden_keys", self.sidebar)

    def test_sensitive_navigation_gates_are_preserved(self):
        self.assertIn('perms.get("can_manage_checkins") or perms.get("is_admin")', self.sidebar)
        self.assertIn('perms.get("can_warn") or perms.get("is_admin")', self.sidebar)
        self.assertIn('perms.get("can_view_analytics",False)', self.sidebar)
        self.assertIn("from pulse_admin import ADMIN_PAGE_KEYS, admin_sections", self.sidebar)

    def test_shared_theme_uses_coral_and_charcoal_tokens(self):
        css = SOURCE.split('PRO_CSS = r"""', 1)[1].split('"""', 1)[0]
        self.assertIn("--p:#FF765C", css)
        self.assertIn("--p2:#FF947B", css)
        self.assertIn("--bg:#0D0F12", css)
        self.assertIn("--surface:#15191F", css)
        self.assertIn("Pulse OpenClaw visual system", css)

    def test_dashboard_displays_existing_operational_metrics(self):
        for marker in (
            "data-live=\"active\"", "data-live=\"open_tickets\"",
            "data-live=\"applications\"", "data-live=\"loas\"",
            "Aktive Verwarnungen", "active_warn_total",
            'action="/shift/action"', 'or "/pulse-inbox"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.dashboard)


if __name__ == "__main__":
    unittest.main()
