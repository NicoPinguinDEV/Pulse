"""U-03: Tailwind comes as a prebuilt, cacheable /static/pulse.css instead of the Play-CDN."""
import unittest

from fastapi.testclient import TestClient

import webserver as ws


class StaticCssTests(unittest.TestCase):
    def test_pages_link_the_built_css_not_the_cdn(self):
        self.assertTrue(ws.STATIC_CSS.is_file(), "static/pulse.css fehlt – tools/build_css.py ausführen")
        self.assertIn(f"/static/pulse.css?v={ws.STATIC_CSS_VERSION}", ws.HEAD_SCRIPT)
        self.assertNotIn("cdn.tailwindcss.com", ws.HEAD_SCRIPT)
        self.assertIn("toggleTheme", ws.HEAD_SCRIPT)  # dark mode switch is still there

    def test_css_is_served_with_long_cache(self):
        response = TestClient(ws.app).get("/static/pulse.css")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/css"))
        self.assertIn("immutable", response.headers["cache-control"])
        self.assertNotIn("no-store", response.headers["cache-control"])

    def test_built_css_has_the_classes_the_ui_uses(self):
        css = ws.STATIC_CSS.read_text(encoding="utf-8")
        for selector in (".dark\\:bg-\\[\\#141824\\]", ".bg-indigo-600", ".bg-amber-500\\/10", ".text-rose-500",
                         ".md\\:grid-cols-2", ".rounded-2xl", ".sr-only"):
            self.assertIn(selector, css)


if __name__ == "__main__":
    unittest.main()
