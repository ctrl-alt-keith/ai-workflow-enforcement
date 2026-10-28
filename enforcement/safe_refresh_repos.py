"""Safely refresh branch-cleanup-resolved Git repository checkouts.

This helper intentionally owns only the deterministic refresh mechanics:
verify the checkout is safe to update, fetch, fast-forward, and report the
result. Inventory is resolved by the existing branch-cleanup JSON contract.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable
from urllib.parse import urlparse

from . import branch_cleanup
from .github_repository_identity import (
    RepositoryIdentityVerification,
    _github_locator,
    _noninteractive_environment,
    verify_local_repository_identity,
)


STATUS_REFRESHED = "refreshed"
STATUS_ALREADY_CURRENT = "already-current"
STATUS_SKIPPED = "skipped"
STATUS_BLOCKED = "blocked"


@dataclass(frozen=True)
class RepoTarget:
    name: str
    path: Path
    remote: str = "origin"
    default_branch: str = "main"
    expected_repository: str = ""
    expected_repository_id: int | None = None
    checkout_branch: str = ""
    catalog_locator_only: bool = False


@dataclass(frozen=True)
class SafeRefreshConfig:
    repositories: tuple[RepoTarget, ...]


@dataclass(frozen=True)
class GitCommand:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass
class RepoRefreshResult:
    name: str
    path: str
    remote: str
    default_branch: str
    checkout_branch: str
    status: str
    details: list[str] = field(default_factory=list)
    before: str = ""
    after: str = ""
    identity: RepositoryIdentityVerification | None = None


@dataclass(frozen=True)
class SafeRefreshReport:
    schema_version: int
    report_type: str
    started_at: str
    finished_at: str
    repositories: tuple[RepoRefreshResult, ...]

    @property
    def blocked(self) -> bool:
        return any(repo.status == STATUS_BLOCKED for repo in self.repositories)


def load_config(path: Path) -> SafeRefreshConfig:
    """Resolve the canonical branch-cleanup inventory for safe refresh."""
    cleanup_config = branch_cleanup.resolve_branch_cleanup_scope(branch_cleanup.load_config(path))
    scope = cleanup_config.scope_reconciliation
    if scope is not None and not scope.mutation_ready:
        details = "; ".join(scope.mutation_blockers) or scope.detail
        raise ValueError(f"safe refresh requires mutation-ready provider-backed candidate scope: {details}")
    repositories = tuple(
        RepoTarget(
            target.name,
            target.path,
            target.remote,
            target.default_branch or "main",
            target.expected_repository,
            target.expected_repository_id,
        )
        for target in cleanup_config.repositories
    )
    if not repositories:
        raise ValueError("safe refresh config resolved no active, included repositories")
    return SafeRefreshConfig(repositories=repositories)


def load_startup_catalog(path: Path, workspace_root: Path, checkout_branch: str = "work") -> SafeRefreshConfig:
    """Use the managed environment's selected, commit-pinned repository catalog."""
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", checkout_branch) or checkout_branch.startswith("-"):
        raise ValueError("invalid checkout branch")
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError("startup catalog must be a nonempty repository array")
    targets: list[RepoTarget] = []
    names: set[str] = set()
    mounts: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("startup catalog contains a non-object entry")
        name, host, mount, commit = (entry.get(key) for key in ("name", "host", "mount_path", "commit"))
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name)
            or host != "github.com"
            or not isinstance(mount, str)
            or mount in {"", ".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", mount)
            or not isinstance(commit, str)
            or not re.fullmatch(r"[0-9a-fA-F]{40}", commit)
        ):
            raise ValueError(f"invalid startup catalog entry: {name!r}")
        if name in names or mount in mounts:
            raise ValueError(f"duplicate startup catalog repository or mount: {name!r}")
        names.add(name)
        mounts.add(mount)
        targets.append(
            RepoTarget(
                name,
                workspace_root / mount,
                expected_repository=name,
                checkout_branch=checkout_branch,
                catalog_locator_only=True,
            )
        )
    return SafeRefreshConfig(tuple(targets))


def safe_refresh_repos(
    config: SafeRefreshConfig,
    *,
    selected_repos: Iterable[str] = (),
) -> SafeRefreshReport:
    """Refresh configured repositories, skipping non-selected entries when requested."""
    selected = frozenset(selected_repos)
    configured = {repo.name for repo in config.repositories}
    unknown = sorted(selected - configured)
    if unknown:
        raise ValueError(f"unknown repositories: {unknown}")

    started = _utc_now()
    results: list[RepoRefreshResult] = []
    for target in config.repositories:
        if selected and target.name not in selected:
            results.append(
                RepoRefreshResult(
                    name=target.name,
                    path=str(target.path),
                    remote=target.remote,
                    default_branch=target.default_branch,
                    checkout_branch=target.checkout_branch or target.default_branch,
                    status=STATUS_SKIPPED,
                    details=["not selected"],
                )
            )
            continue
        results.append(safe_refresh_repo(target))
    finished = _utc_now()
    return SafeRefreshReport(
        schema_version=1,
        report_type="safe_refresh_repos",
        started_at=started,
        finished_at=finished,
        repositories=tuple(results),
    )


def safe_refresh_repo(target: RepoTarget) -> RepoRefreshResult:
    """Safely refresh one repository checkout with fail-closed checks."""
    result = RepoRefreshResult(
        name=target.name,
        path=str(target.path),
        remote=target.remote,
        default_branch=target.default_branch,
        checkout_branch=target.checkout_branch or target.default_branch,
        status=STATUS_BLOCKED,
    )
    expected_upstream = f"{target.remote}/{target.default_branch}"
    expected_branch = target.checkout_branch or target.default_branch

    if not target.path.exists():
        result.details.append("checkout does not exist")
        return result
    if not target.path.is_dir():
        result.details.append("checkout path is not a directory")
        return result
    if target.path.is_symlink():
        result.details.append("checkout path is a symlink")
        return result
    git_dir = _git(target.path, "rev-parse", "--git-dir")
    if git_dir.returncode != 0:
        result.details.append(f"path is not a Git repository: {_command_failure_detail(git_dir)}")
        return result
    top_level = _git(target.path, "rev-parse", "--show-toplevel")
    if top_level.returncode != 0:
        result.details.append(f"cannot determine checkout top level: {_command_failure_detail(top_level)}")
        return result
    if Path(top_level.stdout.strip()).resolve() != target.path.resolve():
        result.details.append("configured checkout path is not the Git worktree top level")
        return result
    if target.catalog_locator_only:
        locator_error = _verify_catalog_locator(target)
        if locator_error:
            result.details.append(locator_error)
            return result
    elif bool(target.expected_repository) != (target.expected_repository_id is not None):
        result.details.append("repository identity requires both expected repository and repository ID")
        return result
    if (
        not target.catalog_locator_only
        and target.expected_repository
        and target.expected_repository_id is not None
    ):
        identity = verify_local_repository_identity(
            target.path,
            target.remote,
            target.expected_repository,
            target.expected_repository_id,
        )
        result.identity = identity
        if not identity.verified:
            result.details.append(f"repository identity {identity.status}: {identity.detail}")
            return result

    clean = _require_clean_worktree(target.path)
    if clean is not None:
        result.details.append(clean)
        return result

    branch = _git(target.path, "branch", "--show-current")
    if branch.returncode != 0:
        result.details.append(f"cannot determine current branch: {_command_failure_detail(branch)}")
        return result
    current_branch = branch.stdout.strip()
    if not current_branch:
        result.details.append(f"checkout is detached; expected branch {expected_branch!r}")
        return result
    if current_branch != expected_branch:
        result.details.append(f"current branch is {current_branch!r}, expected {expected_branch!r}")
        return result

    upstream = _git(target.path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if target.checkout_branch:
        branch_remote = _git(target.path, "config", "--get", f"branch.{expected_branch}.remote")
        branch_merge = _git(target.path, "config", "--get", f"branch.{expected_branch}.merge")
        if upstream.returncode == 0 or branch_remote.returncode == 0 or branch_merge.returncode == 0:
            result.details.append("task checkout branch has upstream configuration; expected none")
            return result
        if any(command.returncode != 1 for command in (branch_remote, branch_merge)):
            result.details.append("cannot verify task checkout upstream configuration")
            return result
    else:
        if upstream.returncode != 0:
            result.details.append(f"cannot determine upstream: {_command_failure_detail(upstream)}")
            return result
        actual_upstream = upstream.stdout.strip()
        if actual_upstream != expected_upstream:
            result.details.append(f"upstream is {actual_upstream!r}, expected {expected_upstream!r}")
            return result

    before = _git(target.path, "rev-parse", "HEAD")
    if before.returncode != 0:
        result.details.append(f"cannot determine HEAD: {_command_failure_detail(before)}")
        return result
    result.before = before.stdout.strip()

    fetch = _git(target.path, "fetch", target.remote, target.default_branch)
    if fetch.returncode != 0:
        result.details.append(f"git fetch {target.remote} failed: {_command_failure_detail(fetch)}")
        return result

    remote_head = _git(target.path, "rev-parse", expected_upstream)
    if remote_head.returncode != 0:
        result.details.append(f"cannot determine {expected_upstream}: {_command_failure_detail(remote_head)}")
        return result
    expected_head = remote_head.stdout.strip()
    ancestor = _git(target.path, "merge-base", "--is-ancestor", result.before, expected_head)
    if ancestor.returncode != 0:
        if ancestor.returncode != 1:
            result.details.append(f"cannot check ancestry: {_command_failure_detail(ancestor)}")
        else:
            reverse = _git(target.path, "merge-base", "--is-ancestor", expected_head, result.before)
            if reverse.returncode == 0:
                result.details.append("local commits ahead of main")
            elif reverse.returncode == 1:
                result.details.append("checkout diverged from main")
            else:
                result.details.append(f"cannot classify ancestry: {_command_failure_detail(reverse)}")
        return result
    clean_before_merge = _require_clean_worktree(target.path)
    head_before_merge = _git(target.path, "rev-parse", "HEAD")
    branch_before_merge = _git(target.path, "branch", "--show-current")
    if (
        clean_before_merge
        or head_before_merge.returncode != 0
        or head_before_merge.stdout.strip() != result.before
        or branch_before_merge.returncode != 0
        or branch_before_merge.stdout.strip() != expected_branch
    ):
        result.details.append("checkout changed during refresh; preserving it")
        return result
    merge = _git(target.path, "merge", "--ff-only", expected_head)
    if merge.returncode != 0:
        result.details.append(f"git merge --ff-only failed: {_command_failure_detail(merge)}")
        return result

    clean_after = _require_clean_worktree(target.path)
    if clean_after is not None:
        result.details.append(f"working tree became unsafe after merge: {clean_after}")
        return result

    after = _git(target.path, "rev-parse", "HEAD")
    if after.returncode != 0:
        result.details.append(f"cannot determine refreshed HEAD: {_command_failure_detail(after)}")
        return result
    result.after = after.stdout.strip()

    if result.after != expected_head:
        result.details.append(f"HEAD {result.after} does not match {expected_upstream} {expected_head}")
        return result

    result.status = STATUS_REFRESHED if result.before != result.after else STATUS_ALREADY_CURRENT
    result.details.append("safe refresh complete")
    return result


def _verify_catalog_locator(target: RepoTarget) -> str | None:
    if not target.expected_repository or target.expected_repository_id is not None:
        return "startup catalog requires a repository locator without provider ID"
    fetch = _git(target.path, "remote", "get-url", "--all", target.remote)
    push = _git(target.path, "remote", "get-url", "--push", "--all", target.remote)
    if fetch.returncode != 0 or push.returncode != 0:
        return "cannot inspect catalog checkout remote"
    fetch_urls = fetch.stdout.splitlines()
    push_urls = push.stdout.splitlines()
    if len(fetch_urls) != 1 or len(push_urls) != 1:
        return "catalog checkout remote must have one fetch and one push URL"
    if any(urlparse(url).username or urlparse(url).password for url in (*fetch_urls, *push_urls)):
        return "catalog checkout remote contains embedded credentials"
    if (
        _github_locator(fetch_urls[0]) != target.expected_repository
        or _github_locator(push_urls[0]) != target.expected_repository
    ):
        return f"catalog checkout remote does not match {target.expected_repository!r}"
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely refresh configured Git repository checkouts.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--config",
        type=Path,
        help="JSON config with a branch-cleanup-compatible repositories inventory.",
    )
    source.add_argument("--startup-catalog", type=Path, help="Managed selected-repository catalog JSON.")
    parser.add_argument("--workspace-root", type=Path, help="Workspace containing catalog checkouts.")
    parser.add_argument("--checkout-branch", default="work", help="Expected task checkout branch for catalog mode.")
    parser.add_argument(
        "--repo",
        action="append",
        help="Repository name to refresh. May be repeated; defaults to all.",
    )
    parser.add_argument(
        "--output-format",
        choices=("text", "json"),
        default="text",
        help="Report format. Default is human-readable text.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.startup_catalog:
            if args.workspace_root is None:
                raise ValueError("--workspace-root is required with --startup-catalog")
            config = load_startup_catalog(args.startup_catalog, args.workspace_root, args.checkout_branch)
        else:
            if args.workspace_root is not None:
                raise ValueError("--workspace-root requires --startup-catalog")
            config = load_config(args.config)
        report = safe_refresh_repos(config, selected_repos=args.repo or ())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.output_format == "json":
        print(render_json_report(report))
    else:
        print(render_text_report(report))
    return 1 if report.blocked else 0


def render_text_report(report: SafeRefreshReport) -> str:
    counts = _status_counts(report)
    lines = [
        "Safe refresh report",
        f"Started: {report.started_at}",
        f"Finished: {report.finished_at}",
        "Summary: "
        f"{STATUS_REFRESHED}={counts[STATUS_REFRESHED]}, "
        f"{STATUS_ALREADY_CURRENT}={counts[STATUS_ALREADY_CURRENT]}, "
        f"{STATUS_SKIPPED}={counts[STATUS_SKIPPED]}, "
        f"{STATUS_BLOCKED}={counts[STATUS_BLOCKED]}",
        "",
    ]
    for repo in report.repositories:
        lines.append(f"{repo.name}: {repo.status}")
        lines.append(f"  path: {repo.path}")
        lines.append(f"  main branch: {repo.default_branch}")
        lines.append(f"  checkout branch: {repo.checkout_branch}")
        lines.append(f"  remote: {repo.remote}")
        if repo.identity is not None:
            lines.append(f"  identity: {repo.identity.status} ({repo.identity.detail})")
        if repo.before:
            lines.append(f"  before: {repo.before}")
        if repo.after:
            lines.append(f"  after: {repo.after}")
        for detail in repo.details:
            lines.append(f"  detail: {detail}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_json_report(report: SafeRefreshReport) -> str:
    return json.dumps(_report_to_json(report), indent=2, sort_keys=True)


def _report_to_json(report: SafeRefreshReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "report_type": report.report_type,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "summary": _status_counts(report),
        "repositories": [
            {
                "name": repo.name,
                "path": repo.path,
                "remote": repo.remote,
                "default_branch": repo.default_branch,
                "checkout_branch": repo.checkout_branch,
                "status": repo.status,
                "before": repo.before,
                "after": repo.after,
                "identity": _identity_to_json(repo.identity),
                "details": list(repo.details),
            }
            for repo in report.repositories
        ],
    }


def _identity_to_json(identity: RepositoryIdentityVerification | None) -> dict[str, object] | None:
    if identity is None:
        return None
    return {
        "status": identity.status,
        "detail": identity.detail,
        "expected_repository": identity.expected_repository,
        "expected_repository_id": identity.expected_repository_id,
        "remote": identity.remote,
        "fetch_urls": list(identity.fetch_urls),
        "push_urls": list(identity.push_urls),
        "observed_repository": identity.observed_repository,
        "observed_repository_id": identity.observed_repository_id,
    }


def _require_clean_worktree(path: Path) -> str | None:
    status = _git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if status.returncode != 0:
        return f"git status failed: {_command_failure_detail(status)}"
    if status.stdout:
        return "working tree is not clean, including untracked files"
    return None


def _git(cwd: Path, *argv: str) -> GitCommand:
    proc = subprocess.run(
        ("git", *argv),
        cwd=cwd,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=_noninteractive_environment(),
    )
    return GitCommand(("git", *argv), proc.returncode, proc.stdout.strip(), proc.stderr.strip())


def _command_failure_detail(command: GitCommand) -> str:
    return command.stderr or command.stdout or f"exit {command.returncode}"


def _status_counts(report: SafeRefreshReport) -> dict[str, int]:
    return {
        status: sum(1 for repo in report.repositories if repo.status == status)
        for status in (STATUS_REFRESHED, STATUS_ALREADY_CURRENT, STATUS_SKIPPED, STATUS_BLOCKED)
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    sys.exit(main())
