from __future__ import annotations

from dataclasses import dataclass
import json
import unittest

from enforcement.github_org_repositories import enumerate_organization_repositories


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class GitHubOrganizationRepositoryTests(unittest.TestCase):
    def test_complete_paginated_owner_enumeration_preserves_provider_metadata(self) -> None:
        runner = FakeRunner(
            pages=[
                [_repo("public", 1)],
                [_repo("private", 2, private=True), _repo("archived", 3, archived=True)],
            ],
            membership={"state": "active", "role": "admin"},
        )

        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)

        self.assertTrue(enumeration.complete)
        self.assertEqual(("archived", "private", "public"), tuple(repo.name for repo in enumeration.repositories))
        self.assertTrue(enumeration.repositories[0].archived)
        self.assertTrue(enumeration.repositories[1].private)
        self.assertIn("--paginate", runner.commands[0])
        self.assertIn("--slurp", runner.commands[0])

    def test_malformed_entry_preserves_valid_partial_evidence_as_unknown(self) -> None:
        malformed = _repo("broken", 2)
        del malformed["archived"]
        runner = FakeRunner(
            pages=[[_repo("valid", 1), malformed]],
            membership={"state": "active", "role": "admin"},
        )

        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)

        self.assertFalse(enumeration.complete)
        self.assertEqual(("valid",), tuple(repo.name for repo in enumeration.repositories))
        self.assertIn("missing boolean archived/private metadata", enumeration.errors[0])

    def test_visibility_is_unknown_without_active_owner_membership(self) -> None:
        runner = FakeRunner(
            pages=[[_repo("visible", 1)]],
            membership={"state": "active", "role": "member"},
        )

        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)

        self.assertFalse(enumeration.complete)
        self.assertEqual(("visible",), tuple(repo.name for repo in enumeration.repositories))
        self.assertIn("not a proven active organization owner", enumeration.errors[0])

    def test_failed_enumeration_is_unknown_not_empty_complete_scope(self) -> None:
        runner = FakeRunner(
            pages=Result(1, stderr="HTTP 403: Forbidden"),
            membership={"state": "active", "role": "admin"},
        )

        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)

        self.assertFalse(enumeration.complete)
        self.assertEqual((), enumeration.repositories)
        self.assertIn("HTTP 403", enumeration.errors[0])


class FakeRunner:
    def __init__(self, *, pages: object, membership: object) -> None:
        self.pages = pages
        self.membership = membership
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> Result:
        self.commands.append(argv)
        response = self.pages if "/repos?" in argv[-1] else self.membership
        if isinstance(response, Result):
            return response
        return Result(0, json.dumps(response))


def _repo(name: str, repository_id: int, *, archived: bool = False, private: bool = False) -> dict[str, object]:
    return {
        "id": repository_id,
        "name": name,
        "full_name": f"ctrl-alt-keith/{name}",
        "owner": {"login": "ctrl-alt-keith"},
        "archived": archived,
        "private": private,
        "default_branch": "main",
    }


if __name__ == "__main__":
    unittest.main()
