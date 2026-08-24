from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock

from enforcement.stewardship.github import GitHubError, GitHubGateway
from enforcement.stewardship.models import (
    AGENTS_STARTUP_ROUTING_METADATA,
    DOCS_DRIFT_METADATA,
    WORKTREE_IGNORE_BASELINE_METADATA,
)


class StewardshipGitHubGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = GitHubGateway(read_token="read-token")

    def test_resolve_ref_uses_commit_endpoint_for_branch_tag_or_sha(self) -> None:
        resolved_sha = "a" * 40
        result = subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout=json.dumps({"sha": resolved_sha}),
            stderr="",
        )
        with mock.patch.object(self.gateway, "_run", return_value=result) as run:
            actual = self.gateway.resolve_ref(
                "ctrl-alt-keith/ai-workflow-enforcement",
                "test/controlled-drift",
            )

        self.assertEqual(resolved_sha, actual)
        run.assert_called_once_with(
            (
                "gh",
                "api",
                "repos/ctrl-alt-keith/ai-workflow-enforcement/commits/test%2Fcontrolled-drift",
            ),
            token="read-token",
            check=False,
        )

    def test_resolve_ref_returns_none_for_not_found(self) -> None:
        result = subprocess.CompletedProcess(
            args=(), returncode=1, stdout="", stderr="gh: Not Found (HTTP 404)\n"
        )
        with mock.patch.object(self.gateway, "_run", return_value=result):
            self.assertIsNone(
                self.gateway.resolve_ref(
                    "ctrl-alt-keith/ai-workflow-enforcement", "missing/ref"
                )
            )

    def test_resolve_ref_fails_closed_for_other_errors(self) -> None:
        result = subprocess.CompletedProcess(
            args=(), returncode=1, stdout="", stderr="gh: service unavailable\n"
        )
        with mock.patch.object(self.gateway, "_run", return_value=result):
            with self.assertRaisesRegex(GitHubError, "service unavailable"):
                self.gateway.resolve_ref(
                    "ctrl-alt-keith/ai-workflow-enforcement", "test/ref"
                )

    def test_branch_sha_fails_closed_for_invalid_json(self) -> None:
        result = subprocess.CompletedProcess(
            args=(), returncode=0, stdout="{not-json", stderr=""
        )
        with mock.patch.object(self.gateway, "_run", return_value=result):
            with self.assertRaisesRegex(
                GitHubError, "GitHub returned malformed branch ref JSON"
            ):
                self.gateway.branch_sha(
                    "ctrl-alt-keith/ai-workflow-enforcement", "main"
                )

    def test_branch_sha_fails_closed_for_missing_sha_shape(self) -> None:
        result = subprocess.CompletedProcess(
            args=(), returncode=0, stdout=json.dumps({"object": None}), stderr=""
        )
        with mock.patch.object(self.gateway, "_run", return_value=result):
            with self.assertRaisesRegex(
                GitHubError, "GitHub returned malformed branch ref JSON"
            ):
                self.gateway.branch_sha(
                    "ctrl-alt-keith/ai-workflow-enforcement", "main"
                )

    def test_branch_sha_fails_closed_for_non_commit_sha(self) -> None:
        result = subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout=json.dumps({"object": {"sha": "not-a-commit"}}),
            stderr="",
        )
        with mock.patch.object(self.gateway, "_run", return_value=result):
            with self.assertRaisesRegex(
                GitHubError, "GitHub returned malformed branch ref SHA"
            ):
                self.gateway.branch_sha(
                    "ctrl-alt-keith/ai-workflow-enforcement", "main"
                )

    def test_resolve_ref_fails_closed_for_non_commit_sha(self) -> None:
        result = subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout=json.dumps({"sha": "A" * 40}),
            stderr="",
        )
        with mock.patch.object(self.gateway, "_run", return_value=result):
            with self.assertRaisesRegex(
                GitHubError, "GitHub returned malformed commit SHA"
            ):
                self.gateway.resolve_ref(
                    "ctrl-alt-keith/ai-workflow-enforcement", "test/ref"
                )

    def test_resolve_ref_fails_closed_for_non_object_json(self) -> None:
        for body in ([], None):
            with self.subTest(body=body):
                result = subprocess.CompletedProcess(
                    args=(), returncode=0, stdout=json.dumps(body), stderr=""
                )
                with mock.patch.object(self.gateway, "_run", return_value=result):
                    with self.assertRaisesRegex(
                        GitHubError, "GitHub returned malformed commit JSON"
                    ):
                        self.gateway.resolve_ref(
                            "ctrl-alt-keith/ai-workflow-enforcement", "test/ref"
                        )

    def test_existing_pr_lookup_matches_only_selected_strategy_marker(self) -> None:
        pages = [
            [
                {
                    "body": DOCS_DRIFT_METADATA.collision_marker,
                    "html_url": "https://github.com/example/pull/1",
                },
                {
                    "body": AGENTS_STARTUP_ROUTING_METADATA.collision_marker,
                    "html_url": "https://github.com/example/pull/2",
                },
                {
                    "body": WORKTREE_IGNORE_BASELINE_METADATA.collision_marker,
                    "html_url": "https://github.com/example/pull/3",
                },
            ]
        ]
        with mock.patch.object(self.gateway, "_gh_json", return_value=pages):
            actual = self.gateway.existing_stewardship_pr(
                "ctrl-alt-keith/ai-workflow-enforcement",
                AGENTS_STARTUP_ROUTING_METADATA.collision_marker,
            )

        self.assertEqual("https://github.com/example/pull/2", actual)

    def test_cross_strategy_pr_is_not_a_collision(self) -> None:
        pages = [
            [
                {
                    "body": DOCS_DRIFT_METADATA.collision_marker,
                    "html_url": "https://github.com/example/pull/1",
                }
            ]
        ]
        with mock.patch.object(self.gateway, "_gh_json", return_value=pages):
            actual = self.gateway.existing_stewardship_pr(
                "ctrl-alt-keith/ai-workflow-enforcement",
                AGENTS_STARTUP_ROUTING_METADATA.collision_marker,
            )

        self.assertIsNone(actual)

    def test_worktree_strategy_marker_matches_only_worktree_pr(self) -> None:
        pages = [
            [
                {
                    "body": AGENTS_STARTUP_ROUTING_METADATA.collision_marker,
                    "html_url": "https://github.com/example/pull/2",
                },
                {
                    "body": WORKTREE_IGNORE_BASELINE_METADATA.collision_marker,
                    "html_url": "https://github.com/example/pull/3",
                },
            ]
        ]
        with mock.patch.object(self.gateway, "_gh_json", return_value=pages):
            actual = self.gateway.existing_stewardship_pr(
                "ctrl-alt-keith/ai-workflow-enforcement",
                WORKTREE_IGNORE_BASELINE_METADATA.collision_marker,
            )

        self.assertEqual("https://github.com/example/pull/3", actual)

    def test_existing_pr_lookup_fails_closed_for_malformed_page(self) -> None:
        with mock.patch.object(self.gateway, "_gh_json", return_value=[{}]):
            with self.assertRaisesRegex(
                GitHubError, "malformed pull-request pagination JSON"
            ):
                self.gateway.existing_stewardship_pr(
                    "ctrl-alt-keith/ai-workflow-enforcement",
                    AGENTS_STARTUP_ROUTING_METADATA.collision_marker,
                )

    def test_existing_pr_lookup_fails_closed_for_malformed_entry(self) -> None:
        with mock.patch.object(
            self.gateway,
            "_gh_json",
            return_value=[[{"body": 3, "html_url": "url"}]],
        ):
            with self.assertRaisesRegex(GitHubError, "malformed pull-request JSON"):
                self.gateway.existing_stewardship_pr(
                    "ctrl-alt-keith/ai-workflow-enforcement",
                    AGENTS_STARTUP_ROUTING_METADATA.collision_marker,
                )


if __name__ == "__main__":
    unittest.main()
