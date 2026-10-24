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

    def test_docker_never_reads_the_piped_script(self):
        # The workflow (and the manual PowerShell command) run the script via `bash -s` on stdin. Without
        # `</dev/null`, `docker compose exec/run` forwards that stdin into the container and swallows the rest
        # of the script: on 2026-10-24 a deploy stopped silently right after the backup line.
        for name in ("deploy/remote_deploy.sh", "deploy/rollback_code.sh"):
            for number, line in enumerate(read(name).splitlines(), 1):
                code = line.split("#", 1)[0]
                if re.search(r"\bdocker compose (exec|run|up|logs)\b", code):
                    with self.subTest(script=name, line=number):
                        self.assertIn("</dev/null", code.replace("< /dev/null", "</dev/null"), line.strip())

    def test_git_trusts_the_checkout_owned_by_the_container_user(self):
        # The scripts run as root but chown the checkout to UID 1000 (O-01). Git then refuses every command
        # ("dubious ownership") – on 2026-10-24 the deploy stopped before `git reset`. The exception is set only
        # for the git calls of the script (environment), not in the global git configuration.
        for name in ("deploy/remote_deploy.sh", "deploy/rollback_code.sh"):
            with self.subTest(script=name):
                script = read(name)
                marker = 'export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$(pwd -P)"'
                self.assertIn(marker, script)
                self.assertLess(script.index('cd "$REPO_DIR"'), script.index(marker))
                self.assertLess(script.index(marker), script.index("git rev-parse HEAD"))
                self.assertNotIn("git config --global", script)

    def test_remote_script_is_valid_bash(self):
        for script in ("remote_deploy.sh", "vps_bootstrap.sh"):
            with self.subTest(script=script):
                result = bash_syntax_check(ROOT / "deploy" / script)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_deploy_files_keep_lf_line_endings(self):
        # A CRLF from a Windows editor breaks `bash -s` on the VPS – .gitattributes pins LF.
        attributes = read(".gitattributes")
        for pattern in ("*.sh", "Dockerfile", "Caddyfile", "*.yml", "*.yaml"):
            self.assertRegex(attributes, rf"(?m)^{re.escape(pattern)}\s+text eol=lf$")
        for name in ("deploy/remote_deploy.sh", "deploy/vps_bootstrap.sh"):
            self.assertNotIn(b"\r\n", (ROOT / name).read_bytes(), name)


class BootstrapTests(unittest.TestCase):
    """O-08: one-time VPS preparation (Vionity, Ubuntu 24.04, 2 GiB) – idempotent and data-safe."""

    def setUp(self):
        self.script = read("deploy/vps_bootstrap.sh")

    def test_firewall_keeps_ssh_open_and_dashboard_port_closed(self):
        self.assertIn('ufw allow "${SSH_PORT}/tcp"', self.script)
        self.assertLess(self.script.index('ufw allow "${SSH_PORT}/tcp"'), self.script.index("ufw --force enable"))
        self.assertIn("sshd -T", self.script)  # real SSH port, no lock-out on a non-default port
        self.assertNotIn("25095", re.sub(r"(?m)^\s*(#|echo).*$", "", self.script))

    def test_swap_and_repo_steps_are_idempotent(self):
        self.assertIn("swapon --show", self.script)
        self.assertIn("grep -q '^/swapfile ' /etc/fstab", self.script)
        self.assertIn('if [ -d "$REPO_DIR/.git" ]', self.script)

    def test_never_touches_runtime_data_or_starts_pulse(self):
        code = re.sub(r"(?m)^\s*#.*$", "", self.script.split("cat <<EOF\n\nFertig.")[0])
        for forbidden in ("rm -", "git reset", "git clean", "docker compose up", "> \"$REPO_DIR/.env\""):
            self.assertNotIn(forbidden, code)
        self.assertIn("set -euo pipefail", self.script)


def bash_syntax_check(path: Path):
    """`bash -n` with a real bash; the Windows WSL stub without a distribution does not count."""
    try:
        probe = subprocess.run(["bash", "-c", "echo ok"], capture_output=True, timeout=20)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        raise unittest.SkipTest("bash nicht verfügbar")
    if probe.returncode != 0 or probe.stdout.strip() != b"ok":
        raise unittest.SkipTest("bash nicht lauffähig (z. B. WSL ohne Distribution)")
    return subprocess.run(["bash", "-n", path.as_posix()], capture_output=True, timeout=20)


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
