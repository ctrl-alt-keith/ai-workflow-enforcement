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
    visibility: str = ""


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
    attested_public_repositories: int | None = None
    attested_private_repositories: int | None = None
    enumerated_public_repositories: int = 0
    enumerated_private_repositories: int = 0
    enumerated_total_repositories: int = 0
    count_attestation_status: str = "unknown"
    count_attestation_detail: str = "repository-count attestation was not established"


def enumerate_organization_repositories(
    organization: str,
    runner: Runner,
) -> OrganizationRepositoryEnumeration:
    """Enumerate every visible repository and prove completeness conservatively.

    The supported completeness contract requires a scope-bearing OAuth
    credential with ``repo`` and ``admin:org`` (which subsumes ``read:org``),
    the same credential's active organization-owner membership, full
    organization details without an SSO restriction, matching authoritative
    public/private repository counts, and complete pagination. Other credential
    profiles remain unknown while valid partial repository evidence is
    preserved.
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

    enumerated_public = sum(repository.visibility == "public" for repository in repositories)
    enumerated_private = sum(repository.visibility == "private" for repository in repositories)
    enumerated_total = len(repositories)
    count_errors: list[str] = []
    internal = tuple(repository.full_name for repository in repositories if repository.visibility == "internal")
    if internal:
        count_errors.append(
            "repository-count attestation does not support internal-visibility repositories because "
            "GitHub does not define whether they contribute to public_repos or total_private_repos: "
            + ", ".join(internal)
        )
    if credential.public_repositories is not None:
        if enumerated_public != credential.public_repositories:
            count_errors.append(
                "enumerated public repository count does not match organization public_repos "
                f"(enumerated={enumerated_public}, attested={credential.public_repositories})"
            )
    if credential.private_repositories is not None:
        if enumerated_private != credential.private_repositories:
            count_errors.append(
                "enumerated private repository count does not match organization total_private_repos "
                f"(enumerated={enumerated_private}, attested={credential.private_repositories})"
            )
    if credential.public_repositories is not None and credential.private_repositories is not None:
        attested_total = credential.public_repositories + credential.private_repositories
        if enumerated_total != attested_total:
            count_errors.append(
                "enumerated total repository count does not match attested public/private total "
                f"(enumerated={enumerated_total}, attested={attested_total})"
            )
    errors.extend(count_errors)
    count_attestation_status = (
        "matched"
        if credential.access == "all_repositories"
        and not credential.count_errors and not count_errors
        and credential.public_repositories is not None
        and credential.private_repositories is not None
        else "unknown"
    )
    count_attestation_detail = (
        "fully paginated public, private, and total repository counts match full organization details"
        if count_attestation_status == "matched"
        else "authoritative organization repository counts were unavailable, invalid, unsupported, or did not match"
    )

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
        and count_attestation_status == "matched"
        and state == "active"
        and role == "admin"
    )
    detail = (
        "all GitHub REST result pages and entries matched full organization public/private/total "
        "counts for a scope-bearing OAuth credential with repo/admin:org, unrestricted organization "
        "access, and active organization-owner identity"
        if complete
        else "pagination, credential repository breadth, repository entries, independent repository counts, and active-owner identity were not all proven"
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
        attested_public_repositories=credential.public_repositories,
        attested_private_repositories=credential.private_repositories,
        enumerated_public_repositories=enumerated_public,
        enumerated_private_repositories=enumerated_private,
        enumerated_total_repositories=enumerated_total,
        count_attestation_status=count_attestation_status,
        count_attestation_detail=count_attestation_detail,
    )


@dataclass(frozen=True)
class _CredentialAccess:
    kind: str = "unknown"
    access: str = "unknown"
    actor: str = ""
    scopes: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    public_repositories: int | None = None
    private_repositories: int | None = None
    count_errors: tuple[str, ...] = ()


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
    kind = "oauth_scope_bearing" if raw_scopes is not None else "unknown"
    scopes = (
        tuple(sorted({item.strip() for item in raw_scopes.split(",") if item.strip()}, key=str.casefold))
        if raw_scopes is not None
        else ()
    )
    access_errors: list[str] = []
    if raw_scopes is None:
        access_errors.append(
            "acting credential is not a supported scope-bearing OAuth credential; "
            "fine-grained PAT and GitHub App repository breadth is unproven"
        )
    # GitHub's OAuth scope hierarchy documents read:org beneath admin:org, so
    # admin:org is the conservative single organization scope required here.
    missing = tuple(scope for scope in ("repo", "admin:org") if scope not in scopes)
    if raw_scopes is not None and missing:
        access_errors.append(f"acting OAuth credential lacks required all-repository scopes: {', '.join(missing)}")

    endpoint = f"/orgs/{quote(organization, safe='')}"
    org_command = runner(("gh", "api", "--include", endpoint))
    if org_command.returncode != 0:
        return _CredentialAccess(
            kind=kind,
            actor=actor,
            scopes=scopes,
            errors=(*access_errors, f"full organization details request failed: {_failure_detail(org_command)}"),
        )
    org_headers, _org_body, error = _included_response(org_command.stdout)
    if error:
        return _CredentialAccess(
            kind=kind,
            actor=actor,
            scopes=scopes,
            errors=(*access_errors, f"full organization details response was malformed: {error}"),
        )
    org_scopes = org_headers.get("x-oauth-scopes")
    if raw_scopes is not None and (
        org_scopes is None
        or {item.strip() for item in org_scopes.split(",") if item.strip()} != set(scopes)
    ):
        access_errors.append("full organization details response did not prove the same OAuth scope set")
    if org_headers.get("x-github-sso"):
        access_errors.append("full organization details response reported an SSO authorization restriction")
    try:
        org_details = json.loads(_org_body)
    except json.JSONDecodeError as exc:
        return _CredentialAccess(
            kind=kind,
            actor=actor,
            scopes=scopes,
            errors=(*access_errors, f"full organization details returned invalid JSON: {exc}"),
        )
    if not isinstance(org_details, dict):
        return _CredentialAccess(
            kind=kind,
            actor=actor,
            scopes=scopes,
            errors=(*access_errors, "full organization details response was not an object"),
        )
    org_login = org_details.get("login")
    if not isinstance(org_login, str) or org_login.casefold() != organization.casefold():
        return _CredentialAccess(
            kind=kind,
            actor=actor,
            scopes=scopes,
            errors=(*access_errors, "full organization details did not identify the requested organization"),
        )
    public_repositories, public_error = _repository_count(org_details, "public_repos")
    private_repositories, private_error = _repository_count(org_details, "total_private_repos")
    count_errors = tuple(error for error in (public_error, private_error) if error)
    return _CredentialAccess(
        kind=kind,
        access="all_repositories" if not access_errors else "unknown",
        actor=actor,
        scopes=scopes,
        errors=(*access_errors, *count_errors),
        public_repositories=public_repositories,
        private_repositories=private_repositories,
        count_errors=count_errors,
    )


def _repository_count(details: dict[str, object], field: str) -> tuple[int | None, str]:
    if field not in details:
        return None, f"full organization details omitted required {field} count"
    value = details[field]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None, f"full organization details returned invalid {field} count: {value!r}"
    return value, ""


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
    visibility = entry.get("visibility")
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
    if visibility not in {"public", "private", "internal"}:
        return None, "repository entry missing supported public/private/internal visibility metadata"
    if (visibility == "public" and private) or (visibility == "private" and not private):
        return None, "repository entry contained inconsistent private and visibility metadata"
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
            visibility=visibility,
        ),
        "",
    )


def _failure_detail(command: CommandResult) -> str:
    return (command.stderr or command.stdout or f"exit {command.returncode}").splitlines()[0]
