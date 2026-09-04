from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "hosted-stewardship.yml"
CONFIG_PATH = ROOT / "config" / "hosted-stewardship.json"
SCHEMA_PATH = ROOT / "schemas" / "hosted-stewardship-receipt.schema.json"
PRODUCT_BOUNDARY_PATH = ROOT / "docs" / "product-boundary.md"


class HostedStewardshipWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.product_boundary = PRODUCT_BOUNDARY_PATH.read_text(encoding="utf-8")

    def test_manual_single_repository_two_mode_contract(self) -> None:
        self.assertRegex(self.workflow, r"(?m)^\s*workflow_dispatch\s*:")
        self.assertNotRegex(self.workflow, r"(?m)^\s*schedule\s*:")
        self.assertRegex(self.workflow, r"(?m)^\s*-\s+ctrl-alt-keith/ai-workflow-enforcement\s*$")
        self.assertRegex(self.workflow, r"(?m)^\s*target_ref\s*:")
        self.assertRegex(self.workflow, r"default:\s*(?:\"\"|'')")
        modes = re.findall(r"(?m)^\s*-\s+(dry-run|propose)\s*$", self.workflow)
        self.assertEqual(2, len(modes))
        self.assertEqual({"dry-run", "propose"}, set(modes))
        self.assertRegex(self.workflow, r"group:\s*hosted-stewardship-\$\{\{ inputs\.repository \}\}")
        self.assertRegex(self.workflow, r"cancel-in-progress:\s*false")

    def test_strategy_input_has_exact_three_choices_and_docs_drift_default(self) -> None:
        self.assertRegex(self.workflow, r"default:\s*docs-drift")
        strategies = re.findall(
            r"(?m)^\s*-\s+(docs-drift|agents-startup-routing|worktree-ignore-baseline)\s*$",
            self.workflow,
        )
        self.assertEqual(3, len(strategies))
        self.assertEqual(
            {"docs-drift", "agents-startup-routing", "worktree-ignore-baseline"},
            set(strategies),
        )

    def test_read_and_write_identities_are_distinct_and_narrow(self) -> None:
        self.assertRegex(self.workflow, r"contents:\s*read")
        self.assertIn("WORKFLOW_DRIFT_APP_CLIENT_ID", self.workflow)
        self.assertIn("WORKFLOW_DRIFT_APP_PRIVATE_KEY", self.workflow)
        self.assertIn("STEWARDSHIP_WRITE_APP_CLIENT_ID", self.workflow)
        self.assertIn("STEWARDSHIP_WRITE_APP_PRIVATE_KEY", self.workflow)
        self.assertIn("inputs.mode == 'propose'", self.workflow)
        self.assertIn("inputs.target_ref == ''", self.workflow)
        self.assertEqual(2, self.workflow.count("repositories: ${{ inputs.repository }}"))
        self.assertRegex(self.workflow, r"permission-contents:\s*read")
        self.assertRegex(self.workflow, r"permission-contents:\s*write")
        self.assertRegex(self.workflow, r"permission-pull-requests:\s*write")
        self.assertNotIn("permission-administration", self.workflow)

    def test_modes_share_one_cli_pipeline_and_only_propose_receives_write_token(self) -> None:
        self.assertEqual(1, self.workflow.count("python3 -m enforcement.stewardship.cli"))
        self.assertIn('--mode "${{ inputs.mode }}"', self.workflow)
        self.assertIn('--strategy "${{ inputs.strategy }}"', self.workflow)
        self.assertIn('--target-ref "${TARGET_REF}"', self.workflow)
        self.assertRegex(
            self.workflow,
            r"STEWARDSHIP_READ_TOKEN:\s*\$\{\{ steps\.read_auth\.outputs\.token \}\}",
        )
        self.assertRegex(
            self.workflow,
            r"STEWARDSHIP_WRITE_TOKEN:\s*\$\{\{ steps\.write_auth\.outputs\.token \}\}",
        )
        self.assertRegex(self.workflow, r"TARGET_REF:\s*\$\{\{ inputs\.target_ref \}\}")

    def test_exact_engine_identity_evidence_and_no_forbidden_delivery(self) -> None:
        self.assertRegex(self.workflow, r"ref:\s*\$\{\{ github\.sha \}\}")
        self.assertRegex(self.workflow, r"persist-credentials:\s*false")
        self.assertIn('--engine-revision "${GITHUB_SHA}"', self.workflow)
        self.assertIn("actions/upload-artifact@v7", self.workflow)
        self.assertRegex(self.workflow, r"retention-days:\s*14")
        for forbidden in (
            "gh pr merge",
            "--auto",
            "--force",
            "force-with-lease",
            "permission-administration",
            "permission-organization",
        ):
            self.assertNotIn(forbidden, self.workflow)

    def test_allowlist_policy_marker_and_receipt_schema_are_repository_owned(self) -> None:
        self.assertEqual(1, self.config["schema_version"])
        self.assertEqual(
            ["ctrl-alt-keith/ai-workflow-enforcement"],
            list(self.config["repositories"]),
        )
        policy = self.config["repositories"]["ctrl-alt-keith/ai-workflow-enforcement"]
        self.assertIn(policy["required_policy_marker"], self.product_boundary)
        self.assertEqual(1, self.schema["properties"]["schema_version"]["const"])
        self.assertEqual(
            [
                "docs-drift",
                "agents-startup-routing",
                "worktree-ignore-baseline",
            ],
            self.schema["properties"]["strategy_identifier"]["enum"],
        )
        self.assertEqual("1", self.schema["properties"]["strategy_revision"]["const"])
        identity_pairs = {
            (
                option["properties"]["strategy_identifier"]["const"],
                option["properties"]["strategy_revision"]["const"],
            )
            for option in self.schema["allOf"][0]["oneOf"]
        }
        self.assertEqual(
            {
                ("docs-drift", "1"),
                ("agents-startup-routing", "1"),
                ("worktree-ignore-baseline", "1"),
            },
            identity_pairs,
        )
        required = set(self.schema["required"])
        self.assertEqual(
            {"string", "null"},
            set(self.schema["properties"]["requested_target_ref"]["type"]),
        )
        self.assertEqual(
            {"string", "null"},
            set(self.schema["properties"]["effective_target_ref"]["type"]),
        )
        self.assertNotIn("requested_target_ref", required)
        self.assertNotIn("effective_target_ref", required)
        self.assertTrue(
            {
                "eligibility",
                "strategy_result",
                "diff_digest",
                "validation",
                "collision",
                "would_create_pr",
                "remote_mutations_attempted",
                "remote_mutation_results",
                "final_terminal_state",
                "failure_stage",
                "bounded_error",
            }.issubset(required)
        )


if __name__ == "__main__":
    unittest.main()
