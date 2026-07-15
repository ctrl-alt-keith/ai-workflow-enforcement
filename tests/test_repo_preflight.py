from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
import io

from enforcement.repo_preflight import (
    CommandResult,
    NOTICE,
    inspect_repository,
    main,
    render_json,
    render_markdown,
)


STAMP = "2026-07-15T20:00:00Z"


class RepoPreflightTests(unittest.TestCase):
    def test_documentation_repository_reports_direct_sources(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = _repo(Path(raw), agents="# Instructions\n\n## Validation\n", makefile="check: ## Validate docs\n\ttrue\n")
            report = inspect_repository(repo, clock=lambda: STAMP)

        self.assertEqual("repository_preflight", report.report_type)
        self.assertEqual("partial", report.overall_source_status)
        agents = _source(report, "repo_local_agents")
        tooling = _source(report, "validation_tooling")
        self.assertEqual(["Instructions", "Validation"], agents.facts["headings"])
        self.assertEqual([{"name": "check", "command": "make check", "line": 1}], tooling.facts["targets"])

    def test_code_repository_reports_branch_remotes_and_dirty_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = _repo(Path(raw), agents="# Agent\n", makefile="test:\n\tpython3 -m unittest\n")
            _git(repo, "remote", "add", "origin", "git@github.com:example/code.git")
            (repo / "app.py").write_text("print('dirty')\n", encoding="utf-8")
            report = inspect_repository(repo, clock=lambda: STAMP)

        facts = _source(report, "git_metadata").facts
        self.assertEqual("main", facts["current_branch"])
        self.assertEqual("dirty", facts["working_tree"])
        self.assertEqual("unknown", facts["default_branch"])
        self.assertEqual("origin", facts["remotes"][0]["name"])

    def test_sanitized_private_shape_reports_only_explicit_hosted_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = _repo(Path(raw), agents="# Private repo guidance\n", makefile="check:\n\ttrue\n")
            _git(repo, "remote", "add", "origin", "https://github.com/example/private-repo.git")

            def runner(cwd: Path, argv: tuple[str, ...]) -> CommandResult:
                if argv[:2] == ("gh", "api"):
                    return CommandResult(0, json.dumps({"visibility": "private", "default_branch": "main", "archived": False, "secret": "ignored"}), "")
                return _run(cwd, argv)

            report = inspect_repository(repo, include_hosted=True, clock=lambda: STAMP, runner=runner)

        hosted = _source(report, "hosted_repository")
        self.assertEqual("available", hosted.status)
        self.assertEqual({"repository": "example/private-repo", "visibility": "private", "default_branch": "main", "archived": False}, hosted.facts)

    def test_missing_agents_and_makefile_are_explicitly_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = inspect_repository(_repo(Path(raw)), clock=lambda: STAMP)

        self.assertEqual("unavailable", _source(report, "repo_local_agents").status)
        tooling = _source(report, "validation_tooling")
        self.assertEqual("unavailable", tooling.status)
        self.assertIn("not inferred", tooling.errors[0])

    def test_non_repository_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ValueError, "not a Git repository"):
                inspect_repository(Path(raw), clock=lambda: STAMP)

    def test_partial_hosted_failure_preserves_local_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = _repo(Path(raw), agents="# Agent\n", makefile="check:\n\ttrue\n")
            _git(repo, "remote", "add", "origin", "git@github.com:example/code.git")

            def runner(cwd: Path, argv: tuple[str, ...]) -> CommandResult:
                if argv[:2] == ("gh", "api"):
                    return CommandResult(1, "", "API unavailable")
                return _run(cwd, argv)

            report = inspect_repository(repo, include_hosted=True, clock=lambda: STAMP, runner=runner)

        self.assertEqual("partial", report.overall_source_status)
        self.assertEqual("available", _source(report, "repo_local_agents").status)
        self.assertEqual("unavailable", _source(report, "hosted_repository").status)
        self.assertIn("API unavailable", _source(report, "hosted_repository").errors)

    def test_markdown_and_json_are_deterministic_and_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = _repo(Path(raw), agents="# Agent\n", makefile="check:\n\ttrue\n")
            first = inspect_repository(repo, clock=lambda: STAMP)
            second = inspect_repository(repo, clock=lambda: STAMP)

        self.assertEqual(render_json(first), render_json(second))
        self.assertEqual(render_markdown(first), render_markdown(second))
        payload = json.loads(render_json(first))
        self.assertTrue(payload["advisory"])
        self.assertEqual(NOTICE, payload["notice"])
        self.assertIn("stale after capture", render_markdown(first))
        self.assertTrue(all(source["captured_at"] == STAMP for source in payload["sources"]))

    def test_cli_returns_clear_error_for_non_repository(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            errors = io.StringIO()
            with redirect_stderr(errors):
                self.assertEqual(2, main([raw, "--output-format", "json"]))
            self.assertIn("not a Git repository", errors.getvalue())


def _source(report, name: str):
    return next(source for source in report.sources if source.name == name)


def _repo(root: Path, *, agents: str | None = None, makefile: str | None = None) -> Path:
    repo = root / "sample"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    if agents is not None:
        (repo / "AGENTS.md").write_text(agents, encoding="utf-8")
    if makefile is not None:
        (repo / "Makefile").write_text(makefile, encoding="utf-8")
    return repo


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(("git", *args), cwd=repo, check=False, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)


def _run(repo: Path, argv: tuple[str, ...]) -> CommandResult:
    result = subprocess.run(argv, cwd=repo, check=False, capture_output=True, text=True)
    return CommandResult(result.returncode, result.stdout, result.stderr)


if __name__ == "__main__":
    unittest.main()
