from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import unittest

from enforcement.github_repository_identity import verify_local_repository_identity


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class RepositoryIdentityTests(unittest.TestCase):
    def test_matching_fetch_push_locator_and_provider_id_are_verified(self) -> None:
        verification = _verify()

        self.assertTrue(verification.verified)
        self.assertEqual("ctrl-alt-keith/sample", verification.observed_repository)
        self.assertEqual(7, verification.observed_repository_id)

    def test_wrong_owner_is_mismatch(self) -> None:
        verification = _verify(
            fetch="git@github.com:someone-else/sample.git",
            push="git@github.com:someone-else/sample.git",
        )

        self.assertEqual("mismatch", verification.status)

    def test_wrong_repository_name_is_mismatch(self) -> None:
        verification = _verify(fetch="https://github.com/ctrl-alt-keith/other.git")

        self.assertEqual("mismatch", verification.status)
        self.assertEqual("ctrl-alt-keith/other", verification.observed_repository)

    def test_transferred_or_renamed_stale_remote_is_mismatch(self) -> None:
        verification = _verify(
            fetch="git@github.com:old-owner/old-name.git",
            push="git@github.com:old-owner/old-name.git",
        )

        self.assertEqual("mismatch", verification.status)
        self.assertEqual("old-owner/old-name", verification.observed_repository)

    def test_missing_remote_is_unverified(self) -> None:
        verification = _verify(fetch_result=Result(2, stderr="No such remote 'origin'"))

        self.assertEqual("unverified", verification.status)

    def test_ambiguous_fetch_remote_is_unverified(self) -> None:
        verification = _verify(
            fetch="git@github.com:ctrl-alt-keith/sample.git\nhttps://github.com/ctrl-alt-keith/sample.git"
        )

        self.assertEqual("unverified", verification.status)

    def test_provider_repository_id_mismatch_fails_closed(self) -> None:
        verification = _verify(provider_id=99)

        self.assertEqual("mismatch", verification.status)
        self.assertEqual(99, verification.observed_repository_id)

    def test_invalid_provider_identity_json_is_unverified(self) -> None:
        verification = _verify(provider_stdout="not-json")

        self.assertEqual("unverified", verification.status)
        self.assertEqual("ctrl-alt-keith/sample", verification.observed_repository)


def _verify(
    *,
    fetch: str = "git@github.com:ctrl-alt-keith/sample.git",
    push: str = "git@github.com:ctrl-alt-keith/sample.git",
    fetch_result: Result | None = None,
    provider_id: int = 7,
    provider_stdout: str | None = None,
):
    def git_runner(_path: Path, argv: tuple[str, ...]) -> Result:
        if "--push" in argv:
            return Result(0, push)
        return fetch_result or Result(0, fetch)

    def provider_runner(_argv: tuple[str, ...]) -> Result:
        return Result(0, provider_stdout or json.dumps({"id": provider_id, "full_name": "ctrl-alt-keith/sample"}))

    return verify_local_repository_identity(
        Path("/not-inspected"),
        "origin",
        "ctrl-alt-keith/sample",
        7,
        git_runner=git_runner,
        provider_runner=provider_runner,
    )


if __name__ == "__main__":
    unittest.main()
