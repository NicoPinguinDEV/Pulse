"""scripts/pulse-crash-restart.sh starts Pulse again only after a real crash – never at boot or during a deploy.

Runs the real script against fake `docker`/`systemctl` (and `flock` where the shell has none); nothing touches
Docker or the VPS. On Windows set PULSE_TEST_BASH to Git Bash (`bash` there is the WSL stub).
"""
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pulse-crash-restart.sh"


def _bash() -> str:
    bash = os.environ.get("PULSE_TEST_BASH", "bash")
    try:
        probe = subprocess.run([bash, "-c", "echo ok"], capture_output=True, timeout=20)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        raise unittest.SkipTest("bash nicht verfügbar")
    if probe.returncode != 0 or probe.stdout.strip() != b"ok":
        raise unittest.SkipTest("bash nicht lauffähig (z. B. WSL ohne Distribution)")
    return bash


class CrashRestartTests(unittest.TestCase):
    def setUp(self):
        self.bash = _bash()
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "bin").mkdir()
        self.state = self.tmp / "state"
        self.log = self.tmp / "calls.log"

    def _stub(self, name: str, body: str) -> None:
        (self.tmp / "bin" / name).write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")

    def _run(self, inspect: str, update_state: str = "active", lock_busy: bool = False):
        log = self.log.as_posix()
        self._stub("docker", f'echo "docker $*" >> "{log}"\n'
                             f'if [ "$1" = inspect ]; then echo "{inspect}"; fi\n'
                             'exit 0\n')
        self._stub("systemctl", f'echo "{update_state}"\n')
        self._stub("flock", "exit 1\n" if lock_busy else "exit 0\n")
        bin_dir = (self.tmp / "bin").as_posix()
        script = (f'export PATH="$(cd "{bin_dir}" && pwd):$PATH"; chmod +x "{bin_dir}"/*; '
                  f'export STATE_DIRECTORY="{self.state.as_posix()}" PULSE_DEPLOY_LOCK="{(self.tmp / "lock").as_posix()}"; '
                  f'bash "{SCRIPT.as_posix()}"')
        result = subprocess.run([self.bash, "-c", script], capture_output=True, timeout=60)
        calls = self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []
        return result, [c for c in calls if c.startswith("docker start")]

    def test_crash_with_error_exit_is_restarted_and_recorded(self):
        result, starts = self._run("exited 1 false")
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
        self.assertEqual(starts, ["docker start pulse-teamos"])
        self.assertEqual(len((self.state / "restarts").read_text(encoding="utf-8").split()), 1)

    def test_oom_kill_is_restarted(self):
        _, starts = self._run("exited 137 true")
        self.assertEqual(starts, ["docker start pulse-teamos"])

    def test_clean_stop_running_or_missing_container_is_left_alone(self):
        for inspect in ("exited 0 false", "running 0 false", "created 0 false", "restarting 1 false"):
            with self.subTest(inspect=inspect):
                _, starts = self._run(inspect)
                self.assertEqual(starts, [])

    def test_never_during_boot_update_deploy_or_pause(self):
        _, starts = self._run("exited 1 false", update_state="activating")
        self.assertEqual(starts, [], "Boot-Update läuft")
        _, starts = self._run("exited 1 false", lock_busy=True)
        if (self.tmp / "bin" / "flock").exists():
            self.assertEqual(starts, [], "Deploy hält die Sperre")
        self.state.mkdir(exist_ok=True)
        (self.state / "paused").write_text("", encoding="utf-8")
        _, starts = self._run("exited 1 false")
        self.assertEqual(starts, [], "pausiert")

    def test_crash_loop_stops_after_three_starts_per_hour(self):
        self.state.mkdir()
        now = int(time.time())
        (self.state / "restarts").write_text(f"{now - 7200}\n{now - 600}\n{now - 300}\n{now - 60}\n", encoding="utf-8")
        result, starts = self._run("exited 1 false")
        self.assertEqual(starts, [])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("KRITISCH", result.stdout.decode(errors="replace"))

    def test_old_restarts_no_longer_count(self):
        self.state.mkdir()
        now = int(time.time())
        (self.state / "restarts").write_text(f"{now - 7200}\n{now - 5000}\n{now - 4000}\n", encoding="utf-8")
        _, starts = self._run("exited 1 false")
        self.assertEqual(starts, ["docker start pulse-teamos"])
        self.assertEqual(len((self.state / "restarts").read_text(encoding="utf-8").split()), 1)


class CrashRestartFilesTests(unittest.TestCase):
    def test_units_and_safety_properties(self):
        script = SCRIPT.read_text(encoding="utf-8")
        service = (ROOT / "systemd" / "pulse-crash-restart.service").read_text(encoding="utf-8")
        timer = (ROOT / "systemd" / "pulse-crash-restart.timer").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/usr/local/sbin/pulse-crash-restart.sh", service)  # never run from the repo
        self.assertIn("After=docker.service pulse-github-update.service", service)
        self.assertIn("OnBootSec=10min", timer)
        for forbidden in ("git ", "docker compose", "--build", "rm -"):
            self.assertNotIn(forbidden, script.split("set -Eeuo pipefail", 1)[1])
        self.assertIn("/run/lock/pulse-deploy.lock", script)
        self.assertIn("/run/lock/pulse-deploy.lock", (ROOT / "deploy" / "remote_deploy.sh").read_text(encoding="utf-8"))
        self.assertIn('restart: "no"', (ROOT / "compose.yaml").read_text(encoding="utf-8"))  # boot race stays closed
        for name in ("scripts/pulse-crash-restart.sh", "systemd/pulse-crash-restart.service", "systemd/pulse-crash-restart.timer"):
            self.assertNotIn(b"\r\n", (ROOT / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main()
