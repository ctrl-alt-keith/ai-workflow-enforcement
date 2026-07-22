from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "workflow-drift-audit.yml"
CONFIG_PATH = ROOT / "config" / "workflow-drift-audit.json"


class WorkflowDriftAuditWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

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

    def test_scanner_canonical_validation_and_evidence_contract(self) -> None:
        self.assertIn("WORKFLOW_DRIFT_READ_TOKEN", self.workflow)
        self.assertIn("gh repo list ctrl-alt-keith", self.workflow)
        self.assertIn("gh repo clone", self.workflow)
        self.assertIn('git -C "$SCAN_WORKSPACE/$name" status --porcelain=v1', self.workflow)
        self.assertIn("make workflow-drift-audit", self.workflow)
        self.assertIn("make check", self.workflow)
        self.assertIn("actions/upload-artifact@v7", self.workflow)
        self.assertIn("ARTIFACT_DIGEST: ${{ steps.upload.outputs.artifact-digest }}", self.workflow)
        self.assertIn("retention-days: 14", self.workflow)

    def test_all_result_classes_and_advisory_drift_semantics_are_explicit(self) -> None:
        for result in ("Clean", "Drift detected", "Failed", "Unable to verify"):
            self.assertIn(result, self.workflow)
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
