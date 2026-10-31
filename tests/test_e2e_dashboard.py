"""E2E: Dashboard im echten Browser (Playwright) – Navigation, Formulare speichern, Ergebnis/Persistenz prüfen,
ungültige Eingaben, Serverfehler, Rechte sowie Lade-, Fehler- und Leerzustände, Scrollposition, Desktop und Mobil.

Startet tools/preview_server.py (127.0.0.1, Fake-Login, Fake-Guild, temporäre Daten – kein Discord, kein OAuth,
GitHub-Abgleich abgeschaltet) und steuert Chromium headless. Externe Requests (Tailwind-CDN, Discord-CDN …) werden
blockiert, damit der Test hermetisch bleibt. Serverfehler werden über die nur mit --e2e-hooks registrierten
/__preview/-Schalter des Preview-Prozesses ausgelöst (echter Wartungsmodus bzw. echte Fehlerseite).

Ohne Playwright/Chromium wird der Test übersprungen. Mit PULSE_E2E=1 (CI-Job „e2e“) ist ein fehlendes Playwright
ein Fehler statt eines Skips.

    python -m pip install -r requirements-dev.txt
    python -m playwright install chromium
    python -m unittest tests.test_e2e_dashboard -v
"""
import json
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


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _wait_for_port(port: int, proc: subprocess.Popen, log: Path, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Preview-Server beendet (Exit {proc.returncode}):\n{log.read_text(encoding='utf-8', errors='replace')[-4000:]}")
        if _port_open(port):
            return
        time.sleep(0.2)
    raise TimeoutError(f"Preview-Server auf Port {port} nicht erreichbar")


def _block_external(route):
    url = route.request.url
    if url.startswith("http://127.0.0.1"):
        route.continue_()
    else:
        route.abort()


@unittest.skipIf(sync_playwright is None and not REQUIRED, "Playwright nicht installiert (requirements-dev.txt)")
class _PreviewE2EBase(unittest.TestCase):
    ROLE = "admin"

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise RuntimeError("PULSE_E2E=1, aber Playwright ist nicht installiert")
        cls.tmp = tempfile.TemporaryDirectory(prefix="pulse-e2e-")
        cls.port = _free_port()
        env = dict(os.environ, PYTHONUTF8="1", TMP=cls.tmp.name, TEMP=cls.tmp.name, TMPDIR=cls.tmp.name,
                   PULSE_GITHUB_REPO="")  # Server Control Center: kein Request an api.github.com
        for secret in ("DISCORD_TOKEN", "PULSE_HOST_AGENT_URL", "PULSE_HOST_AGENT_TOKEN"):
            env.pop(secret, None)
        # Ausgabe in eine Datei, nicht in eine PIPE: eine nie gelesene PIPE läuft bei vielen Log-/Traceback-Zeilen
        # voll und blockiert dann den Server (Tests liefen danach in Timeouts).
        cls.log = Path(cls.tmp.name) / "preview-server.log"
        cls.log_handle = cls.log.open("w", encoding="utf-8")
        cls.server = subprocess.Popen(
            [sys.executable, str(ROOT / "tools" / "preview_server.py"), "--port", str(cls.port), "--role", cls.ROLE,
             "--e2e-hooks"],
            cwd=ROOT, env=env, stdout=cls.log_handle, stderr=subprocess.STDOUT)
        try:
            _wait_for_port(cls.port, cls.server, cls.log)
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
        cls.log_handle.close()
        cls.tmp.cleanup()
        # Kein Prozess darf den Port weiter belegen (sonst laufen Folgetests gegen einen alten Server).
        if _port_open(cls.port):
            raise RuntimeError(f"Preview-Server lauscht nach dem Beenden noch auf Port {cls.port}")

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

    def _config(self) -> dict:
        """Gespeicherte Config der Preview-Instanz (liegt im temporären Datenordner des Servers)."""
        files = list(Path(self.tmp.name).glob("pulse-preview-*/config.json"))
        self.assertEqual(len(files), 1, files)
        return json.loads(files[0].read_text(encoding="utf-8"))

    def _toast(self, page, role: str) -> str:
        return page.locator(f'#toast[role="{role}"]').inner_text()


class DashboardSaveE2ETests(_PreviewE2EBase):
    def _goal_input(self, page):
        # Aktiver Handler: pulse_pro überschreibt /settings, Formular postet an /settings/pro-goal
        return page.locator('form[action="/settings/pro-goal"] input[name="weekly_goal"]')

    def _submit(self, page, field):
        with page.expect_navigation():
            field.locator("xpath=ancestor::form").locator('button:not([type="button"])').first.click()

    def _save_weekly_goal(self, page, value: str):
        response = page.goto(self._url("/settings"))
        self.assertEqual(response.status, 200)
        goal = self._goal_input(page)
        goal.fill(value)
        self._submit(page, goal)

    def _assert_ok_toast(self, page):
        self.assertIn("Wochenziel gespeichert.", self._toast(page, "status"))

    def test_weekly_goal_save_roundtrip_desktop(self):
        page, errors = self._page(viewport={"width": 1366, "height": 900})
        self._save_weekly_goal(page, "4.5")

        self.assertTrue(page.url.endswith("/settings"), page.url)  # ?msg=… entfernt das Frontend per replaceState
        self._assert_ok_toast(page)
        self.assertEqual(self._goal_input(page).input_value(), "4.5")
        # Persistenz: frische Seite liest den gespeicherten Wert aus der Config
        page.goto(self._url("/settings"))
        self.assertEqual(self._goal_input(page).input_value(), "4.5")
        self.assertEqual(self._config().get("weekly_goal_hours"), 4.5)
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
        self._submit(page, self._goal_input(page))
        page.goto(self._url("/settings"))
        self.assertEqual(self._goal_input(page).input_value(), "100")

    def test_dashboard_profile_invalid_then_saved_with_scroll_restore(self):
        """Zwischenrolle: leerer Name wird serverseitig abgelehnt (nichts gespeichert), gültiger Name wird
        gespeichert und nach Neuladen angezeigt; nach dem Speichern bleibt die Scrollposition erhalten."""
        page, errors = self._page(viewport={"width": 1366, "height": 800})
        page.goto(self._url("/settings"))
        form = page.locator('form[action="/settings/dashboard-profile/save"]').first
        name = form.locator('input[name="dashboard_profile_name"]')
        before = len(self._config().get("dashboard_roles", {}))

        # Ungültig: Browser-"required" entfernen, Server muss selbst ablehnen
        name.evaluate("el => el.removeAttribute('required')")
        name.fill("   ")
        self._submit(page, name)
        self.assertIn("Bitte gib einen Namen", self._toast(page, "alert"))
        self.assertEqual(len(self._config().get("dashboard_roles", {})), before, "Ungültige Eingabe wurde gespeichert")

        # Gültig: Formular in die Mitte scrollen, speichern, Scrollposition und Ergebnis prüfen
        form = page.locator('form[action="/settings/dashboard-profile/save"]').first
        name = form.locator('input[name="dashboard_profile_name"]')
        name.scroll_into_view_if_needed()
        y_before = page.evaluate("window.scrollY")
        self.assertGreater(y_before, 100, "Formular liegt nicht unterhalb des ersten Bildschirms – Test sinnlos")
        name.fill("E2E Support-Rechte")
        form.locator('input[name="dashboard_discord_roles"][value="100"]').check()
        self._submit(page, name)
        self.assertIn("E2E Support-Rechte", self._toast(page, "status"))
        page.wait_for_function(f"Math.abs(window.scrollY - {y_before}) < 150")

        profiles = self._config().get("dashboard_roles", {})
        self.assertEqual(len(profiles), before + 1)
        saved = next(p for p in profiles.values() if p.get("name") == "E2E Support-Rechte")
        self.assertEqual(saved.get("discord_role_ids"), [100])
        page.goto(self._url("/settings"))
        self.assertEqual(page.evaluate("window.scrollY"), 0, "Normale Navigation darf keine alte Scrollposition setzen")
        self.assertGreater(page.get_by_text("E2E Support-Rechte").count(), 0)
        self.assertEqual(errors, [], f"JavaScript-Fehler: {errors}")

    def test_save_during_maintenance_shows_error_and_keeps_data(self):
        """Serverseitiger Speicherfehler (echter Wiederherstellungs-Sperrmodus): 503-Seite, Wert unverändert."""
        page, _ = self._page()
        page.goto(self._url("/settings"))
        old = self._goal_input(page).input_value()
        new = "7.5" if old != "7.5" else "8"

        def maintenance(on: bool):
            result = page.evaluate(
                "on => fetch('/__preview/maintenance', {method: 'POST', headers: {'Content-Type': 'application/json'},"
                " body: JSON.stringify({on})}).then(r => r.json())", on)
            self.assertEqual(result.get("maintenance"), on)

        maintenance(True)
        self.addCleanup(lambda: page.is_closed() or maintenance(False))
        self._goal_input(page).fill(new)
        with page.expect_navigation():
            self._goal_input(page).locator("xpath=ancestor::form").locator("button").first.click()
        self.assertIn("Änderung nicht gespeichert", page.content())
        maintenance(False)

        page.goto(self._url("/settings"))
        self.assertEqual(self._goal_input(page).input_value(), old, "Wert trotz Schreibsperre geändert")

    def test_unexpected_server_error_and_unknown_route_pages(self):
        page, _ = self._page()
        response = page.goto(self._url("/__preview/fail"))
        self.assertEqual(response.status, 500)
        self.assertIn("Da ist etwas schiefgelaufen", page.content())
        self.assertIn("Fehler-ID", page.content())
        self.assertNotIn("Traceback", page.content())  # Details nur im Log

        response = page.goto(self._url("/gibt-es-nicht"))
        self.assertEqual(response.status, 404)
        self.assertGreater(page.get_by_role("link", name="Zum Dashboard").count(), 0)


class DashboardNavigationE2ETests(_PreviewE2EBase):
    def test_sidebar_navigation_desktop(self):
        page, errors = self._page(viewport={"width": 1366, "height": 900})
        response = page.goto(self._url("/me"))
        self.assertEqual(response.status, 200)
        sidebar = page.locator("#pulseSidebar")
        group_for_href = {
            "/team": "Team",
            "/tickets": "Support",
            "/stats": "Auswertung",
            "/me": "Mein Bereich",
        }
        for href, title in (("/team", "Teamliste"), ("/tickets", "Ticket-Zentrale"), ("/stats", "Team-Analytics"),
                            ("/me", "Mein Pulse")):
            with self.subTest(href=href):
                # Main navigation groups are intentionally collapsed unless they
                # contain the active page. Open the requested group before clicking.
                group = sidebar.locator("details.pulse-primary-group").filter(
                    has_text=group_for_href[href]
                ).first
                self.assertEqual(group.count(), 1, f"Navigationsgruppe fehlt: {group_for_href[href]}")
                if group.get_attribute("open") is None:
                    group.locator("summary").click()
                with page.expect_navigation() as nav:
                    sidebar.locator(f'a[href="{href}"]').first.click()
                self.assertEqual(nav.value.status, 200, href)
                self.assertTrue(page.url.endswith(href), page.url)
                self.assertIn(title, page.content())
                self.assertEqual(page.evaluate("window.scrollY"), 0)
        self.assertEqual(errors, [], f"JavaScript-Fehler: {errors}")

    def test_sidebar_groups_and_desktop_collapse_controls(self):
        page, errors = self._page(viewport={"width": 1366, "height": 900})
        self.assertEqual(page.goto(self._url("/me")).status, 200)
        sidebar = page.locator("#pulseSidebar")

        for name in ("Mein Bereich", "Team", "Support", "Recruiting", "Planung", "Auswertung"):
            with self.subTest(group=name):
                group = sidebar.locator("details.pulse-primary-group").filter(has_text=name)
                self.assertEqual(group.count(), 1, f"Navigationsgruppe fehlt: {name}")
                self.assertTrue(group.locator("summary").is_visible())

        collapse = page.locator("#pulseSidebarCollapseBtn")
        self.assertTrue(collapse.is_visible(), "Desktop-Klappbutton fehlt")
        self.assertEqual(collapse.get_attribute("aria-pressed"), "false")
        collapse.click()
        self.assertIn("pulse-sidebar-collapsed", sidebar.get_attribute("class"))
        self.assertEqual(collapse.get_attribute("aria-pressed"), "true")
        self.assertEqual(page.evaluate("localStorage.getItem('pulse.sidebar.collapsed')"), "1")

        page.reload()
        sidebar = page.locator("#pulseSidebar")
        collapse = page.locator("#pulseSidebarCollapseBtn")
        self.assertIn("pulse-sidebar-collapsed", sidebar.get_attribute("class"))
        self.assertEqual(collapse.get_attribute("aria-pressed"), "true")
        collapse.click()
        self.assertNotIn("pulse-sidebar-collapsed", sidebar.get_attribute("class"))
        self.assertEqual(collapse.get_attribute("aria-pressed"), "false")
        self.assertEqual(page.evaluate("localStorage.getItem('pulse.sidebar.collapsed')"), "0")
        self.assertEqual(errors, [], f"JavaScript-Fehler: {errors}")

    def test_empty_states_render(self):
        page, _ = self._page()
        page.goto(self._url("/tickets"))
        self.assertGreater(page.locator(".pulse-empty").count(), 0, "Ticket-Zentrale ohne Leerzustand")
        page.goto(self._url("/warns"))
        self.assertIn("Keine passenden Verwarnungen gefunden.", page.locator(".pulse-empty").first.inner_text())

    def test_mobile_layout_without_horizontal_overflow(self):
        page, errors = self._page(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
        for path in ("/me", "/team", "/tickets", "/settings", "/admin/server"):
            with self.subTest(path=path):
                self.assertEqual(page.goto(self._url(path)).status, 200)
                overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                self.assertLessEqual(overflow, 1, f"Horizontaler Überlauf auf {path}")
        self.assertEqual(errors, [], f"JavaScript-Fehler: {errors}")

    def test_server_control_center_loading_loaded_and_error_states(self):
        page, errors = self._page(viewport={"width": 1366, "height": 900})
        page.goto(self._url("/admin/server"))
        diagnose = page.locator("#scc-diagnose")
        self.assertIn("Wird geladen", diagnose.inner_text())  # Ladezustand vor dem ersten Abruf

        page.locator('.scc-tab[data-tab="diagnose"]').click()
        page.wait_for_function("!document.querySelector('#scc-diagnose').innerText.includes('Wird geladen')")
        self.assertGreater(diagnose.locator(".scc-finding, .scc-banner").count(), 0, diagnose.inner_html())

        # Fehlerzustände: API antwortet mit 500 bzw. ist nicht erreichbar
        page.route("**/admin/server/api/diagnose", lambda r: r.fulfill(
            status=500, content_type="application/json", body='{"detail": "Interner Fehler", "error_id": "E2E001"}'))
        page.locator('[data-refresh="diagnose"]').click()
        page.wait_for_function("document.querySelector('#scc-diagnose').innerText.includes('Nicht verfügbar')")
        self.assertIn("Interner Fehler", diagnose.inner_text())

        page.unroute("**/admin/server/api/diagnose")
        page.route("**/admin/server/api/diagnose", lambda r: r.abort())
        page.locator('[data-refresh="diagnose"]').click()
        page.wait_for_function("document.querySelector('#scc-diagnose').innerText.includes('Laden fehlgeschlagen')")
        self.assertEqual(errors, [], f"JavaScript-Fehler: {errors}")


class BackupFlowE2ETests(_PreviewE2EBase):
    """Phase 8: Komplett-Backup erstellen → Probelauf → Wiederherstellung nur mit Bestätigungswort (Desktop + Mobil)."""

    def _backups(self) -> list[str]:
        return sorted(p.name for p in Path(self.tmp.name).glob("pulse-preview-*/backups/pulse_full_*.zip"))

    def test_create_preview_and_guarded_restore(self):
        page, errors = self._page(viewport={"width": 1366, "height": 900})
        self.assertEqual(page.goto(self._url("/backups")).status, 200)
        before = set(self._backups())
        page.get_by_role("button", name="Komplett-Backup jetzt erstellen").click()
        page.wait_for_load_state()
        self.assertIn("Komplett-Backup erstellt", self._toast(page, "status"))
        created = self._backups()
        new = sorted(set(created) - before)
        self.assertEqual(len(new), 1, created)

        page.locator(f'a[href="/backup/full/preview/{new[0]}"]').click()
        page.wait_for_load_state()
        self.assertIn("Probelauf", page.locator("h1").inner_text())
        confirm = page.locator('form[action="/backup/full/restore"] input[name="confirm"]')
        self.assertEqual(confirm.get_attribute("required"), "")

        # Sicherheitsdialog muss erscheinen (früher verdeckte das Feld name="confirm" window.confirm → TypeError,
        # das Formular wurde ohne Dialog abgeschickt). Abbrechen sendet nichts.
        dialogs = []
        page.once("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        confirm.fill("WIEDERHERSTELLEN")
        page.locator('form[action="/backup/full/restore"] button').click()
        page.wait_for_timeout(300)
        self.assertEqual(len(dialogs), 1, "Kein Bestätigungsdialog vor der Wiederherstellung")
        self.assertIn("/backup/full/preview/", page.url)
        self.assertEqual(self._backups(), created, "Abgebrochener Dialog hat trotzdem etwas ausgelöst")

        # Falsches Bestätigungswort: Fehlermeldung, keine Sicherheitskopie, also auch keine Wiederherstellung.
        page.once("dialog", lambda dialog: dialog.accept())
        confirm.fill("ja")
        page.locator('form[action="/backup/full/restore"] button').click()
        page.wait_for_load_state()
        self.assertIn("WIEDERHERSTELLEN", self._toast(page, "alert"))
        self.assertEqual(self._backups(), created, "Ohne Bestätigung wurde trotzdem etwas geschrieben")
        self.assertEqual(errors, [], f"JavaScript-Fehler: {errors}")

    def test_backup_pages_on_mobile_without_horizontal_overflow(self):
        page, errors = self._page(viewport={"width": 375, "height": 812}, is_mobile=True, has_touch=True)
        page.goto(self._url("/backups"))
        page.get_by_role("button", name="Komplett-Backup jetzt erstellen").click()
        page.wait_for_load_state()
        name = self._backups()[-1]
        for path in ("/backups", f"/backup/full/preview/{name}"):
            with self.subTest(path=path):
                self.assertEqual(page.goto(self._url(path)).status, 200)
                overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                self.assertLessEqual(overflow, 1, f"Horizontaler Überlauf auf {path}")
        self.assertEqual(errors, [], f"JavaScript-Fehler: {errors}")


class TeamMemberPermissionE2ETests(_PreviewE2EBase):
    """Ein normales Teammitglied darf Admin-Bereiche weder sehen noch per direktem POST ändern."""
    ROLE = "team"

    def test_admin_pages_are_forbidden(self):
        page, _ = self._page()
        self.assertEqual(page.goto(self._url("/me")).status, 200)
        for path in ("/settings", "/admin/server"):
            with self.subTest(path=path):
                response = page.goto(self._url(path))
                self.assertEqual(response.status, 403, path)
                self.assertIn("Fehler 403", page.content())

    def test_direct_post_is_rejected_without_write(self):
        page, _ = self._page()
        page.goto(self._url("/me"))
        before = self._config()
        status = page.evaluate(
            "() => fetch('/settings/pro-goal', {method: 'POST', body: new URLSearchParams({weekly_goal: '42'}),"
            " redirect: 'manual'}).then(r => r.status)")
        self.assertEqual(status, 403)
        self.assertEqual(self._config(), before, "Abgelehnte Anfrage hat die Config verändert")

    def test_direct_backup_actions_are_rejected_without_write(self):
        """Phase 8: Backup erstellen/wiederherstellen per direktem POST – als Teammitglied 403, keine Datei angelegt."""
        page, _ = self._page()
        page.goto(self._url("/me"))
        for path, body in (("/backup/full/create", {}),
                           ("/backup/full/restore", {"filename": "pulse_full_20260101_000000.zip", "confirm": "WIEDERHERSTELLEN"})):
            with self.subTest(path=path):
                status = page.evaluate(
                    "([path, body]) => fetch(path, {method: 'POST', body: new URLSearchParams(body), redirect: 'manual'})"
                    ".then(r => r.status)", [path, body])
                self.assertEqual(status, 403)
        self.assertEqual(list(Path(self.tmp.name).glob("pulse-preview-*/backups/pulse_full_*")), [])


if __name__ == "__main__":
    unittest.main()
