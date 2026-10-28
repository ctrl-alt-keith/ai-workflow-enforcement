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
    load_startup_catalog,
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

    def test_inventory_mode_still_fetches_other_branches_and_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            _push_remote_commit(root, repo, "remote.txt", "new main\n")
            writer = root / "writer"
            _git(writer, "tag", "-a", "v1", "-m", "version 1")
            _git(writer, "push", "origin", "v1")
            _git(writer, "switch", "-c", "side")
            (writer / "side.txt").write_text("side branch\n", encoding="utf-8")
            _git(writer, "add", "side.txt")
            _git(writer, "commit", "-m", "Side branch")
            _git(writer, "push", "origin", "side")

            result = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo),))).repositories[0]

            self.assertEqual("refreshed", result.status)
            self.assertEqual(_rev_parse(repo, "FETCH_HEAD"), _rev_parse(repo, "HEAD"))
            self.assertEqual(0, _git(repo, "show-ref", "--verify", "--quiet", "refs/remotes/origin/side").returncode)
            self.assertEqual(0, _git(repo, "show-ref", "--verify", "--quiet", "refs/tags/v1").returncode)

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
        self.assertEqual("", result.after)
        self.assertEqual(local_head, after)
        self.assertIn("local commits ahead", result.details[0])

    def test_diverged_remote_blocks_after_fetch_without_changing_local_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            (repo / "local.txt").write_text("local work\n", encoding="utf-8")
            _git(repo, "add", "local.txt")
            _git(repo, "commit", "-m", "Local work")
            local_head = _rev_parse(repo, "HEAD")
            _push_remote_commit(root, repo, "remote.txt", "remote work\n")

            report = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo),)))

            self.assertEqual(local_head, _rev_parse(repo, "HEAD"))
            self.assertNotEqual(local_head, _rev_parse(repo, "origin/main"))
            self.assertEqual("", _git(repo, "status", "--porcelain").stdout)

        result = report.repositories[0]
        self.assertEqual("blocked", result.status)
        self.assertEqual(local_head, result.before)
        self.assertEqual("", result.after)
        self.assertIn("diverged from main", result.details[0])

    def test_clean_task_work_branch_fast_forwards_to_fetched_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            _git(repo, "switch", "-c", "work")
            before = _rev_parse(repo, "HEAD")
            _push_remote_commit(root, repo, "remote.txt", "remote change\n")

            result = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo, checkout_branch="work"),))).repositories[0]

            self.assertEqual("refreshed", result.status)
            self.assertEqual(before, result.before)
            self.assertEqual(_rev_parse(repo, "origin/main"), _rev_parse(repo, "HEAD"))
            self.assertEqual("work", _git(repo, "branch", "--show-current").stdout.strip())

    def test_fetch_head_drives_refresh_when_origin_main_tracking_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            _git(repo, "switch", "-c", "work")
            stale_main = _rev_parse(repo, "origin/main")
            _push_remote_commit(root, repo, "remote.txt", "new main\n")
            writer = root / "writer"
            _git(writer, "switch", "-c", "other")
            _git(writer, "push", "origin", "other")
            _git(repo, "config", "remote.origin.fetch", "+refs/heads/other:refs/remotes/origin/other")

            result = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo, checkout_branch="work"),))).repositories[0]

            self.assertEqual("refreshed", result.status)
            self.assertEqual(stale_main, _rev_parse(repo, "origin/main"))
            self.assertNotEqual(stale_main, _rev_parse(repo, "HEAD"))
            self.assertEqual(_rev_parse(repo, "FETCH_HEAD"), _rev_parse(repo, "HEAD"))

    def test_task_work_branch_preserves_dirty_ahead_diverged_and_detached(self) -> None:
        for state in ("dirty", "ahead", "diverged", "detached"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo = _make_repo(root)
                _git(repo, "switch", "-c", "work")
                if state == "dirty":
                    (repo / "scratch.txt").write_text("private work\n", encoding="utf-8")
                elif state in {"ahead", "diverged"}:
                    (repo / "local.txt").write_text("local work\n", encoding="utf-8")
                    _git(repo, "add", "local.txt")
                    _git(repo, "commit", "-m", "Local work")
                if state == "diverged":
                    _push_remote_commit(root, repo, "remote.txt", "remote work\n")
                if state == "detached":
                    _git(repo, "switch", "--detach")
                before = _rev_parse(repo, "HEAD")

                result = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo, checkout_branch="work"),))).repositories[0]

                self.assertEqual("blocked", result.status)
                self.assertEqual(before, _rev_parse(repo, "HEAD"))
                self.assertIn(state if state != "dirty" else "not clean", " ".join(result.details))

    def test_task_work_branch_with_upstream_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "switch", "-c", "work")
            _git(repo, "branch", "--set-upstream-to=origin/main", "work")
            result = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo, checkout_branch="work"),))).repositories[0]
        self.assertEqual("blocked", result.status)
        self.assertIn("expected none", result.details[0])

    def test_startup_catalog_maps_selected_pins_without_provider_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / "repositories.json"
            catalog.write_text(json.dumps([{
                "name": "ctrl-alt-keith/sample", "host": "github.com",
                "mount_path": "sample", "commit": "a" * 40,
            }]), encoding="utf-8")
            config = load_startup_catalog(catalog, root)
            self.assertEqual("work", config.repositories[0].checkout_branch)
            self.assertEqual(root / "sample", config.repositories[0].path)
            self.assertTrue(config.repositories[0].catalog_locator_only)
            self.assertEqual("a" * 40, config.repositories[0].catalog_commit)
            catalog.write_text(json.dumps([{
                "name": "ctrl-alt-keith/sample", "host": "github.com",
                "mount_path": "../escape", "commit": "a" * 40,
            }]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_startup_catalog(catalog, root)

    def test_catalog_locator_mismatch_blocks_before_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "switch", "-c", "work")
            target = RepoTarget("ctrl-alt-keith/sample", repo, expected_repository="ctrl-alt-keith/sample",
                                checkout_branch="work", catalog_locator_only=True)
            with mock.patch.object(safe_refresh_module, "_git", wraps=safe_refresh_module._git) as git:
                result = safe_refresh_repos(SafeRefreshConfig((target,))).repositories[0]
            self.assertEqual("blocked", result.status)
            self.assertIn("catalog checkout remote does not match", result.details[0])
            self.assertFalse(any(call.args[1][0] == "fetch" for call in git.call_args_list))

    def test_catalog_locator_verification_accepts_plain_url_and_rejects_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            target = RepoTarget("ctrl-alt-keith/sample", repo, expected_repository="ctrl-alt-keith/sample",
                                checkout_branch="work", catalog_locator_only=True)
            _git(repo, "remote", "set-url", "origin", "https://github.com/ctrl-alt-keith/sample.git")
            self.assertIsNone(safe_refresh_module._verify_catalog_locator(target))
            _git(repo, "remote", "set-url", "origin", "ssh://git@github.com/CTRL-ALT-KEITH/Sample.git")
            self.assertIsNone(safe_refresh_module._verify_catalog_locator(target))
            _git(repo, "remote", "set-url", "origin", "git@github.com:ctrl-alt-keith/sample.git")
            self.assertIsNone(safe_refresh_module._verify_catalog_locator(target))
            _git(repo, "remote", "set-url", "origin", "https://token@github.com/ctrl-alt-keith/sample.git")
            self.assertIn("embedded credentials", safe_refresh_module._verify_catalog_locator(target))
            _git(repo, "remote", "set-url", "origin", "ssh://other@github.com/ctrl-alt-keith/sample.git")
            self.assertIn("embedded credentials", safe_refresh_module._verify_catalog_locator(target))
            _git(repo, "remote", "set-url", "origin", "https://github.example.com/ctrl-alt-keith/sample.git")
            self.assertIn("does not match", safe_refresh_module._verify_catalog_locator(target))

    def test_catalog_commit_mismatch_blocks_before_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "switch", "-c", "work")
            _git(repo, "remote", "set-url", "origin", "https://github.com/ctrl-alt-keith/sample.git")
            target = RepoTarget("ctrl-alt-keith/sample", repo, expected_repository="ctrl-alt-keith/sample",
                                checkout_branch="work", catalog_locator_only=True, catalog_commit="b" * 40)
            with mock.patch.object(safe_refresh_module, "_git", wraps=safe_refresh_module._git) as git:
                result = safe_refresh_repos(SafeRefreshConfig((target,))).repositories[0]
            self.assertEqual("blocked", result.status)
            self.assertIn("does not contain", result.details[0])
            self.assertFalse(any(call.args[1][0] == "fetch" for call in git.call_args_list))

    def test_dirty_worktree_blocks_without_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            (repo / "scratch.txt").write_text("untracked\n", encoding="utf-8")

            report = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo),)))

        result = report.repositories[0]
        self.assertEqual("blocked", result.status)
        self.assertEqual("", result.before)
        self.assertTrue(result.details)

    def test_fetch_failure_blocks_without_pulling_or_changing_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            before = _rev_parse(repo, "HEAD")
            _git(repo, "remote", "set-url", "origin", str(Path(tmp) / "missing.git"))

            with mock.patch.object(
                safe_refresh_module,
                "_git",
                wraps=safe_refresh_module._git,
            ) as git:
                report = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo),)))

            self.assertEqual(before, _rev_parse(repo, "HEAD"))
            self.assertEqual("", _git(repo, "status", "--porcelain").stdout)

        result = report.repositories[0]
        self.assertEqual("blocked", result.status)
        self.assertEqual(before, result.before)
        self.assertEqual("", result.after)
        self.assertIn("git fetch origin failed", result.details[0])
        self.assertFalse(any(call.args[1][0] == "pull" for call in git.call_args_list))

    def test_wrong_branch_blocks_before_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "switch", "-c", "topic")

            report = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo),)))

        result = report.repositories[0]
        self.assertEqual("blocked", result.status)
        self.assertTrue(result.details)

    def test_nested_checkout_path_blocks_before_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            nested = repo / "nested"
            nested.mkdir()
            with mock.patch.object(
                safe_refresh_module,
                "_git",
                wraps=safe_refresh_module._git,
            ) as git:
                report = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", nested),)))

        result = report.repositories[0]
        self.assertEqual("blocked", result.status)
        self.assertIn("not the Git worktree top level", result.details[0])
        self.assertFalse(any(call.args[1][0] == "fetch" for call in git.call_args_list))

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

    def test_incomplete_repository_identity_blocks_before_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            for target in (
                RepoTarget("sample", repo, expected_repository="ctrl-alt-keith/sample"),
                RepoTarget("sample", repo, expected_repository_id=7),
            ):
                with (
                    self.subTest(target=target),
                    mock.patch.object(
                        safe_refresh_module,
                        "_git",
                        wraps=safe_refresh_module._git,
                    ) as git,
                ):
                    result = safe_refresh_repos(SafeRefreshConfig((target,))).repositories[0]

                self.assertEqual("blocked", result.status)
                self.assertIn("requires both", result.details[0])
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
