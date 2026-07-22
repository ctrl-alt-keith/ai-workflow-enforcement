from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from enforcement.stewardship.cli import build_parser, main
from enforcement.stewardship.docs_drift import DocsDriftStrategy
from enforcement.stewardship.engine import StewardshipEngine
from enforcement.stewardship.github import GitHubGateway
from enforcement.stewardship.github import _scrubbed_environment


ROOT = Path(__file__).resolve().parents[1]


class StewardshipCliTests(unittest.TestCase):
    def test_strategy_selection_has_exact_choices_and_docs_drift_default(self) -> None:
        parser = build_parser()
        strategy_action = next(
            action for action in parser._actions if action.dest == "strategy"
        )

        self.assertEqual(
            (
                "docs-drift",
                "agents-startup-routing",
                "worktree-ignore-baseline",
            ),
            strategy_action.choices,
        )
        self.assertEqual("docs-drift", strategy_action.default)

    def test_child_process_environment_omits_all_token_variables(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "GH_TOKEN": "gh-secret",
                "STEWARDSHIP_READ_TOKEN": "read-secret",
                "STEWARDSHIP_WRITE_TOKEN": "write-secret",
            },
            clear=False,
        ):
            environment = _scrubbed_environment()

        self.assertNotIn("GH_TOKEN", environment)
        self.assertNotIn("STEWARDSHIP_READ_TOKEN", environment)
        self.assertNotIn("STEWARDSHIP_WRITE_TOKEN", environment)

    def test_missing_read_identity_writes_blocked_receipt_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch.object(GitHubGateway, "hydrate") as hydrate,
                mock.patch.object(DocsDriftStrategy, "run") as strategy,
                mock.patch.object(StewardshipEngine, "_run_validation") as validation,
                mock.patch.object(GitHubGateway, "deliver") as delivery,
                redirect_stdout(io.StringIO()),
            ):
                exit_code = main(
                    [
                        "--repository",
                        "ctrl-alt-keith/ai-workflow-enforcement",
                        "--mode",
                        "dry-run",
                        "--run-identifier",
                        "missing-read-run",
                        "--engine-revision",
                        "engine-sha",
                        "--workspace",
                        str(root / "workspace"),
                        "--evidence-dir",
                        str(evidence),
                        "--config",
                        str(ROOT / "config" / "hosted-stewardship.json"),
                    ]
                )

            receipt_path = evidence / "receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(1, exit_code)
            self.assertTrue(receipt_path.is_file())
            self.assertEqual("blocked_before_strategy", receipt["final_terminal_state"])
            self.assertEqual("github_read_access", receipt["failure_stage"])
            self.assertEqual("blocked", receipt["eligibility"]["decision"])
            self.assertEqual("blocked", receipt["validation"]["status"])
            self.assertIsNone(receipt["requested_target_ref"])
            self.assertIsNone(receipt["effective_target_ref"])
            self.assertEqual([], receipt["changed_paths"])
            self.assertEqual([], receipt["remote_mutations_attempted"])
            self.assertEqual([], receipt["remote_mutation_results"])
            self.assertEqual("docs-drift", receipt["strategy_identifier"])
            self.assertEqual("1", receipt["strategy_revision"])
            hydrate.assert_not_called()
            strategy.assert_not_called()
            validation.assert_not_called()
            delivery.assert_not_called()

    def test_initialization_failure_still_writes_redacted_terminal_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            secret = "write-token-that-must-not-appear"
            config.write_text(secret, encoding="utf-8")
            evidence = root / "evidence"
            with mock.patch.dict(
                os.environ,
                {"STEWARDSHIP_WRITE_TOKEN": secret},
                clear=False,
            ):
                with redirect_stdout(io.StringIO()):
                    exit_code = main(
                        [
                            "--repository",
                            "ctrl-alt-keith/ai-workflow-enforcement",
                            "--mode",
                            "propose",
                            "--strategy",
                            "agents-startup-routing",
                            "--run-identifier",
                            "run-1",
                            "--engine-revision",
                            "engine-sha",
                            "--workspace",
                            str(root / "workspace"),
                            "--evidence-dir",
                            str(evidence),
                            "--config",
                            str(config),
                        ]
                    )

            receipt_text = (evidence / "receipt.json").read_text(encoding="utf-8")
            receipt = json.loads(receipt_text)
            self.assertEqual(1, exit_code)
            self.assertEqual("blocked_before_strategy", receipt["final_terminal_state"])
            self.assertEqual("engine_initialization", receipt["failure_stage"])
            self.assertEqual("agents-startup-routing", receipt["strategy_identifier"])
            self.assertEqual("1", receipt["strategy_revision"])
            self.assertNotIn(secret, receipt_text)

    def test_propose_target_ref_writes_blocked_receipt_before_hydration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence"
            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch.object(GitHubGateway, "repository_info") as repository_info,
                mock.patch.object(GitHubGateway, "hydrate") as hydrate,
                mock.patch.object(GitHubGateway, "deliver") as delivery,
                redirect_stdout(io.StringIO()),
            ):
                exit_code = main(
                    [
                        "--repository",
                        "ctrl-alt-keith/ai-workflow-enforcement",
                        "--mode",
                        "propose",
                        "--target-ref",
                        "test/controlled-drift",
                        "--run-identifier",
                        "target-ref-run",
                        "--engine-revision",
                        "engine-sha",
                        "--workspace",
                        str(root / "workspace"),
                        "--evidence-dir",
                        str(evidence),
                        "--config",
                        str(ROOT / "config" / "hosted-stewardship.json"),
                    ]
                )

            receipt = json.loads((evidence / "receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(1, exit_code)
            self.assertEqual("blocked_before_strategy", receipt["final_terminal_state"])
            self.assertEqual("target_ref_validation", receipt["failure_stage"])
            self.assertEqual("test/controlled-drift", receipt["requested_target_ref"])
            self.assertIsNone(receipt["effective_target_ref"])
            repository_info.assert_not_called()
            hydrate.assert_not_called()
            delivery.assert_not_called()


if __name__ == "__main__":
    unittest.main()
