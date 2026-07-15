from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
