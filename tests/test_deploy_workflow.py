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

    def test_deploy_is_serialized_monotonic_and_health_checked_with_rollback(self):
        workflow = (ROOT / ".github" / "workflows" / "deploy-vps.yml").read_text(encoding="utf-8")
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("group: pulse-vps-production", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("git diff --quiet", workflow)
        self.assertIn("git merge-base --is-ancestor HEAD", workflow)
        self.assertIn("PREVIOUS_SHA", workflow)
        self.assertIn("docker compose up -d --build --wait --wait-timeout 180", workflow)
        self.assertIn(r'git checkout --detach \"\$PREVIOUS_SHA\"', workflow)
        self.assertIn("/healthz", compose)
        self.assertIn("start_period: 120s", compose)


if __name__ == "__main__":
    unittest.main()
