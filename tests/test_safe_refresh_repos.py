from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from enforcement import branch_cleanup, safe_refresh_repos as safe_refresh_module
from enforcement.github_org_repositories import OrganizationRepository, OrganizationRepositoryEnumeration

from enforcement.safe_refresh_repos import (
    RepoTarget,
    SafeRefreshConfig,
    load_config,
    safe_refresh_repos,
)


class SafeRefreshReposTests(unittest.TestCase):
    def test_repo_behind_origin_fast_forwards_and_reports_refreshed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            before = _rev_parse(repo, "HEAD")
            _push_remote_commit(root, repo, "remote.txt", "remote change\n")

            report = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo),)))
            after = _rev_parse(repo, "HEAD")

        result = report.repositories[0]
        self.assertEqual("refreshed", result.status)
        self.assertEqual(before, result.before)
        self.assertEqual(after, result.after)
        self.assertNotEqual(before, after)

    def test_unpushed_local_commit_blocks_without_changing_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            remote_head = _rev_parse(repo, "origin/main")
            (repo / "local.txt").write_text("local work\n", encoding="utf-8")
            _git(repo, "add", "local.txt")
            _git(repo, "commit", "-m", "Local work")
            local_head = _rev_parse(repo, "HEAD")

            report = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo),)))
            after = _rev_parse(repo, "HEAD")

        result = report.repositories[0]
        self.assertNotEqual(remote_head, local_head)
        self.assertEqual("blocked", result.status)
        self.assertEqual(local_head, result.before)
        self.assertEqual(local_head, result.after)
        self.assertEqual(local_head, after)
        self.assertTrue(result.details)

    def test_dirty_worktree_blocks_without_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            (repo / "scratch.txt").write_text("untracked\n", encoding="utf-8")

            report = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo),)))

        result = report.repositories[0]
        self.assertEqual("blocked", result.status)
        self.assertEqual("", result.before)
        self.assertTrue(result.details)

    def test_wrong_branch_blocks_before_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "switch", "-c", "topic")

            report = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo),)))

        result = report.repositories[0]
        self.assertEqual("blocked", result.status)
        self.assertTrue(result.details)

    def test_provider_identity_mismatch_blocks_before_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            target = RepoTarget(
                "sample",
                repo,
                expected_repository="ctrl-alt-keith/sample",
                expected_repository_id=7,
            )
            identity = branch_cleanup.RepositoryIdentityVerification(
                status="mismatch",
                detail="configured remote identifies 'someone-else/sample'",
                expected_repository="ctrl-alt-keith/sample",
                expected_repository_id=7,
                remote="origin",
                observed_repository="someone-else/sample",
            )
            with (
                mock.patch.object(
                    safe_refresh_module,
                    "verify_local_repository_identity",
                    return_value=identity,
                ),
                mock.patch.object(
                    safe_refresh_module,
                    "_git",
                    wraps=safe_refresh_module._git,
                ) as git,
            ):
                report = safe_refresh_repos(SafeRefreshConfig((target,)))

        result = report.repositories[0]
        self.assertEqual("blocked", result.status)
        self.assertEqual("mismatch", result.identity.status)
        self.assertFalse(any(call.args[1][0] == "fetch" for call in git.call_args_list))

    def test_loads_complete_provider_backed_branch_cleanup_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "branch-cleanup.json"
            config_path.write_text(
                json.dumps(
                    {
                        "scope": {
                            "provider": "github_organization",
                            "organization": "ctrl-alt-keith",
                            "workspace_root": str(root),
                            "exclusions": [],
                            "repository_overrides": {},
                        }
                    }
                ),
                encoding="utf-8",
            )
            enumeration = OrganizationRepositoryEnumeration(
                organization="ctrl-alt-keith",
                repositories=(
                    OrganizationRepository(1, "sample", "ctrl-alt-keith/sample", False, True, "main"),
                ),
                complete=True,
                detail="complete",
            )
            with mock.patch.object(
                branch_cleanup,
                "enumerate_organization_repositories",
                return_value=enumeration,
            ):
                config = load_config(config_path)

        self.assertEqual(("sample",), tuple(repo.name for repo in config.repositories))
        self.assertEqual(root / "sample", config.repositories[0].path)

    def test_config_rejects_duplicate_object_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "branch-cleanup.json"
            config_path.write_text(
                '{"repositories":[{"name":"visible","name":"hidden","path":"repo"}]}',
                encoding="utf-8",
            )

            with self.assertRaises(json.JSONDecodeError):
                load_config(config_path)


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
    return repo


def _push_remote_commit(root: Path, repo: Path, filename: str, content: str) -> None:
    writer = root / "writer"
    _git(root, "clone", str(repo.parent / f"{repo.name}.git"), str(writer))
    _git(writer, "config", "user.email", "tests@example.com")
    _git(writer, "config", "user.name", "Tests")
    _git(writer, "config", "commit.gpgsign", "false")
    (writer / filename).write_text(content, encoding="utf-8")
    _git(writer, "add", filename)
    _git(writer, "commit", "-m", f"Add {filename}")
    _git(writer, "push", "origin", "main")


def _rev_parse(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", ref).stdout.strip()


def _git(cwd: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ("git",) + tuple(argv),
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(argv)} failed in {cwd}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


if __name__ == "__main__":
    unittest.main()
