from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from enforcement.branch_cleanup import (
    BranchCleanupConfig,
    RepoTarget,
    StaleApproval,
    cleanup_branches,
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

    def test_symbolic_refs_are_not_remote_delete_names(self) -> None:
        self.assertIsNone(remote_branch_name("refs/remotes/origin/HEAD"))
        self.assertIsNone(remote_branch_name("refs/remotes/upstream/topic"))
        self.assertEqual("feature/deep/name", remote_branch_name("refs/remotes/origin/feature/deep/name"))

    def test_worktree_checked_out_branch_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            _git(repo, "branch", "done", "main")
            _git(repo, "worktree", "add", str(root / "linked"), "done")

            report = cleanup_branches(_config(repo))

        action = _action(report, "done", "local", "normal_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertIn("checked out in worktree", action.reason)

    def test_ambiguous_ref_names_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "branch", "ambiguous", "main")
            _git(repo, "tag", "ambiguous", "main")

            report = cleanup_branches(_config(repo))

        action = _action(report, "ambiguous", "local", "normal_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertEqual("ambiguous ref name", action.reason)

    def test_dot_github_is_conservative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp), name=".github")

            report = cleanup_branches(BranchCleanupConfig(repositories=(RepoTarget(".github", repo),)))

        self.assertEqual("conservative repository requires explicit human handling", report.repos[0].skipped)

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


if __name__ == "__main__":
    unittest.main()
