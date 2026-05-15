from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from enforcement.repo_settings_audit import (
    GhCommand,
    audit_repo_settings,
    render_json_report,
    render_text_report,
)


class RepoSettingsAuditTests(unittest.TestCase):
    def test_uses_explicit_remote_ref_as_governance_source(self) -> None:
        gh = FakeGh(_responses(source_ref="reviewed-ref"))

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="reviewed-ref",
            repo_root=Path("/does/not/exist"),
            runner=gh,
        )

        self.assertEqual("reviewed-ref", report.source_ref)
        self.assertEqual("remote-sha", report.source_sha)
        self.assertIn(("gh", "api", "/repos/ctrl-alt-keith/sample/commits/reviewed-ref"), gh.commands)
        self.assertTrue(
            all(
                item.source == "GitHub reviewed-ref (remote-sha)"
                for item in report.items
                if item.setting not in {
                    "local current branch vs source-of-truth ref",
                    "local governance docs vs source-of-truth ref",
                }
            )
        )

    def test_reports_local_governance_docs_that_differ_from_remote_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            _init_repo(repo)
            _write(repo / "AGENTS.md", "Use pull requests. Target `main`.\n")
            _write(repo / "docs" / "governance-ci.md", "Required status checks: `old matrix`.\n")
            _git(repo, "add", ".")
            _git(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "--no-gpg-sign",
                "-m",
                "initial",
            )
            _write(repo / "docs" / "governance-ci.md", "Required status checks: `working tree edit`.\n")

            report = audit_repo_settings(
                "ctrl-alt-keith/sample",
                source_ref="main",
                repo_root=repo,
                runner=FakeGh(_responses(required_check="remote check")),
            )

        item = _item(report, "local governance docs vs source-of-truth ref")

        self.assertEqual("drift", item.status)
        self.assertIn("local current branch docs differ:", item.actual)
        self.assertIn("local working-tree docs differ:", item.actual)
        self.assertIn("docs/governance-ci.md", item.actual)
        self.assertIn("Do not validate hosted settings against stale local docs", item.follow_up)

    def test_required_checks_compare_hosted_settings_to_remote_docs(self) -> None:
        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(_responses(required_check="old matrix", hosted_check="new matrix")),
        )

        item = _item(report, "required status checks")

        self.assertEqual("drift", item.status)
        self.assertEqual("old matrix", item.expected)
        self.assertEqual("new matrix", item.actual)
        self.assertIn("source-of-truth governance docs", item.follow_up)

    def test_required_checks_parse_explicit_section_list(self) -> None:
        responses = _responses(required_check="build")
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content(
            "## Hosted Branch Protection\n\n"
            "The intended hosted branch protection for `main` is:\n\n"
            "- require pull requests before merge;\n"
            "- require these status checks:\n"
            "  - `build`\n"
            "  - `lint / docs`\n"
        )
        responses["/repos/ctrl-alt-keith/sample/branches/main/protection"][
            "required_status_checks"
        ] = {
            "strict": True,
            "contexts": ["build", "lint / docs"],
            "checks": [],
        }

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "required status checks")

        self.assertEqual("match", item.status)
        self.assertEqual("build, lint / docs", item.expected)

    def test_prose_required_check_mentions_do_not_define_expectations(self) -> None:
        responses = _responses()
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content(
            "Use `make check` before review. Required CI canaries are separate from local checks.\n"
            "Do not rename workflow jobs without checking branch protection later.\n"
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "required status checks")

        self.assertEqual("unknown", item.status)
        self.assertEqual("no required-check expectation found", item.expected)

    def test_historical_required_check_references_do_not_define_expectations(self) -> None:
        responses = _responses()
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content(
            "PR #26 added the workflow file, then left branch protection and required status checks "
            "as a hosted GitHub follow-up.\n"
            "As of May 14, 2026, hosted inspection was still pending.\n"
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "required status checks")

        self.assertEqual("unknown", item.status)
        self.assertEqual("no required-check expectation found", item.expected)

    def test_example_command_blocks_do_not_define_required_checks(self) -> None:
        responses = _responses()
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content(
            "Inspect required status checks with a read-only command:\n\n"
            "```sh\n"
            "gh pr view 26 --json statusCheckRollup\n"
            "gh api repos/ctrl-alt-keith/sample/branches/main/protection\n"
            "```\n"
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "required status checks")

        self.assertEqual("unknown", item.status)
        self.assertEqual("no required-check expectation found", item.expected)

    def test_linode_backup_lab_style_governance_ignores_manifest_prose_noise(self) -> None:
        responses = _responses(required_check="make check (Python 3.10)")
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content(
            "## Current Workflows\n\n"
            "- `.github/workflows/check.yml` runs on pull requests. Its required status\n"
            "  check names are:\n"
            "  - `make check (Python 3.10)`\n"
            "  - `make check (Python 3.11)`\n"
            "- `.github/workflows/authoritative-source-check.yml` runs on pull requests.\n"
            "  Its required status check name is\n"
            "  `authoritative-source-check / authoritative-source-check`.\n\n"
            "## Hosted Branch Protection\n\n"
            "- require these status checks:\n"
            "  - `make check (Python 3.10)`\n"
            "  - `make check (Python 3.11)`\n"
            "  - `authoritative-source-check / authoritative-source-check`\n\n"
            "| `passed_with_unverified_provider_state` | `plan` | snapshot-label checks passed, "
            "but a fresh `inspect` is required before mutation. |\n"
        )
        responses["/repos/ctrl-alt-keith/sample/branches/main/protection"][
            "required_status_checks"
        ] = {
            "strict": True,
            "contexts": [
                "make check (Python 3.10)",
                "make check (Python 3.11)",
                "authoritative-source-check / authoritative-source-check",
            ],
            "checks": [],
        }

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "required status checks")

        self.assertEqual("match", item.status)
        self.assertEqual(
            "authoritative-source-check / authoritative-source-check, "
            "make check (Python 3.10), make check (Python 3.11)",
            item.expected,
        )
        self.assertNotIn("inspect", item.expected)
        self.assertNotIn("passed_with_unverified_provider_state", item.expected)
        self.assertNotIn("plan", item.expected)

    def test_no_explicit_required_check_section_returns_unknown(self) -> None:
        responses = _responses()
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content("CI runs the repository validation workflow and local `make check` remains canonical.\n")

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "required status checks")

        self.assertEqual("unknown", item.status)
        self.assertEqual("no required-check expectation found", item.expected)

    def test_generic_private_language_is_not_visibility_expectation(self) -> None:
        responses = _responses()
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content("Avoid private repository details in public artifacts.\n")

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "repository visibility")

        self.assertEqual("unknown", item.status)
        self.assertIn("no visibility expectation", item.expected)

    def test_default_branch_protection_phrase_does_not_define_default_branch(self) -> None:
        responses = _responses()
        responses["/repos/ctrl-alt-keith/sample/contents/AGENTS.md?ref=remote-sha"] = _content(
            "Use pull requests. Run `make check`.\n"
        )
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content("Default branch protection is required.\n")

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "default branch")

        self.assertEqual("unknown", item.status)
        self.assertIn("no default-branch expectation", item.expected)

    def test_workflow_state_is_compared_to_remote_ref_files(self) -> None:
        responses = _responses()
        responses["/repos/ctrl-alt-keith/sample/actions/workflows"] = {
            "workflows": [
                {
                    "path": ".github/workflows/check.yml",
                    "state": "disabled_manually",
                }
            ]
        }

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "Actions workflow presence and state")

        self.assertEqual("drift", item.status)
        self.assertEqual(".github/workflows/check.yml", item.expected)
        self.assertIn("disabled_manually", item.actual)
        self.assertIn("Review disabled hosted workflows", item.follow_up)

    def test_prose_merge_method_mention_reports_unknown_not_match(self) -> None:
        responses = _responses()
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content(
            "Use pull requests. Compare squash merge and merge commit settings during governance review.\n"
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "merge method settings")

        self.assertEqual("unknown", item.status)
        self.assertIn("no concrete expected settings are parsed", item.expected)
        self.assertIn("squash merges: yes", item.actual)
        self.assertIn("Compare allowed merge methods manually", item.follow_up)

    def test_json_report_marks_audit_read_only(self) -> None:
        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(_responses()),
        )

        data = json.loads(render_json_report(report))
        text = render_text_report(report)

        self.assertTrue(data["read_only"])
        self.assertEqual("repo_settings_audit", data["report_type"])
        self.assertIn("Read-only: yes", text)
        self.assertIn("Source-of-truth ref: main", text)


class FakeGh:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> GhCommand:
        self.commands.append(argv)
        endpoint = argv[-1]
        response = self.responses[endpoint]
        if isinstance(response, GhCommand):
            return response
        return GhCommand(argv=argv, returncode=0, stdout=json.dumps(response), stderr="")


def _responses(
    *,
    source_ref: str = "main",
    required_check: str = "make check",
    hosted_check: str | None = None,
) -> dict[str, object]:
    hosted_check = hosted_check or required_check
    governance_doc = (
        "Default branch `main` uses branch protection.\n"
        f"Required status checks: `{required_check}`.\n"
        "Pull requests are required and branches must be up-to-date.\n"
        "Force-push and branch deletion restrictions are expected.\n"
    )
    files = {
        "AGENTS.md": "Use pull requests. Target `main`. Run `make check`.\n",
        "Makefile": "check:\n\tpython3 -m unittest discover -s tests\n",
        "docs/governance-ci.md": governance_doc,
        ".github/workflows/check.yml": "name: check\n",
    }
    tree = [
        {"path": path, "type": "blob"}
        for path in files
    ]
    responses: dict[str, object] = {
        "/repos/ctrl-alt-keith/sample": {
            "name": "sample",
            "full_name": "ctrl-alt-keith/sample",
            "private": False,
            "default_branch": "main",
            "allow_merge_commit": True,
            "allow_squash_merge": True,
            "allow_rebase_merge": False,
            "allow_auto_merge": False,
            "delete_branch_on_merge": True,
        },
        f"/repos/ctrl-alt-keith/sample/commits/{source_ref}": {"sha": "remote-sha"},
        "/repos/ctrl-alt-keith/sample/git/trees/remote-sha?recursive=1": {"tree": tree},
        "/repos/ctrl-alt-keith/sample/branches/main/protection": {
            "required_status_checks": {
                "strict": True,
                "contexts": [hosted_check],
                "checks": [],
            },
            "required_pull_request_reviews": {},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
        },
        "/repos/ctrl-alt-keith/sample/rulesets?targets=branch": [],
        "/repos/ctrl-alt-keith/sample/actions/workflows": {
            "workflows": [
                {
                    "path": ".github/workflows/check.yml",
                    "state": "active",
                }
            ]
        },
    }
    for path, text in files.items():
        responses[f"/repos/ctrl-alt-keith/sample/contents/{path}?ref=remote-sha"] = _content(text)
    return responses


def _content(text: str) -> dict[str, str]:
    return {
        "encoding": "base64",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }


def _item(report, setting: str):
    for item in report.items:
        if item.setting == setting:
            return item
    raise AssertionError(f"missing audit item: {setting}")


def _init_repo(repo: Path) -> None:
    _git(repo, "init")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
