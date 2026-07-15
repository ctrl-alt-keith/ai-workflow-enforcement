"""Compose existing work-state reports into a timestamped advisory index."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from typing import Callable, Iterable

from . import branch_cleanup, org_pr_issue_scan


ADVISORY_NOTICE = (
    "This report is advisory, stale after capture, not a source of truth, and "
    "not authorization for cleanup or mutation."
)


@dataclass(frozen=True)
class SourceSection:
    name: str
    command: str
    captured_at: str | None
    status: str
    freshness: str
    stale_after_capture: bool
    errors: tuple[str, ...]
    payload: object | None


@dataclass(frozen=True)
class WorkStateIndex:
    schema_version: int
    report_type: str
    advisory: bool
    generated_at: str
    notice: str
    view: str
    organization: str
    repositories: tuple[str, ...]
    sources: tuple[SourceSection, ...]


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Clock = Callable[[], str]
CommandRunner = Callable[[Path, tuple[str, ...]], CommandResult]


def compose_work_state_index(
    *,
    org: str = org_pr_issue_scan.DEFAULT_ORG,
    selected_repos: Iterable[str] = (),
    branch_config_path: Path | None = None,
    audit_stale: bool = False,
    audit_github_prs: bool = False,
    clock: Clock | None = None,
    org_scanner: Callable[..., org_pr_issue_scan.OrgWorkReport] = org_pr_issue_scan.scan_org_work,
    branch_scanner: Callable[..., branch_cleanup.BranchCleanupReport] = branch_cleanup.cleanup_branches,
    worktree_runner: CommandRunner | None = None,
) -> WorkStateIndex:
    """Run independent read-only sources and retain partial or unavailable results."""
    now = clock or _utc_now
    selected = tuple(dict.fromkeys(item.strip() for item in selected_repos if item.strip()))
    sources: list[SourceSection] = []

    org_command = _org_command(org, selected)
    try:
        report = org_scanner(org, selected_repos=selected)
        errors = tuple(report.errors) + tuple(
            f"{repo.full_name}: {error}" for repo in report.repositories for error in repo.skipped
        )
        status = "failed" if report.errors and not report.repositories else "partial" if errors else "available"
        sources.append(
            _section(
                "organization_pr_issue_scan",
                org_command,
                report.finished_at,
                status,
                errors,
                json.loads(org_pr_issue_scan.render_json_report(report)),
            )
        )
    except Exception as exc:  # source boundaries must preserve partial composition
        sources.append(_failed_section("organization_pr_issue_scan", org_command, now(), exc))

    config: branch_cleanup.BranchCleanupConfig | None = None
    branch_command = _branch_command(branch_config_path, audit_stale, audit_github_prs)
    if branch_config_path is None:
        sources.append(_unavailable_section("branch_cleanup_dry_run", branch_command, "branch cleanup config not provided"))
    else:
        try:
            config = branch_cleanup.load_config(branch_config_path)
            config = _select_branch_targets(config, selected)
            report = branch_scanner(
                config,
                apply=False,
                audit_stale=audit_stale,
                audit_github_prs=audit_github_prs,
            )
            errors = tuple(f"{repo.repo}: {repo.skipped}" for repo in report.repos if repo.skipped)
            sources.append(
                _section(
                    "branch_cleanup_dry_run",
                    branch_command,
                    report.finished_at,
                    "partial" if errors else "available",
                    errors,
                    json.loads(branch_cleanup.render_json_report(report)),
                )
            )
        except Exception as exc:
            sources.append(_failed_section("branch_cleanup_dry_run", branch_command, now(), exc))

    if config is None:
        sources.append(
            _unavailable_section(
                "local_git_worktrees",
                "git worktree list --porcelain (per configured repository)",
                "no local repository context available",
            )
        )
    else:
        sources.append(_capture_worktrees(config.repositories, now, worktree_runner or _run_command))

    return WorkStateIndex(
        schema_version=1,
        report_type="work_state_advisory_index",
        advisory=True,
        generated_at=now(),
        notice=ADVISORY_NOTICE,
        view="repository" if selected else "workspace_organization",
        organization=org,
        repositories=selected,
        sources=tuple(sorted(sources, key=lambda item: item.name)),
    )


def render_json_report(index: WorkStateIndex) -> str:
    return json.dumps(_index_to_dict(index), indent=2, sort_keys=True)


def render_markdown_report(index: WorkStateIndex) -> str:
    lines = [
        "# Work-State Advisory Index",
        "",
        f"> {index.notice}",
        "",
        f"- Generated at: `{index.generated_at}`",
        f"- View: `{index.view}`",
        f"- Organization: `{index.organization}`",
        f"- Repositories: {', '.join(f'`{item}`' for item in index.repositories) if index.repositories else 'all visible/configured repositories'}",
        "",
    ]
    for source in index.sources:
        lines.extend(
            [
                f"## {source.name}",
                "",
                f"- Command/tool: `{source.command}`",
                f"- Capture time: `{source.captured_at}`" if source.captured_at else "- Capture time: unavailable",
                f"- Status: `{source.status}`",
                f"- Freshness: `{source.freshness}`",
                f"- Stale after capture: `{'true' if source.stale_after_capture else 'not applicable'}`",
            ]
        )
        if source.errors:
            lines.extend(["- Errors:", *[f"  - {error}" for error in source.errors]])
        lines.extend(["", "```json", json.dumps(source.payload, indent=2, sort_keys=True) if source.payload is not None else "null", "```", ""])
    return "\n".join(lines).rstrip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compose existing work-state evidence into an advisory index.")
    parser.add_argument("--org", default=org_pr_issue_scan.DEFAULT_ORG)
    parser.add_argument("--repo", action="append", default=[], help="Repository name to include; may be repeated.")
    parser.add_argument("--branch-cleanup-config", type=Path, help="Existing branch-cleanup JSON config.")
    parser.add_argument("--audit-stale", action="store_true", help="Pass through existing branch stale-audit reporting.")
    parser.add_argument("--audit-github-prs", action="store_true", help="Pass through existing branch GitHub PR audit reporting.")
    parser.add_argument("--output-format", choices=("markdown", "json"), default="markdown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    index = compose_work_state_index(
        org=args.org,
        selected_repos=args.repo,
        branch_config_path=args.branch_cleanup_config,
        audit_stale=args.audit_stale,
        audit_github_prs=args.audit_github_prs,
    )
    print(render_json_report(index) if args.output_format == "json" else render_markdown_report(index))
    return 0


def _capture_worktrees(targets: tuple[branch_cleanup.RepoTarget, ...], clock: Clock, runner: CommandRunner) -> SourceSection:
    command = "git worktree list --porcelain (per configured repository)"
    captured_at = clock()
    repositories: list[dict[str, object]] = []
    errors: list[str] = []
    for target in sorted(targets, key=lambda item: item.name.lower()):
        result = runner(target.path, ("git", "worktree", "list", "--porcelain"))
        if result.returncode:
            detail = (result.stderr or result.stdout or f"exit {result.returncode}").splitlines()[0]
            errors.append(f"{target.name}: {detail}")
            repositories.append({"name": target.name, "path": str(target.path), "status": "unavailable", "error": detail})
        else:
            repositories.append(
                {
                    "name": target.name,
                    "path": str(target.path),
                    "status": "available",
                    "porcelain": result.stdout,
                }
            )
    available = sum(item["status"] == "available" for item in repositories)
    status = "available" if not errors else "partial" if available else "unavailable"
    return _section(
        "local_git_worktrees",
        command,
        captured_at,
        status,
        tuple(errors),
        {"repositories": repositories} if repositories else None,
    )


def _select_branch_targets(config: branch_cleanup.BranchCleanupConfig, selected: tuple[str, ...]) -> branch_cleanup.BranchCleanupConfig:
    if not selected:
        return config
    names = {item.split("/", 1)[-1] for item in selected}
    targets = tuple(target for target in config.repositories if target.name in names)
    if not targets:
        raise ValueError("selected repositories are absent from branch cleanup config")
    return branch_cleanup.BranchCleanupConfig(targets, config.protected_branches, config.stale_approvals)


def _section(name: str, command: str, captured_at: str | None, status: str, errors: tuple[str, ...], payload: object | None) -> SourceSection:
    return SourceSection(name, command, captured_at, status, "fresh_at_capture" if captured_at else "unavailable", bool(captured_at), errors, payload)


def _failed_section(name: str, command: str, captured_at: str, error: Exception) -> SourceSection:
    return _section(name, command, captured_at, "failed", (f"{type(error).__name__}: {error}",), None)


def _unavailable_section(name: str, command: str, reason: str) -> SourceSection:
    return _section(name, command, None, "unavailable", (reason,), None)


def _org_command(org: str, selected: tuple[str, ...]) -> str:
    parts = ["python3 -m enforcement.org_pr_issue_scan", "--org", org]
    for repo in selected:
        parts.extend(("--repo", repo))
    parts.extend(("--output-format", "json"))
    return " ".join(parts)


def _branch_command(config: Path | None, audit_stale: bool, audit_github_prs: bool) -> str:
    if config is None:
        return "python3 -m enforcement.branch_cleanup (config not provided)"
    parts = ["python3 -m enforcement.branch_cleanup", "--config", str(config)]
    if audit_stale:
        parts.append("--audit-stale")
    if audit_github_prs:
        parts.append("--audit-github-prs")
    parts.extend(("--output-format", "json"))
    return " ".join(parts)


def _index_to_dict(index: WorkStateIndex) -> dict[str, object]:
    return {
        "schema_version": index.schema_version,
        "report_type": index.report_type,
        "advisory": index.advisory,
        "generated_at": index.generated_at,
        "notice": index.notice,
        "view": index.view,
        "organization": index.organization,
        "repositories": list(index.repositories),
        "sources": [
            {
                "name": source.name,
                "command": source.command,
                "captured_at": source.captured_at,
                "status": source.status,
                "freshness": source.freshness,
                "stale_after_capture": source.stale_after_capture,
                "errors": list(source.errors),
                "payload": source.payload,
            }
            for source in index.sources
        ],
    }


def _run_command(cwd: Path, argv: tuple[str, ...]) -> CommandResult:
    try:
        process = subprocess.run(argv, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
    except OSError as exc:
        return CommandResult(1, "", str(exc))
    return CommandResult(process.returncode, process.stdout.rstrip(), process.stderr.rstrip())


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
