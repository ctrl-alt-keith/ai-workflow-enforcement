"""Verify that a local Git remote is the expected GitHub repository identity."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Callable, Protocol
from urllib.parse import quote, urlparse


class CommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


GitRunner = Callable[[Path, tuple[str, ...]], CommandResult]
ProviderRunner = Callable[[tuple[str, ...]], CommandResult]


@dataclass(frozen=True)
class RepositoryIdentityVerification:
    status: str
    detail: str
    expected_repository: str
    expected_repository_id: int
    remote: str
    fetch_urls: tuple[str, ...] = ()
    push_urls: tuple[str, ...] = ()
    observed_repository: str = ""
    observed_repository_id: int | None = None

    @property
    def verified(self) -> bool:
        return self.status == "verified"


def verify_local_repository_identity(
    path: Path,
    remote: str,
    expected_repository: str,
    expected_repository_id: int,
    *,
    git_runner: GitRunner | None = None,
    provider_runner: ProviderRunner | None = None,
) -> RepositoryIdentityVerification:
    """Require exact current locator and stable provider ID before mutation."""
    run_git = git_runner or _git
    run_provider = provider_runner or _gh
    fetch = run_git(path, ("git", "remote", "get-url", "--all", remote))
    push = run_git(path, ("git", "remote", "get-url", "--push", "--all", remote))
    fetch_urls = _urls(fetch)
    push_urls = _urls(push)
    base = {
        "expected_repository": expected_repository,
        "expected_repository_id": expected_repository_id,
        "remote": remote,
        "fetch_urls": fetch_urls,
        "push_urls": push_urls,
    }
    if fetch.returncode != 0 or push.returncode != 0:
        detail = _failure(fetch if fetch.returncode else push)
        return RepositoryIdentityVerification("unverified", f"configured remote is unavailable: {detail}", **base)
    if len(fetch_urls) != 1 or len(push_urls) != 1:
        return RepositoryIdentityVerification(
            "unverified",
            "configured remote must have exactly one fetch URL and one effective push URL",
            **base,
        )
    fetch_locator = _github_locator(fetch_urls[0])
    push_locator = _github_locator(push_urls[0])
    if fetch_locator is None or push_locator is None:
        return RepositoryIdentityVerification(
            "unverified",
            "configured remote URL is not an unambiguous github.com repository locator",
            **base,
        )
    if fetch_locator.casefold() != push_locator.casefold():
        return RepositoryIdentityVerification(
            "mismatch",
            f"fetch locator {fetch_locator!r} and push locator {push_locator!r} disagree",
            observed_repository=fetch_locator,
            **base,
        )
    if fetch_locator.casefold() != expected_repository.casefold():
        return RepositoryIdentityVerification(
            "mismatch",
            f"configured remote identifies {fetch_locator!r}, expected current locator {expected_repository!r}",
            observed_repository=fetch_locator,
            **base,
        )

    owner, name = expected_repository.split("/", 1)
    endpoint = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
    provider = run_provider(("gh", "api", endpoint))
    if provider.returncode != 0:
        return RepositoryIdentityVerification(
            "unverified",
            f"provider identity lookup failed: {_failure(provider)}",
            observed_repository=fetch_locator,
            **base,
        )
    try:
        payload = json.loads(provider.stdout or "{}")
    except json.JSONDecodeError as exc:
        return RepositoryIdentityVerification(
            "unverified",
            f"provider identity lookup returned invalid JSON: {exc}",
            observed_repository=fetch_locator,
            **base,
        )
    observed_id = payload.get("id") if isinstance(payload, dict) else None
    observed_repository = payload.get("full_name") if isinstance(payload, dict) else None
    if (
        not isinstance(observed_id, int)
        or isinstance(observed_id, bool)
        or not isinstance(observed_repository, str)
    ):
        return RepositoryIdentityVerification(
            "unverified",
            "provider identity lookup omitted numeric id or current full_name",
            observed_repository=fetch_locator,
            **base,
        )
    if observed_id != expected_repository_id or observed_repository.casefold() != expected_repository.casefold():
        return RepositoryIdentityVerification(
            "mismatch",
            "provider identity does not match the enumerated repository ID and current locator",
            observed_repository=observed_repository,
            observed_repository_id=observed_id,
            **base,
        )
    return RepositoryIdentityVerification(
        "verified",
        "fetch URL, push URL, current provider locator, and stable repository ID match",
        observed_repository=observed_repository,
        observed_repository_id=observed_id,
        **base,
    )


def _github_locator(remote_url: str) -> str | None:
    value = remote_url.strip()
    scp = re.fullmatch(r"git@github\.com:([^/]+)/(.+)", value, flags=re.IGNORECASE)
    if scp:
        owner, name = scp.groups()
    else:
        parsed = urlparse(value)
        if parsed.scheme not in {"https", "ssh"} or (parsed.hostname or "").casefold() != "github.com":
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2:
            return None
        owner, name = parts
    name = name.removesuffix(".git")
    if not owner or not name or "/" in owner or "/" in name:
        return None
    return f"{owner}/{name}"


def _urls(command: CommandResult) -> tuple[str, ...]:
    return tuple(line.strip() for line in command.stdout.splitlines() if line.strip())


def _failure(command: CommandResult) -> str:
    return (command.stderr or command.stdout or f"exit {command.returncode}").splitlines()[0]


def _git(path: Path, argv: tuple[str, ...]) -> CommandResult:
    process = subprocess.run(
        argv,
        cwd=path,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=_noninteractive_environment(),
    )
    return process


def _gh(argv: tuple[str, ...]) -> CommandResult:
    process = subprocess.run(
        argv,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=_noninteractive_environment(),
    )
    return process


def _noninteractive_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GH_PROMPT_DISABLED"] = "1"
    return environment
