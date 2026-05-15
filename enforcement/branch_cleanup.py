"""Dry-run-first branch cleanup planning and execution.

The module is intentionally small and operational. It inspects configured Git
repositories, reports deletion candidates, and only mutates refs when callers
pass ``--apply``.
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


DEFAULT_PROTECTED_BRANCHES = ("main", "master", "trunk", "develop")


@dataclass(frozen=True)
class RepoTarget:
    name: str
    path: Path
    remote: str = "origin"
    default_branch: str | None = None
    conservative: bool = False


@dataclass(frozen=True)
class StaleApproval:
    repo: str
    scope: str
    branch: str
    approved_by: str
    reason: str
    evidence: dict[str, object]


@dataclass(frozen=True)
class BranchCleanupConfig:
    repositories: tuple[RepoTarget, ...]
    protected_branches: tuple[str, ...] = DEFAULT_PROTECTED_BRANCHES
    stale_approvals: tuple[StaleApproval, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "protected_branches", _protected_branches(self.protected_branches))


@dataclass(frozen=True)
class GitCommand:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RefInfo:
    refname: str
    oid: str
    symref: str = ""

    @property
    def branch(self) -> str:
        if self.refname.startswith("refs/heads/"):
            return self.refname.removeprefix("refs/heads/")
        return self.refname


@dataclass(frozen=True)
class BranchAction:
    repo: str
    phase: str
    scope: str
    branch: str
    action: str
    reason: str
    evidence: tuple[str, ...] = ()


@dataclass
class RepoReport:
    repo: str
    path: str
    skipped: str = ""
    default_branch: str = ""
    default_branch_evidence: str = ""
    starting_branch: str = ""
    actions: list[BranchAction] = field(default_factory=list)


@dataclass(frozen=True)
class BranchCleanupReport:
    schema_version: int
    dry_run: bool
    started_at: str
    finished_at: str
    repos: tuple[RepoReport, ...]


def load_config(path: Path) -> BranchCleanupConfig:
    """Load branch cleanup JSON config, resolving repo paths near the config."""
    data = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    repositories = tuple(_repo_target(item, base) for item in data.get("repositories", ()))
    approvals = tuple(_stale_approval(item) for item in data.get("stale_approvals", ()))
    protected = _protected_branches(data.get("protected_branches", ()))
    if not repositories:
        raise ValueError("branch cleanup config must define at least one repository")
    return BranchCleanupConfig(
        repositories=repositories,
        protected_branches=protected,
        stale_approvals=approvals,
    )


def cleanup_branches(config: BranchCleanupConfig, *, apply: bool = False) -> BranchCleanupReport:
    """Run discover, audit, normal cleanup, approved stale cleanup, and report phases."""
    started = _utc_now()
    repo_reports = tuple(_cleanup_repo(config, target, apply=apply) for target in config.repositories)
    finished = _utc_now()
    return BranchCleanupReport(
        schema_version=1,
        dry_run=not apply,
        started_at=started,
        finished_at=finished,
        repos=repo_reports,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan or apply evidence-gated Git branch cleanup.")
    parser.add_argument("--config", required=True, type=Path, help="JSON branch cleanup configuration.")
    parser.add_argument("--apply", action="store_true", help="Mutate refs. Omit for dry-run planning.")
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
        report = cleanup_branches(load_config(args.config), apply=args.apply)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.output_format == "json":
        print(render_json_report(report))
    else:
        print(render_text_report(report))
    return 0


def render_text_report(report: BranchCleanupReport) -> str:
    mode = "dry-run" if report.dry_run else "apply"
    lines = [
        "Branch cleanup report",
        f"Mode: {mode}",
        f"Started: {report.started_at}",
        f"Finished: {report.finished_at}",
        "",
    ]
    for repo in report.repos:
        lines.append(f"{repo.repo}:")
        lines.append(f"  path: {repo.path}")
        if repo.skipped:
            lines.append(f"  skipped: {repo.skipped}")
            lines.append("")
            continue
        lines.append(f"  starting branch: {repo.starting_branch or 'unknown'}")
        lines.append(f"  default branch: {repo.default_branch}")
        if repo.default_branch_evidence:
            lines.append(f"  default branch evidence: {repo.default_branch_evidence}")
        if not repo.actions:
            lines.append("  actions: none")
        for action in repo.actions:
            lines.append(
                f"  - [{action.phase}] {action.scope} {action.branch}: "
                f"{action.action} ({action.reason})"
            )
            for evidence in action.evidence:
                lines.append(f"      evidence: {evidence}")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_json_report(report: BranchCleanupReport) -> str:
    data = {
        "schema_version": report.schema_version,
        "report_type": "branch_cleanup",
        "dry_run": report.dry_run,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "repositories": [
            {
                "repo": repo.repo,
                "path": repo.path,
                "skipped": repo.skipped,
                "starting_branch": repo.starting_branch,
                "default_branch": repo.default_branch,
                "default_branch_evidence": repo.default_branch_evidence,
                "actions": [
                    {
                        "phase": action.phase,
                        "scope": action.scope,
                        "branch": action.branch,
                        "action": action.action,
                        "reason": action.reason,
                        "evidence": list(action.evidence),
                    }
                    for action in repo.actions
                ],
            }
            for repo in report.repos
        ],
    }
    return json.dumps(data, indent=2, sort_keys=True)


def remote_branch_name(refname: str, remote: str = "origin") -> str | None:
    prefix = f"refs/remotes/{remote}/"
    if not refname.startswith(prefix):
        return None
    branch = refname.removeprefix(prefix)
    if not branch or branch == "HEAD" or branch.startswith("HEAD/"):
        return None
    return branch


def _cleanup_repo(config: BranchCleanupConfig, target: RepoTarget, *, apply: bool) -> RepoReport:
    report = RepoReport(repo=target.name, path=str(target.path))
    path = target.path
    if _is_conservative_repo(target):
        report.skipped = "conservative repository requires explicit human handling"
        return report
    if not path.exists():
        report.skipped = "repository path does not exist"
        return report
    if _git(path, "rev-parse", "--git-dir").returncode != 0:
        report.skipped = "path is not a Git repository"
        return report
    dirty = _git(path, "status", "--porcelain=v1", "-z")
    if dirty.returncode != 0:
        report.skipped = "could not inspect working tree state"
        return report
    if dirty.stdout:
        report.skipped = "dirty working tree"
        return report

    if apply:
        fetch = _git(path, "fetch", target.remote, "--prune")
        if fetch.returncode != 0:
            report.skipped = "fetch/prune failed"
            return report

    report.starting_branch = _current_branch(path)
    default_branch, default_evidence = _resolve_default_branch(path, target)
    report.default_branch = default_branch
    report.default_branch_evidence = default_evidence
    default_ref = f"refs/remotes/{target.remote}/{default_branch}"
    if _verify_ref(path, default_ref).returncode != 0:
        report.skipped = f"default remote ref missing: {default_ref}; {default_evidence}"
        return report

    local_refs = _refs(path, "refs/heads")
    remote_refs = _refs(path, f"refs/remotes/{target.remote}")
    worktree_branches = _worktree_branches(path)

    normal, normal_keys = _audit_normal_cleanup(
        config,
        target,
        default_branch,
        default_ref,
        local_refs,
        remote_refs,
        worktree_branches,
    )
    stale = _audit_stale_cleanup(
        config,
        target,
        default_branch,
        default_ref,
        normal_keys,
        local_refs,
        remote_refs,
        worktree_branches,
    )
    report.actions.extend(normal)
    report.actions.extend(stale)
    if apply:
        report.actions = [_apply_action(path, target.remote, default_ref, action) for action in report.actions]
    return report


def _audit_normal_cleanup(
    config: BranchCleanupConfig,
    target: RepoTarget,
    default_branch: str,
    default_ref: str,
    local_refs: tuple[RefInfo, ...],
    remote_refs: tuple[RefInfo, ...],
    worktree_branches: dict[str, str],
) -> tuple[list[BranchAction], set[tuple[str, str]]]:
    actions: list[BranchAction] = []
    delete_keys: set[tuple[str, str]] = set()
    path = target.path
    for ref in local_refs:
        branch = ref.branch
        reason = _branch_skip_reason(
            path,
            branch,
            default_branch,
            config.protected_branches,
        )
        if reason:
            actions.append(_preserved(target.name, "normal_cleanup", "local", branch, reason))
            continue
        if _is_ancestor(path, ref.refname, default_ref):
            worktree_reason = _worktree_delete_skip_reason(path, branch, worktree_branches)
            if worktree_reason:
                actions.append(_preserved(target.name, "normal_cleanup", "local", branch, worktree_reason))
                continue
            delete_keys.add(("local", branch))
            evidence = [f"tip={ref.oid}"]
            if branch in worktree_branches:
                evidence.append(f"worktree={worktree_branches[branch]} clean")
            actions.append(
                BranchAction(
                    target.name,
                    "normal_cleanup",
                    "local",
                    branch,
                    "would_delete",
                    f"Git proves branch is ancestor of {default_ref}",
                    tuple(evidence),
                )
            )

    for ref in remote_refs:
        branch = remote_branch_name(ref.refname, target.remote)
        if branch is None:
            actions.append(_preserved(target.name, "normal_cleanup", "remote", ref.refname, "symbolic or non-branch remote ref"))
            continue
        reason = _branch_skip_reason(
            path,
            branch,
            default_branch,
            config.protected_branches,
        )
        if not reason:
            reason = _worktree_branch_skip_reason(branch, worktree_branches)
        if reason:
            actions.append(_preserved(target.name, "normal_cleanup", "remote", branch, reason))
            continue
        remote_reason = _remote_branch_skip_reason(path, target.remote, branch)
        if remote_reason:
            actions.append(_preserved(target.name, "normal_cleanup", "remote", branch, remote_reason))
            continue
        if _is_ancestor(path, ref.refname, default_ref):
            delete_keys.add(("remote", branch))
            actions.append(
                BranchAction(
                    target.name,
                    "normal_cleanup",
                    "remote",
                    branch,
                    "would_delete",
                    f"Git proves remote ref is ancestor of {default_ref}",
                    (f"tip={ref.oid}",),
                )
            )
    return actions, delete_keys


def _audit_stale_cleanup(
    config: BranchCleanupConfig,
    target: RepoTarget,
    default_branch: str,
    default_ref: str,
    normal_keys: set[tuple[str, str]],
    local_refs: tuple[RefInfo, ...],
    remote_refs: tuple[RefInfo, ...],
    worktree_branches: dict[str, str],
) -> list[BranchAction]:
    actions: list[BranchAction] = []
    path = target.path
    for ref in local_refs:
        branch = ref.branch
        if ("local", branch) in normal_keys:
            continue
        reason = _branch_skip_reason(
            path,
            branch,
            default_branch,
            config.protected_branches,
        )
        if not reason:
            reason = _worktree_branch_skip_reason(branch, worktree_branches)
        if reason:
            actions.append(_preserved(target.name, "stale_cleanup", "local", branch, reason))
            continue
        actions.append(_stale_action(config, target, "local", branch, ref.refname, ref.oid, default_ref))

    for ref in remote_refs:
        branch = remote_branch_name(ref.refname, target.remote)
        if branch is None or ("remote", branch) in normal_keys:
            continue
        reason = _branch_skip_reason(
            path,
            branch,
            default_branch,
            config.protected_branches,
        )
        if not reason:
            reason = _worktree_branch_skip_reason(branch, worktree_branches)
        if reason:
            actions.append(_preserved(target.name, "stale_cleanup", "remote", branch, reason))
            continue
        remote_reason = _remote_branch_skip_reason(path, target.remote, branch)
        if remote_reason:
            actions.append(_preserved(target.name, "stale_cleanup", "remote", branch, remote_reason))
            continue
        actions.append(_stale_action(config, target, "remote", branch, ref.refname, ref.oid, default_ref))
    return actions


def _stale_action(
    config: BranchCleanupConfig,
    target: RepoTarget,
    scope: str,
    branch: str,
    refname: str,
    oid: str,
    default_ref: str,
) -> BranchAction:
    approval = _approval_for(config.stale_approvals, target.name, scope, branch)
    if approval is None:
        return _preserved(
            target.name,
            "stale_cleanup",
            scope,
            branch,
            "non-ancestor ref requires explicit stale approval and evidence",
        )

    valid, evidence = _validate_stale_approval(target.path, approval, refname, oid, default_ref)
    if not valid:
        return _preserved(target.name, "stale_cleanup", scope, branch, "stale approval evidence is incomplete or mismatched", evidence)
    return BranchAction(
        target.name,
        "stale_cleanup",
        scope,
        branch,
        "would_delete",
        f"explicit stale approval from {approval.approved_by}: {approval.reason}",
        evidence,
    )


def _apply_action(path: Path, remote: str, default_ref: str, action: BranchAction) -> BranchAction:
    if action.action != "would_delete":
        return action
    if action.scope == "local" and action.phase == "normal_cleanup":
        worktree_error = _remove_worktree_for_branch(path, action.branch, default_ref)
        if worktree_error:
            return _replace_action(action, "failed", worktree_error)
        result = _git(path, "branch", "-d", "--", action.branch)
    elif action.scope == "local" and action.phase == "stale_cleanup":
        result = _git(path, "branch", "-D", "--", action.branch)
    elif action.scope == "remote":
        result = _git(path, "push", remote, "--delete", "--", action.branch)
    else:
        return _replace_action(action, "failed", "unsupported action scope or phase")

    if result.returncode == 0:
        return _replace_action(action, "deleted", action.reason)
    detail = (result.stderr or result.stdout or "no output").splitlines()[0]
    return _replace_action(action, "failed", detail)


def _validate_stale_approval(
    path: Path,
    approval: StaleApproval,
    refname: str,
    oid: str,
    default_ref: str,
) -> tuple[bool, tuple[str, ...]]:
    evidence = approval.evidence
    kind = str(evidence.get("kind", ""))
    if not approval.approved_by or not approval.reason:
        return False, ("approval requires approved_by and reason",)
    if kind == "github_merged_pr":
        state = str(evidence.get("state", ""))
        merged_at = str(evidence.get("merged_at", ""))
        head_oid = str(evidence.get("head_oid", ""))
        pr_number = evidence.get("pr_number")
        if state != "MERGED" or not merged_at or not head_oid:
            return False, ("GitHub evidence requires state=MERGED, merged_at, and head_oid",)
        if head_oid != oid:
            return False, (f"GitHub head_oid {head_oid} does not match {refname} tip {oid}",)
        return True, (f"GitHub merged PR #{pr_number} head_oid matches {oid}",)
    if kind == "patch_equivalent":
        cherry = _git(path, "cherry", default_ref, refname)
        lines = [line for line in cherry.stdout.splitlines() if line]
        if cherry.returncode == 0 and lines and all(line.startswith("-") for line in lines):
            return True, (f"git cherry proves patch-equivalence to {default_ref}",)
        return False, (f"patch-equivalence evidence did not match {refname}",)
    return False, (f"unsupported stale evidence kind: {kind or 'missing'}",)


def _branch_skip_reason(
    path: Path,
    branch: str,
    default_branch: str,
    protected_branches: Iterable[str],
) -> str:
    if branch == default_branch or branch in set(protected_branches):
        return "protected branch"
    if _has_ambiguous_name(path, branch):
        return "ambiguous ref name"
    return ""


def _worktree_branch_skip_reason(branch: str, worktree_branches: dict[str, str]) -> str:
    if branch in worktree_branches:
        return f"branch is checked out in worktree {worktree_branches[branch]}"
    return ""


def _worktree_delete_skip_reason(path: Path, branch: str, worktree_branches: dict[str, str]) -> str:
    raw_worktree_path = worktree_branches.get(branch)
    if not raw_worktree_path:
        return ""
    worktree_path = Path(raw_worktree_path)
    if worktree_path.resolve() == path.resolve():
        return f"branch is checked out in target worktree {raw_worktree_path}"
    clean, reason = _worktree_is_clean(worktree_path)
    if clean:
        return ""
    return f"{reason}: {raw_worktree_path}"


def _worktree_is_clean(worktree_path: Path) -> tuple[bool, str]:
    if not worktree_path.exists():
        return False, "could not inspect worktree state"
    try:
        status = _git(worktree_path, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    except OSError:
        return False, "could not inspect worktree state"
    if status.returncode != 0:
        return False, "could not inspect worktree state"
    entries = [entry for entry in status.stdout.split("\0") if entry]
    if not entries:
        return True, ""
    if any(entry.startswith("??") for entry in entries):
        return False, "worktree has untracked files"
    return False, "worktree has uncommitted changes"


def _remove_worktree_for_branch(path: Path, branch: str, default_ref: str) -> str:
    worktree_branches = _worktree_branches(path)
    raw_worktree_path = worktree_branches.get(branch)
    if not raw_worktree_path:
        return ""
    reason = _worktree_delete_skip_reason(path, branch, worktree_branches)
    if reason:
        return reason
    branch_ref = f"refs/heads/{branch}"
    if _verify_ref(path, branch_ref).returncode != 0:
        return f"branch is no longer available: {branch_ref}"
    if not _is_ancestor(path, branch_ref, default_ref):
        return f"branch is no longer proven merged into {default_ref}"
    result = _git(path, "worktree", "remove", raw_worktree_path)
    if result.returncode == 0:
        return ""
    return (result.stderr or result.stdout or "git worktree remove failed").splitlines()[0]


def _has_ambiguous_name(path: Path, branch: str) -> bool:
    tag = _git(path, "show-ref", "--verify", "--quiet", f"refs/tags/{branch}")
    local = _git(path, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    return tag.returncode == 0 and local.returncode == 0


def _remote_branch_skip_reason(path: Path, remote: str, branch: str) -> str:
    if _has_remote_ambiguous_name(path, remote, branch):
        return "ambiguous remote ref name"
    return ""


def _has_remote_ambiguous_name(path: Path, remote: str, branch: str) -> bool:
    result = _git(
        path,
        "ls-remote",
        remote,
        f"refs/heads/{branch}",
        f"refs/tags/{branch}",
        f"refs/tags/{branch}^{{}}",
    )
    if result.returncode != 0:
        return True
    heads = False
    tags = False
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        heads = heads or parts[1] == f"refs/heads/{branch}"
        tags = tags or parts[1] in {f"refs/tags/{branch}", f"refs/tags/{branch}^{{}}"}
    return heads and tags


def _refs(path: Path, namespace: str) -> tuple[RefInfo, ...]:
    result = _git(path, "for-each-ref", namespace, "--format=%(refname)%09%(objectname)%09%(symref)")
    refs: list[RefInfo] = []
    if result.returncode != 0:
        return ()
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        refname = parts[0]
        oid = parts[1]
        symref = parts[2] if len(parts) > 2 else ""
        if not refname or symref:
            continue
        refs.append(RefInfo(refname=refname, oid=oid, symref=symref))
    return tuple(refs)


def _worktree_branches(path: Path) -> dict[str, str]:
    result = _git(path, "worktree", "list", "--porcelain")
    branches: dict[str, str] = {}
    if result.returncode != 0:
        return branches
    current_path = ""
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = line.split(" ", 1)[1]
        elif line.startswith("branch refs/heads/"):
            branches[line.removeprefix("branch refs/heads/")] = current_path
    return branches


def _resolve_default_branch(path: Path, target: RepoTarget) -> tuple[str, str]:
    if target.default_branch:
        return target.default_branch, "configured default_branch"
    return _origin_default_branch(path, target.remote)


def _origin_default_branch(path: Path, remote: str) -> tuple[str, str]:
    result = _git(path, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD")
    if result.returncode == 0 and result.stdout.startswith(f"{remote}/"):
        return result.stdout.split("/", 1)[1].strip(), f"resolved from refs/remotes/{remote}/HEAD"
    if result.returncode == 1:
        return "main", f"remote HEAD missing for {remote}; fell back to main"
    detail = result.stderr or result.stdout or f"exit {result.returncode}"
    return "main", f"symbolic-ref lookup failed unexpectedly for {remote}: {detail}; fell back to main"


def _current_branch(path: Path) -> str:
    result = _git(path, "branch", "--show-current")
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "(detached)"


def _is_ancestor(path: Path, ancestor: str, descendant: str) -> bool:
    return _git(path, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


def _verify_ref(path: Path, ref: str) -> GitCommand:
    return _git(path, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")


def _git(cwd: Path, *argv: str) -> GitCommand:
    process = subprocess.run(
        ("git",) + tuple(argv),
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    return GitCommand(
        argv=("git",) + tuple(argv),
        returncode=process.returncode,
        stdout=process.stdout.strip(),
        stderr=process.stderr.strip(),
    )


def _repo_target(item: dict[str, object], base: Path) -> RepoTarget:
    name = str(item.get("name", ""))
    raw_path = item.get("path")
    if not name or raw_path is None:
        raise ValueError("each repository requires name and path")
    path = Path(str(raw_path))
    if not path.is_absolute():
        path = (base / path).resolve()
    return RepoTarget(
        name=name,
        path=path,
        remote=str(item.get("remote", "origin")),
        default_branch=str(item["default_branch"]) if item.get("default_branch") else None,
        conservative=bool(item.get("conservative", False)),
    )


def _stale_approval(item: dict[str, object]) -> StaleApproval:
    scope = str(item.get("scope", ""))
    if scope not in {"local", "remote"}:
        raise ValueError(f"stale approval scope must be 'local' or 'remote': {scope or 'missing'}")
    return StaleApproval(
        repo=str(item.get("repo", "")),
        scope=scope,
        branch=str(item.get("branch", "")),
        approved_by=str(item.get("approved_by", "")),
        reason=str(item.get("reason", "")),
        evidence=dict(item.get("evidence", {})),
    )


def _approval_for(
    approvals: Iterable[StaleApproval],
    repo: str,
    scope: str,
    branch: str,
) -> StaleApproval | None:
    for approval in approvals:
        if approval.repo == repo and approval.scope == scope and approval.branch == branch:
            return approval
    return None


def _is_conservative_repo(target: RepoTarget) -> bool:
    return target.conservative


def _protected_branches(configured: Iterable[str]) -> tuple[str, ...]:
    protected: list[str] = []
    for branch in DEFAULT_PROTECTED_BRANCHES + tuple(configured):
        if branch and branch not in protected:
            protected.append(branch)
    return tuple(protected)


def _preserved(
    repo: str,
    phase: str,
    scope: str,
    branch: str,
    reason: str,
    evidence: tuple[str, ...] = (),
) -> BranchAction:
    return BranchAction(repo, phase, scope, branch, "preserved", reason, evidence)


def _replace_action(action: BranchAction, new_action: str, reason: str) -> BranchAction:
    return BranchAction(
        repo=action.repo,
        phase=action.phase,
        scope=action.scope,
        branch=action.branch,
        action=new_action,
        reason=reason,
        evidence=action.evidence,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
