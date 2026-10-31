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

    def test_primary_navigation_groups_are_collapsible_and_open_when_active(self):
        self.assertIn('class="pulse-nav-group pulse-primary-group"', self.sidebar)
        self.assertIn("any(item[0] == current_page for item in visible)", self.sidebar)
        self.assertIn('{" open" if is_open else ""}', self.sidebar)

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
        self.assertIn("item for item in visible_nav(items, leader) if item[0] not in hidden_keys", self.sidebar)
        self.assertIn("pulse-advanced-group", self.sidebar)
        self.assertIn('str(current_page).startswith(("u:", "s:"))', self.sidebar)

    def test_sidebar_collapse_is_persistent_and_accessible(self):
        # The button and its script are emitted after the navigation function's local
        # string-building section, so inspect the complete module rather than the
        # truncated sidebar slice used for the navigation entries above.
        self.assertIn('id="pulseSidebarCollapseBtn"', SOURCE)
        self.assertIn("function pulseToggleSidebarCollapsed()", SOURCE)
        self.assertIn('aria-label="{label}" title="{label}"', SOURCE)
        self.assertIn("pulse.sidebar.collapsed", SOURCE)
        self.assertIn("aria-pressed", SOURCE)
        self.assertIn("pulse-sidebar-collapsed", SOURCE)
        self.assertIn("min-width: 768px", SOURCE)

    def test_removed_workflow_shortcuts_stay_out_of_sidebar(self):
        self.assertIn('hidden_keys = {"handover", "approvals", "tasks2"} if prefix == "u" else {"tasks"}', self.sidebar)
        self.assertIn("item for item in visible_nav(items, leader) if item[0] not in hidden_keys", self.sidebar)

    def test_sensitive_navigation_gates_are_preserved(self):
        self.assertIn('perms.get("can_manage_checkins") or perms.get("is_admin")', self.sidebar)
        self.assertIn('perms.get("can_warn") or perms.get("is_admin")', self.sidebar)
        self.assertIn('perms.get("can_view_analytics",False)', self.sidebar)
        self.assertIn("from pulse_admin import ADMIN_PAGE_KEYS, admin_sections", self.sidebar)

    def test_shared_theme_uses_black_and_red_tokens(self):
        css = SOURCE.split('PRO_CSS = r"""', 1)[1].split('"""', 1)[0]
        dark = css.split(".dark{", 1)[1].split("}", 1)[0]
        for token in ("--p:#E31B23", "--p2:#FF333D", "--p3:#FF5058", "--bg:#08090B", "--bg2:#0E1014",
                      "--surface:#13161B", "--surface2:#191D24", "--text:#F5F5F5", "--muted:#A1A1AA",
                      "--border:rgba(255,255,255,.07)", "--glow:rgba(255,40,55,.20)"):
            with self.subTest(token=token):
                self.assertIn(token, dark)
        # Light mode keeps its own readable tokens, status colours stay semantic.
        light = css.split(":root{", 1)[1].split("}", 1)[0]
        self.assertIn("--bg:#F4F4F5", light)
        self.assertIn("--text:#18181B", light)
        for token in ("--good:#12B76A", "--warn:#F79009", "--bad:#F04438"):
            self.assertIn(token, light)
        self.assertIn("Pulse OpenClaw visual system", css)
        self.assertIn("Pulse Red Particle Experience", css)
        self.assertNotIn("255,118,92", css)

    def test_particle_canvas_never_blocks_interaction(self):
        css = SOURCE.split('PRO_CSS = r"""', 1)[1].split('"""', 1)[0]
        rule = css.split("#pulseParticles{", 1)[1].split("}", 1)[0]
        self.assertIn("pointer-events:none", rule)
        self.assertIn("position:fixed", rule)
        self.assertIn("z-index:-1", rule)

    def test_particle_script_is_lightweight_and_respects_the_browser(self):
        import pulse_pro
        js = pulse_pro.PARTICLE_JS
        self.assertIn("PARTICLE_JS", SOURCE.split("def patched_head(", 1)[1].split("def patched_log(", 1)[0])
        for marker in (
            "requestAnimationFrame(frame)", "cancelAnimationFrame(raf)",       # one loop, cancellable
            "prefers-reduced-motion: reduce", "RM.matches",                      # static frame when motion is off
            "visibilityState==='hidden'", "devicePixelRatio", "Math.min(window.devicePixelRatio||1",  # capped DPR
            "(pointer: fine)", "e.pointerType!=='mouse'",                        # no fake cursor on touch
            "if(window.PulseParticles)return;", "!cv.isConnected){destroy()", "removeEventListener('pointermove'",
            "passive:true",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, js)
        self.assertNotIn("setInterval", js)
        self.assertNotIn("shadowBlur", js)  # glow comes from a pre-rendered sprite
        self.assertNotIn("http", js)        # no external dependency

    def test_dashboard_displays_existing_operational_metrics(self):
        for marker in (
            "data-live=\"active\"", "data-live=\"open_tickets\"",
            "data-live=\"applications\"", "data-live=\"loas\"",
            "Aktive Verwarnungen", "active_warn_total",
            'action="/shift/action"', 'or "/pulse-inbox"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.dashboard)


    def test_roblox_suggestion_script_has_valid_quotes(self):
        # DASHBOARD_HEAD is a normal (non-raw) string: an escaped \" ends up as a bare quote in the
        # browser and broke the whole inline script ("missing ) after argument list").
        web = (ROOT / "webserver.py").read_text(encoding="utf-8")
        head = web.split('DASHBOARD_HEAD = """', 1)[1].split('"""', 1)[0]
        self.assertNotIn('<option value=\\"', head)
        self.assertIn("'<option value=\"' + escapeHtml(u.name) + '\">'", head)


if __name__ == "__main__":
    unittest.main()
