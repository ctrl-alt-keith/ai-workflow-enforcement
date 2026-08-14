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
    credential_kind: str = "unknown"
    credential_access: str = "unknown"
    credential_actor: str = ""
    credential_scopes: tuple[str, ...] = ()


def enumerate_organization_repositories(
    organization: str,
    runner: Runner,
) -> OrganizationRepositoryEnumeration:
    """Enumerate every visible repository and prove completeness conservatively.

    The supported completeness contract requires a scope-bearing OAuth
    credential with ``repo`` and ``read:org``, the same credential's active
    organization-owner membership, an organization-access probe without an SSO
    restriction, and complete pagination. Other credential profiles remain
    unknown while valid partial repository evidence is preserved.
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

    credential = _credential_access(org, runner)
    errors.extend(credential.errors)

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
    membership_user = membership.get("user") if isinstance(membership, dict) else None
    membership_login = membership_user.get("login") if isinstance(membership_user, dict) else None
    if membership is not None and (state != "active" or role != "admin"):
        errors.append(
            "authenticated caller is not a proven active organization owner "
            f"(state={state!r}, role={role!r})"
        )
    if membership is not None and membership_login != credential.actor:
        errors.append(
            "organization membership identity does not match the acting credential "
            f"(credential={credential.actor!r}, membership={membership_login!r})"
        )

    complete = (
        not errors
        and credential.access == "all_repositories"
        and state == "active"
        and role == "admin"
    )
    detail = (
        "all GitHub REST result pages were followed by a scope-bearing OAuth credential with "
        "repo/read:org, unrestricted organization access, and active organization-owner identity"
        if complete
        else "pagination, credential repository breadth, repository entries, and active-owner identity were not all proven"
    )
    return OrganizationRepositoryEnumeration(
        organization=org,
        repositories=tuple(sorted(repositories, key=lambda item: item.full_name.casefold())),
        complete=complete,
        detail=detail,
        errors=tuple(errors),
        credential_kind=credential.kind,
        credential_access=credential.access,
        credential_actor=credential.actor,
        credential_scopes=credential.scopes,
    )


@dataclass(frozen=True)
class _CredentialAccess:
    kind: str = "unknown"
    access: str = "unknown"
    actor: str = ""
    scopes: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def _credential_access(organization: str, runner: Runner) -> _CredentialAccess:
    user_command = runner(("gh", "api", "--include", "/user"))
    if user_command.returncode != 0:
        return _CredentialAccess(errors=(f"acting credential inspection failed: {_failure_detail(user_command)}",))
    user_headers, user_body, error = _included_response(user_command.stdout)
    if error:
        return _CredentialAccess(errors=(f"acting credential inspection was malformed: {error}",))
    try:
        user = json.loads(user_body)
    except json.JSONDecodeError as exc:
        return _CredentialAccess(errors=(f"acting credential user response returned invalid JSON: {exc}",))
    actor = user.get("login") if isinstance(user, dict) else None
    if not isinstance(actor, str) or not actor:
        return _CredentialAccess(errors=("acting credential user response did not identify a login",))

    raw_scopes = user_headers.get("x-oauth-scopes")
    if raw_scopes is None:
        return _CredentialAccess(
            actor=actor,
            errors=(
                "acting credential is not a supported scope-bearing OAuth credential; "
                "fine-grained PAT and GitHub App repository breadth is unproven",
            ),
        )
    scopes = tuple(sorted({item.strip() for item in raw_scopes.split(",") if item.strip()}, key=str.casefold))
    missing = tuple(scope for scope in ("repo", "read:org") if scope not in scopes)
    if missing:
        return _CredentialAccess(
            kind="oauth_scope_bearing",
            actor=actor,
            scopes=scopes,
            errors=(f"acting OAuth credential lacks required all-repository scopes: {', '.join(missing)}",),
        )

    endpoint = f"/orgs/{quote(organization, safe='')}/repos?type=all&per_page=1"
    org_command = runner(("gh", "api", "--include", endpoint))
    if org_command.returncode != 0:
        return _CredentialAccess(
            kind="oauth_scope_bearing",
            actor=actor,
            scopes=scopes,
            errors=(f"organization credential-access probe failed: {_failure_detail(org_command)}",),
        )
    org_headers, _org_body, error = _included_response(org_command.stdout)
    if error:
        return _CredentialAccess(
            kind="oauth_scope_bearing",
            actor=actor,
            scopes=scopes,
            errors=(f"organization credential-access probe was malformed: {error}",),
        )
    org_scopes = org_headers.get("x-oauth-scopes")
    if org_scopes is None or {
        item.strip() for item in org_scopes.split(",") if item.strip()
    } != set(scopes):
        return _CredentialAccess(
            kind="oauth_scope_bearing",
            actor=actor,
            scopes=scopes,
            errors=("organization probe did not prove the same OAuth scope set",),
        )
    if org_headers.get("x-github-sso"):
        return _CredentialAccess(
            kind="oauth_scope_bearing",
            actor=actor,
            scopes=scopes,
            errors=("organization probe reported an SSO authorization restriction",),
        )
    return _CredentialAccess(
        kind="oauth_scope_bearing",
        access="all_repositories",
        actor=actor,
        scopes=scopes,
    )


def _included_response(value: str) -> tuple[dict[str, str], str, str]:
    normalized = value.replace("\r\n", "\n")
    header_text, separator, body = normalized.partition("\n\n")
    if not separator or not header_text.startswith("HTTP/"):
        return {}, "", "response did not contain an HTTP header block and body"
    headers: dict[str, str] = {}
    for line in header_text.splitlines()[1:]:
        name, colon, content = line.partition(":")
        if not colon or not name.strip():
            return {}, "", f"invalid HTTP header line: {line!r}"
        headers[name.strip().casefold()] = content.strip()
    if not body.strip():
        return {}, "", "response body was empty"
    return headers, body.strip(), ""


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
