"""Phase 13.2: the operations documentation must match the code and must not contradict itself.

Checks the Python version (Dockerfile ↔ docs ↔ CI), the order of the VPS migration runbook in VPS_DEPLOYMENT.md
(root helpers → migration → verify → deploy → checks → crash restart last), the documented way back, that the
documented commands/words/paths exist in the scripts, and that deploys stay manual. Reads files only.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def image_python() -> str:
    match = re.search(r"^FROM python:(\d+\.\d+)-slim\s*$", read("Dockerfile"), re.M)
    assert match, "FROM python:X.Y-slim fehlt im Dockerfile"
    return match.group(1)


def runbook() -> str:
    text = read("VPS_DEPLOYMENT.md")
    start = text.index("### Phase 13.1: einmalige Datenmigration auf dem VPS (Schritt für Schritt)")
    end = text.index("### Phase 13.1: Rückweg zur bisherigen Struktur")
    return text[text.index("#### A", start):end]  # numbered steps only (the reasons above are numbered too)


def rollback_section() -> str:
    text = read("VPS_DEPLOYMENT.md")
    start = text.index("### Phase 13.1: Rückweg zur bisherigen Struktur")
    return text[start:text.index("### Neustart nach Absturz", start)]


def step(text: str, number: int) -> int:
    """Position of the numbered runbook step (`12. `, also indented)."""
    match = re.search(rf"^\s*{number}\. ", text, re.M)
    assert match, f"Schritt {number} fehlt"
    return match.start()


class PythonVersionTests(unittest.TestCase):
    def test_docs_name_the_image_python(self):
        version = image_python()
        self.assertIn(f"Python {version}", read("VPS_DEPLOYMENT.md").split("\n", 4)[2])
        self.assertIn(f"Python {version}", read("docs/ARCHITEKTUR.md"))
        self.assertIn(f"derzeit {version}", read("CLAUDE.md"))
        self.assertIn(f"python:{version}-slim", read("docs/SECURITY_HARDENING.md"))

    def test_dockerfile_comment_names_no_other_version(self):
        comments = "\n".join(line for line in read("Dockerfile").splitlines() if line.lstrip().startswith("#"))
        self.assertIsNone(re.search(r"Python 3\.\d+", comments), "veraltete Versionsangabe im Dockerfile-Kommentar")

    def test_ci_runs_the_full_suite_on_the_image_python(self):
        ci = read(".github/workflows/python-syntax.yml")
        job = ci.split("  syntax-image-python:", 1)[1].split("\n  e2e-scope:", 1)[0]
        self.assertIn(f'python-version: "{image_python()}"', job)
        self.assertIn("--require-hashes -r requirements.lock", job)
        self.assertIn("python -m unittest discover -s tests -p 'test_*.py'", job)
        self.assertIn("python tests/feature_smoke.py", job)


class MigrationOrderTests(unittest.TestCase):
    def setUp(self):
        self.text = runbook()

    def test_the_runbook_is_complete_and_numbered(self):
        positions = [step(self.text, n) for n in range(1, 42)]
        self.assertEqual(positions, sorted(positions), "Schritte nicht fortlaufend nummeriert")

    def test_snapshot_and_backup_come_first(self):
        self.assertIn("Snapshot", self.text[step(self.text, 1):step(self.text, 2)])
        self.assertIn("herunterladen", self.text[step(self.text, 2):step(self.text, 3)])

    def test_order_follows_the_code(self):
        t = self.text
        order = [
            ("git fetch origin main", "Fetch"),
            ("install -m 755 /root/pulse-vps-boot-update.sh /usr/local/sbin/pulse-vps-boot-update.sh", "Boot-Helfer"),
            ("install -m 755 /root/pulse-crash-restart.sh /usr/local/sbin/pulse-crash-restart.sh", "Crash-Helfer"),
            ("command -v flock", "flock"),
            ("flock -n /run/lock/pulse-deploy.lock", "Sperre"),
            ("touch /var/lib/pulse-crash-restart/paused", "Crash pausieren"),
            ("docker stop --time 40 pulse-teamos", "Stop"),
            ("git show origin/main:scripts/pulse_migrate_data.py > /root/pulse_migrate_data.py", "Skript holen"),
            ("--target /opt/pulse/data\n", "Probelauf"),
            ("--target /opt/pulse/data --execute", "Migration"),
            ("--target /opt/pulse/data --verify", "Verify"),
            ("Deploy Pulse VPS", "Deploy"),
            ("curl -s http://127.0.0.1:25095/healthz", "Health"),
            ("Dashboard-Login", "Dashboard"),
            ("rm /var/lib/pulse-crash-restart/paused", "Crash wieder an"),
        ]
        positions = []
        for needle, label in order:
            if label == "Health":  # the health check after the deploy, not the pre-check in part B
                positions.append(t.index(needle, t.index("Deploy Pulse VPS")))
            elif label == "Crash wieder an":  # step 41, not the abort hint in step 29
                positions.append(t.index(needle, step(t, 41)))
            else:
                self.assertIn(needle, t, label)
                positions.append(t.index(needle))
        self.assertEqual(positions, sorted(positions),
                         [label for (_needle, label), _pos in sorted(zip(order, positions), key=lambda x: x[1])])

    def test_helpers_come_from_the_checked_main(self):
        self.assertIn("git show origin/main:scripts/pulse-vps-boot-update.sh > /root/pulse-vps-boot-update.sh", self.text)
        self.assertIn("git show origin/main:scripts/pulse-crash-restart.sh > /root/pulse-crash-restart.sh", self.text)
        self.assertNotIn("install -m 755 scripts/", self.text)  # never from the (old) checkout

    def test_first_deploy_never_through_the_checkout_script(self):
        deploy = self.text[step(self.text, 33):step(self.text, 34)]
        self.assertIn("Actions", deploy)
        self.assertIn("**Nicht** über das Server Control Center", deploy)
        self.assertNotIn("bash deploy/remote_deploy.sh", self.text)
        self.assertIn("das Server Control Center **nicht** verwenden", read("VPS_DEPLOYMENT.md"))  # reason 3

    def test_crash_restart_only_after_the_functional_check(self):
        resume = self.text.index("rm /var/lib/pulse-crash-restart/paused", step(self.text, 41))
        self.assertGreater(resume, step(self.text, 40))
        early = self.text[:step(self.text, 40)]
        self.assertNotIn("rm /var/lib/pulse-crash-restart/paused`", early.replace("`docker start pulse-teamos` und `rm /var/lib/pulse-crash-restart/paused`", ""))

    def test_every_critical_step_has_an_expectation_and_stop(self):
        for n in (8, 9, 11, 16, 19, 22, 23, 27, 29, 30, 31, 32):
            with self.subTest(step=n):
                block = self.text[step(self.text, n):step(self.text, n + 1)]
                self.assertIn("Erwartet", block)
                self.assertIn("STOPP", block)

    def test_commands_are_one_per_block_and_not_powershell(self):
        for block in re.findall(r"```bash\n(.*?)```", self.text, re.S):
            lines = [line for line in block.strip().splitlines() if line.strip()]
            self.assertLessEqual(len(lines), 1, block)
            self.assertNotIn("<<", block)  # no heredocs
            self.assertNotRegex(block, r"Get-|\$env:")
        for block in re.findall(r"```powershell\n(.*?)```", self.text, re.S):
            self.assertNotRegex(block, r"\b(sudo|docker|systemctl|git|install -m)\b")


class ConsistencyWithCodeTests(unittest.TestCase):
    def test_words_paths_and_names_exist_in_the_code(self):
        migrate = read("scripts/pulse_migrate_data.py")
        text = runbook() + rollback_section()
        for word in ("MIGRIEREN", "ZURUECK"):
            self.assertIn(f'"{word}"', migrate)
            self.assertIn(word, text)
        for flag in ("--execute", "--verify", "--rollback", "--source", "--target"):
            self.assertIn(f'"{flag}"', migrate)
        self.assertIn("Probelauf – nichts verändert.", migrate)
        self.assertIn("Ziel stimmt mit der Quelle überein.", migrate)
        self.assertIn("container_name: pulse-teamos", read("compose.yaml"))
        self.assertIn("ExecStart=/usr/local/sbin/pulse-vps-boot-update.sh", read("systemd/pulse-github-update.service"))
        self.assertIn("ExecStart=/usr/local/sbin/pulse-crash-restart.sh", read("systemd/pulse-crash-restart.service"))
        self.assertIn("StateDirectory=pulse-crash-restart", read("systemd/pulse-crash-restart.service"))
        self.assertIn('"$STATE_DIR/paused"', read("scripts/pulse-crash-restart.sh"))
        self.assertIn("/run/lock/pulse-deploy.lock", read("scripts/pulse-vps-boot-update.sh"))
        self.assertIn("stop_grace_period: 40s", read("compose.yaml"))

    def test_version_check_of_the_new_helper_matches(self):
        boot = read("scripts/pulse-vps-boot-update.sh")
        hits = sum(1 for line in boot.splitlines() if "pulse-deploy.lock" in line or "data_dir_ready" in line)
        self.assertGreaterEqual(hits, 3)  # runbook step 19 expects ≥ 3

    def test_the_host_agent_really_runs_the_checkout_script(self):
        """Why the first deploy must go through GitHub Actions (runbook, reason 3)."""
        agent = read("deploy/pulse_host_agent.py")
        self.assertIn('self.config.repo / "deploy" / "remote_deploy.sh"', agent)
        self.assertIn("< deploy/remote_deploy.sh", read(".github/workflows/deploy-vps.yml"))

    def test_reports_point_to_the_runbook(self):
        for name in ("docs/PHASE13_BERICHT.md", "docs/PHASE13_1_BERICHT.md"):
            with self.subTest(report=name):
                self.assertIn("Phase 13.1: einmalige Datenmigration auf dem VPS (Schritt für Schritt)", read(name))


class RollbackDocumentationTests(unittest.TestCase):
    def test_way_back_is_complete(self):
        text = rollback_section()
        order = ["touch /var/lib/pulse-crash-restart/paused", "docker stop --time 40 pulse-teamos",
                 "--target /opt/pulse/data --rollback\n", "ZURUECK", "--rollback --execute",
                 "git reset --hard d221b6c", "docker compose up -d --build", "rm /var/lib/pulse-crash-restart/paused"]
        positions = [text.index(needle) for needle in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("rolled_back", text)
        self.assertIn(".pulse-legacy-", text)
        self.assertNotIn("cp -a /opt/pulse/data", text)  # the old overwrite-copy is gone

    def test_no_destructive_commands_in_the_runbook(self):
        text = "\n".join(re.findall(r"```bash\n(.*?)```", runbook() + rollback_section(), re.S))  # commands only
        for forbidden in ("rm -rf", "git clean", "down -v", "rm /opt/pulse/data", "--force"):
            with self.subTest(command=forbidden):
                self.assertNotIn(forbidden, text)


class ManualDeployTests(unittest.TestCase):
    def test_workflow_deploys_only_manually_or_with_explicit_variable(self):
        workflow = read(".github/workflows/deploy-vps.yml")
        self.assertIn("if: github.event_name == 'workflow_dispatch' || vars.PULSE_AUTO_DEPLOY == 'true'", workflow)

    def test_docs_do_not_claim_automatic_deploys(self):
        for name in ("docs/BETRIEB.md", "VPS_DEPLOYMENT.md"):
            with self.subTest(doc=name):
                self.assertNotIn("Automatisch nach einem Merge", read(name))
        self.assertIn("**Nur von Hand:**", read("docs/BETRIEB.md"))
        self.assertIn("PULSE_AUTO_DEPLOY", read("docs/BETRIEB.md"))


if __name__ == "__main__":
    unittest.main()
