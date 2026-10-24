"""Production deployments must use verified commits and recover from unhealthy releases."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentWorkflowTests(unittest.TestCase):
    def test_deploy_is_gated_by_successful_main_push_quality_run(self):
        workflow = (ROOT / ".github" / "workflows" / "deploy-vps.yml").read_text(encoding="utf-8")

        self.assertIn('workflows: ["Pulse Quality and Security Checks"]', workflow)
        self.assertIn('run.conclusion !== "success"', workflow)
        self.assertIn('run.event !== "push"', workflow)
        self.assertIn('run.head_branch !== "main"', workflow)
        self.assertIn('workflow_id: "python-syntax.yml"', workflow)
        self.assertIn('latestMainPush.conclusion !== "success"', workflow)
        self.assertIn('latestMainPush.status !== "completed"', workflow)
        self.assertIn('run.event === "push"', workflow)
        self.assertIn('core.setOutput("sha", run.head_sha)', workflow)
        self.assertNotIn("git reset --hard origin/main", workflow)

    def test_green_ci_alone_never_deploys_without_explicit_opt_in(self):
        workflow = (ROOT / ".github" / "workflows" / "deploy-vps.yml").read_text(encoding="utf-8")

        self.assertIn("if: github.event_name == 'workflow_dispatch' || vars.PULSE_AUTO_DEPLOY == 'true'", workflow)
        self.assertIn("environment: production", workflow)  # optional reviewer approval in GitHub
        self.assertNotIn("branches: [main]\n  push:", workflow)
        self.assertNotRegex(workflow, r"(?m)^  push:")  # no deploy directly on a commit

    def test_deploy_is_serialized_monotonic_and_health_checked_with_rollback(self):
        workflow = (ROOT / ".github" / "workflows" / "deploy-vps.yml").read_text(encoding="utf-8")
        # O-03: the remote steps live in deploy/remote_deploy.sh (piped over SSH by the workflow).
        script = (ROOT / "deploy" / "remote_deploy.sh").read_text(encoding="utf-8")
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("group: pulse-vps-production", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("< deploy/remote_deploy.sh", workflow)
        self.assertIn("git diff --quiet", script)
        self.assertIn('git merge-base --is-ancestor "$SHA" origin/main', script)
        self.assertIn('git merge-base --is-ancestor "$PREV" "$SHA"', script)
        self.assertIn("create_full_backup", script)
        self.assertIn("wait_healthy", script)
        self.assertIn('git reset --hard "$PREV"', script)
        self.assertIn("/healthz", compose)
        self.assertIn("start_period: 120s", compose)

if __name__ == "__main__":
    unittest.main()
