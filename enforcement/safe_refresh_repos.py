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
import subprocess
import sys
from typing import Iterable

from . import branch_cleanup


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
    status: str
    details: list[str] = field(default_factory=list)
    before: str = ""
    after: str = ""


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
    if scope is not None and scope.completeness == "unknown":
        details = "; ".join(scope.errors) or scope.detail
        raise ValueError(f"safe refresh requires complete provider-backed candidate scope: {details}")
    repositories = tuple(
        RepoTarget(
            target.name,
            target.path,
            target.remote,
            target.default_branch or "main",
        )
        for target in cleanup_config.repositories
    )
    if not repositories:
        raise ValueError("safe refresh config resolved no active, included repositories")
    return SafeRefreshConfig(repositories=repositories)


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
        status=STATUS_BLOCKED,
    )
    expected_upstream = f"{target.remote}/{target.default_branch}"

    if not target.path.exists():
        result.details.append("checkout does not exist")
        return result
    if not target.path.is_dir():
        result.details.append("checkout path is not a directory")
        return result
    git_dir = _git(target.path, "rev-parse", "--git-dir")
    if git_dir.returncode != 0:
        result.details.append(f"path is not a Git repository: {_command_failure_detail(git_dir)}")
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
        result.details.append(f"checkout is detached; expected branch {target.default_branch!r}")
        return result
    if current_branch != target.default_branch:
        result.details.append(f"current branch is {current_branch!r}, expected {target.default_branch!r}")
        return result

    upstream = _git(target.path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
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

    fetch = _git(target.path, "fetch", target.remote)
    if fetch.returncode != 0:
        result.details.append(f"git fetch {target.remote} failed: {_command_failure_detail(fetch)}")
        return result

    pull = _git(target.path, "pull", "--ff-only")
    if pull.returncode != 0:
        result.details.append(f"git pull --ff-only failed: {_command_failure_detail(pull)}")
        return result

    clean_after = _require_clean_worktree(target.path)
    if clean_after is not None:
        result.details.append(f"working tree became unsafe after pull: {clean_after}")
        return result

    after = _git(target.path, "rev-parse", "HEAD")
    if after.returncode != 0:
        result.details.append(f"cannot determine refreshed HEAD: {_command_failure_detail(after)}")
        return result
    result.after = after.stdout.strip()

    remote_head = _git(target.path, "rev-parse", expected_upstream)
    if remote_head.returncode != 0:
        result.details.append(f"cannot determine {expected_upstream}: {_command_failure_detail(remote_head)}")
        return result
    expected_head = remote_head.stdout.strip()
    if result.after != expected_head:
        result.details.append(f"HEAD {result.after} does not match {expected_upstream} {expected_head}")
        return result

    result.status = STATUS_REFRESHED if result.before != result.after else STATUS_ALREADY_CURRENT
    result.details.append("safe refresh complete")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely refresh configured Git repository checkouts.")
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="JSON config with a branch-cleanup-compatible repositories inventory.",
    )
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
        lines.append(f"  expected branch: {repo.default_branch}")
        lines.append(f"  remote: {repo.remote}")
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
                "status": repo.status,
                "before": repo.before,
                "after": repo.after,
                "details": list(repo.details),
            }
            for repo in report.repositories
        ],
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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
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
