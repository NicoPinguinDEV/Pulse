"""The VPS boot updater must pull main safely before starting the container."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BootUpdateTests(unittest.TestCase):
    def test_compose_does_not_start_old_container_ahead_of_systemd_update(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        unit = (ROOT / "systemd" / "pulse-github-update.service").read_text(encoding="utf-8")
        self.assertIn('restart: "no"', compose)
        self.assertIn("After=network-online.target docker.service", unit)
        self.assertIn("ExecStart=/usr/local/sbin/pulse-vps-boot-update.sh", unit)
        self.assertIn("WantedBy=multi-user.target", unit)

    def test_boot_script_syntax_and_safety_guards(self):
        path = ROOT / "scripts" / "pulse-vps-boot-update.sh"
        script = path.read_text(encoding="utf-8")
        result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("git fetch origin main", script)
        self.assertIn('git merge --ff-only "$target"', script)
        self.assertIn("git merge-base --is-ancestor HEAD", script)
        self.assertIn("BACKUP_ROOT=", script)
        self.assertIn("assets/support_wait.mp3", script)
        self.assertIn("docker compose up -d --build", script)
        self.assertIn("/healthz", script)
        self.assertIn('git reset --hard "$previous"', script)
        self.assertNotIn("git reset --hard origin/main", script)

    def test_compose_healthcheck_stays_enabled(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("/healthz", compose)
        self.assertIn("start_period: 120s", compose)


if __name__ == "__main__":
    unittest.main()
