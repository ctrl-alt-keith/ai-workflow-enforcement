from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from enforcement import branch_cleanup
from enforcement.branch_cleanup import (
    BranchCleanupConfig,
    RepoTarget,
    StaleApproval,
    cleanup_branches,
    cleanup_branches_with_retries,
    load_config,
    remote_branch_name,
)


class BranchCleanupTests(unittest.TestCase):
    def test_protected_refs_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "branch", "release", "main")
            config = BranchCleanupConfig(
                repositories=(RepoTarget("sample", repo),),
                protected_branches=("main", "release"),
            )

            report = cleanup_branches(config)

        release = _action(report, "release", "local", "normal_cleanup")
        self.assertEqual("preserved", release.action)
        self.assertEqual("protected branch", release.reason)

    def test_configured_protected_refs_cannot_unprotect_builtin_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "branch", "master", "main")
            config = BranchCleanupConfig(
                repositories=(RepoTarget("sample", repo),),
                protected_branches=("release",),
            )

            report = cleanup_branches(config)

        master = _action(report, "master", "local", "normal_cleanup")
        self.assertIn("release", config.protected_branches)
        self.assertIn("master", config.protected_branches)
        self.assertEqual("preserved", master.action)
        self.assertEqual("protected branch", master.reason)

    def test_loaded_config_adds_protected_refs_to_builtin_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            _git(repo, "branch", "trunk", "main")
            config_path = root / "branch-cleanup.json"
            config_path.write_text(
                """
{
  "repositories": [{"name": "sample", "path": "repo"}],
  "protected_branches": ["release"]
}
""",
                encoding="utf-8",
            )

            config = load_config(config_path)
            report = cleanup_branches(config)

        trunk = _action(report, "trunk", "local", "normal_cleanup")
        self.assertIn("release", config.protected_branches)
        self.assertIn("trunk", config.protected_branches)
        self.assertEqual("preserved", trunk.action)
        self.assertEqual("protected branch", trunk.reason)

    def test_symbolic_refs_are_not_remote_delete_names(self) -> None:
        self.assertIsNone(remote_branch_name("refs/remotes/origin/HEAD"))
        self.assertIsNone(remote_branch_name("refs/remotes/upstream/topic"))
        self.assertEqual("feature/deep/name", remote_branch_name("refs/remotes/origin/feature/deep/name"))

    def test_merged_branch_with_clean_worktree_is_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")

            report = cleanup_branches(_config(repo), apply=True)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")
            linked_exists = linked.exists()

        action = _action(report, "done", "local", "normal_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertIn("worktree=", action.evidence[1])
        self.assertFalse(linked_exists)
        self.assertNotEqual(0, ref_check.returncode)

    def test_worktree_with_uncommitted_changes_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            (linked / "README.md").write_text("changed\n", encoding="utf-8")

            report = cleanup_branches(_config(repo), apply=True)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")
            linked_exists = linked.exists()

        action = _action(report, "done", "local", "normal_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertIn("worktree has uncommitted changes", action.reason)
        self.assertTrue(linked_exists)
        self.assertEqual(0, ref_check.returncode)

    def test_worktree_with_untracked_files_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            (linked / "scratch.txt").write_text("pending\n", encoding="utf-8")

            report = cleanup_branches(_config(repo), apply=True)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")
            linked_exists = linked.exists()

        action = _action(report, "done", "local", "normal_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertIn("worktree has untracked files", action.reason)
        self.assertTrue(linked_exists)
        self.assertEqual(0, ref_check.returncode)

    def test_missing_worktree_path_is_preserved_as_inspection_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            shutil.rmtree(linked)

            report = cleanup_branches(_config(repo), apply=True)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")

        action = _action(report, "done", "local", "normal_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertIn("could not inspect worktree state", action.reason)
        self.assertEqual(0, ref_check.returncode)

    def test_worktree_remove_revalidates_branch_is_still_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            real_worktree_branches = branch_cleanup._worktree_branches
            calls = 0

            def worktree_branches_with_late_commit(path: Path) -> dict[str, str]:
                nonlocal calls
                calls += 1
                if calls == 2:
                    (linked / "late.txt").write_text("late\n", encoding="utf-8")
                    _git(linked, "add", "late.txt")
                    _git(linked, "commit", "-m", "Late branch change")
                return real_worktree_branches(path)

            with mock.patch.object(
                branch_cleanup,
                "_worktree_branches",
                side_effect=worktree_branches_with_late_commit,
            ):
                report = cleanup_branches(_config(repo), apply=True)

            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")
            linked_exists = linked.exists()

        action = _action(report, "done", "local", "normal_cleanup")
        self.assertEqual("failed", action.action)
        self.assertIn("branch is no longer proven merged", action.reason)
        self.assertTrue(linked_exists)
        self.assertEqual(0, ref_check.returncode)

    def test_normal_merged_branch_cleanup_still_works_without_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_and_merge_branch(repo, "done")

            report = cleanup_branches(_config(repo), apply=True)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")

        action = _action(report, "done", "local", "normal_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertNotEqual(0, ref_check.returncode)

    def test_retry_normal_cleanup_deletes_remote_exposed_after_worktree_removal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "push", "origin", "done")
            _git(repo, "fetch", "origin")
            _git(repo, "worktree", "add", str(linked), "done")

            report = cleanup_branches_with_retries(_config(repo), apply=True, max_apply_passes=3)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")
            remote_check = _git(repo, "ls-remote", "--heads", "origin", "done")

        self.assertEqual("no normal_cleanup would_delete refs remain", report.stopped_reason)
        self.assertGreaterEqual(len(report.reports), 5)
        self.assertEqual("deleted", _action(report.reports[1], "done", "local", "normal_cleanup").action)
        self.assertEqual("deleted", _action(report.reports[3], "done", "remote", "normal_cleanup").action)
        self.assertNotEqual(0, ref_check.returncode)
        self.assertEqual("", remote_check.stdout.strip())

    def test_retry_normal_cleanup_preserves_approved_stale_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="PR merged and local branch is stale",
                evidence={
                    "kind": "github_merged_pr",
                    "pr_number": 456,
                    "state": "MERGED",
                    "merged_at": "2026-05-08T00:00:00Z",
                    "head_oid": oid,
                },
            )

            report = cleanup_branches_with_retries(
                BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                apply=True,
                max_apply_passes=2,
            )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")

        action = _action(report.reports[1], "stale", "local", "stale_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertIn("stale cleanup requires single-pass --apply", action.reason)
        self.assertEqual(0, ref_check.returncode)

    def test_ambiguous_ref_names_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "branch", "ambiguous", "main")
            _git(repo, "tag", "ambiguous", "main")

            report = cleanup_branches(_config(repo))

        action = _action(report, "ambiguous", "local", "normal_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertEqual("ambiguous ref name", action.reason)

    def test_ambiguous_remote_ref_names_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "branch", "remote-ambiguous", "main")
            _git(repo, "push", "origin", "remote-ambiguous")
            _git(repo, "fetch", "origin")
            _git(repo, "tag", "remote-ambiguous", "main")
            _git(repo, "push", "origin", "refs/tags/remote-ambiguous")
            _git(repo, "tag", "-d", "remote-ambiguous")

            report = cleanup_branches(_config(repo))

        action = _action(report, "remote-ambiguous", "remote", "normal_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertEqual("ambiguous remote ref name", action.reason)

    def test_dot_github_participates_in_normal_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp), name=".github")
            _commit_and_merge_branch(repo, "done")

            report = cleanup_branches(BranchCleanupConfig(repositories=(RepoTarget(".github", repo),)))

        action = _action(report, "done", "local", "normal_cleanup")
        self.assertEqual("", report.repos[0].skipped)
        self.assertEqual("would_delete", action.action)

    def test_dirty_repo_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            (repo / "untracked.txt").write_text("pending\n", encoding="utf-8")

            report = cleanup_branches(_config(repo))

        self.assertEqual("dirty working tree", report.repos[0].skipped)

    def test_github_pr_evidence_must_match_branch_tip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="merged elsewhere",
                evidence={
                    "kind": "github_merged_pr",
                    "pr_number": 123,
                    "state": "MERGED",
                    "merged_at": "2026-05-08T00:00:00Z",
                    "head_oid": "0" * 40,
                },
            )

            report = cleanup_branches(
                BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,))
            )

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertEqual("stale approval evidence is incomplete or mismatched", action.reason)
        self.assertIn("does not match", action.evidence[0])

    def test_non_ancestor_stale_refs_are_preserved_without_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")

            report = cleanup_branches(_config(repo))

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertEqual("non-ancestor ref requires explicit stale approval and evidence", action.reason)

    def test_valid_approval_gates_stale_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="PR merged and remote stale ref remains",
                evidence={
                    "kind": "github_merged_pr",
                    "pr_number": 456,
                    "state": "MERGED",
                    "merged_at": "2026-05-08T00:00:00Z",
                    "head_oid": oid,
                },
            )

            report = cleanup_branches(
                BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                apply=True,
            )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertNotEqual(0, ref_check.returncode)

    def test_valid_approval_gates_remote_stale_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "remote-stale", "remote-stale.txt", "unmerged\n")
            _git(repo, "push", "origin", "remote-stale")
            oid = _git(repo, "rev-parse", "refs/remotes/origin/remote-stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="remote",
                branch="remote-stale",
                approved_by="keith",
                reason="remote PR branch is stale after merge",
                evidence={
                    "kind": "github_merged_pr",
                    "pr_number": 789,
                    "state": "MERGED",
                    "merged_at": "2026-05-08T00:00:00Z",
                    "head_oid": oid,
                },
            )

            report = cleanup_branches(
                BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                apply=True,
            )
            remote_check = _git(repo, "ls-remote", "--heads", "origin", "remote-stale")

        action = _action(report, "remote-stale", "remote", "stale_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertEqual("", remote_check.stdout.strip())

    def test_patch_equivalent_evidence_gates_stale_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale-patch", "patch.txt", "same change\n")
            _git(repo, "switch", "main")
            (repo / "patch.txt").write_text("same change\n", encoding="utf-8")
            _git(repo, "add", "patch.txt")
            _git(repo, "commit", "-m", "Add equivalent patch")
            _git(repo, "push", "origin", "main")
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale-patch",
                approved_by="keith",
                reason="patch-equivalent stale branch",
                evidence={"kind": "patch_equivalent"},
            )

            report = cleanup_branches(
                BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                apply=True,
            )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale-patch")

        action = _action(report, "stale-patch", "local", "stale_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertNotEqual(0, ref_check.returncode)

    def test_stale_audit_reports_patch_equivalent_candidate_without_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale-patch", "patch.txt", "same change\n")
            _git(repo, "switch", "main")
            (repo / "patch.txt").write_text("same change\n", encoding="utf-8")
            _git(repo, "add", "patch.txt")
            _git(repo, "commit", "-m", "Add equivalent patch")
            _git(repo, "push", "origin", "main")

            report = cleanup_branches(_config(repo), audit_stale=True)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale-patch")

        action = _action(report, "stale-patch", "local", "stale_candidate_patch_equivalent")
        self.assertEqual("report_only", action.action)
        self.assertIn("git cherry", "\n".join(action.evidence))
        self.assertEqual(0, ref_check.returncode)

    def test_stale_audit_reports_dirty_worktree_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            _git(repo, "worktree", "add", str(linked), "stale")
            (linked / "scratch.txt").write_text("pending\n", encoding="utf-8")

            report = cleanup_branches(_config(repo), audit_stale=True)

        action = _action(report, "stale", "local", "blocked_dirty_worktree")
        self.assertEqual("report_only", action.action)
        self.assertIn("untracked", action.reason)

    def test_stale_audit_reports_merged_pr_exact_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()

            with mock.patch.object(
                branch_cleanup,
                "_gh",
                return_value=branch_cleanup.GitCommand(
                    ("gh",),
                    0,
                    f'[{{"number": 1, "state": "MERGED", "mergedAt": "2026-05-08T00:00:00Z", "headRefOid": "{oid}", "title": "Merged", "url": "https://example.test/pr/1"}}]',
                    "",
                ),
            ):
                report = cleanup_branches(_config(repo), audit_stale=True, audit_github_prs=True)

        action = _action(report, "stale", "local", "stale_candidate_merged_pr_exact_head")
        self.assertEqual("report_only", action.action)
        self.assertIn("head SHA matches", action.reason)

    def test_merged_pr_exact_head_without_approval_remains_report_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()

            with mock.patch.object(branch_cleanup, "_gh", return_value=_merged_pr_command(oid)):
                report = cleanup_branches(_config(repo), audit_stale=True, audit_github_prs=True)

        cleanup = _action(report, "stale", "local", "stale_cleanup")
        audit = _action(report, "stale", "local", "stale_candidate_merged_pr_exact_head")
        self.assertEqual("preserved", cleanup.action)
        self.assertEqual("non-ancestor ref requires explicit stale approval and evidence", cleanup.reason)
        self.assertEqual("report_only", audit.action)
        self.assertIn("head SHA matches", audit.reason)

    def test_merged_pr_exact_head_live_approval_is_would_delete_in_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            with mock.patch.object(branch_cleanup, "_gh", return_value=_merged_pr_command(oid)):
                report = cleanup_branches(
                    BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                    audit_stale=True,
                    audit_github_prs=True,
                )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("would_delete", action.action)
        self.assertIn("eligible because GitHub merged PR", action.reason)
        self.assertIn("GitHub PR #1 state=MERGED", "\n".join(action.evidence))
        self.assertEqual(0, ref_check.returncode)

    def test_merged_pr_exact_head_live_approval_deletes_in_apply_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            with mock.patch.object(branch_cleanup, "_gh", return_value=_merged_pr_command(oid)):
                report = cleanup_branches(
                    BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                    apply=True,
                    audit_stale=True,
                    audit_github_prs=True,
                )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertIn("eligible because GitHub merged PR", action.reason)
        self.assertNotEqual(0, ref_check.returncode)

    def test_approved_stale_local_branch_in_clean_linked_worktree_is_would_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            _git(repo, "worktree", "add", str(linked), "stale")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            with mock.patch.object(branch_cleanup, "_gh", return_value=_merged_pr_command(oid)):
                report = cleanup_branches(
                    BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                    audit_stale=True,
                    audit_github_prs=True,
                )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("would_delete", action.action)
        self.assertIn("eligible because GitHub merged PR", action.reason)
        self.assertIn("worktree=", "\n".join(action.evidence))
        self.assertIn("apply will remove linked worktree", "\n".join(action.evidence))
        self.assertEqual(0, ref_check.returncode)

    def test_approved_stale_local_branch_in_clean_linked_worktree_apply_removes_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            _git(repo, "worktree", "add", str(linked), "stale")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            with mock.patch.object(branch_cleanup, "_gh", return_value=_merged_pr_command(oid)):
                report = cleanup_branches(
                    BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                    apply=True,
                    audit_stale=True,
                    audit_github_prs=True,
                )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")
            linked_exists = linked.exists()

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertFalse(linked_exists)
        self.assertNotEqual(0, ref_check.returncode)

    def test_stale_worktree_apply_revalidates_branch_tip_before_removal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            _git(repo, "worktree", "add", str(linked), "stale")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )
            real_worktree_branches = branch_cleanup._worktree_branches
            calls = 0

            def worktree_branches_with_late_commit(path: Path) -> dict[str, str]:
                nonlocal calls
                calls += 1
                if calls == 2:
                    (linked / "late.txt").write_text("late\n", encoding="utf-8")
                    _git(linked, "add", "late.txt")
                    _git(linked, "commit", "-m", "Late stale branch change")
                return real_worktree_branches(path)

            with (
                mock.patch.object(branch_cleanup, "_gh", return_value=_merged_pr_command(oid)),
                mock.patch.object(
                    branch_cleanup,
                    "_worktree_branches",
                    side_effect=worktree_branches_with_late_commit,
                ),
            ):
                report = cleanup_branches(
                    BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                    apply=True,
                    audit_stale=True,
                    audit_github_prs=True,
                )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")
            linked_exists = linked.exists()

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("failed", action.action)
        self.assertIn("branch tip changed since stale approval planning", action.reason)
        self.assertTrue(linked_exists)
        self.assertEqual(0, ref_check.returncode)

    def test_live_exact_head_approval_requires_github_pr_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            report = cleanup_branches(
                BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                audit_stale=True,
                audit_github_prs=False,
            )

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertIn("stale approval evidence", action.reason)
        self.assertIn("requires --audit-github-prs", "\n".join(action.evidence))

    def test_stale_audit_preserves_closed_unmerged_pr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")

            with mock.patch.object(
                branch_cleanup,
                "_gh",
                return_value=branch_cleanup.GitCommand(
                    ("gh",),
                    0,
                    '[{"number": 2, "state": "CLOSED", "mergedAt": null, "headRefOid": "abc", "title": "Closed", "url": "https://example.test/pr/2"}]',
                    "",
                ),
            ):
                report = cleanup_branches(_config(repo), audit_stale=True, audit_github_prs=True)

        action = _action(report, "stale", "local", "closed_unmerged_preserve")
        self.assertEqual("report_only", action.action)
        self.assertIn("closed without merge", action.reason)

    def test_closed_unmerged_live_approval_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="reviewed stale branch",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            with mock.patch.object(
                branch_cleanup,
                "_gh",
                return_value=branch_cleanup.GitCommand(
                    ("gh",),
                    0,
                    '[{"number": 2, "state": "CLOSED", "mergedAt": null, "headRefOid": "abc", "title": "Closed", "url": "https://example.test/pr/2"}]',
                    "",
                ),
            ):
                report = cleanup_branches(
                    BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                    audit_stale=True,
                    audit_github_prs=True,
                )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")

        cleanup = _action(report, "stale", "local", "stale_cleanup")
        audit = _action(report, "stale", "local", "closed_unmerged_preserve")
        self.assertEqual("preserved", cleanup.action)
        self.assertIn("stale approval evidence", cleanup.reason)
        self.assertEqual("report_only", audit.action)
        self.assertEqual(0, ref_check.returncode)

    def test_dirty_worktree_stale_ref_is_preserved_even_with_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            _git(repo, "worktree", "add", str(linked), "stale")
            (linked / "scratch.txt").write_text("pending\n", encoding="utf-8")
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            report = cleanup_branches(
                BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                audit_stale=True,
                audit_github_prs=True,
            )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")

        cleanup = _action(report, "stale", "local", "stale_cleanup")
        blocker = _action(report, "stale", "local", "blocked_dirty_worktree")
        self.assertEqual("preserved", cleanup.action)
        self.assertIn("worktree has untracked files", cleanup.reason)
        self.assertEqual("report_only", blocker.action)
        self.assertEqual(0, ref_check.returncode)

    def test_remote_stale_ref_with_matching_clean_linked_worktree_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_branch(repo, "remote-stale", "remote-stale.txt", "unmerged\n")
            _git(repo, "push", "origin", "remote-stale")
            _git(repo, "fetch", "origin")
            _git(repo, "worktree", "add", str(linked), "remote-stale")
            oid = _git(repo, "rev-parse", "refs/remotes/origin/remote-stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="remote",
                branch="remote-stale",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            with mock.patch.object(branch_cleanup, "_gh", return_value=_merged_pr_command(oid)):
                report = cleanup_branches(
                    BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                    audit_stale=True,
                    audit_github_prs=True,
                )
            remote_check = _git(repo, "ls-remote", "--heads", "origin", "remote-stale")
            linked_exists = linked.exists()

        cleanup = _action(report, "remote-stale", "remote", "stale_cleanup")
        audit = _action(report, "remote-stale", "remote", "stale_candidate_merged_pr_exact_head")
        self.assertEqual("preserved", cleanup.action)
        self.assertIn("branch is checked out in worktree", cleanup.reason)
        self.assertEqual("report_only", audit.action)
        self.assertTrue(linked_exists)
        self.assertIn("remote-stale", remote_check.stdout)

    def test_protected_stale_ref_is_preserved_even_with_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "release", "release.txt", "unmerged\n")
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="release",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            report = cleanup_branches(
                BranchCleanupConfig(
                    repositories=(RepoTarget("sample", repo),),
                    protected_branches=("release",),
                    stale_approvals=(approval,),
                ),
                audit_stale=True,
                audit_github_prs=True,
            )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/release")

        action = _action(report, "release", "local", "stale_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertEqual("protected branch", action.reason)
        self.assertEqual(0, ref_check.returncode)

    def test_remote_branch_names_starting_with_dash_are_deleted_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            remote = root / "repo.git"
            oid = _git(repo, "rev-parse", "main").stdout.strip()
            _git(root, "--git-dir", str(remote), "update-ref", "refs/heads/--remote-stale", oid)
            _git(repo, "fetch", "origin")

            report = cleanup_branches(_config(repo), apply=True)
            remote_check = _git(repo, "ls-remote", "--heads", "origin", "refs/heads/--remote-stale")

        action = _action(report, "--remote-stale", "remote", "normal_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertEqual("", remote_check.stdout.strip())

    def test_branch_names_with_shell_metacharacters_are_direct_argv_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            branch = "feature/cleanup;touch"
            _git(repo, "branch", branch, "main")

            report = cleanup_branches(_config(repo), apply=True)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")

            self.assertFalse((repo / "touch").exists())

        action = _action(report, branch, "local", "normal_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertNotEqual(0, ref_check.returncode)

    def test_invalid_stale_approval_scope_fails_config_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root)
            config_path = root / "branch-cleanup.json"
            config_path.write_text(
                """
{
  "repositories": [{"name": "sample", "path": "repo"}],
  "stale_approvals": [
    {
      "repo": "sample",
      "scope": "global",
      "branch": "stale",
      "approved_by": "keith",
      "reason": "invalid",
      "evidence": {"kind": "patch_equivalent"}
    }
  ]
}
""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "scope must be 'local' or 'remote'"):
                load_config(config_path)

    def test_default_branch_fallback_reports_missing_remote_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")

            report = cleanup_branches(BranchCleanupConfig(repositories=(RepoTarget("sample", repo),)))

        self.assertEqual("main", report.repos[0].default_branch)
        self.assertEqual("remote HEAD missing for origin; fell back to main", report.repos[0].default_branch_evidence)


def _config(repo: Path) -> BranchCleanupConfig:
    return BranchCleanupConfig(repositories=(RepoTarget("sample", repo),))


def _action(report, branch: str, scope: str, phase: str):
    for repo in report.repos:
        for action in repo.actions:
            if action.branch == branch and action.scope == scope and action.phase == phase:
                return action
    raise AssertionError(f"missing action for {phase} {scope} {branch}")


def _make_repo(root: Path, name: str = "repo") -> Path:
    repo = root / name
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Tests")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Initial commit")
    _git(repo, "branch", "-M", "main")
    remote = root / f"{name}.git"
    _git(root, "init", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "main")
    _git(root, "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, "fetch", "origin")
    _git(repo, "remote", "set-head", "origin", "-a")
    return repo


def _commit_branch(repo: Path, branch: str, filename: str, content: str) -> None:
    _git(repo, "switch", "-c", branch)
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", f"Add {filename}")
    _git(repo, "switch", "main")


def _commit_and_merge_branch(repo: Path, branch: str) -> None:
    filename = f"{branch.replace('/', '-')}.txt"
    _commit_branch(repo, branch, filename, "merged\n")
    _git(repo, "merge", "--ff-only", branch)
    _git(repo, "push", "origin", "main")
    _git(repo, "fetch", "origin")


def _git(cwd: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ("git",) + tuple(argv),
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    if result.returncode != 0 and "--quiet" not in argv:
        raise AssertionError(
            f"git {' '.join(argv)} failed in {cwd}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def _merged_pr_command(oid: str) -> branch_cleanup.GitCommand:
    return branch_cleanup.GitCommand(
        ("gh",),
        0,
        (
            '[{"number": 1, "state": "MERGED", '
            '"mergedAt": "2026-05-08T00:00:00Z", '
            f'"headRefOid": "{oid}", '
            '"title": "Merged", "url": "https://example.test/pr/1"}]'
        ),
        "",
    )


if __name__ == "__main__":
    unittest.main()
