from __future__ import annotations

import base64
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from enforcement import repo_settings_audit
from enforcement.repo_settings_audit import (
    GhCommand,
    audit_org_repo_settings,
    audit_repo_settings,
    main,
    render_org_json_report,
    render_org_text_report,
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

    def test_required_check_format_examples_do_not_render_punctuation_expectations(self) -> None:
        responses = _responses()
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content(
            "Required-status-check name comparison is intentionally conservative. The audit\n"
            "extracts exact hosted check names from explicit repo-local declarations such as\n"
            "`required status checks:`, `require these status checks:`, or structured\n"
            "required-check lists under governance or branch-protection sections.\n"
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
        self.assertNotEqual(",", item.expected)

    def test_empty_required_check_declaration_uses_descriptive_expectation(self) -> None:
        responses = _responses()
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content(
            "## Hosted Branch Protection\n\n"
            "- require status checks before merge: yes\n"
            "- required status checks:\n\n"
            "Exact hosted check names are intentionally pending.\n"
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

    def test_malformed_required_check_values_are_ignored(self) -> None:
        responses = _responses()
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content(
            "## Hosted Branch Protection\n\n"
            "- require status checks before merge: yes\n"
            "- required status checks: `,`\n"
            "  - `;`\n"
            "  - ``\n"
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
        self.assertIn("Document exact hosted check names", item.follow_up)

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

    def test_central_policy_ignores_blank_and_punctuation_required_checks(self) -> None:
        responses = _responses()
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content("Central policy requires status checks, but exact names are not local.\n")
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "repo-settings-policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "baseline": {
                            "visibility": "public",
                            "default_branch": "main",
                            "require_status_checks": True,
                            "required_checks": ["", "  ", ",", None],
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(repo_settings_audit, "CENTRAL_POLICY_PATH", policy_path):
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

    def test_central_exact_required_checks_still_render_correctly(self) -> None:
        repo = "ctrl-alt-keith/linode-backup-lab"
        responses = _responses(repo=repo)
        responses[
            f"/repos/{repo}/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content("Local governance inherits exact required checks from central policy.\n")
        responses[f"/repos/{repo}/branches/main/protection"]["required_status_checks"] = {
            "strict": True,
            "contexts": [
                "authoritative-source-check / authoritative-source-check",
                "make check",
            ],
            "checks": [],
        }

        report = audit_repo_settings(
            repo,
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "required status checks")

        self.assertEqual("match", item.status)
        self.assertEqual(
            "authoritative-source-check / authoritative-source-check, make check",
            item.expected,
        )

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

    def test_baseline_strict_required_checks_are_disabled(self) -> None:
        responses = _responses()
        responses["/repos/ctrl-alt-keith/sample/branches/main/protection"][
            "required_status_checks"
        ] = {
            "strict": False,
            "contexts": ["make check"],
            "checks": [],
        }

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "branch up-to-date requirement")

        self.assertEqual("match", item.status)
        self.assertEqual("branches up to date before merge: disabled", item.expected)
        self.assertEqual("no", item.actual)

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
        self.assertIn("auto-merge: disabled", item.expected)
        self.assertIn("delete branch on merge: enabled", item.expected)

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

    def test_merge_hygiene_baseline_drifts_when_auto_merge_or_branch_cleanup_differs(self) -> None:
        responses = _responses(
            allow_merge_commit=False,
            allow_squash_merge=True,
            allow_rebase_merge=False,
            allow_auto_merge=True,
            delete_branch_on_merge=False,
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "merge method settings")

        self.assertEqual("drift", item.status)
        self.assertIn("auto-merge: disabled", item.expected)
        self.assertIn("delete branch on merge: enabled", item.expected)
        self.assertIn("auto-merge: yes", item.actual)
        self.assertIn("delete branch on merge: no", item.actual)

    def test_merge_hygiene_baseline_matches_disabled_auto_merge_and_branch_cleanup(self) -> None:
        responses = _responses(
            allow_merge_commit=False,
            allow_squash_merge=True,
            allow_rebase_merge=False,
            allow_auto_merge=False,
            delete_branch_on_merge=True,
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "merge method settings")

        self.assertEqual("match", item.status)
        self.assertIn("auto-merge: disabled", item.expected)
        self.assertEqual(
            "Hosted merge method and hygiene settings match the effective policy.",
            item.follow_up,
        )

    def test_auto_merge_enabled_override_matches_hosted_setting_and_renders_fields(self) -> None:
        repo = "ctrl-alt-keith/ai-workflow-incubator"
        report = audit_repo_settings(
            repo,
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(
                _responses(
                    repo=repo,
                    private=True,
                    allow_merge_commit=False,
                    allow_squash_merge=True,
                    allow_rebase_merge=False,
                    allow_auto_merge=True,
                )
            ),
        )

        item = _item(report, "merge method settings")

        self.assertEqual("match", item.status)
        self.assertEqual("central repo-settings policy + GitHub main (remote-sha)", item.source)
        self.assertIn("auto-merge: enabled", item.expected)
        self.assertIn("auto-merge: yes", item.actual)
        self.assertEqual(
            "Hosted merge method and hygiene settings match the effective policy.",
            item.follow_up,
        )

        json_item = next(
            rendered_item
            for rendered_item in json.loads(render_json_report(report))["items"]
            if rendered_item["setting"] == "merge method settings"
        )
        self.assertEqual(item.source, json_item["source"])
        self.assertEqual(item.expected, json_item["expected"])
        self.assertEqual(item.actual, json_item["actual"])
        self.assertEqual(item.follow_up, json_item["follow_up"])

        text_report = render_text_report(report)
        self.assertIn(f"  source: {item.source}", text_report)
        self.assertIn(f"  expected: {item.expected}", text_report)
        self.assertIn(f"  actual: {item.actual}", text_report)
        self.assertIn(f"  follow-up: {item.follow_up}", text_report)

    def test_auto_merge_enabled_override_drifts_when_hosted_setting_is_disabled(self) -> None:
        repo = "ctrl-alt-keith/ai-workflow-incubator"
        report = audit_repo_settings(
            repo,
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(
                _responses(
                    repo=repo,
                    private=True,
                    allow_merge_commit=False,
                    allow_squash_merge=True,
                    allow_rebase_merge=False,
                    allow_auto_merge=False,
                )
            ),
        )

        item = _item(report, "merge method settings")

        self.assertEqual("drift", item.status)
        self.assertIn("auto-merge: enabled", item.expected)
        self.assertIn("auto-merge: no", item.actual)
        self.assertIn("Align hosted merge methods", item.follow_up)

    def test_explicit_disabled_auto_merge_override_replaces_enabled_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "repo-settings-policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "baseline": {"auto_merge": "enabled"},
                        "repositories": {
                            "ctrl-alt-keith/sample": {"auto_merge": "disabled"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(repo_settings_audit, "CENTRAL_POLICY_PATH", policy_path):
                report = audit_repo_settings(
                    "ctrl-alt-keith/sample",
                    source_ref="main",
                    repo_root=Path("/does/not/exist"),
                    runner=FakeGh(_responses(allow_auto_merge=False)),
                )

        item = _item(report, "merge method settings")

        self.assertEqual("match", item.status)
        self.assertEqual("auto-merge: disabled", item.expected)
        self.assertIn("auto-merge: no", item.actual)

    def test_invalid_auto_merge_policy_values_fail_closed(self) -> None:
        policies = {
            "baseline": {
                "baseline": {"auto_merge": "sometimes"},
                "repositories": {},
            },
            "selected repository override": {
                "baseline": {"auto_merge": "disabled"},
                "repositories": {
                    "ctrl-alt-keith/sample": {"auto_merge": True},
                },
            },
        }
        for label, policy in policies.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                policy_path = Path(temp_dir) / "repo-settings-policy.json"
                policy_path.write_text(json.dumps(policy), encoding="utf-8")
                with patch.object(repo_settings_audit, "CENTRAL_POLICY_PATH", policy_path):
                    with self.assertRaisesRegex(RuntimeError, "auto_merge.*enabled.*disabled"):
                        audit_repo_settings(
                            "ctrl-alt-keith/sample",
                            source_ref="main",
                            repo_root=Path("/does/not/exist"),
                            runner=FakeGh(_responses()),
                        )

    def test_unavailable_hosted_auto_merge_setting_reports_unknown(self) -> None:
        repo = "ctrl-alt-keith/sample"
        responses = _responses(
            repo=repo,
            allow_merge_commit=False,
            allow_squash_merge=True,
            allow_rebase_merge=False,
        )
        responses[f"/repos/{repo}"].pop("allow_auto_merge")

        report = audit_repo_settings(
            repo,
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "merge method settings")

        self.assertEqual("unknown", item.status)
        self.assertIn("auto-merge: disabled", item.expected)
        self.assertIn("auto-merge: unknown", item.actual)
        self.assertIn("unknown_unavailable", item.follow_up)
        self.assertIn("auto-merge", item.follow_up)

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
        responses["/repos/ctrl-alt-keith/sample/branches/main/protection"][
            "required_status_checks"
        ] = {
            "strict": True,
            "contexts": ["make check"],
            "checks": [],
        }
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

    def test_explicit_up_to_date_policy_still_drifts_when_hosted_strict_checks_are_off(self) -> None:
        responses = _responses()
        responses[
            "/repos/ctrl-alt-keith/sample/contents/docs/governance-ci.md?ref=remote-sha"
        ] = _content("- require branches up to date before merge: yes\n")
        responses["/repos/ctrl-alt-keith/sample/branches/main/protection"][
            "required_status_checks"
        ] = {
            "strict": False,
            "contexts": ["make check"],
            "checks": [],
        }

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "branch up-to-date requirement")

        self.assertEqual("drift", item.status)
        self.assertEqual("branches up to date before merge: enabled", item.expected)
        self.assertEqual("no", item.actual)

    def test_classic_branch_protection_matches_baseline_effective_policy(self) -> None:
        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(_responses(enforce_admins=False)),
        )

        self.assertEqual("match", _item(report, "default branch protection or ruleset").status)
        self.assertEqual("match", _item(report, "required status checks").status)
        self.assertEqual("match", _item(report, "required pull requests").status)
        self.assertEqual("match", _item(report, "review and administrator policy").status)
        self.assertEqual("match", _item(report, "branch up-to-date requirement").status)
        self.assertEqual("match", _item(report, "force-push and deletion restrictions").status)

    def test_equivalent_ruleset_matches_baseline_effective_policy(self) -> None:
        responses = _responses()
        protection_endpoint = "/repos/ctrl-alt-keith/sample/branches/main/protection"
        responses[protection_endpoint] = GhCommand(
            argv=("gh", "api", protection_endpoint),
            returncode=1,
            stdout="",
            stderr="HTTP 404 Not Found",
        )
        responses["/repos/ctrl-alt-keith/sample/rulesets?targets=branch"] = [
            {
                "id": 123,
                "name": "Protect main",
                "target": "branch",
                "enforcement": "active",
            }
        ]
        responses["/repos/ctrl-alt-keith/sample/rulesets/123"] = _ruleset_detail(
            current_user_can_bypass="always",
            include_deletion=True,
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        self.assertEqual("match", _item(report, "default branch protection or ruleset").status)
        self.assertEqual("match", _item(report, "required status checks").status)
        self.assertEqual("match", _item(report, "required pull requests").status)
        self.assertEqual("match", _item(report, "review and administrator policy").status)
        self.assertEqual("match", _item(report, "branch up-to-date requirement").status)
        self.assertEqual("match", _item(report, "force-push and deletion restrictions").status)

    def test_admin_bypass_enabled_through_classic_branch_protection(self) -> None:
        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(_responses(enforce_admins=False)),
        )

        item = _item(report, "review and administrator policy")

        self.assertEqual("match", item.status)
        self.assertEqual("required approving reviews: 0; administrator bypass: enabled", item.actual)

    def test_admin_bypass_enabled_through_ruleset_bypass_actors(self) -> None:
        responses = _responses()
        protection_endpoint = "/repos/ctrl-alt-keith/sample/branches/main/protection"
        responses[protection_endpoint] = GhCommand(
            argv=("gh", "api", protection_endpoint),
            returncode=1,
            stdout="",
            stderr="HTTP 404 Not Found",
        )
        responses["/repos/ctrl-alt-keith/sample/rulesets?targets=branch"] = [
            {
                "id": 123,
                "name": "Protect main",
                "target": "branch",
                "enforcement": "active",
            }
        ]
        responses["/repos/ctrl-alt-keith/sample/rulesets/123"] = _ruleset_detail(
            current_user_can_bypass="",
            include_deletion=True,
            bypass_actors=[{"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always"}],
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "review and administrator policy")

        self.assertEqual("match", item.status)
        self.assertEqual("required approving reviews: 0; administrator bypass: enabled", item.actual)

    def test_strict_check_behavior_matches_across_classic_and_ruleset_mechanisms(self) -> None:
        classic = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(_responses()),
        )

        ruleset_responses = _responses()
        protection_endpoint = "/repos/ctrl-alt-keith/sample/branches/main/protection"
        ruleset_responses[protection_endpoint] = GhCommand(
            argv=("gh", "api", protection_endpoint),
            returncode=1,
            stdout="",
            stderr="HTTP 404 Not Found",
        )
        ruleset_responses["/repos/ctrl-alt-keith/sample/rulesets?targets=branch"] = [
            {
                "id": 123,
                "name": "Protect main",
                "target": "branch",
                "enforcement": "active",
            }
        ]
        ruleset_responses["/repos/ctrl-alt-keith/sample/rulesets/123"] = _ruleset_detail(
            current_user_can_bypass="always",
            include_deletion=True,
            strict_checks=False,
        )
        ruleset = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(ruleset_responses),
        )

        self.assertEqual("match", _item(classic, "branch up-to-date requirement").status)
        self.assertEqual("no", _item(classic, "branch up-to-date requirement").actual)
        self.assertEqual("match", _item(ruleset, "branch up-to-date requirement").status)
        self.assertEqual("no", _item(ruleset, "branch up-to-date requirement").actual)

    def test_dependabot_github_actions_baseline_matches_weekly_config(self) -> None:
        responses = _responses(dependabot_config=_dependabot_config("github-actions"))

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "Dependabot config presence")

        self.assertEqual("match", item.status)
        self.assertIn("github-actions", item.expected)
        self.assertIn("github-actions (weekly)", item.actual)

    def test_dependabot_pip_baseline_matches_weekly_config(self) -> None:
        responses = _responses(
            include_workflow=False,
            include_pyproject=True,
            dependabot_config=_dependabot_config("pip"),
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "Dependabot config presence")

        self.assertEqual("match", item.status)
        self.assertIn("pip", item.expected)
        self.assertIn("pip (weekly)", item.actual)

    def test_dependabot_missing_config_drifts_when_supported_ecosystem_present(self) -> None:
        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(_responses()),
        )

        item = _item(report, "Dependabot config presence")

        self.assertEqual("drift", item.status)
        self.assertIn("github-actions", item.expected)
        self.assertIn("not present", item.actual)

    def test_dependabot_config_missing_required_ecosystem_drifts(self) -> None:
        responses = _responses(
            include_pyproject=True,
            dependabot_config=_dependabot_config("github-actions"),
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "Dependabot config presence")

        self.assertEqual("drift", item.status)
        self.assertIn("missing ecosystems: pip", item.actual)

    def test_dependabot_not_applicable_matches_without_supported_ecosystems(self) -> None:
        responses = _responses(include_workflow=False)

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "Dependabot config presence")

        self.assertEqual("match", item.status)
        self.assertIn("not applicable", item.expected)

    def test_dependabot_repo_override_disables_expectation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "repo-settings-policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "baseline": {
                            "visibility": "public",
                            "default_branch": "main",
                            "dependabot": {
                                "enabled": "auto",
                                "ecosystems": ["github-actions"],
                                "schedule": "weekly",
                            },
                        },
                        "repositories": {
                            "ctrl-alt-keith/sample": {
                                "dependabot": {"enabled": False},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(repo_settings_audit, "CENTRAL_POLICY_PATH", policy_path):
                report = audit_repo_settings(
                    "ctrl-alt-keith/sample",
                    source_ref="main",
                    repo_root=Path("/does/not/exist"),
                    runner=FakeGh(_responses()),
                )

        item = _item(report, "Dependabot config presence")

        self.assertEqual("match", item.status)
        self.assertIn("disabled", item.expected)

    def test_dependabot_repo_override_customizes_schedule(self) -> None:
        responses = _responses(dependabot_config=_dependabot_config("github-actions", interval="daily"))
        with tempfile.TemporaryDirectory() as temp_dir:
            policy_path = Path(temp_dir) / "repo-settings-policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "baseline": {
                            "visibility": "public",
                            "default_branch": "main",
                            "dependabot": {
                                "enabled": "auto",
                                "ecosystems": ["github-actions"],
                                "schedule": "weekly",
                            },
                        },
                        "repositories": {
                            "ctrl-alt-keith/sample": {
                                "dependabot": {"schedule": "daily"},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(repo_settings_audit, "CENTRAL_POLICY_PATH", policy_path):
                report = audit_repo_settings(
                    "ctrl-alt-keith/sample",
                    source_ref="main",
                    repo_root=Path("/does/not/exist"),
                    runner=FakeGh(responses),
                )

        item = _item(report, "Dependabot config presence")

        self.assertEqual("match", item.status)
        self.assertIn("github-actions (daily)", item.actual)

    def test_malformed_dependabot_config_reports_unknown_unavailable(self) -> None:
        responses = _responses(dependabot_config="version: 2\nupdates: [\n")

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "Dependabot config presence")

        self.assertEqual("unknown", item.status)
        self.assertIn("unknown_unavailable", item.actual)
        self.assertIn("unknown_unavailable", item.follow_up)

    def test_ruleset_detail_parses_reviews_checks_and_admin_bypass(self) -> None:
        responses = _responses()
        protection_endpoint = "/repos/ctrl-alt-keith/sample/branches/main/protection"
        responses[protection_endpoint] = GhCommand(
            argv=("gh", "api", protection_endpoint),
            returncode=1,
            stdout="",
            stderr="HTTP 404 Not Found",
        )
        responses["/repos/ctrl-alt-keith/sample/rulesets?targets=branch"] = [
            {
                "id": 123,
                "name": "Protect main",
                "target": "branch",
                "enforcement": "active",
            }
        ]
        responses["/repos/ctrl-alt-keith/sample/rulesets/123"] = _ruleset_detail(
            current_user_can_bypass="never",
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        self.assertEqual("match", _item(report, "required status checks").status)
        self.assertEqual("match", _item(report, "required pull requests").status)
        self.assertEqual("drift", _item(report, "review and administrator policy").status)
        self.assertEqual(
            "required approving reviews: 0; administrator bypass: disabled",
            _item(report, "review and administrator policy").actual,
        )

    def test_ruleset_detail_without_admin_fields_reports_specific_unknown(self) -> None:
        responses = _responses()
        protection_endpoint = "/repos/ctrl-alt-keith/sample/branches/main/protection"
        responses[protection_endpoint] = GhCommand(
            argv=("gh", "api", protection_endpoint),
            returncode=1,
            stdout="",
            stderr="HTTP 404 Not Found",
        )
        responses["/repos/ctrl-alt-keith/sample/rulesets?targets=branch"] = [
            {
                "id": 123,
                "name": "Protect main",
                "target": "branch",
                "enforcement": "active",
            }
        ]
        detail = dict(_ruleset_detail(current_user_can_bypass="never"))
        detail.pop("current_user_can_bypass")
        detail.pop("bypass_actors")
        responses["/repos/ctrl-alt-keith/sample/rulesets/123"] = detail

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "review and administrator policy")

        self.assertEqual("unknown", item.status)
        self.assertIn("current_user_can_bypass or bypass_actors", item.actual)

    def test_optional_hosted_call_succeeds_on_retry(self) -> None:
        responses = _responses()
        endpoint = "/repos/ctrl-alt-keith/sample/branches/main/protection"
        responses[endpoint] = (
            GhCommand(argv=("gh", "api", endpoint), returncode=1, stdout="", stderr="HTTP 502"),
            responses[endpoint],
        )
        gh = FakeGh(responses)

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=gh,
        )

        self.assertEqual(2, _command_count(gh, endpoint))
        self.assertEqual((), report.errors)
        self.assertEqual("match", _item(report, "default branch protection or ruleset").status)
        self.assertEqual("match", _item(report, "required status checks").status)

    def test_optional_hosted_call_unavailable_after_retry_reports_reason(self) -> None:
        responses = _responses()
        endpoint = "/repos/ctrl-alt-keith/sample/branches/main/protection"
        responses[endpoint] = (
            GhCommand(argv=("gh", "api", endpoint), returncode=1, stdout="", stderr="HTTP 403"),
            GhCommand(argv=("gh", "api", endpoint), returncode=1, stdout="", stderr="HTTP 403"),
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        branch_item = _item(report, "default branch protection or ruleset")
        checks_item = _item(report, "required status checks")

        self.assertEqual("unknown", branch_item.status)
        self.assertIn("unknown_after_retry", branch_item.actual)
        self.assertEqual("unknown", checks_item.status)
        self.assertIn("unknown_after_retry", checks_item.actual)
        self.assertTrue(any("unknown_after_retry" in error for error in report.errors))

    def test_missing_branch_protection_reports_drift_when_policy_expects_protection(self) -> None:
        responses = _responses()
        endpoint = "/repos/ctrl-alt-keith/sample/branches/main/protection"
        responses[endpoint] = GhCommand(
            argv=("gh", "api", endpoint),
            returncode=1,
            stdout="",
            stderr="HTTP 404 Not Found",
        )

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        self.assertEqual("drift", _item(report, "default branch protection or ruleset").status)
        self.assertEqual("drift", _item(report, "required status checks").status)
        self.assertEqual("drift", _item(report, "required pull requests").status)
        self.assertEqual("match", _item(report, "branch up-to-date requirement").status)
        self.assertEqual("drift", _item(report, "force-push and deletion restrictions").status)

    def test_admin_bypass_unknown_after_retry_stays_unknown(self) -> None:
        responses = _responses()
        endpoint = "/repos/ctrl-alt-keith/sample/branches/main/protection"
        partial = dict(responses[endpoint])
        partial.pop("enforce_admins")
        responses[endpoint] = (partial, dict(partial))
        gh = FakeGh(responses)

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=gh,
        )

        item = _item(report, "review and administrator policy")

        self.assertEqual(2, _command_count(gh, endpoint))
        self.assertEqual("unknown", item.status)
        self.assertIn("unknown_after_retry", item.actual)
        self.assertIn("administrator bypass: unknown", item.actual)

    def test_required_checks_absent_with_policy_requiring_checks_reports_drift(self) -> None:
        responses = _responses()
        endpoint = "/repos/ctrl-alt-keith/sample/branches/main/protection"
        protection = dict(responses[endpoint])
        protection.pop("required_status_checks")
        responses[endpoint] = protection

        report = audit_repo_settings(
            "ctrl-alt-keith/sample",
            source_ref="main",
            repo_root=Path("/does/not/exist"),
            runner=FakeGh(responses),
        )

        item = _item(report, "required status checks")

        self.assertEqual("drift", item.status)
        self.assertEqual("make check", item.expected)
        self.assertEqual("no required hosted status checks detected", item.actual)

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

    def test_json_report_separates_hosted_governance_from_local_source_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            _init_repo(repo)
            _write(repo / "AGENTS.md", "Use pull requests. Target `main`.\n")
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

            report = audit_repo_settings(
                "ctrl-alt-keith/sample",
                source_ref="main",
                repo_root=repo,
                runner=FakeGh(_clean_responses()),
            )

        data = json.loads(render_json_report(report))
        text = render_text_report(report)

        self.assertEqual(0, data["hosted_governance_summary"]["drift"])
        self.assertGreater(data["local_source_summary"]["drift"], 0)
        self.assertIn("Hosted governance summary: match=", text)
        self.assertIn("Local source summary: match=", text)

    def test_org_audit_without_workspace_root_is_hosted_only(self) -> None:
        responses = {"__repo_list__": [{"nameWithOwner": "ctrl-alt-keith/sample"}]}
        responses.update(_clean_responses())

        report = audit_org_repo_settings("ctrl-alt-keith", runner=FakeGh(responses))

        data = json.loads(render_org_json_report(report))
        text = render_org_text_report(report)

        self.assertEqual("org_repo_settings_audit", data["report_type"])
        self.assertEqual("not_checked", data["local_source_mode"])
        self.assertEqual({"match": 0, "drift": 0, "unknown": 0}, data["local_source_summary"])
        self.assertEqual(0, data["hosted_governance_summary"]["drift"])
        self.assertIn("Local source mode: not_checked", text)
        self.assertIn("local repo root: not checked", text)

    def test_org_audit_workspace_root_scopes_local_source_to_target_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            sample = workspace / "sample"
            sample.mkdir()
            _init_repo(sample)
            _write(sample / "AGENTS.md", "Use pull requests. Target `main`.\n")
            _git(sample, "add", ".")
            _git(
                sample,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "--no-gpg-sign",
                "-m",
                "initial",
            )
            responses = {"__repo_list__": [{"nameWithOwner": "ctrl-alt-keith/sample"}]}
            responses.update(_clean_responses())

            report = audit_org_repo_settings(
                "ctrl-alt-keith",
                workspace_root=workspace,
                runner=FakeGh(responses),
            )

        data = json.loads(render_org_json_report(report))
        repo_report = data["reports"][0]

        self.assertEqual("workspace_root", data["local_source_mode"])
        self.assertEqual(str(sample), repo_report["local_repo_root"])
        self.assertEqual(0, repo_report["hosted_governance_summary"]["drift"])
        self.assertGreater(repo_report["local_source_summary"]["drift"], 0)

    def test_org_audit_aggregates_per_repo_runtime_errors(self) -> None:
        responses = {
            "__repo_list__": [
                {"nameWithOwner": "ctrl-alt-keith/sample"},
                {"nameWithOwner": "ctrl-alt-keith/broken"},
            ],
            "/repos/ctrl-alt-keith/broken": GhCommand(
                argv=("gh", "api", "/repos/ctrl-alt-keith/broken"),
                returncode=1,
                stdout="",
                stderr="not found",
            ),
        }
        responses.update(_clean_responses())

        report = audit_org_repo_settings("ctrl-alt-keith", runner=FakeGh(responses))
        data = json.loads(render_org_json_report(report))

        self.assertEqual(("ctrl-alt-keith/broken", "ctrl-alt-keith/sample"), report.repositories)
        self.assertEqual(("ctrl-alt-keith/sample",), tuple(repo.repository for repo in report.reports))
        self.assertEqual(1, data["summary"]["audited_repository_count"])
        self.assertEqual(2, data["summary"]["repository_count"])
        self.assertEqual(1, len(data["errors"]))
        self.assertIn("ctrl-alt-keith/broken", data["errors"][0])

    def test_fail_on_error_exits_nonzero_for_org_runtime_errors(self) -> None:
        responses = {
            "__repo_list__": [
                {"nameWithOwner": "ctrl-alt-keith/sample"},
                {"nameWithOwner": "ctrl-alt-keith/broken"},
            ],
            "/repos/ctrl-alt-keith/broken": GhCommand(
                argv=("gh", "api", "/repos/ctrl-alt-keith/broken"),
                returncode=1,
                stdout="",
                stderr="not found",
            ),
        }
        responses.update(_clean_responses())

        code, stdout = _run_repo_settings_cli(
            "--org",
            "ctrl-alt-keith",
            "--output-format",
            "json",
            "--fail-on-error",
            runner=FakeGh(responses),
        )
        data = json.loads(stdout)

        self.assertEqual(1, code)
        self.assertEqual(1, len(data["errors"]))
        self.assertIn("ctrl-alt-keith/broken", data["errors"][0])

    def test_fail_on_drift_ignores_local_source_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            _init_repo(repo)
            _write(repo / "AGENTS.md", "Use pull requests. Target `main`.\n")
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

            code, stdout = _run_repo_settings_cli(
                "--repo",
                "ctrl-alt-keith/sample",
                "--repo-root",
                str(repo),
                "--output-format",
                "json",
                "--fail-on-drift",
                runner=FakeGh(_clean_responses()),
            )

        data = json.loads(stdout)

        self.assertEqual(0, code)
        self.assertEqual(0, data["hosted_governance_summary"]["drift"])
        self.assertGreater(data["local_source_summary"]["drift"], 0)

    def test_fail_on_drift_exits_nonzero_for_hosted_governance_drift(self) -> None:
        code, stdout = _run_repo_settings_cli(
            "--repo",
            "ctrl-alt-keith/sample",
            "--repo-root",
            "/does/not/exist",
            "--output-format",
            "json",
            "--fail-on-drift",
            runner=FakeGh(_responses()),
        )
        data = json.loads(stdout)

        self.assertEqual(1, code)
        self.assertGreater(data["hosted_governance_summary"]["drift"], 0)


class FakeGh:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> GhCommand:
        self.commands.append(argv)
        key = "__repo_list__" if argv[:3] == ("gh", "repo", "list") else argv[-1]
        response = self.responses[key]
        if isinstance(response, tuple):
            response = response[0]
            self.responses[key] = self.responses[key][1:]
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
    allow_auto_merge: bool = False,
    delete_branch_on_merge: bool = True,
    required_review_count: int = 0,
    enforce_admins: bool = False,
    include_workflow: bool = True,
    include_pyproject: bool = False,
    dependabot_config: str = "",
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
    }
    if include_workflow:
        files[".github/workflows/check.yml"] = "name: check\n"
    if include_pyproject:
        files["pyproject.toml"] = "[project]\nname = \"sample\"\n"
    if dependabot_config:
        files[".github/dependabot.yml"] = dependabot_config
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
            "allow_auto_merge": allow_auto_merge,
            "delete_branch_on_merge": delete_branch_on_merge,
        },
        f"/repos/{repo}/commits/{source_ref}": {"sha": "remote-sha"},
        f"/repos/{repo}/git/trees/remote-sha?recursive=1": {"tree": tree},
        f"/repos/{repo}/branches/main/protection": {
            "required_status_checks": {
                "strict": False,
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


def _clean_responses(repo: str = "ctrl-alt-keith/sample") -> dict[str, object]:
    responses = _responses(
        repo=repo,
        allow_merge_commit=False,
        dependabot_config=_dependabot_config("github-actions"),
    )
    responses[f"/repos/{repo}/contents/docs/governance-ci.md?ref=remote-sha"] = _content(
        "Default branch `main` uses branch protection.\n"
        "Required status checks: `make check`.\n"
        "Pull requests are required.\n"
        "Force-push and branch deletion restrictions are expected.\n"
    )
    return responses


def _run_repo_settings_cli(*args: str, runner: FakeGh) -> tuple[int, str]:
    stdout = StringIO()
    with patch("enforcement.repo_settings_audit._gh", runner), redirect_stdout(stdout):
        code = main(list(args))
    return code, stdout.getvalue()


def _content(text: str) -> dict[str, str]:
    return {
        "encoding": "base64",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }


def _dependabot_config(*ecosystems: str, interval: str = "weekly") -> str:
    entries = []
    for ecosystem in ecosystems:
        entries.append(
            "  - package-ecosystem: \"{ecosystem}\"\n"
            "    directory: \"/\"\n"
            "    schedule:\n"
            "      interval: \"{interval}\"\n".format(ecosystem=ecosystem, interval=interval)
        )
    return "version: 2\nupdates:\n" + "".join(entries)


def _ruleset_detail(
    current_user_can_bypass: str,
    *,
    strict_checks: bool = False,
    include_deletion: bool = False,
    bypass_actors: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    rules: list[dict[str, object]] = [
        {
            "type": "pull_request",
            "parameters": {
                "required_approving_review_count": 0,
            },
        },
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": strict_checks,
                "required_status_checks": [
                    {
                        "context": "make check",
                        "integration_id": 15368,
                    }
                ],
            },
        },
        {
            "type": "non_fast_forward",
        },
    ]
    if include_deletion:
        rules.append({"type": "deletion"})
    return {
        "id": 123,
        "name": "Protect main",
        "target": "branch",
        "enforcement": "active",
        "rules": rules,
        "bypass_actors": bypass_actors or [],
        "current_user_can_bypass": current_user_can_bypass,
    }


def _item(report, setting: str):
    for item in report.items:
        if item.setting == setting:
            return item
    raise AssertionError(f"missing audit item: {setting}")


def _command_count(gh: FakeGh, endpoint: str) -> int:
    return sum(1 for command in gh.commands if command[-1] == endpoint)


def _init_repo(repo: Path) -> None:
    _git(repo, "init")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
