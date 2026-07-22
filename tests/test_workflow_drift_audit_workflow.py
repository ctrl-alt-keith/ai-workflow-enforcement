from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "workflow-drift-audit.yml"
CONFIG_PATH = ROOT / "config" / "workflow-drift-audit.json"
README_PATH = ROOT / "README.md"


class WorkflowDriftAuditWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cls.readme = README_PATH.read_text(encoding="utf-8")

    def test_schedule_dispatch_permissions_and_runtime_contract(self) -> None:
        self.assertIn('cron: "40 17 * * 1"', self.workflow)
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertIn("cancel-in-progress: false", self.workflow)
        self.assertIn("timeout-minutes: 30", self.workflow)
        self.assertIn('python-version: "3.12"', self.workflow)
        self.assertIn("make workflow-drift-setup", self.workflow)

    def test_exact_checkout_and_read_only_contract(self) -> None:
        self.assertIn("ref: ${{ github.sha }}", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertIn('test "$tested_sha" = "$GITHUB_SHA"', self.workflow)
        self.assertIn("git diff --exit-code", self.workflow)
        self.assertIn("git diff --cached --exit-code", self.workflow)
        self.assertIn("git status --porcelain=v1 --untracked-files=all", self.workflow)
        self.assertNotIn("contents: write", self.workflow)
        self.assertNotIn("pull-requests: write", self.workflow)
        self.assertNotIn("issues: write", self.workflow)
        for mutation in ("git push", "gh pr create", "gh issue create", "gh api --method"):
            self.assertNotIn(mutation, self.workflow)
        for workstation_path in ("/Users/", "~/.codex", "C:\\Users\\"):
            self.assertNotIn(workstation_path, self.workflow)

    def test_github_app_authentication_is_least_privilege_and_step_local(self) -> None:
        self.assertIn("actions/create-github-app-token@v3", self.workflow)
        self.assertIn("client-id: ${{ vars.WORKFLOW_DRIFT_APP_CLIENT_ID }}", self.workflow)
        self.assertIn(
            "private-key: ${{ secrets.WORKFLOW_DRIFT_APP_PRIVATE_KEY }}",
            self.workflow,
        )
        self.assertIn("owner: ctrl-alt-keith", self.workflow)
        self.assertIn("permission-metadata: read", self.workflow)
        self.assertIn("permission-contents: read", self.workflow)
        self.assertIn("APP_CLIENT_ID: ${{ vars.WORKFLOW_DRIFT_APP_CLIENT_ID }}", self.workflow)
        self.assertEqual(3, self.workflow.count("GH_TOKEN: ${{ steps.app_auth.outputs.token }}"))
        self.assertNotIn("steps.app_auth.outputs.token", self.workflow.split("jobs:", 1)[0])
        self.assertNotIn("WORKFLOW_DRIFT_READ_TOKEN", self.workflow)
        self.assertNotIn("WORKFLOW_DRIFT_READ_TOKEN", self.readme)
        self.assertNotIn("personal access token secret", self.readme.casefold())

    def test_scanner_canonical_validation_and_evidence_contract(self) -> None:
        self.assertIn('gh api --paginate --slurp "/installation/repositories?per_page=100"', self.workflow)
        self.assertIn("gh repo list ctrl-alt-keith", self.workflow)
        self.assertIn("gh repo clone", self.workflow)
        self.assertIn("installation_repository_count", self.workflow)
        self.assertIn("active_repository_count", self.workflow)
        self.assertIn("archived_repository_count", self.workflow)
        self.assertIn("inventory_completeness", self.workflow)
        self.assertIn("required active repositories were not visible", self.workflow)
        self.assertIn("Hydration was incomplete", self.workflow)
        self.assertIn('git -C "$SCAN_WORKSPACE/$name" status --porcelain=v1', self.workflow)
        self.assertIn("make workflow-drift-audit", self.workflow)
        self.assertIn("make check", self.workflow)
        self.assertIn("actions/upload-artifact@v7", self.workflow)
        self.assertIn("ARTIFACT_DIGEST: ${{ steps.upload.outputs.artifact-digest }}", self.workflow)
        self.assertIn("retention-days: 14", self.workflow)

    def test_all_result_classes_and_advisory_drift_semantics_are_explicit(self) -> None:
        for result in ("Clean", "Drift detected", "Failed", "Unable to verify"):
            self.assertIn(result, self.workflow)
        self.assertIn("APP_AUTH_OUTCOME: ${{ steps.app_auth.outcome }}", self.workflow)
        self.assertIn("INVENTORY_OUTCOME: ${{ steps.inventory.outcome }}", self.workflow)
        self.assertIn('elif [[ "$APP_AUTH_OUTCOME" != "success" ]]', self.workflow)
        self.assertIn('elif [[ "$INVENTORY_OUTCOME" != "success" ]]', self.workflow)
        self.assertIn("A read-only GitHub App installation token could not be generated", self.workflow)
        classification = self.workflow.split("- name: Classify result and publish job summary", 1)[1]
        authentication_branch = classification.split(
            'elif [[ "$APP_AUTH_OUTCOME" != "success" ]]', 1
        )[1].split("elif", 1)[0]
        self.assertIn('result="Unable to verify"', authentication_branch)
        self.assertNotIn('result="Clean"', authentication_branch)
        self.assertIn('"Drift detected")', self.workflow)
        self.assertIn("Drift detected (advisory)", self.workflow)
        self.assertIn("always() && !cancelled()", self.workflow)

    def test_repository_owned_scan_config_preserves_current_scope(self) -> None:
        self.assertEqual(["../../ai-workflow-incubator"], self.config["notes_roots"])
        self.assertEqual(["../../ai-workflow-playbook/docs"], self.config["playbook_roots"])
        self.assertEqual("../..", self.config["workspace_root"])
        self.assertEqual("ctrl-alt-keith", self.config["organization"])
        self.assertEqual(["archive/**"], self.config["ignore"])


if __name__ == "__main__":
    unittest.main()
