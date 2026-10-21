"""Phase 8 (O-01 … O-04): deployment files keep their safety properties.

These files are not executed in tests (no Docker/GitHub here), so the checks read them – each assert names
the property that protects production (non-root, health check, tested SHA, rollback, no secrets).
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


class DockerTests(unittest.TestCase):
    def test_image_runs_unprivileged_with_healthcheck_and_timezone(self):
        dockerfile = read("Dockerfile")
        self.assertRegex(dockerfile, r"(?m)^USER pulse$")
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("/healthz", dockerfile)
        for package in ("ffmpeg", "libopus0", "tzdata"):
            self.assertIn(package, dockerfile)

    def test_runtime_data_never_in_the_image(self):
        ignored = set(read(".dockerignore").split())
        for entry in (".env", "*.db", "uploads", "attachments", "transcripts", "backups", "config.json"):
            self.assertIn(entry, ignored)

    def test_compose_trusts_forwarded_ip_only_from_caddy(self):
        compose = read("compose.yaml")
        self.assertIn("PULSE_FORWARDED_ALLOW_IPS: ${PULSE_FORWARDED_ALLOW_IPS:-172.30.0.10}", compose)
        self.assertIn("ipv4_address: 172.30.0.10", compose)
        self.assertIn("mem_limit", compose)
        self.assertNotIn("FORWARDED_ALLOW_IPS:-*", compose)  # "*" would let anyone fake the rate-limit IP


class DeployTests(unittest.TestCase):
    def test_workflow_deploys_the_tested_commit_one_at_a_time(self):
        workflow = read(".github/workflows/deploy-vps.yml")
        self.assertIn("github.event.workflow_run.head_sha", workflow)
        self.assertIn("concurrency:", workflow)
        self.assertNotIn("reset --hard origin/main", workflow)

    def test_remote_script_backs_up_checks_health_and_rolls_back(self):
        script = read("deploy/remote_deploy.sh")
        order = [script.index(s) for s in ("create_full_backup", 'git reset --hard "$SHA"', "wait_healthy; then", 'git reset --hard "$PREV"')]
        self.assertEqual(order, sorted(order))
        self.assertIn("set -euo pipefail", script)

    def test_remote_script_is_valid_bash(self):
        try:
            result = subprocess.run(["bash", "-n", str(ROOT / "deploy" / "remote_deploy.sh")], capture_output=True, timeout=20)
        except (FileNotFoundError, OSError):
            self.skipTest("bash nicht verfügbar")
        self.assertEqual(result.returncode, 0, result.stderr)


class LockTests(unittest.TestCase):
    def test_lock_pins_every_requirement_with_hashes(self):
        lock = read("requirements.lock")
        pinned = {m.group(1).lower().replace("_", "-") for m in re.finditer(r"(?m)^([A-Za-z0-9_.-]+)==", lock)}
        for line in read("requirements.txt").splitlines():
            name = re.split(r"[<>=!~\[; ]", line.strip(), maxsplit=1)[0].lower().replace("_", "-").replace(".", "-")
            if name:
                self.assertIn(name, pinned, name)
        self.assertGreater(lock.count("--hash=sha256:"), len(pinned))


if __name__ == "__main__":
    unittest.main()
