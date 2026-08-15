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
    def test_exact_public_private_and_total_match_is_complete(self) -> None:
        runner = FakeRunner(
            pages=[
                [_repo("public", 1)],
                [_repo("private", 2, private=True), _repo("archived", 3, archived=True, private=True)],
            ],
        )
        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)
        self.assertTrue(enumeration.complete)
        self.assertEqual("oauth_scope_bearing", enumeration.credential_kind)
        self.assertEqual("all_repositories", enumeration.credential_access)
        self.assertEqual(("admin:org", "repo"), enumeration.credential_scopes)
        self.assertEqual(1, enumeration.attested_public_repositories)
        self.assertEqual(2, enumeration.attested_private_repositories)
        self.assertEqual(1, enumeration.enumerated_public_repositories)
        self.assertEqual(2, enumeration.enumerated_private_repositories)
        self.assertEqual(3, enumeration.enumerated_total_repositories)
        self.assertEqual("matched", enumeration.count_attestation_status)
        self.assertEqual(("archived", "private", "public"), tuple(repo.name for repo in enumeration.repositories))
        self.assertTrue(enumeration.repositories[0].archived)
        self.assertIn("--paginate", runner.commands[0])
        self.assertIn("--slurp", runner.commands[0])
        self.assertIn(("gh", "api", "--include", "/orgs/ctrl-alt-keith"), runner.commands)

    def test_private_count_mismatch_is_unknown_with_partial_evidence(self) -> None:
        runner = FakeRunner(
            pages=[[_repo("public", 1), _repo("visible-private", 2, private=True)]],
            org_details={"login": "ctrl-alt-keith", "public_repos": 1, "total_private_repos": 2},
        )
        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)
        self.assertFalse(enumeration.complete)
        self.assertEqual(("public", "visible-private"), tuple(repo.name for repo in enumeration.repositories))
        self.assertTrue(any("private repository count" in item for item in enumeration.errors))

    def test_public_count_mismatch_is_unknown(self) -> None:
        runner = FakeRunner(
            pages=[[_repo("visible", 1)]],
            org_details={"login": "ctrl-alt-keith", "public_repos": 2, "total_private_repos": 0},
        )
        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)
        self.assertFalse(enumeration.complete)
        self.assertEqual(("visible",), tuple(repo.name for repo in enumeration.repositories))
        self.assertTrue(any("public repository count" in item for item in enumeration.errors))

    def test_total_count_mismatch_and_internal_visibility_fail_closed(self) -> None:
        runner = FakeRunner(
            pages=[[ _repo("public", 1), _repo("private", 2, private=True), _repo("internal", 3, visibility="internal") ]],
            org_details={"login": "ctrl-alt-keith", "public_repos": 1, "total_private_repos": 1},
        )
        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)
        self.assertFalse(enumeration.complete)
        self.assertEqual(3, enumeration.enumerated_total_repositories)
        self.assertEqual(("internal", "private", "public"), tuple(repo.name for repo in enumeration.repositories))
        self.assertTrue(any("internal-visibility" in item for item in enumeration.errors))
        self.assertTrue(any("total repository count" in item for item in enumeration.errors))

    def test_missing_either_count_is_unknown(self) -> None:
        for missing in ("public_repos", "total_private_repos"):
            with self.subTest(missing=missing):
                details = {"login": "ctrl-alt-keith", "public_repos": 1, "total_private_repos": 0}
                del details[missing]
                enumeration = enumerate_organization_repositories(
                    "ctrl-alt-keith", FakeRunner(pages=[[_repo("visible", 1)]], org_details=details)
                )
                self.assertFalse(enumeration.complete)
                self.assertEqual(("visible",), tuple(repo.name for repo in enumeration.repositories))
                self.assertTrue(any(f"omitted required {missing}" in item for item in enumeration.errors))

    def test_invalid_count_values_are_unknown(self) -> None:
        for field in ("public_repos", "total_private_repos"):
            for value in (-1, True, None, "1", {}, []):
                with self.subTest(field=field, value=value):
                    details = {"login": "ctrl-alt-keith", "public_repos": 1, "total_private_repos": 0}
                    details[field] = value
                    enumeration = enumerate_organization_repositories(
                        "ctrl-alt-keith", FakeRunner(pages=[[_repo("visible", 1)]], org_details=details)
                    )
                    self.assertFalse(enumeration.complete)
                    self.assertEqual(("visible",), tuple(repo.name for repo in enumeration.repositories))
                    self.assertTrue(any(f"invalid {field} count" in item for item in enumeration.errors))

    def test_full_organization_details_failure_or_denial_is_unknown(self) -> None:
        for details in (Result(1, stderr="HTTP 403: Forbidden"), Result(0, stdout="not headers")):
            with self.subTest(details=details):
                enumeration = enumerate_organization_repositories(
                    "ctrl-alt-keith", FakeRunner(pages=[[_repo("visible", 1)]], org_details=details)
                )
                self.assertFalse(enumeration.complete)
                self.assertEqual(("visible",), tuple(repo.name for repo in enumeration.repositories))
                self.assertTrue(any("full organization details" in item for item in enumeration.errors))

    def test_repo_and_read_org_without_admin_org_is_unknown(self) -> None:
        enumeration = enumerate_organization_repositories(
            "ctrl-alt-keith", FakeRunner(pages=[[_repo("visible", 1)]], scopes=("read:org", "repo"))
        )
        self.assertFalse(enumeration.complete)
        self.assertEqual("unknown", enumeration.credential_access)
        self.assertEqual(("visible",), tuple(repo.name for repo in enumeration.repositories))
        self.assertEqual(1, enumeration.attested_public_repositories)
        self.assertEqual(0, enumeration.attested_private_repositories)
        self.assertEqual("unknown", enumeration.count_attestation_status)
        self.assertIn("acting OAuth credential lacks required all-repository scopes: admin:org", enumeration.errors)

    def test_fine_grained_or_app_credential_without_scope_breadth_is_unknown(self) -> None:
        enumeration = enumerate_organization_repositories(
            "ctrl-alt-keith", FakeRunner(pages=[[_repo("visible", 1)]], include_scope_header=False)
        )
        self.assertFalse(enumeration.complete)
        self.assertEqual("unknown", enumeration.credential_kind)
        self.assertEqual(("visible",), tuple(repo.name for repo in enumeration.repositories))
        self.assertTrue(any("fine-grained PAT and GitHub App" in item for item in enumeration.errors))

    def test_non_owner_membership_is_unknown(self) -> None:
        runner = FakeRunner(
            pages=[[_repo("visible", 1)]],
            membership={"state": "active", "role": "member", "user": {"login": "operator"}},
        )
        enumeration = enumerate_organization_repositories("ctrl-alt-keith", runner)
        self.assertFalse(enumeration.complete)
        self.assertEqual("matched", enumeration.count_attestation_status)
        self.assertTrue(any("not a proven active organization owner" in item for item in enumeration.errors))

    def test_failed_or_malformed_acting_credential_evidence_is_unknown(self) -> None:
        cases = (
            (Result(1, stderr="credential unavailable"), "credential inspection failed"),
            (Result(0, stdout="not headers"), "credential inspection was malformed"),
        )
        for user, expected in cases:
            with self.subTest(expected=expected):
                enumeration = enumerate_organization_repositories(
                    "ctrl-alt-keith", FakeRunner(pages=[[_repo("visible", 1)]], user=user)
                )
                self.assertFalse(enumeration.complete)
                self.assertEqual(("visible",), tuple(repo.name for repo in enumeration.repositories))
                self.assertTrue(any(expected in item for item in enumeration.errors))

    def test_malformed_entry_preserves_valid_partial_evidence_as_unknown(self) -> None:
        malformed = _repo("broken", 2)
        del malformed["archived"]
        enumeration = enumerate_organization_repositories(
            "ctrl-alt-keith", FakeRunner(pages=[[_repo("valid", 1), malformed]])
        )
        self.assertFalse(enumeration.complete)
        self.assertEqual(("valid",), tuple(repo.name for repo in enumeration.repositories))
        self.assertIn("missing boolean archived/private metadata", enumeration.errors[0])

    def test_sso_restriction_makes_credential_access_unknown(self) -> None:
        enumeration = enumerate_organization_repositories(
            "ctrl-alt-keith",
            FakeRunner(pages=[[_repo("visible", 1)]], sso="required; url=https://github.com/orgs/example/sso"),
        )
        self.assertFalse(enumeration.complete)
        self.assertEqual(("visible",), tuple(repo.name for repo in enumeration.repositories))
        self.assertTrue(any("SSO authorization restriction" in item for item in enumeration.errors))

    def test_failed_enumeration_is_unknown_not_empty_complete_scope(self) -> None:
        enumeration = enumerate_organization_repositories(
            "ctrl-alt-keith", FakeRunner(pages=Result(1, stderr="HTTP 403: Forbidden"))
        )
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
        scopes: tuple[str, ...] = ("admin:org", "repo"),
        include_scope_header: bool = True,
        sso: str = "",
        org_details: object | None = None,
    ) -> None:
        self.pages = pages
        self.membership = membership or {"state": "active", "role": "admin", "user": {"login": "operator"}}
        self.user = user
        self.scopes = scopes
        self.include_scope_header = include_scope_header
        self.sso = sso
        self.org_details = org_details
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> Result:
        self.commands.append(argv)
        endpoint = argv[-1]
        if endpoint == "/user":
            if isinstance(self.user, Result):
                return self.user
            return Result(0, _included({"login": "operator"}, self.scopes, self.include_scope_header))
        if endpoint == "/orgs/ctrl-alt-keith":
            details = self.org_details if self.org_details is not None else _counts_for_pages(self.pages)
            if isinstance(details, Result):
                return details
            return Result(0, _included(details, self.scopes, self.include_scope_header, self.sso))
        response = self.membership if "/user/memberships/orgs/" in endpoint else self.pages
        if isinstance(response, Result):
            return response
        return Result(0, json.dumps(response))


def _counts_for_pages(pages: object) -> dict[str, object]:
    entries = (
        [entry for page in pages if isinstance(page, list) for entry in page if isinstance(entry, dict)]
        if isinstance(pages, list)
        else []
    )
    return {
        "login": "ctrl-alt-keith",
        "public_repos": sum(entry.get("visibility") == "public" for entry in entries),
        "total_private_repos": sum(entry.get("visibility") == "private" for entry in entries),
    }


def _included(body: object, scopes: tuple[str, ...], include_scope_header: bool, sso: str = "") -> str:
    headers = ["HTTP/2.0 200 OK"]
    if include_scope_header:
        headers.append(f"X-OAuth-Scopes: {', '.join(scopes)}")
    if sso:
        headers.append(f"X-GitHub-SSO: {sso}")
    return "\n".join((*headers, "", json.dumps(body)))


def _repo(
    name: str,
    repository_id: int,
    *,
    archived: bool = False,
    private: bool = False,
    visibility: str | None = None,
) -> dict[str, object]:
    return {
        "id": repository_id,
        "name": name,
        "full_name": f"ctrl-alt-keith/{name}",
        "owner": {"login": "ctrl-alt-keith"},
        "archived": archived,
        "private": private,
        "visibility": visibility or ("private" if private else "public"),
        "default_branch": "main",
    }


if __name__ == "__main__":
    unittest.main()
