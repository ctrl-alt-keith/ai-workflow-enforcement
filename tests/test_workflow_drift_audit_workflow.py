from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "workflow-drift-audit.yml"
CONFIG_PATH = ROOT / "config" / "workflow-drift-audit.json"
POLICY_PATH = ROOT / "policy" / "github-apps" / "workflow-drift" / "permissions-policy.json"


class WorkflowDriftAuditWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cls.policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    def test_schedule_dispatch_permissions_and_runtime_contract(self) -> None:
        self.assertRegex(self.workflow, r"cron:\s*['\"]40 17 \* \* 1['\"]")
        self.assertRegex(self.workflow, r"(?m)^\s*workflow_dispatch\s*:")
        self.assertRegex(self.workflow, r"contents:\s*read")
        self.assertRegex(self.workflow, r"cancel-in-progress:\s*false")
        self.assertRegex(self.workflow, r"timeout-minutes:\s*30")
        self.assertRegex(self.workflow, r"python-version:\s*['\"]3\.12['\"]")
        self.assertIn("make workflow-drift-setup", self.workflow)

    def test_exact_checkout_and_read_only_contract(self) -> None:
        self.assertRegex(self.workflow, r"ref:\s*\$\{\{ github\.sha \}\}")
        self.assertRegex(self.workflow, r"persist-credentials:\s*false")
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
        self.assertRegex(self.workflow, r"client-id:\s*\$\{\{ vars\.WORKFLOW_DRIFT_APP_CLIENT_ID \}\}")
        self.assertRegex(self.workflow, r"private-key:\s*\$\{\{ secrets\.WORKFLOW_DRIFT_APP_PRIVATE_KEY \}\}")
        self.assertRegex(self.workflow, r"owner:\s*ctrl-alt-keith")
        self.assertRegex(self.workflow, r"permission-metadata:\s*read")
        self.assertRegex(self.workflow, r"permission-contents:\s*read")
        self.assertRegex(
            self.workflow,
            r"APP_CLIENT_ID:\s*\$\{\{ vars\.WORKFLOW_DRIFT_APP_CLIENT_ID \}\}",
        )
        self.assertEqual(
            3,
            len(
                re.findall(
                    r"GH_TOKEN:\s*\$\{\{ steps\.app_auth\.outputs\.token \}\}",
                    self.workflow,
                )
            ),
        )
        self.assertNotIn("WORKFLOW_DRIFT_READ_TOKEN", self.workflow)

    def test_private_key_uses_the_reviewed_managed_secret_delivery_contract(self) -> None:
        actions_configuration = self.policy["actions_configuration"]
        secret_name = actions_configuration["private_key_secret"]
        self.assertEqual(
            "github_actions_repository_secret_direct_action_input",
            actions_configuration["private_key_delivery"],
        )
        self.assertEqual("forbidden", actions_configuration["plaintext_fallback"])
        self.assertEqual("not_required_by_this_runtime", actions_configuration["webhook_secret"])
        self.assertRegex(self.workflow, rf"private-key:\s*\$\{{\{{ secrets\.{secret_name} \}}\}}")
        self.assertNotRegex(self.workflow, rf"(?m)^\s*{secret_name}\s*:\s*\$\{{\{{ secrets\.{secret_name} \}}\}}")
        self.assertNotIn("base64 -d", self.workflow)
        self.assertNotIn("openssl", self.workflow)
        self.assertNotIn("private-key=", self.workflow)

    def test_scanner_canonical_validation_and_evidence_contract(self) -> None:
        self.assertIn('gh api --paginate --slurp "/installation/repositories?per_page=100"', self.workflow)
        self.assertIn("gh repo list ctrl-alt-keith", self.workflow)
        self.assertIn("gh repo clone", self.workflow)
        self.assertIn("installation_repository_count", self.workflow)
        self.assertIn("active_repository_count", self.workflow)
        self.assertIn("archived_repository_count", self.workflow)
        self.assertIn("inventory_completeness", self.workflow)
        self.assertIn("if missing_required:", self.workflow)
        self.assertIn('if [[ "$hydrated_count" -ne "$expected_count" ]]', self.workflow)
        self.assertIn('git -C "$SCAN_WORKSPACE/$name" status --porcelain=v1', self.workflow)
        self.assertIn("make workflow-drift-audit", self.workflow)
        self.assertIn("make check", self.workflow)
        self.assertIn("actions/upload-artifact@v7", self.workflow)
        self.assertRegex(
            self.workflow,
            r"ARTIFACT_DIGEST:\s*\$\{\{ steps\.upload\.outputs\.artifact-digest \}\}",
        )
        self.assertRegex(self.workflow, r"retention-days:\s*14")

    def test_all_result_classes_and_advisory_drift_semantics_are_explicit(self) -> None:
        for result in ("Clean", "Drift detected", "Failed", "Unable to verify"):
            self.assertIn(result, self.workflow)
        self.assertRegex(
            self.workflow,
            r"APP_AUTH_OUTCOME:\s*\$\{\{ steps\.app_auth\.outcome \}\}",
        )
        self.assertRegex(
            self.workflow,
            r"INVENTORY_OUTCOME:\s*\$\{\{ steps\.inventory\.outcome \}\}",
        )
        self.assertIn('elif [[ "$APP_AUTH_OUTCOME" != "success" ]]', self.workflow)
        self.assertIn('elif [[ "$INVENTORY_OUTCOME" != "success" ]]', self.workflow)
        classification = self.workflow.split("id: classify", 1)[1].split("id: upload", 1)[0]
        authentication_branch = classification.split(
            'elif [[ "$APP_AUTH_OUTCOME" != "success" ]]', 1
        )[1].split("elif", 1)[0]
        self.assertIn('result="Unable to verify"', authentication_branch)
        self.assertNotIn('result="Clean"', authentication_branch)
        self.assertIn('"Drift detected")', self.workflow)
        self.assertIn("always() && !cancelled()", self.workflow)

    def test_repository_owned_scan_config_preserves_current_scope(self) -> None:
        self.assertEqual(["../../ai-workflow-incubator"], self.config["notes_roots"])
        self.assertEqual(["../../ai-workflow-playbook/docs"], self.config["playbook_roots"])
        self.assertEqual("../..", self.config["workspace_root"])
        self.assertEqual("ctrl-alt-keith", self.config["organization"])
        self.assertEqual(["archive/**"], self.config["ignore"])


if __name__ == "__main__":
    unittest.main()
