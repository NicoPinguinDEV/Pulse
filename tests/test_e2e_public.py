"""Browser test of the public website: real rendering, mobile layout, menu, consent. Local server only."""
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

import pulse_public_site as site

ROUTES = ("/", "/regelwerk", "/faq", "/fraktionen", "/news", "/impressum", "/datenschutz", "/cookies", "/sicherheit")
OUTPUT = Path(tempfile.gettempdir()) / "pulse-public-screens"
SERVICE = ({"id": "karte", "label": "Karte", "purpose": "Kartenansicht", "provider": "Beispiel"},)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipIf(sync_playwright is None, "Playwright ist nicht installiert")
class PublicBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import uvicorn

        import webserver as ws

        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.db = patch("pulse_db.DB_PATH", str(Path(cls.tmp.name) / "pulse.db"))
        cls.db.start()
        cls.port = free_port()
        config = uvicorn.Config(ws.app, host="127.0.0.1", port=cls.port, log_level="warning", lifespan="off")
        cls.server = uvicorn.Server(config)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(0.05)
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as exc:
            cls.playwright.stop()
            cls.server.should_exit = True
            cls.db.stop()
            raise unittest.SkipTest(f"Chromium nicht verfügbar: {type(exc).__name__}")
        OUTPUT.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.should_exit = True
        cls.thread.join(5)
        cls.db.stop()
        cls.tmp.cleanup()

    def open(self, width, height, **options):
        context = self.browser.new_context(viewport={"width": width, "height": height}, **options)
        self.addCleanup(context.close)
        page = context.new_page()
        page.problems = []
        page.on("console", lambda m: page.problems.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda exc: page.problems.append(str(exc)))
        return page

    def test_every_page_fits_desktop_and_phone_without_errors(self):
        for width, height, name in ((1440, 900, "desktop"), (375, 812, "mobile")):
            page = self.open(width, height)
            for route in ROUTES:
                with self.subTest(route=route, device=name):
                    page.goto(self.base + route, wait_until="load")
                    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                    self.assertLessEqual(overflow, 0)
                    self.assertEqual(page.problems, [])
                    slug = route.strip("/") or "home"
                    page.screenshot(path=str(OUTPUT / f"{slug}-{name}.png"), full_page=route == "/")

    def test_public_pages_store_nothing_in_the_browser(self):
        page = self.open(1280, 800)
        page.goto(self.base + "/", wait_until="load")
        self.assertEqual(page.context.cookies(), [])
        self.assertEqual(page.evaluate("localStorage.length + sessionStorage.length"), 0)
        self.assertEqual(page.locator(".consent").count(), 0)

    def test_phone_menu_works_with_the_keyboard(self):
        page = self.open(375, 812)
        page.goto(self.base + "/", wait_until="load")
        self.assertFalse(page.locator(".links").is_visible())
        page.locator(".menu summary").focus()
        page.keyboard.press("Enter")
        link = page.locator(".menu a", has_text="Regelwerk")
        self.assertTrue(link.is_visible())
        link.click()
        page.wait_for_url("**/regelwerk")
        self.assertTrue(page.locator("h1", has_text="Regelwerk").is_visible())

    def test_team_login_stays_reachable(self):
        page = self.open(1280, 800)
        page.goto(self.base + "/", wait_until="load")
        page.locator(".top-actions a", has_text="Team-Login").click()
        page.wait_for_url("**/team-login")
        self.assertTrue(page.locator("a", has_text="Mit Discord anmelden").is_visible())

    def test_reduced_motion_stops_sparks_and_wheel(self):
        page = self.open(1280, 800, reduced_motion="reduce")
        page.goto(self.base + "/", wait_until="load")
        self.assertEqual(page.locator(".sparks").evaluate("e => getComputedStyle(e).display"), "none")
        self.assertEqual(page.locator(".spin").first.evaluate("e => getComputedStyle(e).animationName"), "none")

    def consent_page(self):
        page = self.open(1280, 800)
        page.goto(self.base + "/cookies", wait_until="load")
        return page

    def test_consent_refuse_accept_and_withdraw(self):
        with patch.object(site, "OPTIONAL_SERVICES", SERVICE):
            page = self.consent_page()
            self.assertEqual(page.locator(".consent").count(), 1)
            self.assertEqual(page.evaluate("localStorage.getItem('pulse.consent')"), None)
            page.locator(".consent button", has_text="Nur notwendige").click()
            self.assertEqual(json.loads(page.evaluate("localStorage.getItem('pulse.consent')"))["choices"], {"karte": False})
            page.reload()
            self.assertEqual(page.locator(".consent").count(), 0)  # the choice is remembered
            page.locator("[data-consent-open]").click()
            box = page.locator(".consent input[type=checkbox]")
            self.assertFalse(box.is_checked())  # nothing preselected
            box.check()
            page.locator(".consent button", has_text="Auswahl speichern").click()
            saved = json.loads(page.evaluate("localStorage.getItem('pulse.consent')"))
            self.assertEqual(saved["choices"], {"karte": True})
            self.assertEqual(saved["version"], site.CONSENT_VERSION)
            self.assertTrue(saved["at"])
            page.locator("[data-consent-open]").click()  # withdrawing is as easy as agreeing
            page.locator(".consent button", has_text="Nur notwendige").click()
            self.assertEqual(json.loads(page.evaluate("localStorage.getItem('pulse.consent')"))["choices"], {"karte": False})

    def test_accept_all_saves_every_service(self):
        with patch.object(site, "OPTIONAL_SERVICES", SERVICE):
            page = self.consent_page()
            page.locator(".consent button", has_text="Alle akzeptieren").click()
            self.assertEqual(json.loads(page.evaluate("localStorage.getItem('pulse.consent')"))["choices"], {"karte": True})


if __name__ == "__main__":
    unittest.main()
