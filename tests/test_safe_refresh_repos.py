from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from enforcement.safe_refresh_repos import (
    RepoTarget,
    SafeRefreshConfig,
    load_config,
    main,
    safe_refresh_repos,
)


class SafeRefreshReposTests(unittest.TestCase):
    def test_already_current_repo_reports_already_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))

            report = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo),)))

        result = report.repositories[0]
        self.assertEqual("already-current", result.status)
        self.assertEqual(result.before, result.after)
        self.assertEqual(["safe refresh complete"], result.details)

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
        self.assertIn(
            f"HEAD {local_head} does not match origin/main {remote_head}",
            result.details,
        )

    def test_dirty_worktree_blocks_without_fetching(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            (repo / "scratch.txt").write_text("untracked\n", encoding="utf-8")

            report = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo),)))

        result = report.repositories[0]
        self.assertEqual("blocked", result.status)
        self.assertEqual("", result.before)
        self.assertIn("working tree is not clean, including untracked files", result.details)

    def test_wrong_branch_blocks_before_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "switch", "-c", "topic")

            report = safe_refresh_repos(SafeRefreshConfig((RepoTarget("sample", repo),)))

        result = report.repositories[0]
        self.assertEqual("blocked", result.status)
        self.assertIn("current branch is 'topic', expected 'main'", result.details)

    def test_selected_repos_skip_unselected_inventory_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root, "one")
            config = SafeRefreshConfig(
                (
                    RepoTarget("one", repo),
                    RepoTarget("two", root / "missing"),
                )
            )

            report = safe_refresh_repos(config, selected_repos=("one",))

        statuses = {result.name: result.status for result in report.repositories}
        self.assertEqual({"one": "already-current", "two": "skipped"}, statuses)
        skipped = report.repositories[1]
        self.assertEqual(["not selected"], skipped.details)

    def test_loads_only_repository_inventory_from_branch_cleanup_compatible_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root, "repo")
            config_path = root / "branch-cleanup.json"
            config_path.write_text(
                """
{
  "repositories": [
    {
      "name": "sample",
      "path": "repo",
      "remote": "origin",
      "default_branch": "main"
    }
  ],
  "protected_branches": ["release"],
  "stale_approvals": [
    {
      "repo": "sample",
      "scope": "local",
      "branch": "old",
      "approved_by": "test",
      "reason": "ignored by safe refresh",
      "evidence": {"kind": "test"}
    }
  ]
}
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

        self.assertEqual(("sample",), tuple(repo.name for repo in config.repositories))
        self.assertEqual((root / "repo").resolve(), config.repositories[0].path)
        self.assertFalse(hasattr(config, "protected_branches"))
        self.assertFalse(hasattr(config, "stale_approvals"))

    def test_cli_json_returns_one_when_any_repo_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            (repo / "scratch.txt").write_text("untracked\n", encoding="utf-8")
            config_path = root / "branch-cleanup.json"
            config_path.write_text(
                json.dumps({"repositories": [{"name": "sample", "path": str(repo)}]}),
                encoding="utf-8",
            )

            code, stdout, stderr = _run_cli(
                "--config",
                str(config_path),
                "--output-format",
                "json",
            )

        self.assertEqual(1, code)
        self.assertEqual("", stderr)
        data = json.loads(stdout)
        self.assertEqual("safe_refresh_repos", data["report_type"])
        self.assertEqual(1, data["summary"]["blocked"])
        self.assertEqual("blocked", data["repositories"][0]["status"])

    def test_cli_repo_selection_reports_skipped_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            one = _make_repo(root, "one")
            two = _make_repo(root, "two")
            config_path = root / "branch-cleanup.json"
            config_path.write_text(
                json.dumps(
                    {
                        "repositories": [
                            {"name": "one", "path": str(one)},
                            {"name": "two", "path": str(two)},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            code, stdout, stderr = _run_cli(
                "--config",
                str(config_path),
                "--repo",
                "one",
                "--output-format",
                "json",
            )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        data = json.loads(stdout)
        self.assertEqual(1, data["summary"]["skipped"])
        self.assertEqual(["already-current", "skipped"], [repo["status"] for repo in data["repositories"]])


def _run_cli(*args: str) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(list(args))
    return code, stdout.getvalue(), stderr.getvalue()


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
