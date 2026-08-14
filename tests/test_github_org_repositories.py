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
    def test_active_owner_with_proven_oauth_all_repository_access_is_complete(self) -> None:
        runner = FakeRunner(
            pages=[
                [_repo("public", 1)],
                [_repo("private", 2, private=True), _repo("archived", 3, archived=True)],
            ],
        )

        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)

        self.assertTrue(enumeration.complete)
        self.assertEqual("oauth_scope_bearing", enumeration.credential_kind)
        self.assertEqual("all_repositories", enumeration.credential_access)
        self.assertEqual(("read:org", "repo"), enumeration.credential_scopes)
        self.assertEqual(("archived", "private", "public"), tuple(repo.name for repo in enumeration.repositories))
        self.assertTrue(enumeration.repositories[0].archived)
        self.assertTrue(enumeration.repositories[1].private)
        self.assertIn("--paginate", runner.commands[0])
        self.assertIn("--slurp", runner.commands[0])

    def test_active_owner_with_repository_restricted_credential_is_unknown(self) -> None:
        runner = FakeRunner(pages=[[_repo("visible", 1)]], scopes=("read:org",))

        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)

        self.assertFalse(enumeration.complete)
        self.assertEqual("unknown", enumeration.credential_access)
        self.assertIn("acting OAuth credential lacks required all-repository scopes: repo", enumeration.errors)

    def test_fine_grained_or_app_credential_without_scope_breadth_is_unknown(self) -> None:
        runner = FakeRunner(pages=[[_repo("visible", 1)]], include_scope_header=False)

        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)

        self.assertFalse(enumeration.complete)
        self.assertEqual("unknown", enumeration.credential_kind)
        self.assertTrue(any("fine-grained PAT and GitHub App" in item for item in enumeration.errors))

    def test_non_owner_membership_is_unknown(self) -> None:
        runner = FakeRunner(
            pages=[[_repo("visible", 1)]],
            membership={"state": "active", "role": "member", "user": {"login": "operator"}},
        )

        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)

        self.assertFalse(enumeration.complete)
        self.assertEqual(("visible",), tuple(repo.name for repo in enumeration.repositories))
        self.assertTrue(any("not a proven active organization owner" in item for item in enumeration.errors))

    def test_failed_or_malformed_credential_evidence_is_unknown(self) -> None:
        cases = (
            (Result(1, stderr="credential unavailable"), "credential inspection failed"),
            (Result(0, "not headers"), "credential inspection was malformed"),
        )
        for user, message in cases:
            with self.subTest(message=message):
                runner = FakeRunner(pages=[[_repo("visible", 1)]], user=user)

                enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)

                self.assertFalse(enumeration.complete)
                self.assertTrue(any(message in item for item in enumeration.errors))

    def test_malformed_entry_preserves_valid_partial_evidence_as_unknown(self) -> None:
        malformed = _repo("broken", 2)
        del malformed["archived"]
        runner = FakeRunner(pages=[[_repo("valid", 1), malformed]])

        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)

        self.assertFalse(enumeration.complete)
        self.assertEqual(("valid",), tuple(repo.name for repo in enumeration.repositories))
        self.assertIn("missing boolean archived/private metadata", enumeration.errors[0])

    def test_sso_restriction_makes_credential_access_unknown(self) -> None:
        runner = FakeRunner(pages=[[_repo("visible", 1)]], sso="required; url=https://github.com/orgs/example/sso")

        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)

        self.assertFalse(enumeration.complete)
        self.assertTrue(any("SSO authorization restriction" in item for item in enumeration.errors))

    def test_failed_enumeration_is_unknown_not_empty_complete_scope(self) -> None:
        runner = FakeRunner(pages=Result(1, stderr="HTTP 403: Forbidden"))

        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)

        self.assertFalse(enumeration.complete)
        self.assertEqual((), enumeration.repositories)
        self.assertIn("HTTP 403", enumeration.errors[0])


class FakeRunner:
    def __init__(
        self,
        *,
        pages: object,
        membership: object | None = None,
        user: object | None = None,
        scopes: tuple[str, ...] = ("read:org", "repo"),
        include_scope_header: bool = True,
        sso: str = "",
    ) -> None:
        self.pages = pages
        self.membership = membership or {"state": "active", "role": "admin", "user": {"login": "operator"}}
        self.user = user
        self.scopes = scopes
        self.include_scope_header = include_scope_header
        self.sso = sso
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> Result:
        self.commands.append(argv)
        endpoint = argv[-1]
        if endpoint == "/user":
            if isinstance(self.user, Result):
                return self.user
            return Result(0, _included(self.user or {"login": "operator"}, self.scopes, self.include_scope_header))
        if endpoint.endswith("per_page=1"):
            return Result(0, _included([_repo("probe", 99)], self.scopes, self.include_scope_header, self.sso))
        if "/user/memberships/orgs/" in endpoint:
            response = self.membership
        else:
            response = self.pages
        if isinstance(response, Result):
            return response
        return Result(0, json.dumps(response))


def _included(
    body: object,
    scopes: tuple[str, ...],
    include_scope_header: bool,
    sso: str = "",
) -> str:
    headers = ["HTTP/2.0 200 OK"]
    if include_scope_header:
        headers.append(f"X-OAuth-Scopes: {', '.join(scopes)}")
    if sso:
        headers.append(f"X-GitHub-SSO: {sso}")
    return "\n".join((*headers, "", json.dumps(body)))


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
