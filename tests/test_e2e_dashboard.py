"""E2E: Dashboard im echten Browser (Playwright) – Seite öffnen, Wert ändern, speichern, Ergebnis prüfen.

Startet tools/preview_server.py (127.0.0.1, Fake-Login, Fake-Guild, temporäre Daten – kein Discord, kein OAuth)
und steuert Chromium headless. Externe Requests (Tailwind-CDN, Discord-CDN …) werden blockiert, damit der Test
hermetisch bleibt.

Ohne Playwright/Chromium wird der Test übersprungen. Mit PULSE_E2E=1 (CI-Job „e2e“) ist ein fehlendes Playwright
ein Fehler statt eines Skips.

    python -m pip install -r requirements-dev.txt
    python -m playwright install chromium
    python -m unittest tests.test_e2e_dashboard -v
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = os.environ.get("PULSE_E2E") == "1"

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:  # optional dev dependency
    sync_playwright = None
    PlaywrightError = Exception


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Preview-Server beendet (Exit {proc.returncode}):\n{proc.stdout.read() if proc.stdout else ''}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"Preview-Server auf Port {port} nicht erreichbar")


def _block_external(route):
    url = route.request.url
    if url.startswith("http://127.0.0.1"):
        route.continue_()
    else:
        route.abort()


@unittest.skipIf(sync_playwright is None and not REQUIRED, "Playwright nicht installiert (requirements-dev.txt)")
class DashboardSaveE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise RuntimeError("PULSE_E2E=1, aber Playwright ist nicht installiert")
        cls.tmp = tempfile.TemporaryDirectory(prefix="pulse-e2e-")
        cls.port = _free_port()
        env = dict(os.environ, PYTHONUTF8="1", TMP=cls.tmp.name, TEMP=cls.tmp.name, TMPDIR=cls.tmp.name)
        env.pop("DISCORD_TOKEN", None)
        cls.server = subprocess.Popen(
            [sys.executable, str(ROOT / "tools" / "preview_server.py"), "--port", str(cls.port), "--role", "admin"],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        try:
            _wait_for_port(cls.port, cls.server)
            cls.pw = sync_playwright().start()
            try:
                cls.browser = cls.pw.chromium.launch()
            except PlaywrightError as exc:
                cls.pw.stop()
                if REQUIRED:
                    raise
                raise unittest.SkipTest(f"Chromium fehlt (python -m playwright install chromium): {exc}") from exc
        except BaseException:
            cls._stop_server()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls._stop_server()

    @classmethod
    def _stop_server(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.server.kill()
            cls.server.wait(timeout=10)
        if cls.server.stdout:
            cls.server.stdout.close()
        cls.tmp.cleanup()

    def _page(self, **context_args):
        context = self.browser.new_context(**context_args)
        self.addCleanup(context.close)
        context.route("**/*", _block_external)
        page = context.new_page()
        page.set_default_timeout(15_000)
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        return page, errors

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def _goal_input(self, page):
        # Aktiver Handler: pulse_pro überschreibt /settings, Formular postet an /settings/pro-goal
        return page.locator('form[action="/settings/pro-goal"] input[name="weekly_goal"]')

    def _save_weekly_goal(self, page, value: str):
        response = page.goto(self._url("/settings"))
        self.assertEqual(response.status, 200)
        goal = self._goal_input(page)
        goal.fill(value)
        with page.expect_navigation():
            goal.locator("xpath=ancestor::form").get_by_role("button", name="Speichern").click()

    def _assert_ok_toast(self, page):
        toast = page.locator('#toast[role="status"]')
        self.assertIn("Wochenziel gespeichert.", toast.inner_text())

    def test_weekly_goal_save_roundtrip_desktop(self):
        page, errors = self._page(viewport={"width": 1366, "height": 900})
        self._save_weekly_goal(page, "4.5")

        self.assertTrue(page.url.endswith("/settings"), page.url)  # ?msg=… entfernt das Frontend per replaceState
        self._assert_ok_toast(page)
        self.assertEqual(self._goal_input(page).input_value(), "4.5")
        # Persistenz: frische Seite liest den gespeicherten Wert aus der Config
        page.goto(self._url("/settings"))
        self.assertEqual(self._goal_input(page).input_value(), "4.5")
        self.assertEqual(errors, [], f"JavaScript-Fehler: {errors}")

    def test_weekly_goal_save_mobile(self):
        page, errors = self._page(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
        self._save_weekly_goal(page, "6")
        self._assert_ok_toast(page)
        self.assertEqual(self._goal_input(page).input_value(), "6")
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        self.assertLessEqual(overflow, 1, "Horizontaler Überlauf auf Mobilgerät")
        self.assertEqual(errors, [], f"JavaScript-Fehler: {errors}")

    def test_out_of_range_weekly_goal_is_clamped_server_side(self):
        page, _ = self._page()
        page.goto(self._url("/settings"))
        # Browser-Validierung (min/max) umgehen: der Server muss den Bereich selbst erzwingen (0,5–100)
        self._goal_input(page).evaluate("el => { el.removeAttribute('min'); el.removeAttribute('max'); }")
        self._goal_input(page).fill("500")
        with page.expect_navigation():
            self._goal_input(page).locator("xpath=ancestor::form").get_by_role("button", name="Speichern").click()
        page.goto(self._url("/settings"))
        self.assertEqual(self._goal_input(page).input_value(), "100")


if __name__ == "__main__":
    unittest.main()
