from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from enforcement.stewardship.github import GitHubError, GitHubGateway
from enforcement.stewardship.models import (
    AGENTS_STARTUP_ROUTING_METADATA,
    CollisionResult,
    DeliveryProposal,
    DOCS_DRIFT_METADATA,
    ValidationResult,
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
            with self.assertRaises(GitHubError):
                self.gateway.resolve_ref(
                    "ctrl-alt-keith/ai-workflow-enforcement", "test/ref"
                )

    def test_resolve_ref_fails_closed_for_non_commit_sha(self) -> None:
        result = subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout=json.dumps({"sha": "A" * 40}),
            stderr="",
        )
        with mock.patch.object(self.gateway, "_run", return_value=result):
            with self.assertRaises(GitHubError):
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

    def test_existing_pr_lookup_fails_closed_for_malformed_page(self) -> None:
        with mock.patch.object(self.gateway, "_gh_json", return_value=[{}]):
            with self.assertRaises(GitHubError):
                self.gateway.existing_stewardship_pr(
                    "ctrl-alt-keith/ai-workflow-enforcement",
                    AGENTS_STARTUP_ROUTING_METADATA.collision_marker,
                )

    def test_delivery_creates_only_new_branch_even_with_a_racing_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / "remote.git"
            checkout = root / "checkout"
            subprocess.run(
                ("git", "init", "--bare", str(remote)), check=True, capture_output=True
            )
            subprocess.run(
                ("git", "init", "-b", "main", str(checkout)),
                check=True, capture_output=True,
            )

            def git(*arguments: str) -> str:
                result = subprocess.run(
                    ("git", *arguments),
                    cwd=checkout, check=True, capture_output=True, text=True,
                )
                return result.stdout.strip()

            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.com")
            git("config", "commit.gpgsign", "false")
            (checkout / "README.md").write_text("base\n", encoding="utf-8")
            git("add", "README.md")
            git("commit", "-m", "base")
            base_sha = git("rev-parse", "HEAD")
            git("remote", "add", "origin", str(remote))
            git("push", "origin", "main")

            for existing_tip in ("absent", "base", "proposed"):
                with self.subTest(existing_tip=existing_tip):
                    branch = f"stewardship/test/{existing_tip}"
                    git("checkout", "--detach", base_sha)
                    (checkout / "README.md").write_text("changed\n", encoding="utf-8")
                    patch = git("diff", "--binary", "--full-index") + "\n"
                    proposal = DeliveryProposal(
                        repository="example/repo",
                        base_branch="main",
                        base_sha=base_sha,
                        branch=branch,
                        commit_message="proposal",
                        pr_title="proposal",
                        pr_body="proposal",
                        changed_paths=("README.md",),
                        patch=patch,
                        diff_digest="unused",
                        validation=ValidationResult(status="passed", exit_code=0),
                        collision=CollisionResult(decision="clear", reason="clear"),
                    )
                    gateway = GitHubGateway(read_token="read", write_token="write")
                    real_run = subprocess.run
                    pushed = False
                    tip = ""

                    def collide(command, *args, **kwargs):
                        nonlocal pushed, tip
                        if command[:2] == ("git", "push") and not pushed:
                            pushed = True
                            if existing_tip != "absent":
                                tip = (
                                    base_sha if existing_tip == "base"
                                    else git("rev-parse", "HEAD")
                                )
                                real_run(
                                    ("git", "push", "origin", f"{tip}:refs/heads/{branch}"),
                                    cwd=checkout, check=True, capture_output=True, text=True,
                                )
                        return real_run(command, *args, **kwargs)

                    with (
                        mock.patch(
                            "enforcement.stewardship.github.subprocess.run",
                            side_effect=collide,
                        ),
                        mock.patch.object(
                            gateway, "_gh_json",
                            return_value={"html_url": "https://example.test/pr/1"},
                        ) as create_pr,
                    ):
                        result = gateway.deliver(checkout, proposal)

                    self.assertTrue(pushed)
                    if existing_tip == "absent":
                        self.assertTrue(result.success)
                        create_pr.assert_called_once()
                        tip = result.commit_sha or ""
                    else:
                        self.assertFalse(result.success)
                        self.assertEqual(
                            ({"operation": "push_branch", "success": False},),
                            result.mutations,
                        )
                        create_pr.assert_not_called()
                    self.assertEqual(
                        tip,
                        subprocess.run(
                            ("git", "--git-dir", str(remote), "rev-parse", f"refs/heads/{branch}"),
                            check=True, capture_output=True, text=True,
                        ).stdout.strip(),
                    )

if __name__ == "__main__":
    unittest.main()
