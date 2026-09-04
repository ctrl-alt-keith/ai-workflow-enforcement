from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from enforcement.repo_preflight import (
    COMMAND_TIMEOUT_SECONDS,
    CommandResult,
    _run as run_command,
    inspect_repository,
    _parse_remotes,
    render_json,
    render_markdown,
)


STAMP = "2026-07-15T20:00:00Z"


class RepoPreflightTests(unittest.TestCase):
    def test_default_runner_bounds_commands_and_reports_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            with mock.patch(
                "enforcement.repo_preflight.subprocess.run",
                side_effect=subprocess.TimeoutExpired(("git", "status"), COMMAND_TIMEOUT_SECONDS),
            ) as run:
                result = run_command(cwd, ("git", "status"))

        self.assertEqual(124, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertTrue(result.stderr)
        self.assertEqual(COMMAND_TIMEOUT_SECONDS, run.call_args.kwargs["timeout"])

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

    def test_remote_urls_strip_non_repository_credentials_and_parameters(self) -> None:
        remotes = _parse_remotes(
            "ordinary https://github.com/example/ordinary.git (fetch)\n"
            "credential https://username:token@github.com/example/credential.git (fetch)\n"
            "token https://token@github.com/example/token.git (push)\n"
            "query https://github.com/example/query.git?access_token=secret#credential (fetch)\n"
            "ssh git@github.com:example/ssh.git (fetch)\n"
        )

        self.assertEqual(
            [
                {"name": "credential", "url": "https://github.com/example/credential.git", "kind": "fetch"},
                {"name": "ordinary", "url": "https://github.com/example/ordinary.git", "kind": "fetch"},
                {"name": "query", "url": "https://github.com/example/query.git", "kind": "fetch"},
                {"name": "ssh", "url": "git@github.com:example/ssh.git", "kind": "fetch"},
                {"name": "token", "url": "https://github.com/example/token.git", "kind": "push"},
            ],
            remotes,
        )

        rendered = json.dumps(remotes)
        self.assertNotIn("access_token", rendered)
        self.assertNotIn("secret", rendered)
        self.assertNotIn("credential#", rendered)

    def test_hosted_identity_uses_sanitized_remote_and_renderers_do_not_leak_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = _repo(Path(raw), agents="# Agent\n", makefile="check:\n\ttrue\n")
            _git(repo, "remote", "add", "origin", "https://username:secret-token@github.com/example/private-repo.git")
            hosted_commands: list[tuple[str, ...]] = []

            def runner(cwd: Path, argv: tuple[str, ...]) -> CommandResult:
                if argv[:2] == ("gh", "api"):
                    hosted_commands.append(argv)
                    return CommandResult(0, json.dumps({"visibility": "private", "default_branch": "main", "archived": False}), "")
                return _run(cwd, argv)

            report = inspect_repository(repo, include_hosted=True, clock=lambda: STAMP, runner=runner)

        self.assertEqual([("gh", "api", "repos/example/private-repo")], hosted_commands)
        remotes = _source(report, "git_metadata").facts["remotes"]
        self.assertTrue(all(remote["url"] == "https://github.com/example/private-repo.git" for remote in remotes))
        for rendered in (render_json(report), render_markdown(report)):
            self.assertNotIn("username", rendered)
            self.assertNotIn("secret-token", rendered)
            self.assertIn("https://github.com/example/private-repo.git", rendered)

    def test_invalid_utf8_sources_are_unavailable_without_aborting_other_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = _repo(Path(raw), agents="# Agent\n", makefile="check:\n\ttrue\n")
            (repo / "AGENTS.md").write_bytes(b"# Agent\n\xff\n")
            (repo / "Makefile").write_bytes(b"check:\n\ttrue\n\xff\n")

            report = inspect_repository(repo, clock=lambda: STAMP)

        self.assertEqual("partial", report.overall_source_status)
        for source_name in ("repo_local_agents", "validation_tooling"):
            source = _source(report, source_name)
            self.assertEqual("unavailable", source.status)
            self.assertTrue(source.errors)
        git_source = _source(report, "git_metadata")
        self.assertEqual("partial", git_source.status)
        self.assertEqual("main", git_source.facts["current_branch"])

    def test_non_repository_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(ValueError):
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
        self.assertTrue(_source(report, "hosted_repository").errors)


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
