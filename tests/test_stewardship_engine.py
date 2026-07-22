from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from enforcement.stewardship.config import RepositoryPolicy, StewardshipConfig
from enforcement.stewardship.docs_drift import DocsDriftContext
from enforcement.stewardship.engine import StewardshipEngine
from enforcement.stewardship.models import (
    DeliveryProposal,
    DeliveryResult,
    RepositoryInfo,
    StrategyResult,
)


REPOSITORY = "ctrl-alt-keith/ai-workflow-enforcement"
POLICY_MARKER = "<!-- hosted-stewardship-policy:review-ready-human-merge -->"


def _run_git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class FakeGateway:
    def __init__(self, source: Path, base_sha: str) -> None:
        self.source = source
        self.base_sha = base_sha
        self.read_available = True
        self.write_available = True
        self.archived = False
        self.existing_pr: str | None = None
        self.branch_exists = False
        self.base_changed = False
        self.delivery_fails = False
        self.hydrate_calls = 0
        self.hydrated_shas: list[str] = []
        self.delivery_calls = 0
        self.existing_pr_calls = 0
        self.proposed_branch_reads = 0
        self.repository_info_calls = 0
        self.resolve_ref_calls: list[str] = []
        self.resolved_refs: dict[str, str] = {}
        self.delivered_proposal: DeliveryProposal | None = None
        self.delivered_patch: str | None = None
        self._base_reads = 0

    def repository_info(self, repository: str) -> RepositoryInfo:
        self.repository_info_calls += 1
        return RepositoryInfo(
            full_name=repository,
            default_branch="main",
            archived=self.archived,
        )

    def branch_sha(self, repository: str, branch: str) -> str | None:
        if branch == "main":
            self._base_reads += 1
            if self.base_changed and self._base_reads > 1:
                return "f" * 40
            return self.base_sha
        self.proposed_branch_reads += 1
        return "e" * 40 if self.branch_exists else None

    def resolve_ref(self, repository: str, target_ref: str) -> str | None:
        self.resolve_ref_calls.append(target_ref)
        return self.resolved_refs.get(target_ref)

    def hydrate(self, repository: str, base_sha: str, destination: Path) -> None:
        self.hydrate_calls += 1
        self.hydrated_shas.append(base_sha)
        subprocess.run(
            ("git", "clone", "--quiet", "--no-hardlinks", str(self.source), str(destination)),
            check=True,
            capture_output=True,
            text=True,
        )
        _run_git(destination, "checkout", "--detach", base_sha)

    def existing_stewardship_pr(self, repository: str) -> str | None:
        self.existing_pr_calls += 1
        return self.existing_pr

    def deliver(self, repository_root: Path, proposal: DeliveryProposal) -> DeliveryResult:
        self.delivery_calls += 1
        self.delivered_proposal = proposal
        if not self.write_available:
            return DeliveryResult(
                success=False,
                error="the repository-scoped stewardship write identity was unavailable",
                mutations=(),
            )
        self.delivered_patch = _run_git(
            repository_root, "diff", "--binary", "--full-index"
        )
        if self.delivery_fails:
            return DeliveryResult(
                success=False,
                error="simulated delivery failure",
                mutations=({"operation": "push_branch", "success": False},),
            )
        return DeliveryResult(
            success=True,
            branch=proposal.branch,
            commit_sha="d" * 40,
            pr_url="https://github.com/ctrl-alt-keith/ai-workflow-enforcement/pull/999",
            mutations=(
                {"operation": "push_branch", "success": True},
                {"operation": "create_pull_request", "success": True},
            ),
        )


class RaisingStrategy:
    def __init__(self, message: str = "simulated strategy failure") -> None:
        self.message = message
        self.calls = 0

    def run(self, context: DocsDriftContext) -> StrategyResult:
        self.calls += 1
        raise RuntimeError(self.message)


class SpyStrategy:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, context: DocsDriftContext) -> StrategyResult:
        self.calls += 1
        return StrategyResult(outcome="no_change", summary="not used")


class StewardshipEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        _run_git(self.source, "init", "-b", "main")
        _run_git(self.source, "config", "user.name", "Test")
        _run_git(self.source, "config", "user.email", "test@example.com")
        _run_git(self.source, "config", "commit.gpgsign", "false")
        (self.source / "docs").mkdir()
        (self.source / "AGENTS.md").write_text(
            "# Instructions\n\n"
            "- Use `make check` as the canonical local validation entrypoint.\n",
            encoding="utf-8",
        )
        (self.source / "docs" / "product-boundary.md").write_text(
            f"# Product boundary\n\n{POLICY_MARKER}\n",
            encoding="utf-8",
        )
        (self.source / "README.md").write_text("# Example\n", encoding="utf-8")
        self._set_validation(passes=True)
        _run_git(self.source, "add", ".")
        _run_git(self.source, "commit", "-m", "fixture")
        self.base_sha = _run_git(self.source, "rev-parse", "HEAD")
        self.config = StewardshipConfig(
            repositories={
                REPOSITORY: RepositoryPolicy(
                    repository=REPOSITORY,
                    policy_path="docs/product-boundary.md",
                    required_policy_marker=POLICY_MARKER,
                    documentation_path="README.md",
                )
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _set_validation(self, *, passes: bool) -> None:
        command = "@true" if passes else "@false"
        (self.source / "Makefile").write_text(
            f"check:\n\t{command}\n",
            encoding="utf-8",
        )

    def _commit_fixture_change(self, message: str) -> None:
        _run_git(self.source, "add", ".")
        _run_git(self.source, "commit", "-m", message)
        self.base_sha = _run_git(self.source, "rev-parse", "HEAD")

    def _run(
        self,
        *,
        mode: str = "dry-run",
        gateway: FakeGateway | None = None,
        strategy=None,
        repository: str = REPOSITORY,
        target_ref: str = "",
        redactions: tuple[str, ...] = (),
    ):
        gateway = gateway or FakeGateway(self.source, self.base_sha)
        engine = StewardshipEngine(
            config=self.config,
            gateway=gateway,
            strategy=strategy,
            clock=lambda: datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
            redactions=redactions,
        )
        evidence = self.root / f"evidence-{mode}-{gateway.hydrate_calls}"
        workspace = self.root / f"workspace-{mode}-{gateway.hydrate_calls}"
        receipt = engine.run(
            repository=repository,
            mode=mode,
            target_ref=target_ref,
            run_identifier="run-1",
            engine_revision="engine-sha",
            workspace_root=workspace,
            evidence_dir=evidence,
        )
        return receipt, gateway, evidence

    def _target_commit(self) -> str:
        _run_git(self.source, "switch", "-c", "test/controlled-drift")
        (self.source / "README.md").write_text("# Controlled target\n", encoding="utf-8")
        _run_git(self.source, "add", "README.md")
        _run_git(self.source, "commit", "-m", "controlled target")
        target_sha = _run_git(self.source, "rev-parse", "HEAD")
        _run_git(self.source, "switch", "main")
        return target_sha

    def test_blank_target_ref_preserves_default_branch_resolution(self) -> None:
        receipt, gateway, _ = self._run()

        self.assertIsNone(receipt.requested_target_ref)
        self.assertEqual("main", receipt.effective_target_ref)
        self.assertEqual(self.base_sha, receipt.base_sha)
        self.assertEqual([self.base_sha], gateway.hydrated_shas)
        self.assertEqual([], gateway.resolve_ref_calls)

    def test_branch_target_ref_resolves_and_hydrates_exact_sha_in_dry_run(self) -> None:
        target_sha = self._target_commit()
        gateway = FakeGateway(self.source, self.base_sha)
        gateway.resolved_refs["test/controlled-drift"] = target_sha

        receipt, gateway, _ = self._run(
            gateway=gateway,
            target_ref="test/controlled-drift",
        )

        self.assertEqual("dry_run_complete", receipt.final_terminal_state)
        self.assertEqual("test/controlled-drift", receipt.requested_target_ref)
        self.assertEqual("test/controlled-drift", receipt.effective_target_ref)
        self.assertEqual(target_sha, receipt.base_sha)
        self.assertEqual(["test/controlled-drift"], gateway.resolve_ref_calls)
        self.assertEqual([target_sha], gateway.hydrated_shas)
        self.assertFalse(receipt.would_create_pr)
        self.assertEqual(0, gateway.existing_pr_calls)
        self.assertEqual(0, gateway.delivery_calls)

    def test_commit_sha_target_ref_is_supported_in_dry_run(self) -> None:
        target_sha = self._target_commit()
        gateway = FakeGateway(self.source, self.base_sha)
        gateway.resolved_refs[target_sha] = target_sha

        receipt, gateway, _ = self._run(gateway=gateway, target_ref=target_sha)

        self.assertEqual("dry_run_complete", receipt.final_terminal_state)
        self.assertEqual(target_sha, receipt.effective_target_ref)
        self.assertEqual(target_sha, receipt.base_sha)
        self.assertEqual([target_sha], gateway.hydrated_shas)

    def test_invalid_target_ref_fails_closed_with_receipt_before_hydration(self) -> None:
        gateway = FakeGateway(self.source, self.base_sha)

        receipt, gateway, _ = self._run(
            gateway=gateway,
            target_ref="missing/ref",
        )

        self.assertEqual("blocked_before_strategy", receipt.final_terminal_state)
        self.assertEqual("target_resolution", receipt.failure_stage)
        self.assertEqual("missing/ref", receipt.requested_target_ref)
        self.assertEqual("missing/ref", receipt.effective_target_ref)
        self.assertIsNone(receipt.base_sha)
        self.assertIn("did not resolve", receipt.bounded_error)
        self.assertEqual(0, gateway.hydrate_calls)
        self.assertEqual(0, gateway.delivery_calls)

    def test_propose_rejects_target_ref_before_repository_reads_or_delivery(self) -> None:
        gateway = FakeGateway(self.source, self.base_sha)

        receipt, gateway, _ = self._run(
            mode="propose",
            gateway=gateway,
            target_ref="test/controlled-drift",
        )

        self.assertEqual("blocked_before_strategy", receipt.final_terminal_state)
        self.assertEqual("target_ref_validation", receipt.failure_stage)
        self.assertEqual("test/controlled-drift", receipt.requested_target_ref)
        self.assertIsNone(receipt.effective_target_ref)
        self.assertEqual(0, gateway.repository_info_calls)
        self.assertEqual([], gateway.resolve_ref_calls)
        self.assertEqual(0, gateway.hydrate_calls)
        self.assertEqual(0, gateway.delivery_calls)

    def test_dry_run_builds_real_validated_patch_and_would_create_pr(self) -> None:
        receipt, gateway, evidence = self._run()

        self.assertEqual("dry_run_complete", receipt.final_terminal_state)
        self.assertEqual("changed", receipt.strategy_result["outcome"])
        self.assertEqual(["README.md"], receipt.changed_paths)
        self.assertEqual("passed", receipt.validation["status"])
        self.assertTrue(receipt.would_create_pr)
        patch = (evidence / "proposal.patch").read_text(encoding="utf-8")
        self.assertIn("## Validation", patch)
        self.assertEqual(hashlib.sha256(patch.encode()).hexdigest(), receipt.diff_digest)
        self.assertEqual([], receipt.remote_mutations_attempted)
        self.assertEqual(0, gateway.delivery_calls)

    def test_dry_run_never_invokes_remote_delivery(self) -> None:
        receipt, gateway, _ = self._run(mode="dry-run")

        self.assertTrue(receipt.would_create_pr)
        self.assertEqual(0, gateway.delivery_calls)
        self.assertEqual([], receipt.remote_mutation_results)

    def test_propose_uses_same_pipeline_and_delivers_exact_validated_patch(self) -> None:
        dry_receipt, _, dry_evidence = self._run(mode="dry-run")
        propose_gateway = FakeGateway(self.source, self.base_sha)
        propose_receipt, propose_gateway, propose_evidence = self._run(
            mode="propose", gateway=propose_gateway
        )

        self.assertEqual("delivery_succeeded", propose_receipt.final_terminal_state)
        self.assertIsNone(propose_receipt.requested_target_ref)
        self.assertEqual("main", propose_receipt.effective_target_ref)
        self.assertEqual(dry_receipt.diff_digest, propose_receipt.diff_digest)
        self.assertEqual(
            (dry_evidence / "proposal.patch").read_text(encoding="utf-8"),
            (propose_evidence / "proposal.patch").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            propose_gateway.delivered_proposal.patch,
            propose_gateway.delivered_patch + "\n" if not propose_gateway.delivered_patch.endswith("\n") else propose_gateway.delivered_patch,
        )
        self.assertEqual(
            ["push_branch", "create_pull_request"],
            propose_receipt.remote_mutations_attempted,
        )

    def test_ineligible_repository_stops_before_strategy(self) -> None:
        gateway = FakeGateway(self.source, self.base_sha)
        strategy = SpyStrategy()
        receipt, gateway, _ = self._run(
            gateway=gateway,
            strategy=strategy,
            repository="ctrl-alt-keith/not-allowed",
        )

        self.assertEqual("blocked_ineligible", receipt.final_terminal_state)
        self.assertEqual("ineligible", receipt.eligibility["decision"])
        self.assertEqual(0, gateway.hydrate_calls)
        self.assertEqual(0, strategy.calls)

    def test_no_justified_docs_change_is_no_change(self) -> None:
        (self.source / "README.md").write_text(
            "# Example\n\nRun `make check`.\n", encoding="utf-8"
        )
        self._commit_fixture_change("document validation")
        gateway = FakeGateway(self.source, self.base_sha)

        receipt, gateway, evidence = self._run(gateway=gateway)

        self.assertEqual("eligible_no_change", receipt.final_terminal_state)
        self.assertEqual("no_change", receipt.strategy_result["outcome"])
        self.assertFalse(receipt.would_create_pr)
        self.assertFalse((evidence / "proposal.patch").exists())
        self.assertEqual(0, gateway.delivery_calls)

    def test_validation_failure_prevents_delivery(self) -> None:
        self._set_validation(passes=False)
        self._commit_fixture_change("make validation fail")
        gateway = FakeGateway(self.source, self.base_sha)

        receipt, gateway, evidence = self._run(mode="propose", gateway=gateway)

        self.assertEqual("validation_failed", receipt.final_terminal_state)
        self.assertEqual("failed", receipt.validation["status"])
        self.assertNotEqual(0, receipt.validation["exit_code"])
        self.assertTrue((evidence / "validation.log").exists())
        self.assertEqual(0, gateway.delivery_calls)

    def test_strategy_failure_has_distinct_receipt(self) -> None:
        strategy = RaisingStrategy()
        receipt, gateway, _ = self._run(mode="propose", strategy=strategy)

        self.assertEqual("strategy_failed", receipt.final_terminal_state)
        self.assertEqual("failed", receipt.strategy_result["outcome"])
        self.assertEqual("strategy", receipt.failure_stage)
        self.assertEqual(0, gateway.delivery_calls)

    def test_existing_stewardship_pr_prevents_duplicate(self) -> None:
        gateway = FakeGateway(self.source, self.base_sha)
        gateway.existing_pr = "https://github.com/example/pull/1"

        receipt, gateway, _ = self._run(mode="propose", gateway=gateway)

        self.assertEqual("skipped_existing_pr", receipt.final_terminal_state)
        self.assertEqual("existing_stewardship_pr", receipt.collision["decision"])
        self.assertFalse(receipt.would_create_pr)
        self.assertEqual(0, gateway.delivery_calls)

    def test_base_sha_change_blocks_delivery(self) -> None:
        gateway = FakeGateway(self.source, self.base_sha)
        gateway.base_changed = True

        receipt, gateway, _ = self._run(mode="propose", gateway=gateway)

        self.assertEqual("blocked_base_sha_changed", receipt.final_terminal_state)
        self.assertEqual("base_sha_changed", receipt.collision["decision"])
        self.assertEqual(0, gateway.delivery_calls)

    def test_existing_branch_is_never_overwritten(self) -> None:
        gateway = FakeGateway(self.source, self.base_sha)
        gateway.branch_exists = True

        receipt, gateway, _ = self._run(mode="propose", gateway=gateway)

        self.assertEqual("blocked_branch_exists", receipt.final_terminal_state)
        self.assertEqual("proposed_branch_exists", receipt.collision["decision"])
        self.assertEqual(0, gateway.delivery_calls)

    def test_delivery_failure_has_distinct_terminal_receipt(self) -> None:
        gateway = FakeGateway(self.source, self.base_sha)
        gateway.delivery_fails = True

        receipt, gateway, _ = self._run(mode="propose", gateway=gateway)

        self.assertEqual("delivery_failed", receipt.final_terminal_state)
        self.assertEqual("remote_delivery", receipt.failure_stage)
        self.assertEqual(["push_branch"], receipt.remote_mutations_attempted)
        self.assertIn("simulated delivery failure", receipt.bounded_error)

    def test_missing_write_identity_fails_only_at_remote_delivery_boundary(self) -> None:
        gateway = FakeGateway(self.source, self.base_sha)
        gateway.write_available = False

        receipt, gateway, evidence = self._run(mode="propose", gateway=gateway)

        self.assertEqual("eligible", receipt.eligibility["decision"])
        self.assertEqual(1, gateway.hydrate_calls)
        self.assertEqual("changed", receipt.strategy_result["outcome"])
        self.assertEqual(["README.md"], receipt.changed_paths)
        self.assertIsNotNone(receipt.diff_digest)
        self.assertEqual("proposal.patch", receipt.patch_artifact)
        self.assertTrue((evidence / "proposal.patch").is_file())
        self.assertEqual("passed", receipt.validation["status"])
        self.assertTrue((evidence / "validation.log").is_file())
        self.assertEqual(1, gateway.existing_pr_calls)
        self.assertEqual(1, gateway.proposed_branch_reads)
        self.assertEqual(2, gateway._base_reads)
        self.assertEqual("clear", receipt.collision["decision"])
        self.assertTrue(receipt.would_create_pr)
        self.assertIsNotNone(receipt.proposed_branch)
        self.assertIsNotNone(receipt.proposed_commit_message)
        self.assertIsNotNone(receipt.proposed_pr_title)
        self.assertIsNotNone(receipt.proposed_pr_body)
        self.assertEqual(1, gateway.delivery_calls)
        self.assertIsNotNone(gateway.delivered_proposal)
        self.assertEqual([], receipt.remote_mutations_attempted)
        self.assertEqual([], receipt.remote_mutation_results)
        self.assertEqual("delivery_failed", receipt.final_terminal_state)
        self.assertEqual("remote_delivery", receipt.failure_stage)
        self.assertEqual(
            "the repository-scoped stewardship write identity was unavailable",
            receipt.bounded_error,
        )

    def test_secrets_are_redacted_from_receipt_errors(self) -> None:
        secret = "super-secret-private-key-material"
        receipt, _, _ = self._run(
            strategy=RaisingStrategy(f"failure exposed {secret}"),
            redactions=(secret,),
        )

        serialized = json.dumps(receipt.to_dict(), sort_keys=True)
        self.assertNotIn(secret, serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_credentials_do_not_reach_validation_logs(self) -> None:
        secret = "runtime-token-that-validation-must-not-see"
        (self.source / "Makefile").write_text(
            "check:\n"
            "\t@printf '%s' \"$$STEWARDSHIP_WRITE_TOKEN\"\n",
            encoding="utf-8",
        )
        self._commit_fixture_change("attempt to print credential")
        gateway = FakeGateway(self.source, self.base_sha)
        with mock.patch.dict(
            os.environ,
            {"STEWARDSHIP_WRITE_TOKEN": secret},
            clear=False,
        ):
            receipt, _, evidence = self._run(
                gateway=gateway,
                redactions=(secret,),
            )

        validation_log = (evidence / "validation.log").read_text(encoding="utf-8")
        self.assertEqual("dry_run_complete", receipt.final_terminal_state)
        self.assertNotIn(secret, validation_log)


if __name__ == "__main__":
    unittest.main()
