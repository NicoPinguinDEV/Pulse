"""Regression checks that keep the Pulse incident runbook operationally complete."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "PULSE_NOTFALLHANDBUCH.md"


class IncidentRunbookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = RUNBOOK.read_text(encoding="utf-8")

    def test_runbook_covers_ownership_severity_and_escalation(self):
        for required in (
            "## 2. Rollen und Verantwortlichkeiten",
            "Incident Lead",
            "Technische Einsatzkraft",
            "Datenschutz-/Datenverantwortung",
            "## 3. Schweregrade und Eskalation",
            "P1 – kritisch",
            "P4 – niedrig",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.content)

    def test_runbook_contains_first_response_and_incident_record(self):
        self.assertIn("## 4. Erste 15 Minuten", self.content)
        self.assertIn("Incident-ID:", self.content)
        self.assertIn("Nächster Schritt und Verantwortlicher:", self.content)

    def test_diagnostic_commands_match_the_repository_deployment(self):
        for required in (
            "/pulse-diagnose",
            "http://127.0.0.1:25095/healthz",
            "docker compose ps",
            "docker compose logs --tail=150 pulse",
            "systemctl status pulse-github-update.service",
            "journalctl -u pulse-github-update.service -n 100 --no-pager",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.content)

    def test_healthcheck_requires_all_readiness_flags(self):
        for required in ("ok: true", "bot_ready: true", "guild_ready: true", "database_ok: true"):
            with self.subTest(required=required):
                self.assertIn(required, self.content)

    def test_restore_process_requires_preview_backup_and_isolation(self):
        for required in (
            "aktuellen Zustand erstellen",
            "Restore-Vorschau",
            "isolierten Testumgebung",
            "ausdrückliche Freigabe",
            "Datenlayout-Kompatibilität",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.content)

    def test_secret_handling_and_recovery_secret_warning_are_documented(self):
        self.assertIn(".env ausgeben", self.content)
        self.assertIn("SESSION_SECRET", self.content)
        self.assertIn("nicht blind austauschen", self.content)
        self.assertIn("Token niemals in Chat, Issue oder Log posten", self.content)

    def test_runbook_has_explicit_recovery_and_closure_checklists(self):
        self.assertIn("## 8. Wiederanlauf-Checkliste", self.content)
        self.assertIn("## 9. Abschluss und Nachbereitung", self.content)
        self.assertIn("Incident Lead bestätigt die Wiederaufnahme", self.content)


if __name__ == "__main__":
    unittest.main()
