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
        cls.sidebar = SOURCE.split("def pro_sidebar_html(", 1)[1].split("user_name =", 1)[0]
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
        self.assertIn('perms.get("can_warn") or perms.get("can_add_notes") or perms.get("is_admin")', self.sidebar)
        self.assertIn('perms.get("can_manage_team_warnings") or perms.get("is_admin")', self.sidebar)
        self.assertIn('perms.get("can_view_analytics",False)', self.sidebar)
        self.assertIn("from pulse_admin import ADMIN_PAGE_KEYS, admin_sections", self.sidebar)

    def test_shared_theme_uses_anthracite_and_accessible_coral_tokens(self):
        css = SOURCE.split('PRO_CSS = r"""', 1)[1].split('"""', 1)[0]
        dark = css.split(".dark{", 1)[1].split("}", 1)[0]
        for token in ("--p:#FF947B", "--p2:#FF765C", "--p3:#FF947B", "--bg:#08090B", "--bg2:#0E1014",
                      "--surface:#13161B", "--surface2:#191D24", "--text:#F5F5F5", "--muted:#A1A1AA",
                      "--border:rgba(255,255,255,.07)", "--glow:rgba(255,118,92,.20)"):
            with self.subTest(token=token):
                self.assertIn(token, dark)
        # Light mode keeps readable surfaces, while semantic status colours stay distinct.
        light = css.split(":root{", 1)[1].split("}", 1)[0]
        self.assertIn("--p:#B94732", light)
        self.assertIn("--p2:#BE4A34", light)
        self.assertIn("--p3:#FF765C", light)
        self.assertIn("--bg:#F4F4F5", light)
        self.assertIn("--text:#18181B", light)
        for token in ("--good:#12B76A", "--warn:#F79009", "--bad:#F04438"):
            self.assertIn(token, light)
        self.assertIn("Pulse OpenClaw visual system", css)
        self.assertIn("Pulse Coral Particle Experience", css)
        self.assertNotIn("227,27,35", css)
        self.assertNotIn("99,102,241", css)
        self.assertNotIn("91,92,240", css)
        self.assertNotIn("124,58,237", css)
        for old_accent in ("#E31B23", "#FF333D", "#FF5058", "rgba(227,27,35,", "rgba(255,80,88,"):
            self.assertNotIn(old_accent, SOURCE)

    def test_coral_primary_colors_meet_white_text_contrast(self):
        css = SOURCE.split('PRO_CSS = r"""', 1)[1].split('"""', 1)[0]
        dark = css.split(".dark{", 1)[1].split("}", 1)[0]

        def luminance(hex_color):
            channels = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [
                channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
                for channel in channels
            ]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        def contrast(first, second):
            values = sorted((luminance(first), luminance(second)), reverse=True)
            return (values[0] + 0.05) / (values[1] + 0.05)

        for token in ("--p:#B94732", "--p2:#BE4A34"):
            value = token.split(":", 1)[1]
            self.assertGreaterEqual(contrast(value, "#FFFFFF"), 4.5)
        dark_primary = dark.split("--p:", 1)[1].split(";", 1)[0]
        self.assertGreaterEqual(contrast(dark_primary, "#08090B"), 4.5)

    def test_legacy_and_page_specific_accents_follow_the_shared_palette(self):
        legacy = (ROOT / "pulse_design.py").read_text(encoding="utf-8")
        ultimate = (ROOT / "pulse_ultimate.py").read_text(encoding="utf-8")
        home = (ROOT / "pulse_home.py").read_text(encoding="utf-8")
        ticket_builder = (ROOT / "pulse_ticket_builder.py").read_text(encoding="utf-8")
        admin = (ROOT / "pulse_admin.py").read_text(encoding="utf-8")
        security = (ROOT / "pulse_guard_admin.py").read_text(encoding="utf-8")
        factions = (ROOT / "pulse_factions_dashboard.py").read_text(encoding="utf-8")
        status = (ROOT / "pulse_phase7.py").read_text(encoding="utf-8")

        for source in (legacy, ticket_builder):
            self.assertNotIn("rgba(91,92,240", source)
            self.assertNotIn("#9296f6", source)
        self.assertIn("color:#2563EB", legacy)  # informational blue remains semantic
        self.assertNotIn("#6366f1", ultimate.lower())
        self.assertIn('"accent": "#B94732"', ultimate)
        self.assertNotIn("#4338ca", home.lower())
        self.assertIn("color:#B94732", home)
        self.assertIn(".pulse-crumb a{color:var(--p)}", admin)
        self.assertNotIn("#6366f1", security.lower())
        self.assertNotIn("#6366f1", factions.lower())
        self.assertIn('else "#B94732"', status)
        # Discord's own preview uses Discord's brand colors intentionally.
        preview = ticket_builder.split("/* Discord preview */", 1)[1]
        self.assertIn("#5865f2", preview)

    def test_particle_canvas_never_blocks_interaction(self):
        css = SOURCE.split('PRO_CSS = r"""', 1)[1].split('"""', 1)[0]
        rule = css.split("#pulseParticles{", 1)[1].split("}", 1)[0]
        self.assertIn("pointer-events:none", rule)
        self.assertIn("position:fixed", rule)
        self.assertIn("z-index:-1", rule)

    def test_particle_script_is_lightweight_and_respects_the_browser(self):
        import pulse_pro
        js = pulse_pro.PARTICLE_JS
        self.assertIn("PARTICLE_JS", SOURCE.split("def pro_head_extra(", 1)[1].split("def pro_audit_mirror(", 1)[0])
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
