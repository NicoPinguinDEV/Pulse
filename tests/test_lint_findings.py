"""A-07: bugs found by the linter (undefined names) – regression tests and a guard against new ones."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, fake_auth_for

ROOT = Path(__file__).resolve().parents[1]
TEAM_ROLE = SimpleNamespace(id=555000000000000001, name="Supporter", managed=False, is_default=lambda: False)


class ProfileSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        self.audit = []
        self.patches = [patch.object(ws, "CONFIG_FILE", str(base / "config.json")),
                        patch.object(pulse_db, "DB_PATH", str(base / "pulse.db")),
                        patch.object(ws, "log_audit", lambda *a, **k: self.audit.append(a[2]))]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        ws._CONFIG_CACHE.update(key=None, data=None)
        ws.save_json(ws.CONFIG_FILE, {"team_role_ids": [TEAM_ROLE.id]})

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        ws._CONFIG_CACHE.update(key=None, data=None)
        self.tmp.cleanup()

    def test_saving_a_dashboard_profile_finishes_with_audit_entry(self):
        base = fake_auth_for(ADMIN)

        def auth(*a, **k):
            ctx = base(*a, **k)
            ctx.guild = SimpleNamespace(**dict(vars(ctx.guild), get_role=lambda rid: TEAM_ROLE if rid == TEAM_ROLE.id else None))
            ctx.config = ws.load_config()
            return ctx
        with patch.object(ws, "auth", auth):
            response = TestClient(ws.app, raise_server_exceptions=False).post(
                "/settings/dashboard-profile/save",
                data={"dashboard_profile_name": "Support-Profil", "dashboard_discord_roles": str(TEAM_ROLE.id),
                      "can_manage_tickets": "1"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text[-300:])  # before: NameError → error page
        self.assertIn("Digitale Zwischenrolle gespeichert", self.audit)


class UndefinedNameGuard(unittest.TestCase):
    def test_no_undefined_names_in_runtime_code(self):
        ruff = shutil.which("ruff")
        cmd = [ruff] if ruff else [sys.executable, "-m", "ruff"]
        probe = subprocess.run(cmd + ["--version"], capture_output=True, text=True)
        if probe.returncode != 0:
            self.skipTest("ruff nicht installiert (Entwicklungswerkzeug)")
        result = subprocess.run(cmd + ["check", "--isolated", "--select", "F821", "--output-format", "concise",
                                       "--exclude", "deps,venv,.venv,tests,tools", "."],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:])


if __name__ == "__main__":
    unittest.main()
