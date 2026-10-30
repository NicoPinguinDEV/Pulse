"""Phase 8 (O-01 … O-04): deployment files keep their safety properties.

These files are not executed in tests (no Docker/GitHub here), so the checks read them – each assert names
the property that protects production (non-root, health check, tested SHA, rollback, no secrets).
"""
import re
import subprocess
import unittest
from pathlib import Path
from typing import Any

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
        # Caddy container (172.30.0.10) or a Caddy on the host, which reaches Pulse via the gateway 172.30.0.1.
        self.assertIn("PULSE_FORWARDED_ALLOW_IPS: ${PULSE_FORWARDED_ALLOW_IPS:-172.30.0.10,172.30.0.1}", compose)
        self.assertIn("ipv4_address: 172.30.0.10", compose)
        self.assertIn('"${PULSE_PUBLISH_HOST:-127.0.0.1}:', compose)  # gateway trust is only safe on loopback
        self.assertIn("mem_limit", compose)
        self.assertNotIn("FORWARDED_ALLOW_IPS:-*", compose)  # "*" would let anyone fake the rate-limit IP

    def test_host_proxy_via_gateway_yields_real_client_ip(self):
        # Regression: a host Caddy connects through the Docker gateway; the audit log showed 172.30.0.1 for everyone.
        import asyncio
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

        seen = {}

        async def app(scope, receive, send):
            seen["client"] = scope.get("client")

        trusted = "172.30.0.10,172.30.0.1"
        for peer, xff, expected in (
            ("172.30.0.1", b"203.0.113.7", "203.0.113.7"),               # host Caddy → real IP
            ("172.30.0.1", b"6.6.6.6, 203.0.113.7", "203.0.113.7"),      # forged left entry is ignored
            ("198.51.100.9", b"203.0.113.7", "198.51.100.9"),            # untrusted peer cannot fake it
        ):
            scope: Any = {"type": "http", "client": (peer, 1234), "headers": [(b"x-forwarded-for", xff)]}
            unused: Any = None
            asyncio.run(ProxyHeadersMiddleware(app, trusted_hosts=trusted)(scope, unused, unused))
            self.assertEqual(seen["client"][0], expected)


class DeployTests(unittest.TestCase):
    def test_workflow_deploys_the_tested_commit_one_at_a_time(self):
        workflow = read(".github/workflows/deploy-vps.yml")
        # The automatic path takes the SHA of the finished check run (context.payload.workflow_run.head_sha) and every
        # later step uses that verified output – also the checkout of the deploy script (M-A4-1).
        self.assertIn('core.setOutput("sha", run.head_sha)', workflow)
        self.assertEqual(workflow.count("${{ steps.verified.outputs.sha }}"), 3)
        self.assertIn("concurrency:", workflow)
        self.assertNotIn("reset --hard origin/main", workflow)

    def test_remote_script_backs_up_checks_health_and_rolls_back(self):
        script = read("deploy/remote_deploy.sh")
        order = [script.index(s) for s in ("create_full_backup", 'git reset --hard "$SHA"', "wait_healthy; then")]
        order.append(script.rindex('git reset --hard "$PREV"'))  # the rollback after the failed health check
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

    def test_image_installs_the_hash_checked_lock(self):
        dockerfile = read("Dockerfile")
        self.assertIn("pip install --require-hashes -r /app/requirements.lock", dockerfile)
        self.assertNotRegex(dockerfile, r"pip install -r \S*requirements\.txt")

    def test_lock_pins_satisfy_the_ranges_in_requirements_txt(self):
        """A Dependabot bump of requirements.txt without regenerating the lock must fail here, not on the VPS."""
        def version(text):
            return tuple(int(part) if part.isdigit() else 0 for part in re.findall(r"\d+", text))

        pins = {m.group(1).lower().replace("_", "-").replace(".", "-"): version(m.group(2))
                for m in re.finditer(r"(?m)^([A-Za-z0-9_.-]+)==(\S+)", read("requirements.lock"))}
        checks = {">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
                  ">": lambda a, b: a > b, "==": lambda a, b: a == b}
        for line in read("requirements.txt").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            name = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0]
            pinned = pins[name.lower().replace("_", "-").replace(".", "-")]
            for op, bound in re.findall(r"(>=|<=|==|<|>)\s*([0-9][^,;\s]*)", line[len(name):]):
                self.assertTrue(checks[op](pinned, version(bound)), f"{name}: Lock-Version verletzt {op}{bound}")


if __name__ == "__main__":
    unittest.main()


class RemoteDeployRunTests(unittest.TestCase):
    """Runs deploy/remote_deploy.sh for real against fake `git`/`docker` commands (no VPS, no Docker)."""

    PREV, SHA = "a" * 40, "b" * 40

    def _bash(self) -> str:
        import os
        bash = os.environ.get("PULSE_TEST_BASH", "bash")  # e.g. Git Bash on Windows, where `bash` is the WSL stub
        try:
            probe = subprocess.run([bash, "-c", "echo ok"], capture_output=True, timeout=20)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            raise unittest.SkipTest("bash nicht verfügbar")
        if probe.returncode != 0 or probe.stdout.strip() != b"ok":
            raise unittest.SkipTest("bash nicht lauffähig (z. B. WSL ohne Distribution)")
        return bash

    def _run(self, failing: str):
        import tempfile
        bash = self._bash()
        tmp = Path(tempfile.mkdtemp())
        (tmp / "bin").mkdir()
        (tmp / "repo").mkdir()
        log = (tmp / "calls.log").as_posix()
        (tmp / "bin" / "git").write_text(
            "#!/usr/bin/env bash\n"
            f'echo "git $*" >> "{log}"\n'
            f'if [ "$1" = rev-parse ]; then echo {self.PREV}; fi\n'
            "exit 0\n", encoding="utf-8", newline="\n")
        (tmp / "bin" / "docker").write_text(
            "#!/usr/bin/env bash\n"
            f'echo "docker $*" >> "{log}"\n'
            f'case "$*" in *"{failing}"*) exit 1;; esac\n'  # "build" matches `up -d --build` and `compose build`
            "exit 0\n", encoding="utf-8", newline="\n")
        (tmp / "bin" / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
        script = (
            f'export PATH="$(cd "{(tmp / "bin").as_posix()}" && pwd):$PATH"; chmod +x "{(tmp / "bin").as_posix()}"/*; '
            f'export PULSE_UID="$(stat -c %u "{(tmp / "repo").as_posix()}")" PULSE_DEPLOY_HEALTH_SECONDS=5 '
            f'PULSE_DEPLOY_LOCK="{(tmp / "deploy.lock").as_posix()}"; '
            f'bash "{(ROOT / "deploy" / "remote_deploy.sh").as_posix()}" {self.SHA} "{(tmp / "repo").as_posix()}"'
        )
        result = subprocess.run([bash, "-c", script], capture_output=True, timeout=60)
        calls = Path(log).read_text(encoding="utf-8").splitlines() if Path(log).exists() else []
        return result, calls

    def test_failed_image_build_puts_the_checkout_back(self):
        """Build fails after `git reset --hard SHA`: the checkout must return to the running commit, otherwise
        the next deploy says "läuft bereits" and a reboot starts untested code."""
        result, calls = self._run(failing="build")
        self.assertNotEqual(result.returncode, 0)
        resets = [c for c in calls if c.startswith("git reset --hard")]
        self.assertEqual(resets[-1], f"git reset --hard {self.PREV}", calls)

    def test_successful_deploy_ends_on_the_target_commit(self):
        result, calls = self._run(failing="never-fails")
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace"))
        resets = [c for c in calls if c.startswith("git reset --hard")]
        self.assertEqual(resets, [f"git reset --hard {self.SHA}"])

    def test_failed_start_rolls_back_to_the_running_commit(self):
        result, calls = self._run(failing="up -d")
        self.assertNotEqual(result.returncode, 0)
        resets = [c for c in calls if c.startswith("git reset --hard")]
        self.assertEqual(resets, [f"git reset --hard {self.SHA}", f"git reset --hard {self.PREV}"])


class HostBoundaryTests(unittest.TestCase):
    """The container may write its data, but never the files root on the host executes or reads."""

    READ_ONLY = (".git", "compose.yaml", "Dockerfile", "deploy", "scripts", "systemd")

    def test_infra_and_git_are_read_only_in_the_container(self):
        compose = read("compose.yaml")
        self.assertIn("- ./:/app\n", compose)  # runtime data stays where it is (no migration)
        for path in self.READ_ONLY:
            with self.subTest(path=path):
                self.assertIn(f"- ./{path}:/app/{path}:ro", compose)
        for path in self.READ_ONLY:  # all of them are tracked, so the bind source always exists (no empty dir)
            self.assertTrue((ROOT / path).exists(), path)

    def test_root_scripts_ignore_compose_overrides(self):
        for name in ("deploy/remote_deploy.sh", "deploy/rollback_code.sh", "scripts/pulse-vps-boot-update.sh"):
            with self.subTest(script=name):
                script = read(name)
                self.assertIn("export COMPOSE_FILE=compose.yaml", script)
                first_compose = min(i for i in (script.find("docker compose ps"), script.find("docker compose up"),
                                                script.find("docker compose stop"), script.find("docker compose build"))
                                    if i >= 0)
                self.assertLess(script.index("export COMPOSE_FILE=compose.yaml"), first_compose)
        self.assertIn('env["COMPOSE_FILE"] = "compose.yaml"', read("deploy/pulse_host_agent.py"))

    def test_pulse_only_reads_git(self):
        """The ro .git mount is safe because Pulse never writes there (read_git_head reads HEAD/refs only)."""
        for path in ROOT.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"""\.git['"/][^\n]*\.(write_text|write_bytes|open\([^)]*['"]w)""", path.name)
