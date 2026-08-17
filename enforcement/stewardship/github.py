"""GitHub read and isolated remote-delivery adapter for stewardship."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Iterator
from urllib.parse import quote

from .models import DeliveryProposal, DeliveryResult, RepositoryInfo


_COMMIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")


class GitHubError(RuntimeError):
    """A bounded GitHub or Git operation failed."""


class GitHubGateway:
    """Use a read identity until the explicit delivery boundary."""

    def __init__(self, *, read_token: str, write_token: str | None = None) -> None:
        self._read_token = read_token
        self._write_token = write_token or ""

    @property
    def read_available(self) -> bool:
        return bool(self._read_token)

    @property
    def write_available(self) -> bool:
        return bool(self._write_token)

    def repository_info(self, repository: str) -> RepositoryInfo:
        data = self._gh_json(("api", f"repos/{repository}"), token=self._read_token)
        return RepositoryInfo(
            full_name=str(data["full_name"]),
            default_branch=str(data["default_branch"]),
            archived=bool(data["archived"]),
        )

    def branch_sha(self, repository: str, branch: str) -> str | None:
        endpoint = f"repos/{repository}/git/ref/heads/{quote(branch, safe='')}"
        result = self._run(
            ("gh", "api", endpoint), token=self._read_token, check=False
        )
        if result.returncode == 1 and "HTTP 404" in result.stderr:
            return None
        if result.returncode != 0:
            raise GitHubError(_bounded(result.stderr or result.stdout))
        try:
            data = json.loads(result.stdout)
            sha = data["object"]["sha"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise GitHubError("GitHub returned malformed branch ref JSON") from exc
        return _commit_sha(sha, source="branch ref")

    def resolve_ref(self, repository: str, target_ref: str) -> str | None:
        """Resolve a branch, tag, or commit reference to an exact commit SHA."""

        endpoint = f"repos/{repository}/commits/{quote(target_ref, safe='')}"
        result = self._run(
            ("gh", "api", endpoint), token=self._read_token, check=False
        )
        if result.returncode == 1 and "HTTP 404" in result.stderr:
            return None
        if result.returncode != 0:
            raise GitHubError(_bounded(result.stderr or result.stdout))
        try:
            data = json.loads(result.stdout)
            sha = data["sha"]
        except (KeyError, json.JSONDecodeError) as exc:
            raise GitHubError("GitHub returned malformed commit JSON") from exc
        return _commit_sha(sha, source="commit")

    def hydrate(self, repository: str, base_sha: str, destination: Path) -> None:
        if destination.exists():
            raise GitHubError(f"hydration destination already exists: {destination}")
        with _askpass(self._read_token) as environment:
            clone = subprocess.run(
                (
                    "git",
                    "clone",
                    "--no-checkout",
                    f"https://github.com/{repository}.git",
                    str(destination),
                ),
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
        if clone.returncode != 0:
            raise GitHubError(_bounded(clone.stderr or clone.stdout))
        self._git(destination, ("checkout", "--detach", base_sha))
        actual_sha = self._git(destination, ("rev-parse", "HEAD")).stdout.strip()
        if actual_sha != base_sha:
            raise GitHubError(
                f"hydrated SHA mismatch: expected {base_sha}, received {actual_sha}"
            )
        if self._git(
            destination,
            ("status", "--porcelain=v1", "--untracked-files=all"),
        ).stdout:
            raise GitHubError("fresh hydrated checkout was not clean")

    def existing_stewardship_pr(
        self, repository: str, collision_marker: str
    ) -> str | None:
        pages = self._gh_json(
            (
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/pulls?state=open&per_page=100",
            ),
            token=self._read_token,
        )
        for page in pages:
            for pull_request in page:
                if collision_marker in (pull_request.get("body") or ""):
                    return str(pull_request["html_url"])
        return None

    def deliver(self, repository_root: Path, proposal: DeliveryProposal) -> DeliveryResult:
        """Create one new branch, commit, push, and review-ready PR."""

        mutations: list[dict[str, object]] = []
        if not self.write_available:
            return DeliveryResult(
                success=False,
                error="the repository-scoped stewardship write identity was unavailable",
                mutations=(),
            )
        current_patch = self._git(
            repository_root, ("diff", "--binary", "--full-index")
        ).stdout
        if current_patch != proposal.patch:
            return DeliveryResult(
                success=False,
                error="the working tree patch changed after proposal validation",
                mutations=(),
            )

        try:
            self._git(repository_root, ("switch", "-c", proposal.branch))
            self._git(repository_root, ("add", "--", *proposal.changed_paths))
            staged_patch = self._git(
                repository_root, ("diff", "--cached", "--binary", "--full-index")
            ).stdout
            if staged_patch != proposal.patch:
                raise GitHubError("the staged patch did not match the validated proposal")
            commit = self._git(
                repository_root,
                (
                    "-c",
                    "user.name=ctrl-alt-keith-stewardship[bot]",
                    "-c",
                    "user.email=ctrl-alt-keith-stewardship[bot]@users.noreply.github.com",
                    "commit",
                    "-m",
                    proposal.commit_message,
                ),
            )
            del commit
            commit_sha = self._git(repository_root, ("rev-parse", "HEAD")).stdout.strip()

            with _askpass(self._write_token) as environment:
                pushed = subprocess.run(
                    (
                        "git",
                        "push",
                        "--porcelain",
                        "--set-upstream",
                        "origin",
                        f"HEAD:refs/heads/{proposal.branch}",
                    ),
                    cwd=repository_root,
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
            if pushed.returncode != 0:
                mutations.append({"operation": "push_branch", "success": False})
                raise GitHubError(_bounded(pushed.stderr or pushed.stdout))
            mutations.append({"operation": "push_branch", "success": True})

            try:
                pull_request = self._gh_json(
                    (
                        "api",
                        "--method",
                        "POST",
                        f"repos/{proposal.repository}/pulls",
                        "--raw-field",
                        f"title={proposal.pr_title}",
                        "--raw-field",
                        f"head={proposal.branch}",
                        "--raw-field",
                        f"base={proposal.base_branch}",
                        "--raw-field",
                        f"body={proposal.pr_body}",
                        "--field",
                        "draft=false",
                    ),
                    token=self._write_token,
                )
            except (GitHubError, KeyError, json.JSONDecodeError):
                mutations.append({"operation": "create_pull_request", "success": False})
                raise
            mutations.append({"operation": "create_pull_request", "success": True})
            return DeliveryResult(
                success=True,
                branch=proposal.branch,
                commit_sha=commit_sha,
                pr_url=str(pull_request["html_url"]),
                mutations=tuple(mutations),
            )
        except (GitHubError, KeyError, json.JSONDecodeError) as exc:
            return DeliveryResult(
                success=False,
                error=_bounded(str(exc)),
                mutations=tuple(mutations),
            )

    def _gh_json(self, arguments: tuple[str, ...], *, token: str):
        result = self._run(("gh", *arguments), token=token)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise GitHubError("GitHub returned malformed JSON") from exc

    def _run(
        self,
        command: tuple[str, ...],
        *,
        token: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        _scrub_token_environment(environment)
        environment["GH_TOKEN"] = token
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        if check and result.returncode != 0:
            raise GitHubError(_bounded(result.stderr or result.stdout))
        return result

    def _git(
        self, repository_root: Path, arguments: tuple[str, ...]
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ("git", *arguments),
            cwd=repository_root,
            check=False,
            capture_output=True,
            text=True,
            env=_scrubbed_environment(),
        )
        if result.returncode != 0:
            raise GitHubError(_bounded(result.stderr or result.stdout))
        return result


@contextmanager
def _askpass(token: str) -> Iterator[dict[str, str]]:
    """Provide Git credentials without putting the token in argv or files."""

    with tempfile.TemporaryDirectory(prefix="stewardship-askpass-") as temporary:
        script = Path(temporary) / "askpass.sh"
        script.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *Username*) printf '%s\\n' \"$STEWARDSHIP_GIT_USERNAME\" ;;\n"
            "  *Password*) printf '%s\\n' \"$STEWARDSHIP_GIT_PASSWORD\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        script.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        environment = os.environ.copy()
        _scrub_token_environment(environment)
        environment.update(
            {
                "GIT_ASKPASS": str(script),
                "GIT_TERMINAL_PROMPT": "0",
                "STEWARDSHIP_GIT_USERNAME": "x-access-token",
                "STEWARDSHIP_GIT_PASSWORD": token,
            }
        )
        yield environment


def _scrubbed_environment() -> dict[str, str]:
    environment = os.environ.copy()
    _scrub_token_environment(environment)
    return environment


def _scrub_token_environment(environment: dict[str, str]) -> None:
    for name in ("GH_TOKEN", "STEWARDSHIP_READ_TOKEN", "STEWARDSHIP_WRITE_TOKEN"):
        environment.pop(name, None)


def _bounded(value: str, limit: int = 2000) -> str:
    normalized = value.strip()
    return normalized[:limit] + ("…" if len(normalized) > limit else "")


def _commit_sha(value: object, *, source: str) -> str:
    if not isinstance(value, str) or _COMMIT_SHA_PATTERN.fullmatch(value) is None:
        raise GitHubError(f"GitHub returned malformed {source} SHA")
    return value
