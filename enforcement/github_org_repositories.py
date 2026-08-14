"""Canonical, completeness-aware GitHub organization repository discovery."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable, Protocol
from urllib.parse import quote


class CommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[tuple[str, ...]], CommandResult]


@dataclass(frozen=True)
class OrganizationRepository:
    repository_id: int
    name: str
    full_name: str
    archived: bool
    private: bool
    default_branch: str


@dataclass(frozen=True)
class OrganizationRepositoryEnumeration:
    organization: str
    repositories: tuple[OrganizationRepository, ...]
    complete: bool
    detail: str
    errors: tuple[str, ...] = ()


def enumerate_organization_repositories(
    organization: str,
    runner: Runner,
) -> OrganizationRepositoryEnumeration:
    """Enumerate every visible repository and prove completeness conservatively.

    Pagination plus an active organization-owner membership is the repository's
    canonical completeness contract. Any failed, malformed, visibility-limited,
    or otherwise ambiguous evidence leaves the result unknown while preserving
    valid partial repository evidence for read-only callers.
    """
    org = organization.strip()
    if not org:
        raise ValueError("GitHub organization must be non-empty")

    endpoint = f"/orgs/{quote(org, safe='')}/repos?type=all&per_page=100"
    command = runner(("gh", "api", "--paginate", "--slurp", endpoint))
    if command.returncode != 0:
        detail = _failure_detail(command)
        return OrganizationRepositoryEnumeration(
            organization=org,
            repositories=(),
            complete=False,
            detail="GitHub organization repository enumeration failed",
            errors=(f"repository enumeration failed: {detail}",),
        )

    try:
        pages = json.loads(command.stdout or "[]")
    except json.JSONDecodeError as exc:
        return OrganizationRepositoryEnumeration(
            organization=org,
            repositories=(),
            complete=False,
            detail="GitHub organization repository enumeration returned malformed JSON",
            errors=(f"repository enumeration returned invalid JSON: {exc}",),
        )
    if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
        return OrganizationRepositoryEnumeration(
            organization=org,
            repositories=(),
            complete=False,
            detail="GitHub organization repository pagination was malformed",
            errors=("repository enumeration did not return paginated repository lists",),
        )

    repositories: list[OrganizationRepository] = []
    errors: list[str] = []
    identities: set[int] = set()
    locators: set[str] = set()
    for page_number, page in enumerate(pages, start=1):
        for entry_number, entry in enumerate(page, start=1):
            location = f"page {page_number} entry {entry_number}"
            repository, error = _repository(entry, org)
            if error:
                errors.append(f"{location}: {error}")
                continue
            assert repository is not None
            locator_key = repository.full_name.casefold()
            if repository.repository_id in identities or locator_key in locators:
                errors.append(f"{location}: duplicate repository identity or locator")
                continue
            identities.add(repository.repository_id)
            locators.add(locator_key)
            repositories.append(repository)

    membership_endpoint = f"/user/memberships/orgs/{quote(org, safe='')}"
    membership_command = runner(("gh", "api", membership_endpoint))
    membership: object = None
    if membership_command.returncode != 0:
        errors.append(
            "authenticated organization-owner membership could not be verified: "
            f"{_failure_detail(membership_command)}"
        )
    else:
        try:
            membership = json.loads(membership_command.stdout or "{}")
        except json.JSONDecodeError as exc:
            errors.append(f"organization membership returned invalid JSON: {exc}")
        if not isinstance(membership, dict):
            errors.append("organization membership response was not an object")
            membership = None

    state = membership.get("state") if isinstance(membership, dict) else None
    role = membership.get("role") if isinstance(membership, dict) else None
    if membership is not None and (state != "active" or role != "admin"):
        errors.append(
            "authenticated caller is not a proven active organization owner "
            f"(state={state!r}, role={role!r})"
        )

    complete = not errors and state == "active" and role == "admin"
    detail = (
        "all GitHub REST result pages were followed and the authenticated caller is an active organization owner"
        if complete
        else "pagination, repository entries, and complete organization-owner visibility were not all proven"
    )
    return OrganizationRepositoryEnumeration(
        organization=org,
        repositories=tuple(sorted(repositories, key=lambda item: item.full_name.casefold())),
        complete=complete,
        detail=detail,
        errors=tuple(errors),
    )


def _repository(entry: object, organization: str) -> tuple[OrganizationRepository | None, str]:
    if not isinstance(entry, dict):
        return None, "repository entry was not an object"
    repository_id = entry.get("id")
    name = entry.get("name")
    full_name = entry.get("full_name")
    owner = entry.get("owner")
    owner_login = owner.get("login") if isinstance(owner, dict) else None
    archived = entry.get("archived")
    private = entry.get("private")
    default_branch = entry.get("default_branch")
    if not isinstance(repository_id, int) or isinstance(repository_id, bool) or repository_id < 1:
        return None, "repository entry missing a positive numeric id"
    if not isinstance(name, str) or not name or "/" in name:
        return None, "repository entry missing a valid name"
    if (
        not isinstance(full_name, str)
        or full_name.casefold() != f"{organization}/{name}".casefold()
        or not isinstance(owner_login, str)
        or owner_login.casefold() != organization.casefold()
    ):
        return None, "repository entry did not include an organization-owned full_name"
    if not isinstance(archived, bool) or not isinstance(private, bool):
        return None, "repository entry missing boolean archived/private metadata"
    if not isinstance(default_branch, str) or not default_branch:
        return None, "repository entry missing a non-empty default_branch"
    return (
        OrganizationRepository(
            repository_id=repository_id,
            name=name,
            full_name=full_name,
            archived=archived,
            private=private,
            default_branch=default_branch,
        ),
        "",
    )


def _failure_detail(command: CommandResult) -> str:
    return (command.stderr or command.stdout or f"exit {command.returncode}").splitlines()[0]
