"""Production deployments must use verified commits and recover from unhealthy releases."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentWorkflowTests(unittest.TestCase):
    def test_deploy_is_gated_by_protected_main_and_both_successful_ci_runs(self):
        workflow = (ROOT / ".github" / "workflows" / "deploy-vps.yml").read_text(encoding="utf-8")

        self.assertIn('- "Pulse Quality and Security Checks"', workflow)
        self.assertIn('- "Pulse Sicherheit & Abdeckung"', workflow)
        self.assertIn('triggerRun.conclusion !== "success"', workflow)
        self.assertIn('triggerRun.event !== "push"', workflow)
        self.assertIn('triggerRun.head_branch !== "main"', workflow)
        self.assertIn('branch.data.protected !== true', workflow)
        self.assertIn('workflow_id: "python-syntax.yml"', workflow)
        self.assertIn('workflow_id: "security.yml"', workflow)
        self.assertIn('run.status !== "completed"', workflow)
        self.assertIn('run.conclusion !== "success"', workflow)
        self.assertIn('run.event === "push"', workflow)
        self.assertIn('run.head_branch === "main"', workflow)
        self.assertIn("github.rest.actions.listJobsForWorkflowRun", workflow)
        self.assertIn('job.conclusion !== "success"', workflow)
        self.assertIn('job.conclusion === "skipped"', workflow)
        self.assertIn('triggerRun.id !== latestCompletedRun.id', workflow)
        self.assertIn('core.setOutput("ready", "false")', workflow)
        self.assertIn('core.setOutput("ready", "true")', workflow)
        self.assertIn('core.setOutput("sha", sha)', workflow)
        self.assertNotIn("git reset --hard origin/main", workflow)

    def test_green_ci_alone_never_deploys_without_explicit_opt_in(self):
        workflow = (ROOT / ".github" / "workflows" / "deploy-vps.yml").read_text(encoding="utf-8")

        job = workflow.split("  deploy:\n", 1)[1].split("\n    environment:", 1)[0]
        self.assertIn("github.event_name == 'workflow_dispatch'", job)
        self.assertIn("vars.PULSE_AUTO_DEPLOY == 'true'", job)
        self.assertIn("github.event.workflow_run.event == 'push'", job)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", job)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", job)
        self.assertIn("environment: production", workflow)  # optional reviewer approval in GitHub
        self.assertNotIn("branches: [main]\n  push:", workflow)
        self.assertNotRegex(workflow, r"(?m)^  push:")  # no deploy directly on a commit

    def test_the_deploy_script_comes_from_the_verified_commit(self):
        """M-A4-1: a manual deploy checked out deploy/remote_deploy.sh (run on the VPS) at github.sha – the ref the
        workflow was dispatched from – instead of the verified main commit; the Discord report named that SHA too."""
        workflow = (ROOT / ".github" / "workflows" / "deploy-vps.yml").read_text(encoding="utf-8")
        checkout = workflow.split("Geprüften Stand auschecken", 1)[1].split("- name:", 1)[0]
        self.assertIn("ref: ${{ steps.verified.outputs.sha }}", checkout)
        report = workflow.split("Ergebnis in den Alert-Kanal melden", 1)[1]
        self.assertIn("DEPLOY_SHA: ${{ steps.verified.outputs.sha }}", report)
        self.assertNotIn("env.DEPLOY_SHA", workflow)

    def test_manual_deploy_only_from_main(self):
        """The dispatched ref supplies the workflow file and the deploy script; a branch must not deploy."""
        workflow = (ROOT / ".github" / "workflows" / "deploy-vps.yml").read_text(encoding="utf-8")
        verified = workflow.split("Geprüften Commit bestimmen", 1)[1].split("- name:", 1)[0]
        guard = verified.index('context.ref !== "refs/heads/main"')
        self.assertLess(guard, verified.index("context.payload.inputs?.commit_sha"))

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
