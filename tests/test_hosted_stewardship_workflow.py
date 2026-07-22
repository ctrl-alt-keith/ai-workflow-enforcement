from __future__ import annotations

import json
from pathlib import Path
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
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertNotIn("schedule:", self.workflow)
        self.assertIn("- ctrl-alt-keith/ai-workflow-enforcement", self.workflow)
        self.assertIn("target_ref:", self.workflow)
        self.assertIn("Optional branch, tag, or commit to inspect in dry-run only", self.workflow)
        self.assertIn('default: ""', self.workflow)
        self.assertEqual(2, self.workflow.count("          - dry-run") + self.workflow.count("          - propose"))
        self.assertIn("group: hosted-stewardship-${{ inputs.repository }}", self.workflow)
        self.assertIn("cancel-in-progress: false", self.workflow)

    def test_strategy_input_has_exact_two_choices_and_docs_drift_default(self) -> None:
        strategy_input = self.workflow.split("      strategy:", 1)[1].split(
            "      target_ref:", 1
        )[0]
        self.assertIn("default: docs-drift", strategy_input)
        self.assertEqual(1, strategy_input.count("          - docs-drift"))
        self.assertEqual(
            1, strategy_input.count("          - agents-startup-routing")
        )

    def test_read_and_write_identities_are_distinct_and_narrow(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertIn("WORKFLOW_DRIFT_APP_CLIENT_ID", self.workflow)
        self.assertIn("WORKFLOW_DRIFT_APP_PRIVATE_KEY", self.workflow)
        self.assertIn("STEWARDSHIP_WRITE_APP_CLIENT_ID", self.workflow)
        self.assertIn("STEWARDSHIP_WRITE_APP_PRIVATE_KEY", self.workflow)
        self.assertIn("inputs.mode == 'propose'", self.workflow)
        self.assertIn("inputs.target_ref == ''", self.workflow)
        self.assertEqual(2, self.workflow.count("repositories: ${{ inputs.repository }}"))
        self.assertIn("permission-contents: read", self.workflow)
        self.assertIn("permission-contents: write", self.workflow)
        self.assertIn("permission-pull-requests: write", self.workflow)
        self.assertNotIn("permission-administration", self.workflow)

    def test_modes_share_one_cli_pipeline_and_only_propose_receives_write_token(self) -> None:
        self.assertEqual(1, self.workflow.count("python3 -m enforcement.stewardship.cli"))
        self.assertIn('--mode "${{ inputs.mode }}"', self.workflow)
        self.assertIn('--strategy "${{ inputs.strategy }}"', self.workflow)
        self.assertIn('--target-ref "${TARGET_REF}"', self.workflow)
        execute_step = self.workflow.split(
            "- name: Execute the shared stewardship pipeline", 1
        )[1].split("- name: Upload durable stewardship evidence", 1)[0]
        self.assertIn("STEWARDSHIP_READ_TOKEN", execute_step)
        self.assertIn("STEWARDSHIP_WRITE_TOKEN", execute_step)
        self.assertIn("TARGET_REF: ${{ inputs.target_ref }}", execute_step)
        for name in ("docs_drift.py", "agents_startup_routing.py"):
            strategy = (ROOT / "enforcement" / "stewardship" / name).read_text(
                encoding="utf-8"
            )
            self.assertNotIn("dry-run", strategy)
            self.assertNotIn("propose", strategy)

    def test_exact_engine_identity_evidence_and_no_forbidden_delivery(self) -> None:
        self.assertIn("ref: ${{ github.sha }}", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)
        self.assertIn('--engine-revision "${GITHUB_SHA}"', self.workflow)
        self.assertIn("actions/upload-artifact@v7", self.workflow)
        self.assertIn("retention-days: 14", self.workflow)
        for forbidden in (
            "gh pr merge",
            "--auto",
            "--force",
            "force-with-lease",
            "permission-administration",
            "permission-organization",
        ):
            self.assertNotIn(forbidden, self.workflow)

        delivery_source = (
            ROOT / "enforcement" / "stewardship" / "github.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            '"--force"',
            "force-with-lease",
            "/merge",
            "enable-auto-merge",
            "update_ref",
        ):
            self.assertNotIn(forbidden, delivery_source)
        self.assertIn('"--set-upstream"', delivery_source)

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
            ["docs-drift", "agents-startup-routing"],
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
            {("docs-drift", "1"), ("agents-startup-routing", "1")},
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
