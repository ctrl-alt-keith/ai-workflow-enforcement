"""Read-only GitHub repository settings audit.

The audit compares hosted repository settings with governance docs and config
read from one explicit GitHub source-of-truth ref. Local checkout state is
reported separately so stale local docs cannot silently define expectations for
hosted validation.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Callable, Iterable
from urllib.parse import quote


DEFAULT_SOURCE_REF = "main"
REPORT_TYPE = "repo_settings_audit"
WORKFLOW_SUFFIXES = (".yml", ".yaml")
DEPENDABOT_PATHS = (".github/dependabot.yml", ".github/dependabot.yaml")
LOCAL_GOVERNANCE_PREFIXES = ("docs/", ".github/workflows/")
LOCAL_GOVERNANCE_NAMES = ("AGENTS.md", "README.md", "Makefile") + DEPENDABOT_PATHS


@dataclass(frozen=True)
class GhCommand:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class AuditItem:
    setting: str
    status: str
    expected: str
    actual: str
    source: str
    follow_up: str


@dataclass(frozen=True)
class RemoteSnapshot:
    ref: str
    sha: str
    files: dict[str, str]

    @property
    def governance_text(self) -> str:
        return "\n".join(
            text
            for path, text in sorted(self.files.items())
            if path.endswith(".md") or path == "Makefile"
        )

    @property
    def workflow_paths(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                path
                for path in self.files
                if path.startswith(".github/workflows/") and path.endswith(WORKFLOW_SUFFIXES)
            )
        )

    @property
    def dependabot_path(self) -> str:
        for path in DEPENDABOT_PATHS:
            if path in self.files:
                return path
        return ""


@dataclass(frozen=True)
class HostedState:
    repo: dict[str, object]
    branch_protection: dict[str, object] | None
    rulesets: tuple[dict[str, object], ...] | None
    workflows: tuple[dict[str, object], ...] | None


@dataclass(frozen=True)
class RepoSettingsReport:
    schema_version: int
    report_type: str
    read_only: bool
    repository: str
    source_ref: str
    source_sha: str
    local_repo_root: str
    started_at: str
    finished_at: str
    items: tuple[AuditItem, ...]
    errors: tuple[str, ...] = ()


Runner = Callable[[tuple[str, ...]], GhCommand]


def audit_repo_settings(
    repo: str,
    *,
    source_ref: str = DEFAULT_SOURCE_REF,
    repo_root: Path = Path("."),
    runner: Runner | None = None,
) -> RepoSettingsReport:
    """Build a read-only settings report for one GitHub repository."""
    started = _utc_now()
    gh = runner or _gh
    errors: list[str] = []

    repo_data = _fetch_object(gh, f"/repos/{repo}")
    source_sha = _source_sha(gh, repo, source_ref)
    remote = _fetch_remote_snapshot(gh, repo, source_ref, source_sha)
    hosted = _fetch_hosted_state(gh, repo, repo_data, errors)

    items = [
        _local_head_item(repo_root, remote),
        _working_tree_item(repo_root, remote),
        *_hosted_items(repo_root, remote, hosted),
    ]
    finished = _utc_now()
    return RepoSettingsReport(
        schema_version=1,
        report_type=REPORT_TYPE,
        read_only=True,
        repository=repo,
        source_ref=source_ref,
        source_sha=source_sha,
        local_repo_root=str(repo_root),
        started_at=started,
        finished_at=finished,
        items=tuple(items),
        errors=tuple(errors),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only GitHub repository settings audit.")
    parser.add_argument("--repo", required=True, help="GitHub repository in owner/name form.")
    parser.add_argument(
        "--source-ref",
        default=DEFAULT_SOURCE_REF,
        help="GitHub ref that owns governance expectations. Default: main.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="Local checkout used only for stale-local-state reporting.",
    )
    parser.add_argument(
        "--output-format",
        choices=("text", "json"),
        default="text",
        help="Report format. Default is human-readable text.",
    )
    parser.add_argument(
        "--fail-on-drift",
        action="store_true",
        help="Exit 1 if any audit item reports drift. Unknowns remain advisory.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = audit_repo_settings(
            args.repo,
            source_ref=args.source_ref,
            repo_root=args.repo_root.resolve(),
        )
    except RuntimeError as exc:
        raise SystemExit(
            "repo-settings-audit: hosted inspection failed. "
            "The audit is read-only, but it requires GitHub CLI authentication "
            f"and repository metadata/content access. Details: {exc}"
        ) from exc

    if args.output_format == "json":
        print(render_json_report(report))
    else:
        print(render_text_report(report))
    if args.fail_on_drift and any(item.status == "drift" for item in report.items):
        return 1
    return 0


def render_text_report(report: RepoSettingsReport) -> str:
    summary = _summary(report.items)
    lines = [
        "Repository Settings Audit",
        f"Report type: {report.report_type}",
        "Read-only: yes",
        f"Repository: {report.repository}",
        f"Source-of-truth ref: {report.source_ref}",
        f"Source-of-truth SHA: {report.source_sha}",
        f"Local repo root: {report.local_repo_root}",
        f"Started: {report.started_at}",
        f"Finished: {report.finished_at}",
        f"Summary: match={summary['match']} drift={summary['drift']} unknown={summary['unknown']}",
        "",
    ]
    for error in report.errors:
        lines.append(f"ERROR: {error}")
    if report.errors:
        lines.append("")

    for item in report.items:
        lines.extend(
            [
                f"- {item.setting}",
                f"  status: {item.status}",
                f"  source: {item.source}",
                f"  expected: {item.expected}",
                f"  actual: {item.actual}",
                f"  follow-up: {item.follow_up}",
            ]
        )
    return "\n".join(lines).rstrip()


def render_json_report(report: RepoSettingsReport) -> str:
    data = {
        "schema_version": report.schema_version,
        "report_type": report.report_type,
        "read_only": report.read_only,
        "repository": report.repository,
        "source_ref": report.source_ref,
        "source_sha": report.source_sha,
        "local_repo_root": report.local_repo_root,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "summary": _summary(report.items),
        "errors": list(report.errors),
        "items": [asdict(item) for item in report.items],
    }
    return json.dumps(data, indent=2, sort_keys=True)


def _hosted_items(repo_root: Path, remote: RemoteSnapshot, state: HostedState) -> list[AuditItem]:
    expected = _expectations(remote)
    items = [
        AuditItem(
            setting="repository visibility",
            status=_compare_if_known(expected.visibility, _visibility(state)),
            expected=expected.visibility or "no visibility expectation found in source-of-truth docs",
            actual=_visibility(state),
            source=_source(remote),
            follow_up=(
                "Document intended visibility in repo governance docs before treating this as drift."
                if not expected.visibility
                else "Change hosted visibility only through an explicit human/org-admin action."
            ),
        ),
        AuditItem(
            setting="default branch",
            status=_compare_if_known(expected.default_branch, _default_branch(state)),
            expected=expected.default_branch or "no default-branch expectation found in source-of-truth docs",
            actual=_default_branch(state),
            source=_source(remote),
            follow_up=(
                "Update the hosted default branch or the governance docs after human review."
                if expected.default_branch
                else "Document the expected default branch if this repo needs default-branch validation."
            ),
        ),
        _branch_rules_item(remote, state, expected),
        _required_checks_item(remote, state, expected),
        _pull_request_item(remote, state, expected),
        _strict_checks_item(remote, state, expected),
        _force_delete_item(remote, state, expected),
        _actions_item(remote, state),
        _dependabot_item(remote, expected),
        _merge_methods_item(remote, state, expected),
        _repo_docs_item(remote),
        _validation_item(remote, expected),
    ]
    return items


@dataclass(frozen=True)
class ExpectedSettings:
    visibility: str
    default_branch: str
    branch_rules: bool
    required_checks: tuple[str, ...]
    required_prs: bool
    strict_checks: bool
    restrict_force_push_delete: bool
    dependabot: bool
    merge_methods_documented: bool
    canonical_validation: str


def _expectations(remote: RemoteSnapshot) -> ExpectedSettings:
    text = remote.governance_text
    normalized = text.lower()
    required_checks = _documented_required_checks(text)
    canonical_validation = _canonical_validation(remote)
    if canonical_validation and not required_checks:
        required_checks = ("at least one hosted check for canonical validation",)
    return ExpectedSettings(
        visibility=_documented_visibility(normalized),
        default_branch=_documented_default_branch(normalized),
        branch_rules=_mentions_any(normalized, ("branch protection", "ruleset")),
        required_checks=required_checks,
        required_prs="pull request" in normalized or "pull requests" in normalized,
        strict_checks=_mentions_any(
            normalized,
            (
                "up-to-date",
                "up to date",
                "strict required status",
                "require branches to be up to date",
            ),
        ),
        restrict_force_push_delete=_mentions_any(
            normalized,
            ("force push", "force-push", "deletion restriction", "branch deletion"),
        ),
        dependabot="dependabot" in normalized,
        merge_methods_documented=_mentions_any(
            normalized,
            ("squash merge", "merge commit", "rebase merge", "auto-merge"),
        ),
        canonical_validation=canonical_validation,
    )


def _branch_rules_item(remote: RemoteSnapshot, state: HostedState, expected: ExpectedSettings) -> AuditItem:
    present = _branch_rules_present(state)
    return AuditItem(
        setting="default branch protection or ruleset",
        status=_compare_if_known(expected.branch_rules, present),
        expected=(
            "default branch protection or an active branch ruleset is documented"
            if expected.branch_rules
            else "no branch protection/ruleset expectation found in source-of-truth docs"
        ),
        actual=_describe_branch_rules(state),
        source=_source(remote),
        follow_up=(
            "Review hosted branch protection/rulesets manually; this audit will not enable them."
            if expected.branch_rules and not present
            else "Document branch protection expectations before treating this setting as drift."
        ),
    )


def _required_checks_item(remote: RemoteSnapshot, state: HostedState, expected: ExpectedSettings) -> AuditItem:
    actual_checks = _required_checks(state)
    if expected.required_checks and expected.required_checks != ("at least one hosted check for canonical validation",):
        missing = [check for check in expected.required_checks if check not in actual_checks]
        status = "match" if not missing else "drift"
        follow_up = (
            "Update branch protection/rulesets or the source-of-truth governance docs so required check names match."
            if missing
            else "Confirm these required checks still map to the canonical validation path."
        )
    elif expected.required_checks:
        status = "match" if actual_checks else "drift"
        follow_up = (
            "Document exact hosted check names for stricter comparison."
            if actual_checks
            else "Require the hosted validation check that corresponds to the canonical local validation path."
        )
    else:
        status = "unknown"
        follow_up = "Document required hosted checks before treating check configuration as drift."

    return AuditItem(
        setting="required status checks",
        status=status,
        expected=", ".join(expected.required_checks) if expected.required_checks else "no required-check expectation found",
        actual=", ".join(actual_checks) if actual_checks else "no required hosted status checks detected",
        source=_source(remote),
        follow_up=follow_up,
    )


def _pull_request_item(remote: RemoteSnapshot, state: HostedState, expected: ExpectedSettings) -> AuditItem:
    actual = _pull_request_required(state)
    return AuditItem(
        setting="required pull requests",
        status=_compare_if_known(expected.required_prs, actual),
        expected=(
            "pull requests are documented for changes"
            if expected.required_prs
            else "no pull-request requirement found in source-of-truth docs"
        ),
        actual=_yes_no(actual),
        source=_source(remote),
        follow_up=(
            "Require pull requests through branch protection/rulesets only after explicit human approval."
            if expected.required_prs and not actual
            else "Document pull-request expectations before treating this setting as drift."
        ),
    )


def _strict_checks_item(remote: RemoteSnapshot, state: HostedState, expected: ExpectedSettings) -> AuditItem:
    actual = _strict_status_checks(state)
    return AuditItem(
        setting="branch up-to-date requirement",
        status=_compare_if_known(expected.strict_checks, actual),
        expected=(
            "source-of-truth docs require up-to-date/strict status checks"
            if expected.strict_checks
            else "no up-to-date requirement found in source-of-truth docs"
        ),
        actual=_yes_no_unknown(actual),
        source=_source(remote),
        follow_up=(
            "Enable strict required checks only through an explicit hosted-settings change."
            if expected.strict_checks and actual is False
            else "Document whether branches must be up to date before merge."
        ),
    )


def _force_delete_item(remote: RemoteSnapshot, state: HostedState, expected: ExpectedSettings) -> AuditItem:
    force_allowed = _force_pushes_allowed(state)
    delete_allowed = _deletions_allowed(state)
    restricted = force_allowed is False and delete_allowed is False
    return AuditItem(
        setting="force-push and deletion restrictions",
        status=_compare_if_known(expected.restrict_force_push_delete, restricted),
        expected=(
            "source-of-truth docs mention force-push or deletion restrictions"
            if expected.restrict_force_push_delete
            else "no force-push/deletion restriction expectation found"
        ),
        actual=f"force pushes allowed: {_yes_no_unknown(force_allowed)}; deletions allowed: {_yes_no_unknown(delete_allowed)}",
        source=_source(remote),
        follow_up=(
            "Review hosted protection/ruleset restrictions manually; this audit is report-only."
            if expected.restrict_force_push_delete and not restricted
            else "Document force-push/deletion expectations before treating this as drift."
        ),
    )


def _actions_item(remote: RemoteSnapshot, state: HostedState) -> AuditItem:
    hosted = _workflow_actual(state)
    missing = [path for path in remote.workflow_paths if path not in hosted]
    inactive = [path for path in remote.workflow_paths if hosted.get(path) not in (None, "active")]
    if state.workflows is None:
        status = "unknown"
        actual = "hosted workflow state unavailable"
    else:
        status = "match" if not missing and not inactive else "drift"
        actual = _describe_workflows(hosted)
    return AuditItem(
        setting="Actions workflow presence and state",
        status=status,
        expected=", ".join(remote.workflow_paths) if remote.workflow_paths else "no workflows in source-of-truth ref",
        actual=actual,
        source=_source(remote),
        follow_up=_workflow_follow_up(remote.workflow_paths, missing, inactive),
    )


def _dependabot_item(remote: RemoteSnapshot, expected: ExpectedSettings) -> AuditItem:
    return AuditItem(
        setting="Dependabot config presence",
        status=_compare_if_known(expected.dependabot, bool(remote.dependabot_path)),
        expected=(
            "Dependabot config is expected because source-of-truth docs mention Dependabot"
            if expected.dependabot
            else "no Dependabot expectation found in source-of-truth docs"
        ),
        actual=remote.dependabot_path or "not present in source-of-truth ref",
        source=_source(remote),
        follow_up=(
            "Add Dependabot config in a normal PR only if dependency-update automation is intended."
            if expected.dependabot and not remote.dependabot_path
            else "Hosted Dependabot/security toggles may still require separate org-admin inspection."
        ),
    )


def _merge_methods_item(remote: RemoteSnapshot, state: HostedState, expected: ExpectedSettings) -> AuditItem:
    return AuditItem(
        setting="merge method settings",
        status="unknown" if not expected.merge_methods_documented else "match",
        expected=(
            "source-of-truth docs mention merge method policy"
            if expected.merge_methods_documented
            else "no merge method expectation found in source-of-truth docs"
        ),
        actual=_describe_merge_methods(state),
        source=_source(remote),
        follow_up=(
            "Compare allowed merge methods with documented policy."
            if expected.merge_methods_documented
            else "Document merge method expectations before treating these hosted settings as drift."
        ),
    )


def _repo_docs_item(remote: RemoteSnapshot) -> AuditItem:
    has_agents = "AGENTS.md" in remote.files
    return AuditItem(
        setting="repo-local governance docs",
        status="match" if has_agents else "drift",
        expected="AGENTS.md present at source-of-truth ref",
        actual="present" if has_agents else "missing",
        source=_source(remote),
        follow_up="Add or update AGENTS.md only through a normal repo-local guidance PR.",
    )


def _validation_item(remote: RemoteSnapshot, expected: ExpectedSettings) -> AuditItem:
    return AuditItem(
        setting="canonical local validation",
        status="match" if expected.canonical_validation else "unknown",
        expected="canonical validation entrypoint documented at source-of-truth ref",
        actual=expected.canonical_validation or "not detected",
        source=_source(remote),
        follow_up="Document the repo's canonical validation command before treating hosted checks as fully comparable.",
    )


def _local_head_item(repo_root: Path, remote: RemoteSnapshot) -> AuditItem:
    head = _git_stdout(repo_root, ("git", "rev-parse", "HEAD"))
    if not head:
        return AuditItem(
            setting="local current branch vs source-of-truth ref",
            status="unknown",
            expected=remote.sha,
            actual="local HEAD unavailable",
            source=f"local git HEAD compared with GitHub {remote.ref}",
            follow_up="Run from a local checkout or rely on the remote source-of-truth ref only.",
        )
    return AuditItem(
        setting="local current branch vs source-of-truth ref",
        status="match" if head == remote.sha else "drift",
        expected=remote.sha,
        actual=head,
        source=f"local git HEAD compared with GitHub {remote.ref}",
        follow_up=(
            "Fetch and inspect the source-of-truth ref before using local docs for hosted governance decisions."
            if head != remote.sha
            else "Local HEAD matches the validated source-of-truth commit."
        ),
    )


def _working_tree_item(repo_root: Path, remote: RemoteSnapshot) -> AuditItem:
    head_diffs, worktree_diffs = _local_doc_diffs(repo_root, remote)
    if head_diffs is None or worktree_diffs is None:
        return AuditItem(
            setting="local governance docs vs source-of-truth ref",
            status="unknown",
            expected="local HEAD docs and working-tree docs match source-of-truth governance docs",
            actual="local governance docs unavailable",
            source=f"local files and HEAD compared with GitHub {remote.ref}",
            follow_up="Run from a local checkout to detect stale local governance docs.",
        )
    status = "match" if not head_diffs and not worktree_diffs else "drift"
    details = []
    if head_diffs:
        details.append(f"local current branch docs differ: {', '.join(head_diffs[:8])}")
    else:
        details.append("local current branch docs match source-of-truth docs")
    if worktree_diffs:
        details.append(f"local working-tree docs differ: {', '.join(worktree_diffs[:8])}")
    else:
        details.append("local working-tree docs match source-of-truth docs")
    return AuditItem(
        setting="local governance docs vs source-of-truth ref",
        status=status,
        expected="local current branch docs and working-tree docs match source-of-truth governance docs",
        actual="; ".join(details),
        source=f"local files and HEAD compared with GitHub {remote.ref}",
        follow_up=(
            "Do not validate hosted settings against stale local docs; use the reported source-of-truth ref."
            if status == "drift"
            else "Local governance docs are aligned with the validated remote ref."
        ),
    )


def _fetch_hosted_state(
    runner: Runner,
    repo: str,
    repo_data: dict[str, object],
    errors: list[str],
) -> HostedState:
    default_branch = str(repo_data.get("default_branch") or DEFAULT_SOURCE_REF)
    branch = quote(default_branch, safe="")
    protection = _fetch_optional_object(runner, f"/repos/{repo}/branches/{branch}/protection", errors)
    rulesets = _fetch_optional_collection(runner, f"/repos/{repo}/rulesets?targets=branch", errors)
    workflows_response = _fetch_optional_object(runner, f"/repos/{repo}/actions/workflows", errors)
    workflows = None
    if workflows_response is not None:
        items = workflows_response.get("workflows")
        if isinstance(items, list):
            workflows = tuple(item for item in items if isinstance(item, dict))
        else:
            errors.append("actions workflows response did not include a workflows list")
    return HostedState(
        repo=repo_data,
        branch_protection=protection,
        rulesets=rulesets,
        workflows=workflows,
    )


def _fetch_remote_snapshot(
    runner: Runner,
    repo: str,
    source_ref: str,
    source_sha: str,
) -> RemoteSnapshot:
    tree = _fetch_object(runner, f"/repos/{repo}/git/trees/{source_sha}?recursive=1")
    entries = tree.get("tree")
    if not isinstance(entries, list):
        raise RuntimeError("source-of-truth tree response did not include tree entries")
    paths = sorted(
        str(entry["path"])
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("type") == "blob"
        and isinstance(entry.get("path"), str)
        and _is_governance_path(str(entry["path"]))
    )
    files = {path: _fetch_content(runner, repo, path, source_sha) for path in paths}
    return RemoteSnapshot(ref=source_ref, sha=source_sha, files=files)


def _fetch_content(runner: Runner, repo: str, path: str, ref: str) -> str:
    encoded_path = quote(path, safe="/")
    data = _fetch_object(runner, f"/repos/{repo}/contents/{encoded_path}?ref={quote(ref, safe='')}")
    content = data.get("content")
    encoding = data.get("encoding")
    if not isinstance(content, str) or encoding != "base64":
        return ""
    return base64.b64decode(content).decode("utf-8", errors="replace")


def _source_sha(runner: Runner, repo: str, source_ref: str) -> str:
    data = _fetch_object(runner, f"/repos/{repo}/commits/{quote(source_ref, safe='')}")
    sha = data.get("sha")
    if not isinstance(sha, str) or not sha:
        raise RuntimeError(f"could not resolve source ref {source_ref!r}")
    return sha


def _fetch_object(runner: Runner, endpoint: str) -> dict[str, object]:
    command = runner(("gh", "api", endpoint))
    if command.returncode != 0:
        detail = (command.stderr or command.stdout or f"exit {command.returncode}").splitlines()[0]
        raise RuntimeError(f"gh api {endpoint} failed: {detail}")
    try:
        data = json.loads(command.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh api {endpoint} returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"gh api {endpoint} did not return an object")
    return data


def _fetch_optional_object(
    runner: Runner,
    endpoint: str,
    errors: list[str],
) -> dict[str, object] | None:
    command = runner(("gh", "api", endpoint))
    if command.returncode != 0:
        detail = (command.stderr or command.stdout or f"exit {command.returncode}").splitlines()[0]
        if "404" in detail or "Not Found" in detail:
            return None
        errors.append(f"{endpoint} inaccessible: {detail}")
        return None
    try:
        data = json.loads(command.stdout or "{}")
    except json.JSONDecodeError as exc:
        errors.append(f"{endpoint} returned invalid JSON: {exc}")
        return None
    if not isinstance(data, dict):
        errors.append(f"{endpoint} did not return an object")
        return None
    return data


def _fetch_optional_collection(
    runner: Runner,
    endpoint: str,
    errors: list[str],
) -> tuple[dict[str, object], ...] | None:
    command = runner(("gh", "api", endpoint))
    if command.returncode != 0:
        detail = (command.stderr or command.stdout or f"exit {command.returncode}").splitlines()[0]
        if "404" in detail or "Not Found" in detail:
            return ()
        errors.append(f"{endpoint} inaccessible: {detail}")
        return None
    try:
        data = json.loads(command.stdout or "[]")
    except json.JSONDecodeError as exc:
        errors.append(f"{endpoint} returned invalid JSON: {exc}")
        return None
    if not isinstance(data, list):
        errors.append(f"{endpoint} did not return a list")
        return None
    return tuple(item for item in data if isinstance(item, dict))


def _gh(argv: tuple[str, ...]) -> GhCommand:
    result = subprocess.run(
        argv,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return GhCommand(argv=argv, returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)


def _is_governance_path(path: str) -> bool:
    return path in LOCAL_GOVERNANCE_NAMES or any(path.startswith(prefix) for prefix in LOCAL_GOVERNANCE_PREFIXES)


def _local_doc_diffs(repo_root: Path, remote: RemoteSnapshot) -> tuple[list[str] | None, list[str] | None]:
    if not (repo_root / ".git").exists() and not _git_stdout(repo_root, ("git", "rev-parse", "--git-dir")):
        return None, None
    paths = sorted(set(remote.files) | set(_local_governance_paths(repo_root)))
    head_diffs: list[str] = []
    worktree_diffs: list[str] = []
    for path in paths:
        remote_text = remote.files.get(path)
        head_text = _git_show(repo_root, f"HEAD:{path}")
        worktree_text = _read_local(repo_root / path)
        if head_text != remote_text:
            head_diffs.append(path)
        if worktree_text != remote_text:
            worktree_diffs.append(path)
    return head_diffs, worktree_diffs


def _local_governance_paths(repo_root: Path) -> tuple[str, ...]:
    paths: list[str] = []
    for name in LOCAL_GOVERNANCE_NAMES:
        if (repo_root / name).is_file():
            paths.append(name)
    docs_dir = repo_root / "docs"
    if docs_dir.is_dir():
        paths.extend(str(path.relative_to(repo_root)) for path in docs_dir.glob("*.md"))
    workflows_dir = repo_root / ".github" / "workflows"
    if workflows_dir.is_dir():
        paths.extend(
            str(path.relative_to(repo_root))
            for path in workflows_dir.iterdir()
            if path.is_file() and path.suffix in WORKFLOW_SUFFIXES
        )
    return tuple(sorted(set(paths)))


def _read_local(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _git_show(repo_root: Path, spec: str) -> str | None:
    try:
        result = subprocess.run(
            ("git", "show", spec),
            cwd=repo_root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _git_stdout(repo_root: Path, argv: tuple[str, ...]) -> str:
    try:
        result = subprocess.run(
            argv,
            cwd=repo_root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _documented_visibility(normalized: str) -> str:
    match = re.search(
        r"\b(?:repository|repo|hosted)?\s*visibility\s*(?::|=|is|should be)\s*`?(public|private)\b",
        normalized,
    )
    if match:
        return match.group(1)
    match = re.search(
        r"\bthis repository is (?:a )?`?(public|private)`? repository\b",
        normalized,
    )
    if match:
        return match.group(1)
    return ""


def _documented_default_branch(normalized: str) -> str:
    if "origin/main" in normalized or "target `main`" in normalized or "target main" in normalized:
        return "main"
    for pattern in (
        r"\b(?:default branch|target|base branch)\s*(?::|=|is|should be)\s*`?([a-z0-9._/-]+)`?",
        r"\b(?:target|base branch)\s+`([a-z0-9._/-]+)`",
    ):
        match = re.search(pattern, normalized)
        if match:
            branch = match.group(1).removeprefix("origin/")
            if branch not in {"branch", "branches", "protection"}:
                return branch
    return ""


def _documented_required_checks(text: str) -> tuple[str, ...]:
    checks: list[str] = []
    for line in text.splitlines():
        normalized = line.lower()
        if "required" not in normalized or "check" not in normalized:
            continue
        for value in re.findall(r"`([^`]+)`", line):
            clean = value.strip()
            if clean and clean not in {"make check", "check"}:
                checks.append(clean)
    return tuple(sorted(set(checks)))


def _canonical_validation(remote: RemoteSnapshot) -> str:
    makefile = remote.files.get("Makefile", "")
    agents = remote.files.get("AGENTS.md", "")
    if re.search(r"^check\s*:", makefile, flags=re.MULTILINE):
        return "make check"
    if "make check" in agents:
        return "make check"
    return ""


def _mentions_any(text: str, markers: Iterable[str]) -> bool:
    return any(marker in text for marker in markers)


def _compare_if_known(expected: object, actual: object) -> str:
    if expected in ("", (), None, False):
        return "unknown"
    if actual is None:
        return "unknown"
    return "match" if expected == actual else "drift"


def _visibility(state: HostedState) -> str:
    return "private" if bool(state.repo.get("private")) else "public"


def _default_branch(state: HostedState) -> str:
    return str(state.repo.get("default_branch") or "")


def _branch_rules_present(state: HostedState) -> bool:
    return state.branch_protection is not None or bool(_active_branch_rulesets(state.rulesets))


def _active_branch_rulesets(rulesets: tuple[dict[str, object], ...] | None) -> tuple[dict[str, object], ...]:
    if not rulesets:
        return ()
    return tuple(
        ruleset
        for ruleset in rulesets
        if ruleset.get("target") == "branch" and ruleset.get("enforcement") == "active"
    )


def _rule_types(rulesets: tuple[dict[str, object], ...] | None) -> set[str]:
    types: set[str] = set()
    for ruleset in _active_branch_rulesets(rulesets):
        rules = ruleset.get("rules")
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if isinstance(rule, dict) and isinstance(rule.get("type"), str):
                types.add(str(rule["type"]))
    return types


def _required_checks(state: HostedState) -> tuple[str, ...]:
    checks: list[str] = []
    required = state.branch_protection.get("required_status_checks") if state.branch_protection else None
    if isinstance(required, dict):
        contexts = required.get("contexts")
        if isinstance(contexts, list):
            checks.extend(str(context) for context in contexts)
        check_items = required.get("checks")
        if isinstance(check_items, list):
            checks.extend(
                str(item["context"])
                for item in check_items
                if isinstance(item, dict) and item.get("context")
            )

    for ruleset in _active_branch_rulesets(state.rulesets):
        rules = ruleset.get("rules")
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
                continue
            parameters = rule.get("parameters")
            if not isinstance(parameters, dict):
                continue
            required_items = parameters.get("required_status_checks")
            if not isinstance(required_items, list):
                continue
            checks.extend(
                str(item["context"])
                for item in required_items
                if isinstance(item, dict) and item.get("context")
            )
    return tuple(sorted(set(checks)))


def _pull_request_required(state: HostedState) -> bool:
    if state.branch_protection and state.branch_protection.get("required_pull_request_reviews"):
        return True
    return "pull_request" in _rule_types(state.rulesets)


def _strict_status_checks(state: HostedState) -> bool | None:
    required = state.branch_protection.get("required_status_checks") if state.branch_protection else None
    if isinstance(required, dict) and isinstance(required.get("strict"), bool):
        return bool(required["strict"])
    for ruleset in _active_branch_rulesets(state.rulesets):
        rules = ruleset.get("rules")
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
                continue
            parameters = rule.get("parameters")
            if isinstance(parameters, dict) and isinstance(parameters.get("strict_required_status_checks_policy"), bool):
                return bool(parameters["strict_required_status_checks_policy"])
    return None


def _force_pushes_allowed(state: HostedState) -> bool | None:
    if state.branch_protection:
        value = state.branch_protection.get("allow_force_pushes")
        if isinstance(value, dict) and isinstance(value.get("enabled"), bool):
            return bool(value["enabled"])
    if "non_fast_forward" in _rule_types(state.rulesets):
        return False
    return None


def _deletions_allowed(state: HostedState) -> bool | None:
    if state.branch_protection:
        value = state.branch_protection.get("allow_deletions")
        if isinstance(value, dict) and isinstance(value.get("enabled"), bool):
            return bool(value["enabled"])
    if "deletion" in _rule_types(state.rulesets):
        return False
    return None


def _workflow_actual(state: HostedState) -> dict[str, str]:
    if state.workflows is None:
        return {}
    workflows: dict[str, str] = {}
    for workflow in state.workflows:
        path = workflow.get("path")
        workflow_state = workflow.get("state")
        if isinstance(path, str) and isinstance(workflow_state, str):
            workflows[path] = workflow_state
    return workflows


def _describe_branch_rules(state: HostedState) -> str:
    parts = ["branch protection present" if state.branch_protection else "branch protection absent"]
    rulesets = _active_branch_rulesets(state.rulesets)
    if rulesets:
        names = ", ".join(str(ruleset.get("name") or ruleset.get("id")) for ruleset in rulesets)
        parts.append(f"active branch rulesets: {names}")
    elif state.rulesets is None:
        parts.append("active branch rulesets: unknown")
    else:
        parts.append("active branch rulesets: none detected")
    return "; ".join(parts)


def _describe_workflows(workflows: dict[str, str]) -> str:
    if not workflows:
        return "no hosted workflows detected"
    return ", ".join(f"{path} ({state})" for path, state in sorted(workflows.items()))


def _workflow_follow_up(
    expected_paths: tuple[str, ...],
    missing: list[str],
    inactive: list[str],
) -> str:
    if not expected_paths:
        return "No workflow files exist at the source-of-truth ref; document CI expectations before treating hosted workflow state as drift."
    if missing:
        return f"Verify these workflow files exist on the default branch: {', '.join(missing)}."
    if inactive:
        return f"Review disabled hosted workflows: {', '.join(inactive)}."
    return "Confirm hosted workflow names align with required status check names."


def _describe_merge_methods(state: HostedState) -> str:
    fields = (
        ("merge commits", "allow_merge_commit"),
        ("squash merges", "allow_squash_merge"),
        ("rebase merges", "allow_rebase_merge"),
        ("auto-merge", "allow_auto_merge"),
        ("delete branch on merge", "delete_branch_on_merge"),
    )
    return "; ".join(f"{label}: {_yes_no_unknown(state.repo.get(field))}" for label, field in fields)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _yes_no_unknown(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "unknown"


def _source(remote: RemoteSnapshot) -> str:
    return f"GitHub {remote.ref} ({remote.sha})"


def _summary(items: Iterable[AuditItem]) -> dict[str, int]:
    items = tuple(items)
    return {
        "match": sum(1 for item in items if item.status == "match"),
        "drift": sum(1 for item in items if item.status == "drift"),
        "unknown": sum(1 for item in items if item.status == "unknown"),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
