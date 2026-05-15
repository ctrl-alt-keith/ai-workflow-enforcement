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
                item.source == "central repo-settings policy + GitHub reviewed-ref (remote-sha)"
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

    def test_prose_required_check_mentions_do_not_define_exact_check_expectations(self) -> None:
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

        self.assertEqual("match", item.status)
        self.assertEqual(
            "hosted required status checks are explicitly required; exact names are not documented",
            item.expected,
        )

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

        self.assertEqual("match", item.status)
        self.assertEqual(
            "hosted required status checks are explicitly required; exact names are not documented",
            item.expected,
        )

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

        self.assertEqual("match", item.status)
        self.assertEqual(
            "hosted required status checks are explicitly required; exact names are not documented",
            item.expected,
        )

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

        self.assertEqual("match", item.status)
        self.assertEqual(
            "hosted required status checks are explicitly required; exact names are not documented",
            item.expected,
        )

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

        self.assertEqual("match", item.status)
        self.assertEqual("public", item.expected)

    def test_private_visibility_expectation_matches_hosted_private_repo(self) -> None:
        responses = _responses(private=True)
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content("- repository visibility: private\n")

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "repository visibility")

        self.assertEqual("match", item.status)
        self.assertEqual("private", item.expected)
        self.assertEqual("private", item.actual)

    def test_public_visibility_expectation_matches_hosted_public_repo(self) -> None:
        responses = _responses(private=False)
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content("- visibility: public\n")

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "repository visibility")

        self.assertEqual("match", item.status)
        self.assertEqual("public", item.expected)
        self.assertEqual("public", item.actual)

    def test_visibility_drift_when_hosted_visibility_differs(self) -> None:
        responses = _responses(private=False)
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content("- repository visibility: private\n")

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "repository visibility")

        self.assertEqual("drift", item.status)
        self.assertEqual("private", item.expected)
        self.assertEqual("public", item.actual)

    def test_baseline_defaults_apply_without_repo_local_governance_docs(self) -> None:
        responses = _responses(
            allow_merge_commit=False,
            allow_squash_merge=True,
            allow_rebase_merge=False,
            enforce_admins=False,
        )
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content("This repo has no hosted settings declarations.\n")

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        self.assertEqual("match", _item(report, "repository visibility").status)
        self.assertEqual("match", _item(report, "default branch").status)
        self.assertEqual("match", _item(report, "required pull requests").status)
        self.assertEqual("match", _item(report, "branch up-to-date requirement").status)
        self.assertEqual("match", _item(report, "force-push and deletion restrictions").status)
        self.assertEqual("match", _item(report, "merge method settings").status)
        self.assertEqual("match", _item(report, "review and administrator policy").status)

    def test_central_private_override_matches_hosted_private_repo(self) -> None:
        repo = "ctrl-alt-keith/ai-workflow-incubator"
        responses = _responses(repo=repo, private=True)
        responses[
            f"/repos/{repo}/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content("No repo-local visibility declaration needed for inherited private exception.\n")

        report = audit_repo_settings(
            repo,
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "repository visibility")

        self.assertEqual("match", item.status)
        self.assertEqual("private", item.expected)
        self.assertEqual("private", item.actual)

    def test_repo_local_explicit_override_takes_precedence_over_central_baseline(self) -> None:
        responses = _responses(private=True)
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content("- repository visibility: private\n")

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "repository visibility")

        self.assertEqual("match", item.status)
        self.assertEqual("private", item.expected)
        self.assertEqual("private", item.actual)

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

        self.assertEqual("match", item.status)
        self.assertEqual("main", item.expected)

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

        self.assertEqual("drift", item.status)
        self.assertIn("merge commits: disabled", item.expected)
        self.assertIn("squash merges: yes", item.actual)
        self.assertIn("Align hosted merge methods", item.follow_up)

    def test_squash_only_merge_policy_matches_hosted_settings(self) -> None:
        responses = _responses(
            allow_merge_commit=False,
            allow_squash_merge=True,
            allow_rebase_merge=False,
        )
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content("- merge policy: squash-only\n")

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "merge method settings")

        self.assertEqual("match", item.status)
        self.assertIn("merge commits: disabled", item.expected)
        self.assertIn("squash merges: enabled", item.expected)
        self.assertIn("rebase merges: disabled", item.expected)

    def test_squash_only_merge_policy_drifts_when_merge_or_rebase_is_enabled(self) -> None:
        responses = _responses(
            allow_merge_commit=True,
            allow_squash_merge=True,
            allow_rebase_merge=True,
        )
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content("- merge methods: squash-only\n")

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "merge method settings")

        self.assertEqual("drift", item.status)
        self.assertIn("merge commits: disabled", item.expected)
        self.assertIn("rebase merges: disabled", item.expected)
        self.assertIn("merge commits: yes", item.actual)
        self.assertIn("rebase merges: yes", item.actual)

    def test_explicit_zero_review_solo_operator_policy_matches_hosted_settings(self) -> None:
        responses = _responses(required_review_count=0, enforce_admins=False)
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content(
            "## Hosted Repository Settings\n\n"
            "- solo-operator review policy: enabled\n"
            "- require status checks before merge: yes\n"
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        review_item = _item(report, "review and administrator policy")
        pr_item = _item(report, "required pull requests")
        checks_item = _item(report, "required status checks")

        self.assertEqual("match", review_item.status)
        self.assertEqual("required approving reviews: 0; administrator bypass: enabled", review_item.expected)
        self.assertEqual("match", pr_item.status)
        self.assertEqual("match", checks_item.status)

    def test_review_admin_policy_drifts_when_hosted_settings_differ(self) -> None:
        responses = _responses(required_review_count=1, enforce_admins=True)
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content(
            "- required approving reviews: 0\n"
            "- administrator bypass: enabled\n"
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "review and administrator policy")

        self.assertEqual("drift", item.status)
        self.assertEqual("required approving reviews: 0; administrator bypass: enabled", item.expected)
        self.assertEqual("required approving reviews: 1; administrator bypass: disabled", item.actual)

    def test_ambiguous_review_admin_prose_remains_unknown(self) -> None:
        responses = _responses(required_review_count=0, enforce_admins=False)
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content("The solo operator can review hosted settings when needed.\n")

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "review and administrator policy")

        self.assertEqual("match", item.status)
        self.assertEqual("required approving reviews: 0; administrator bypass: enabled", item.expected)

    def test_explicit_branch_protection_policy_matches_hosted_settings(self) -> None:
        responses = _responses()
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content(
            "- require pull requests before merge: yes\n"
            "- require branches up to date before merge: yes\n"
            "- force pushes on main: disabled\n"
            "- deletions on main: disabled\n"
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        self.assertEqual("match", _item(report, "required pull requests").status)
        self.assertEqual("match", _item(report, "branch up-to-date requirement").status)
        self.assertEqual("match", _item(report, "force-push and deletion restrictions").status)

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
    repo: str = "ctrl-alt-keith/sample",
    source_ref: str = "main",
    required_check: str = "make check",
    hosted_check: str | None = None,
    private: bool = False,
    allow_merge_commit: bool = True,
    allow_squash_merge: bool = True,
    allow_rebase_merge: bool = False,
    required_review_count: int = 0,
    enforce_admins: bool = False,
) -> dict[str, object]:
    hosted_check = hosted_check or required_check
    _, repo_name = repo.split("/", 1)
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
        f"/repos/{repo}": {
            "name": repo_name,
            "full_name": repo,
            "private": private,
            "default_branch": "main",
            "allow_merge_commit": allow_merge_commit,
            "allow_squash_merge": allow_squash_merge,
            "allow_rebase_merge": allow_rebase_merge,
            "allow_auto_merge": False,
            "delete_branch_on_merge": True,
        },
        f"/repos/{repo}/commits/{source_ref}": {"sha": "remote-sha"},
        f"/repos/{repo}/git/trees/remote-sha?recursive=1": {"tree": tree},
        f"/repos/{repo}/branches/main/protection": {
            "required_status_checks": {
                "strict": True,
                "contexts": [hosted_check],
                "checks": [],
            },
            "required_pull_request_reviews": {
                "required_approving_review_count": required_review_count,
            },
            "enforce_admins": {"enabled": enforce_admins},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
        },
        f"/repos/{repo}/rulesets?targets=branch": [],
        f"/repos/{repo}/actions/workflows": {
            "workflows": [
                {
                    "path": ".github/workflows/check.yml",
                    "state": "active",
                }
            ]
        },
    }
    for path, text in files.items():
        responses[f"/repos/{repo}/contents/{path}?ref=remote-sha"] = _content(text)
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
