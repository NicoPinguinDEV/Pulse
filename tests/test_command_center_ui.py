"""Regression tests for the shared Phase 16.0 command-center interaction layer."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pulse_flags
import pulse_pro
import webserver as ws


class CommandCenterInteractionTests(unittest.TestCase):
    def test_shared_pro_renderer_injects_assets_before_every_page(self):
        ctx = SimpleNamespace(perms={})
        with patch.object(pulse_flags, "banner_html", return_value=""):
            with patch.object(ws, "render_page", side_effect=lambda title, ctx, page, body, extra: body):
                rendered = pulse_pro.render_pro_page(ws, "Test", ctx, "dashboard", "<section>Inhalt</section>")

        self.assertLess(rendered.index("<style>"), rendered.index('<div class="pulse-shell">'))
        self.assertLess(rendered.index("<script>"), rendered.index('<div class="pulse-shell">'))
        self.assertIn('id="pulseCustomCursor"', rendered)
        self.assertIn("<section>Inhalt</section>", rendered)

    def test_cursor_is_restricted_to_fine_pointers_and_respects_motion_preferences(self):
        css = pulse_pro.PULSE_INTERACTION_CSS
        js = pulse_pro.PULSE_INTERACTION_JS
        self.assertIn("(pointer:fine)", css)
        self.assertIn("(pointer:coarse)", css)
        self.assertIn("prefers-reduced-motion:reduce", css.replace(" ", ""))
        self.assertIn('(pointer: fine)', js)
        self.assertIn('(prefers-reduced-motion: reduce)', js)
        self.assertIn('if(reduced.matches||!fine.matches)return;', js)
        self.assertIn('if(reduced.matches||!fine.matches)destroy();', js)

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

    def test_navigation_menus_and_surfaces_share_the_existing_red_brand(self):
        css = pulse_pro.PULSE_INTERACTION_CSS
        for marker in (
            "#pulseSidebar nav a:hover:not(.nav-active)",
            "#pulseSidebar .pulse-nav-group>summary:after",
            ".pulse-topbar:after",
            ".pulse-card:hover,.pulse-stat:hover",
            ".pulse-row:hover",
            ".pulse-command-item:hover",
            "rgba(227,27,35",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, css)


if __name__ == "__main__":
    unittest.main()
