"""Regression tests for the shared Phase 16.0 command-center interaction layer."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pulse_flags
import pulse_pro
import webserver as ws


class CommandCenterInteractionTests(unittest.TestCase):
    def test_shared_shell_hook_supplies_cursor_assets_and_pro_renderer_wraps_page(self):
        ctx = SimpleNamespace(perms={})
        interaction = pulse_pro.pro_body_extra()
        self.assertLess(interaction.index("<style>"), interaction.index("<script>"))
        self.assertIn('id="pulseCustomCursor"', interaction)
        self.assertIn("pointermove", interaction)

        with patch.object(pulse_flags, "banner_html", return_value=""):
            with patch.object(ws, "render_page", side_effect=lambda title, ctx, page, body, extra: body):
                rendered = pulse_pro.render_pro_page(ws, "Test", ctx, "dashboard", "<section>Inhalt</section>")

        self.assertIn('<div class="pulse-shell">', rendered)
        self.assertIn("<section>Inhalt</section>", rendered)
        # Asset insertion is owned by webserver.render_page's shared body_extra hook.
        self.assertNotIn('id="pulseCustomCursor"', rendered)

    def test_cursor_is_restricted_to_fine_pointers_and_respects_motion_preferences(self):
        css = pulse_pro.PULSE_INTERACTION_CSS
        js = pulse_pro.PULSE_INTERACTION_JS
        self.assertIn("(pointer:fine)", css)
        self.assertIn("(pointer:coarse)", css)
        self.assertIn("(hover:hover)", css.replace(" ", ""))
        self.assertIn("prefers-reduced-motion:reduce", css.replace(" ", ""))
        self.assertIn('(pointer: fine)', js)
        self.assertIn('(prefers-reduced-motion: reduce)', js)
        self.assertIn('(hover: hover)', js)
        self.assertIn('if(reduced.matches||!fine.matches||!hover.matches)return;', js)
        self.assertIn('if(reduced.matches||!fine.matches||!hover.matches)destroy();', js)

    def test_cursor_never_blocks_clicks_and_preserves_text_selection(self):
        css = pulse_pro.PULSE_INTERACTION_CSS
        js = pulse_pro.PULSE_INTERACTION_JS
        self.assertIn("pointer-events:none", css)
        self.assertIn("cursor:text!important", css)
        self.assertIn('"pulse-cursor-hover"', js)
        self.assertIn('"pulse-cursor-pressed"', js)
        self.assertIn('"pulse-cursor-text"', js)
        self.assertIn('function pointerOver(event)', js)
        self.assertIn('document.addEventListener("pointerover",pointerOver,true)', js)

    def test_command_palette_lists_visible_pages_and_hides_restricted_actions(self):
        viewer = pulse_pro.build_command_modal(ws, {"can_view_team_status": True})
        for path in (
            "/me", "/dashboard", "/pulse-inbox", "/team", "/team-status",
            "/tickets", "/applications", "/meetings", "/calendar", "/loa",
            "/training", "/wiki", "/achievements",
        ):
            with self.subTest(path=path):
                self.assertIn(f'href="{path}"', viewer)
        self.assertIn('href="/ultimate"', viewer)
        self.assertIn('href="/ultimate/polls"', viewer)
        self.assertIn('href="/suite/search"', viewer)
        self.assertNotIn('href="/ultimate/cases"', viewer)
        self.assertNotIn('href="/suite/audit"', viewer)
        self.assertNotIn('href="/admin/security"', viewer)
        self.assertIn('role="dialog"', viewer)
        self.assertIn('aria-label="Pulse-Seiten und Funktionen suchen"', viewer)
        self.assertIn('pulseCmdEmpty', viewer)
        self.assertIn("ArrowDown", viewer)

    def test_command_palette_includes_leadership_and_permission_gated_pages(self):
        manager = pulse_pro.build_command_modal(
            ws,
            {
                "can_promote": True,
                "can_manage_checkins": True,
                "can_manage_team_warnings": True,
                "can_manage_factions": True,
                "can_view_analytics": True,
            },
        )
        for path in (
            "/activity-check", "/warns", "/factions", "/stats",
            "/ultimate/cases", "/ultimate/departments", "/suite/audit",
            "/suite/search", "/admin",
        ):
            with self.subTest(path=path):
                self.assertIn(f'href="{path}"', manager)
        # The legacy workflow links intentionally hidden from the primary sidebar stay hidden here too.
        self.assertNotIn('href="/ultimate/handovers"', manager)
        self.assertNotIn('href="/ultimate/approvals"', manager)
        self.assertNotIn('href="/ultimate/tasks"', manager)
        self.assertNotIn('href="/suite/tasks"', manager)

    def test_command_palette_supports_ctrl_k_and_restores_focus_on_close(self):
        source = (ROOT / "pulse_pro.py").read_text(encoding="utf-8")
        script = source.split('PRO_JS = r"""', 1)[1].split('"""', 1)[0]
        self.assertIn("(e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'", script)
        self.assertIn("aria-hidden","".join(script.split()))
        self.assertIn("paletteOpener", script)
        self.assertIn("input.focus()", script)

    def test_navigation_menus_and_surfaces_share_the_coral_brand(self):
        css = pulse_pro.PULSE_INTERACTION_CSS
        for marker in (
            "#pulseSidebar nav a:hover:not(.nav-active)",
            "#pulseSidebar .pulse-nav-group>summary:after",
            ".pulse-topbar:after",
            ".pulse-card:hover,.pulse-stat:hover",
            ".pulse-row:hover",
            ".pulse-command-item:hover",
            "rgba(255,118,92",
            "#B94732",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, css)


if __name__ == "__main__":
    unittest.main()
