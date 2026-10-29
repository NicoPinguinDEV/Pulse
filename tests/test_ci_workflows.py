"""CI guard rails: reproducible test tooling, E2E path filter and no duplicated or weakened checks."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(ROOT / "tools"))

import ci_e2e_needed  # noqa: E402


def _pins(lock: Path) -> dict[str, str]:
    return {m.group(1).lower(): m.group(2)
            for m in re.finditer(r"(?m)^([A-Za-z0-9_.-]+)==([^\s;\\]+)", lock.read_text(encoding="utf-8"))}


class E2EScopeTests(unittest.TestCase):
    def test_documentation_and_tooling_metadata_skip_e2e(self):
        for paths in (["README.md"], ["docs/CLAUDE_CODE_SETUP.md", "CHANGELOG.md"], [".claude/skills/pulse-e2e/SKILL.md"],
                      ["tests/test_permissions.py"], [".github/workflows/security.yml"]):
            with self.subTest(paths=paths):
                self.assertFalse(ci_e2e_needed.needs_e2e(paths))

    def test_app_routes_auth_ui_and_e2e_infrastructure_trigger_e2e(self):
        for paths in (["webserver.py"], ["pulse_pro.py"], ["pulse_sessions.py"], ["static/pulse.css"],
                      ["tools/preview_server.py"], ["tests/test_e2e_dashboard.py"], ["tests/fakes.py"],
                      ["requirements.txt"], ["requirements-dev.lock"], [".github/workflows/python-syntax.yml"],
                      ["README.md", "cogs/tickets.py"], ["support_knowledge.md"], ["neu/unbekannt.xyz"]):
            with self.subTest(paths=paths):
                self.assertTrue(ci_e2e_needed.needs_e2e(paths))

    def test_empty_or_unknown_diff_runs_e2e(self):
        self.assertTrue(ci_e2e_needed.needs_e2e([]))
        self.assertTrue(ci_e2e_needed.needs_e2e(["", "  "]))

    def test_cli_prints_decision(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "ci_e2e_needed.py")], input="docs/a.md\n",
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "false")


class ReproducibleToolingTests(unittest.TestCase):
    def test_dev_lock_matches_runtime_lock_and_pins_playwright(self):
        runtime = _pins(ROOT / "requirements.lock")
        dev = _pins(ROOT / "requirements-dev.lock")
        mismatched = {name: (version, dev.get(name)) for name, version in runtime.items() if dev.get(name) != version}
        self.assertEqual(mismatched, {}, "requirements-dev.lock neu erzeugen (Befehl im Kopf der Datei)")
        declared = re.search(r"(?m)^playwright==(\S+)", (ROOT / "requirements-dev.txt").read_text(encoding="utf-8"))
        self.assertIsNotNone(declared)
        self.assertEqual(dev.get("playwright"), declared.group(1))
        self.assertIn("--hash=sha256:", (ROOT / "requirements-dev.lock").read_text(encoding="utf-8"))

    def test_playwright_stays_out_of_runtime_dependencies(self):
        for name in ("requirements.txt", "requirements.lock"):
            self.assertNotRegex((ROOT / name).read_text(encoding="utf-8"), r"(?mi)^playwright\b", name)

    def test_workflows_install_pinned_tools_only(self):
        for workflow in WORKFLOWS.glob("*.yml"):
            text = workflow.read_text(encoding="utf-8")
            with self.subTest(workflow=workflow.name):
                self.assertNotIn("@latest", text)
                for tool in ("ruff", "pip-audit", "coverage", "playwright"):
                    self.assertNotRegex(text, rf"pip install[^\n]*\s{tool}(\s|$)", f"{tool} ohne Versions-Pin")

    def test_third_party_actions_are_pinned_to_a_commit(self):
        """Only GitHub's own actions/* may use a moving tag; anything else runs with our token, so a full SHA."""
        for workflow in WORKFLOWS.glob("*.yml"):
            text = workflow.read_text(encoding="utf-8")
            for action, ref in re.findall(r"uses:\s*([\w.-]+/[\w./-]+)@([\w.-]+)", text):
                if action.startswith("actions/"):
                    continue
                with self.subTest(workflow=workflow.name, action=action):
                    self.assertRegex(ref, r"^[0-9a-f]{40}$")

    def test_ci_tests_the_versions_the_image_installs(self):
        text = (WORKFLOWS / "python-syntax.yml").read_text(encoding="utf-8")
        syntax_job = text.split("\n  syntax:\n", 1)[1].split("\n  e2e-scope:\n", 1)[0]
        self.assertIn("pip install --require-hashes -r requirements.lock", syntax_job)
        self.assertNotIn("pip install -r requirements.txt", syntax_job)
        image_job = text.split("\n  image:\n", 1)[1]
        self.assertIn("docker build", image_job)
        self.assertNotIn("docker push", image_job)

    def test_e2e_job_uses_hash_checked_lock_and_is_required_in_ci(self):
        text = (WORKFLOWS / "python-syntax.yml").read_text(encoding="utf-8")
        self.assertIn("pip install --require-hashes -r requirements-dev.lock", text)
        self.assertIn('PULSE_E2E: "1"', text)
        self.assertIn("needs.e2e-scope.outputs.e2e == 'true'", text)
        self.assertIn("python3 tools/ci_e2e_needed.py", text)


class ExistingChecksStayTests(unittest.TestCase):
    def test_quality_workflow_keeps_all_python_checks(self):
        text = (WORKFLOWS / "python-syntax.yml").read_text(encoding="utf-8")
        for step in ("python -m ruff check .", "python -m compileall -q .", "python tests/feature_smoke.py",
                     "python tests/smoke_test.py", "python -m unittest discover -s tests -p 'test_*.py'",
                     "gitleaks/gitleaks-action", "pip-audit -r requirements.lock --require-hashes"):
            self.assertIn(step, text)
        syntax_job = text.split("\n  syntax:\n", 1)[1].split("\n  e2e-scope:\n", 1)[0]
        self.assertNotIn("if:", syntax_job, "Python-Prüfungen dürfen nicht per Pfadfilter entfallen")

    def test_secret_scan_runs_exactly_once(self):
        count = sum(w.read_text(encoding="utf-8").count("gitleaks/gitleaks-action") for w in WORKFLOWS.glob("*.yml"))
        self.assertEqual(count, 1)

    def test_security_workflow_keeps_lock_audit_and_coverage(self):
        text = (WORKFLOWS / "security.yml").read_text(encoding="utf-8")
        self.assertIn("pip install --require-hashes -r requirements.lock", text)
        self.assertIn("pip-audit -r requirements.lock --require-hashes", text)
        self.assertIn("coverage run -m unittest discover", text)


if __name__ == "__main__":
    unittest.main()
