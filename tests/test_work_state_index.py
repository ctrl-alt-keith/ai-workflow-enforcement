from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from enforcement import branch_cleanup, org_pr_issue_scan
from enforcement.branch_cleanup import BranchCleanupReport, RepoReport
from enforcement.org_pr_issue_scan import OrgWorkReport, RepositoryWork
from enforcement.work_state_index import (
    ADVISORY_NOTICE,
    CommandResult,
    compose_work_state_index,
    render_json_report,
    render_markdown_report,
)


STAMP = "2026-07-15T20:00:00Z"


class WorkStateIndexTests(unittest.TestCase):
    def test_all_sources_available_and_repository_view(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), ("beta", "alpha"))
            index = compose_work_state_index(
                org="example",
                selected_repos=("alpha",),
                branch_config_path=config,
                clock=lambda: STAMP,
                org_scanner=_org_report,
                branch_scanner=_branch_report,
                worktree_runner=lambda cwd, argv: CommandResult(0, f"worktree {cwd}\nHEAD abc", ""),
            )
            selected_config = branch_cleanup.BranchCleanupConfig(
                (branch_cleanup.RepoTarget("alpha", Path(raw) / "alpha"),)
            )
            expected_cleanup = _branch_report(selected_config)

        self.assertEqual("repository", index.view)
        self.assertEqual(("alpha",), index.repositories)
        self.assertEqual(
            ("branch_cleanup_dry_run", "local_git_worktrees", "organization_pr_issue_scan"),
            tuple(source.name for source in index.sources),
        )
        self.assertTrue(all(source.status == "available" for source in index.sources))
        self.assertTrue(all(source.captured_at == STAMP for source in index.sources))
        self.assertTrue(all(source.freshness == "fresh_at_capture" for source in index.sources))
        self.assertTrue(all(source.stale_after_capture for source in index.sources))
        worktrees = next(source for source in index.sources if source.name == "local_git_worktrees")
        self.assertEqual(["alpha"], [item["name"] for item in worktrees.payload["repositories"]])
        org = next(source for source in index.sources if source.name == "organization_pr_issue_scan")
        cleanup = next(source for source in index.sources if source.name == "branch_cleanup_dry_run")
        expected_org = _org_report()
        self.assertEqual(org_pr_issue_scan.report_to_dict(expected_org), org.payload)
        self.assertEqual(json.loads(org_pr_issue_scan.render_json_report(expected_org)), org.payload)
        self.assertEqual(branch_cleanup.report_to_dict(expected_cleanup), cleanup.payload)
        self.assertEqual(json.loads(branch_cleanup.render_json_report(expected_cleanup)), cleanup.payload)

    def test_one_source_unavailable_does_not_invent_payload(self) -> None:
        index = compose_work_state_index(clock=lambda: STAMP, org_scanner=_org_report)
        branch = next(source for source in index.sources if source.name == "branch_cleanup_dry_run")
        worktrees = next(source for source in index.sources if source.name == "local_git_worktrees")
        self.assertEqual("unavailable", branch.status)
        self.assertIsNone(branch.captured_at)
        self.assertIsNone(branch.payload)
        self.assertFalse(branch.stale_after_capture)
        self.assertEqual("unavailable", worktrees.status)
        self.assertIsNone(worktrees.payload)

    def test_one_source_failure_preserves_other_results(self) -> None:
        def fail_org(*args: object, **kwargs: object) -> OrgWorkReport:
            raise RuntimeError("GitHub unavailable")

        index = compose_work_state_index(clock=lambda: STAMP, org_scanner=fail_org)
        org = next(source for source in index.sources if source.name == "organization_pr_issue_scan")
        self.assertEqual("failed", org.status)
        self.assertEqual(("RuntimeError: GitHub unavailable",), org.errors)
        self.assertIsNone(org.payload)
        self.assertEqual("unavailable", index.sources[0].status)

    def test_no_local_worktree_context_is_unavailable_not_empty(self) -> None:
        index = compose_work_state_index(clock=lambda: STAMP, org_scanner=_org_report)
        source = next(source for source in index.sources if source.name == "local_git_worktrees")
        self.assertEqual("unavailable", source.status)
        self.assertIn("no local repository context", source.errors[0])
        self.assertIsNone(source.payload)

    def test_worktree_partial_failure_preserves_error_and_observed_facts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw), ("beta", "alpha"))

            def run(cwd: Path, argv: tuple[str, ...]) -> CommandResult:
                if cwd.name == "beta":
                    return CommandResult(128, "", "not a git repository")
                return CommandResult(0, f"worktree {cwd}\nHEAD abc", "")

            index = compose_work_state_index(
                branch_config_path=config,
                clock=lambda: STAMP,
                org_scanner=_org_report,
                branch_scanner=_branch_report,
                worktree_runner=run,
            )

        source = next(source for source in index.sources if source.name == "local_git_worktrees")
        self.assertEqual("partial", source.status)
        self.assertEqual(("beta: not a git repository",), source.errors)
        self.assertEqual(["alpha", "beta"], [item["name"] for item in source.payload["repositories"]])
        self.assertEqual("unavailable", source.payload["repositories"][1]["status"])

    def test_selected_complete_provider_view_preserves_reconciliation_provenance(self) -> None:
        config = _provider_config(completeness="complete", mutation_ready=True)
        index, selected = _compose_selected(config, ("alpha",))

        scope = selected.scope_reconciliation
        self.assertEqual("github_organization", scope.mode)
        self.assertEqual("example", scope.organization)
        self.assertEqual("complete", scope.completeness)
        self.assertEqual(("example/alpha",), scope.candidates)
        self.assertEqual(("alpha",), tuple(item.name for item in scope.resolved_targets))
        self.assertEqual(("example/archived",), scope.archived_members)
        self.assertEqual("example/excluded", scope.exclusions[0].current_repository)
        self.assertEqual(("example/old-override",), scope.unmatched_overrides)
        cleanup = next(source for source in index.sources if source.name == "branch_cleanup_dry_run")
        self.assertEqual("github_organization", cleanup.payload["scope"]["mode"])

    def test_selected_unknown_provider_view_remains_unknown(self) -> None:
        config = _provider_config(
            completeness="unknown",
            mutation_ready=False,
            errors=("credential breadth unproven",),
            blockers=("complete provider-backed candidate scope could not be established",),
        )
        index, selected = _compose_selected(config, ("alpha",))

        scope = selected.scope_reconciliation
        self.assertEqual("github_organization", scope.mode)
        self.assertEqual("unknown", scope.completeness)
        self.assertEqual(("credential breadth unproven",), scope.errors)
        self.assertFalse(scope.mutation_ready)
        self.assertEqual(
            ("complete provider-backed candidate scope could not be established",),
            scope.mutation_blockers,
        )
        cleanup = next(source for source in index.sources if source.name == "branch_cleanup_dry_run")
        self.assertEqual("partial", cleanup.status)

    def test_selected_repository_missing_from_partial_evidence_stays_provider_unknown(self) -> None:
        config = _provider_config(
            completeness="unknown",
            mutation_ready=False,
            errors=("partial enumeration",),
            blockers=("partial enumeration",),
        )
        _index, selected = _compose_selected(config, ("missing",))

        scope = selected.scope_reconciliation
        self.assertEqual("github_organization", scope.mode)
        self.assertEqual("unknown", scope.completeness)
        self.assertEqual((), selected.repositories)
        self.assertIn("selected repositories were absent from partial provider evidence: missing", scope.errors)
        self.assertFalse(scope.mutation_ready)

    def test_selected_legacy_view_remains_legacy_only_for_legacy_source(self) -> None:
        legacy = branch_cleanup.resolve_branch_cleanup_scope(
            branch_cleanup.BranchCleanupConfig(
                (
                    branch_cleanup.RepoTarget("alpha", Path("/workspace/alpha")),
                    branch_cleanup.RepoTarget("beta", Path("/workspace/beta")),
                )
            )
        )
        _index, selected = _compose_selected(legacy, ("alpha",))

        self.assertEqual("legacy_explicit_compatibility", selected.scope_reconciliation.mode)
        self.assertEqual(("alpha",), tuple(target.name for target in selected.repositories))

    def test_markdown_and_json_rendering_are_deterministic(self) -> None:
        first = compose_work_state_index(clock=lambda: STAMP, org_scanner=_org_report)
        second = compose_work_state_index(clock=lambda: STAMP, org_scanner=_org_report)
        self.assertEqual(render_json_report(first), render_json_report(second))
        self.assertEqual(render_markdown_report(first), render_markdown_report(second))

        data = json.loads(render_json_report(first))
        self.assertTrue(data["advisory"])
        self.assertEqual(ADVISORY_NOTICE, data["notice"])
        markdown = render_markdown_report(first)
        self.assertIn("# Work-State Advisory Index", markdown)
        self.assertIn(ADVISORY_NOTICE, markdown)
        self.assertIn("Capture time", markdown)
        self.assertIn("Stale after capture", markdown)

    @staticmethod
    def _config(root: Path, names: tuple[str, ...]) -> Path:
        path = root / "branch-cleanup.json"
        path.write_text(
            json.dumps({"repositories": [{"name": name, "path": str(root / name)} for name in names]}),
            encoding="utf-8",
        )
        return path


def _org_report(*args: object, **kwargs: object) -> OrgWorkReport:
    return OrgWorkReport(1, "org_pr_issue_scan", "org-pr-issue-scan", "Org scan", "example", (), STAMP, STAMP, (RepositoryWork("alpha", "example/alpha", ""),), ())


def _branch_report(*args: object, **kwargs: object) -> BranchCleanupReport:
    config = args[0]
    return BranchCleanupReport(1, True, STAMP, STAMP, tuple(RepoReport(target.name, str(target.path)) for target in config.repositories))


def _provider_config(
    *,
    completeness: str,
    mutation_ready: bool,
    errors: tuple[str, ...] = (),
    blockers: tuple[str, ...] = (),
) -> branch_cleanup.BranchCleanupConfig:
    targets = (
        branch_cleanup.RepoTarget(
            "alpha", Path("/workspace/alpha"), expected_repository="example/alpha", expected_repository_id=1
        ),
        branch_cleanup.RepoTarget(
            "beta", Path("/workspace/beta"), expected_repository="example/beta", expected_repository_id=2
        ),
    )
    resolved = tuple(
        branch_cleanup.ScopeTarget(
            target.expected_repository_id,
            target.expected_repository,
            target.name,
            False,
            str(target.path),
            target.remote,
            "main",
        )
        for target in targets
    )
    scope = branch_cleanup.ScopeReconciliation(
        mode="github_organization",
        organization="example",
        completeness=completeness,
        detail="provider evidence",
        candidates=("example/alpha", "example/beta"),
        archived_members=("example/archived",),
        exclusions=(
            branch_cleanup.ScopeExclusion(
                3,
                "example/excluded",
                "example/excluded",
                "policy",
                "CAK-999",
                "active_excluded",
                False,
                "stable ID matched",
            ),
        ),
        resolved_targets=resolved,
        unmatched_overrides=("example/old-override",),
        errors=errors,
        credential_kind="oauth_scope_bearing",
        credential_access="all_repositories" if completeness == "complete" else "unknown",
        credential_actor="operator",
        credential_scopes=("read:org", "repo"),
        mutation_ready=mutation_ready,
        mutation_blockers=blockers,
    )
    organization_scope = branch_cleanup.GitHubOrganizationScope("example", Path("/workspace"))
    return branch_cleanup.BranchCleanupConfig(
        targets,
        organization_scope=organization_scope,
        scope_reconciliation=scope,
    )


def _compose_selected(
    config: branch_cleanup.BranchCleanupConfig,
    selected_repos: tuple[str, ...],
) -> tuple[object, branch_cleanup.BranchCleanupConfig]:
    captured: list[branch_cleanup.BranchCleanupConfig] = []

    def scanner(selected: branch_cleanup.BranchCleanupConfig, **_kwargs: object) -> BranchCleanupReport:
        captured.append(selected)
        return BranchCleanupReport(
            5,
            True,
            STAMP,
            STAMP,
            tuple(RepoReport(target.name, str(target.path)) for target in selected.repositories),
            scope=selected.scope_reconciliation,
        )

    with (
        mock.patch.object(branch_cleanup, "load_config", return_value=config),
        mock.patch.object(branch_cleanup, "resolve_branch_cleanup_scope", return_value=config),
    ):
        index = compose_work_state_index(
            selected_repos=selected_repos,
            branch_config_path=Path("/not-read.json"),
            clock=lambda: STAMP,
            org_scanner=_org_report,
            branch_scanner=scanner,
            worktree_runner=lambda _cwd, _argv: CommandResult(0, "", ""),
        )
    return index, captured[0]


if __name__ == "__main__":
    unittest.main()
