from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock

from enforcement.stewardship.github import GitHubError, GitHubGateway


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


if __name__ == "__main__":
    unittest.main()
