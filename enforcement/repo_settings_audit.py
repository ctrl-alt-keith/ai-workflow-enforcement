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
ORG_REPORT_TYPE = "org_repo_settings_audit"
CENTRAL_POLICY_PATH = Path(__file__).resolve().parent.parent / "config" / "repo-settings-policy.json"
WORKFLOW_SUFFIXES = (".yml", ".yaml")
DEPENDABOT_PATHS = (".github/dependabot.yml", ".github/dependabot.yaml")
PYTHON_PACKAGE_METADATA_PATHS = ("pyproject.toml", "setup.cfg", "setup.py")
LOCAL_GOVERNANCE_PREFIXES = ("docs/", ".github/workflows/")
LOCAL_GOVERNANCE_NAMES = ("AGENTS.md", "README.md", "Makefile") + DEPENDABOT_PATHS
SOURCE_METADATA_NAMES = PYTHON_PACKAGE_METADATA_PATHS
LOCAL_SOURCE_SETTINGS = frozenset(
    {
        "local current branch vs source-of-truth ref",
        "local governance docs vs source-of-truth ref",
    }
)


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
    branch_protection_error: str = ""
    branch_protection_incomplete: tuple[str, ...] = ()
    branch_protection_retried: bool = False
    rulesets_error: str = ""
    rulesets_retried: bool = False
    workflows_error: str = ""
    workflows_retried: bool = False


@dataclass(frozen=True)
class OptionalObjectResult:
    value: dict[str, object] | None = None
    error: str = ""
    retried: bool = False
    absent: bool = False


@dataclass(frozen=True)
class OptionalCollectionResult:
    value: tuple[dict[str, object], ...] | None = None
    error: str = ""
    retried: bool = False
    absent: bool = False


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


@dataclass(frozen=True)
class OrgRepoSettingsReport:
    schema_version: int
    report_type: str
    read_only: bool
    org: str
    source_ref: str
    local_source_mode: str
    workspace_root: str
    started_at: str
    finished_at: str
    repositories: tuple[str, ...]
    reports: tuple[RepoSettingsReport, ...]
    errors: tuple[str, ...] = ()


Runner = Callable[[tuple[str, ...]], GhCommand]


def audit_repo_settings(
    repo: str,
    *,
    source_ref: str = DEFAULT_SOURCE_REF,
    repo_root: Path | None = Path("."),
    include_local_source: bool = True,
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

    local_items = []
    if include_local_source:
        local_root = repo_root or Path(".")
        local_items = [_local_head_item(local_root, remote), _working_tree_item(local_root, remote)]

    items = [
        *local_items,
        *_hosted_items(remote, hosted),
    ]
    finished = _utc_now()
    return RepoSettingsReport(
        schema_version=1,
        report_type=REPORT_TYPE,
        read_only=True,
        repository=repo,
        source_ref=source_ref,
        source_sha=source_sha,
        local_repo_root=str(repo_root) if include_local_source and repo_root is not None else "",
        started_at=started,
        finished_at=finished,
        items=tuple(items),
        errors=tuple(errors),
    )


def audit_org_repo_settings(
    org: str,
    *,
    source_ref: str = DEFAULT_SOURCE_REF,
    workspace_root: Path | None = None,
    runner: Runner | None = None,
) -> OrgRepoSettingsReport:
    """Build a read-only settings report for all visible repositories in an organization."""
    started = _utc_now()
    gh = runner or _gh
    repositories, errors = _fetch_org_repositories(org, gh)
    reports: list[RepoSettingsReport] = []
    include_local_source = workspace_root is not None
    for repo in repositories:
        try:
            reports.append(
                audit_repo_settings(
                    repo,
                    source_ref=source_ref,
                    repo_root=_org_local_repo_root(repo, workspace_root),
                    include_local_source=include_local_source,
                    runner=gh,
                )
            )
        except RuntimeError as exc:
            errors.append(f"{repo}: {exc}")
    finished = _utc_now()
    return OrgRepoSettingsReport(
        schema_version=1,
        report_type=ORG_REPORT_TYPE,
        read_only=True,
        org=org,
        source_ref=source_ref,
        local_source_mode="workspace_root" if include_local_source else "not_checked",
        workspace_root=str(workspace_root) if workspace_root else "",
        started_at=started,
        finished_at=finished,
        repositories=tuple(repositories),
        reports=tuple(reports),
        errors=tuple(errors),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only GitHub repository settings audit.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--repo", help="GitHub repository in owner/name form.")
    target.add_argument("--org", help="GitHub organization whose visible repositories should be audited.")
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
        "--workspace-root",
        type=Path,
        help=(
            "Workspace root used in --org mode to scope local-source checks to "
            "<workspace-root>/<repo-name>. Without this, org audits are hosted-only."
        ),
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
        help="Exit 1 if hosted governance reports drift. Local-source drift remains advisory.",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Exit 1 if runtime errors or incomplete repository coverage are reported.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.repo:
            report = audit_repo_settings(
                args.repo,
                source_ref=args.source_ref,
                repo_root=args.repo_root.resolve(),
            )
        else:
            report = audit_org_repo_settings(
                args.org,
                source_ref=args.source_ref,
                workspace_root=args.workspace_root.resolve() if args.workspace_root else None,
            )
    except RuntimeError as exc:
        raise SystemExit(
            "repo-settings-audit: hosted inspection failed. "
            "The audit is read-only, but it requires GitHub CLI authentication "
            f"and repository metadata/content access. Details: {exc}"
        ) from exc

    if args.output_format == "json":
        print(render_json_report(report) if isinstance(report, RepoSettingsReport) else render_org_json_report(report))
    else:
        print(render_text_report(report) if isinstance(report, RepoSettingsReport) else render_org_text_report(report))
    if args.fail_on_error and _has_runtime_errors(report):
        return 1
    if args.fail_on_drift and _has_hosted_drift(report):
        return 1
    return 0


def render_text_report(report: RepoSettingsReport) -> str:
    summary = _summary(report.items)
    hosted_summary = _summary(_hosted_governance_items(report.items))
    local_summary = _summary(_local_source_items(report.items))
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
        (
            "Hosted governance summary: "
            f"match={hosted_summary['match']} drift={hosted_summary['drift']} unknown={hosted_summary['unknown']}"
        ),
        (
            "Local source summary: "
            f"match={local_summary['match']} drift={local_summary['drift']} unknown={local_summary['unknown']}"
        ),
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
    hosted_items = _hosted_governance_items(report.items)
    local_items = _local_source_items(report.items)
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
        "hosted_governance_summary": _summary(hosted_items),
        "local_source_summary": _summary(local_items),
        "errors": list(report.errors),
        "items": [asdict(item) for item in report.items],
    }
    return json.dumps(data, indent=2, sort_keys=True)


def render_org_text_report(report: OrgRepoSettingsReport) -> str:
    hosted_summary = _org_summary(report.reports, hosted=True)
    local_summary = _org_summary(report.reports, hosted=False)
    lines = [
        "Organization Repository Settings Audit",
        f"Report type: {report.report_type}",
        "Read-only: yes",
        f"Organization: {report.org}",
        f"Source-of-truth ref: {report.source_ref}",
        f"Local source mode: {report.local_source_mode}",
        f"Workspace root: {report.workspace_root or 'not configured'}",
        f"Started: {report.started_at}",
        f"Finished: {report.finished_at}",
        f"Repositories discovered: {len(report.repositories)}",
        f"Repositories audited: {len(report.reports)}",
        (
            "Hosted governance summary: "
            f"match={hosted_summary['match']} drift={hosted_summary['drift']} unknown={hosted_summary['unknown']}"
        ),
        (
            "Local source summary: "
            f"match={local_summary['match']} drift={local_summary['drift']} unknown={local_summary['unknown']}"
        ),
        "",
    ]
    for error in report.errors:
        lines.append(f"ERROR: {error}")
    if report.errors:
        lines.append("")

    for repo_report in report.reports:
        hosted = _summary(_hosted_governance_items(repo_report.items))
        local = _summary(_local_source_items(repo_report.items))
        lines.extend(
            [
                f"- {repo_report.repository}",
                f"  source SHA: {repo_report.source_sha}",
                f"  hosted governance: match={hosted['match']} drift={hosted['drift']} unknown={hosted['unknown']}",
                f"  local source: match={local['match']} drift={local['drift']} unknown={local['unknown']}",
                f"  local repo root: {repo_report.local_repo_root or 'not checked'}",
            ]
        )
        hosted_drift = [item.setting for item in _hosted_governance_items(repo_report.items) if item.status == "drift"]
        hosted_unknown = [item.setting for item in _hosted_governance_items(repo_report.items) if item.status == "unknown"]
        local_drift = [item.setting for item in _local_source_items(repo_report.items) if item.status == "drift"]
        if hosted_drift:
            lines.append(f"  hosted governance drift: {', '.join(hosted_drift)}")
        if hosted_unknown:
            lines.append(f"  hosted governance unknown: {', '.join(hosted_unknown)}")
        if local_drift:
            lines.append(f"  local source drift: {', '.join(local_drift)}")
        for error in repo_report.errors:
            lines.append(f"  audit note: {error}")
    return "\n".join(lines).rstrip()


def render_org_json_report(report: OrgRepoSettingsReport) -> str:
    data = {
        "schema_version": report.schema_version,
        "report_type": report.report_type,
        "read_only": report.read_only,
        "org": report.org,
        "source_ref": report.source_ref,
        "local_source_mode": report.local_source_mode,
        "workspace_root": report.workspace_root,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "repositories": list(report.repositories),
        "summary": {
            "repository_count": len(report.repositories),
            "audited_repository_count": len(report.reports),
        },
        "hosted_governance_summary": _org_summary(report.reports, hosted=True),
        "local_source_summary": _org_summary(report.reports, hosted=False),
        "errors": list(report.errors),
        "reports": [json.loads(render_json_report(repo_report)) for repo_report in report.reports],
    }
    return json.dumps(data, indent=2, sort_keys=True)


def _hosted_items(remote: RemoteSnapshot, state: HostedState) -> list[AuditItem]:
    expected = _expectations(state.repo, remote)
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
        _review_admin_item(remote, state, expected),
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
    require_status_checks: bool | None
    required_prs: bool | None
    strict_checks: bool | None
    force_pushes_allowed: bool | None
    deletions_allowed: bool | None
    required_approving_reviews: int | None
    admin_bypass: bool | None
    dependabot: "ExpectedDependabot"
    merge_methods: dict[str, bool]
    merge_methods_documented: bool
    canonical_validation: str


@dataclass(frozen=True)
class CentralPolicy:
    visibility: str
    default_branch: str
    required_checks: tuple[str, ...]
    require_status_checks: bool | None
    required_prs: bool | None
    strict_checks: bool | None
    force_pushes_allowed: bool | None
    deletions_allowed: bool | None
    required_approving_reviews: int | None
    admin_bypass: bool | None
    merge_methods: dict[str, bool]
    dependabot: "DependabotPolicy"


@dataclass(frozen=True)
class DependabotPolicy:
    enabled: str
    ecosystems: tuple[str, ...]
    schedule: str


@dataclass(frozen=True)
class ExpectedDependabot:
    enabled: bool
    ecosystems: tuple[str, ...]
    schedule: str
    reason: str


@dataclass(frozen=True)
class DependabotUpdate:
    ecosystem: str
    interval: str


@dataclass(frozen=True)
class DependabotConfig:
    updates: tuple[DependabotUpdate, ...]
    error: str = ""


def _expectations(repo_data: dict[str, object], remote: RemoteSnapshot) -> ExpectedSettings:
    text = remote.governance_text
    normalized = text.lower()
    declarations = _explicit_declarations(text)
    policy = _central_policy(str(repo_data.get("full_name") or ""))
    required_checks = policy.required_checks
    documented_checks = _documented_required_checks(text)
    if documented_checks:
        required_checks = documented_checks
    canonical_validation = _canonical_validation(remote)
    solo_operator = _bool_declaration(declarations, "solo-operator review policy")
    required_prs = _override_bool(policy.required_prs, _documented_required_prs(declarations))
    require_status_checks = _override_bool(policy.require_status_checks, _documented_require_status_checks(declarations))
    strict_checks = _override_bool(policy.strict_checks, _documented_strict_checks(declarations))
    required_reviews = _override_int(policy.required_approving_reviews, _int_declaration(declarations, "required approving reviews"))
    admin_bypass = _override_bool(policy.admin_bypass, _bool_declaration(declarations, "administrator bypass"))
    if solo_operator is True:
        required_prs = True if required_prs is None else required_prs
        require_status_checks = True if require_status_checks is None else require_status_checks
        required_reviews = 0 if required_reviews is None else required_reviews
        admin_bypass = True if admin_bypass is None else admin_bypass
    force_pushes_allowed = _override_bool(policy.force_pushes_allowed, _bool_declaration(declarations, "force pushes on main"))
    deletions_allowed = _override_bool(policy.deletions_allowed, _bool_declaration(declarations, "deletions on main"))
    merge_methods = {
        **policy.merge_methods,
        **_documented_merge_methods(declarations),
    }
    visibility = _documented_visibility(declarations) or policy.visibility
    default_branch = _documented_default_branch(declarations) or policy.default_branch
    return ExpectedSettings(
        visibility=visibility,
        default_branch=default_branch,
        branch_rules=bool(
            required_prs
            or require_status_checks
            or required_checks
            or strict_checks is not None
            or force_pushes_allowed is not None
            or deletions_allowed is not None
            or required_reviews is not None
            or admin_bypass is not None
        ),
        required_checks=required_checks,
        require_status_checks=require_status_checks,
        required_prs=required_prs,
        strict_checks=strict_checks,
        force_pushes_allowed=force_pushes_allowed,
        deletions_allowed=deletions_allowed,
        required_approving_reviews=required_reviews,
        admin_bypass=admin_bypass,
        dependabot=_expected_dependabot(policy.dependabot, remote),
        merge_methods=merge_methods,
        merge_methods_documented=_mentions_any(
            normalized,
            ("squash merge", "merge commit", "rebase merge", "auto-merge"),
        ),
        canonical_validation=canonical_validation,
    )


def _branch_rules_item(remote: RemoteSnapshot, state: HostedState, expected: ExpectedSettings) -> AuditItem:
    present = _branch_rules_present(state)
    unknown_reason = _hosted_rule_unknown_reason(state)
    if expected.branch_rules and present is None:
        status = "unknown"
        follow_up = f"Retry completed, but hosted branch protection/ruleset state is still unavailable: {unknown_reason}."
    else:
        status = _compare_if_known(expected.branch_rules, present)
        follow_up = (
            "Review hosted default-branch enforcement manually; this audit will not enable it."
            if expected.branch_rules and not present
            else "Document effective default-branch governance expectations before treating this setting as drift."
        )
    return AuditItem(
        setting="default branch protection or ruleset",
        status=status,
        expected=(
            "effective default branch enforcement is documented"
            if expected.branch_rules
            else "no default branch enforcement expectation found in source-of-truth docs"
        ),
        actual=_describe_branch_rules(state),
        source=_source(remote),
        follow_up=follow_up,
    )


def _required_checks_item(remote: RemoteSnapshot, state: HostedState, expected: ExpectedSettings) -> AuditItem:
    actual_checks = _required_checks(state)
    unknown_reason = _hosted_rule_unknown_reason(state)
    if expected.required_checks:
        if actual_checks and expected.required_checks != actual_checks:
            status = "drift"
        elif unknown_reason:
            status = "unknown"
        else:
            status = "match" if expected.required_checks == actual_checks else "drift"
        follow_up = (
            "Update hosted enforcement or the source-of-truth governance docs so effective required check names match."
            if status == "drift"
            else f"Retry completed, but hosted required-check state is still unavailable: {unknown_reason}."
            if status == "unknown"
            else "Confirm these required checks still map to the canonical validation path."
        )
        expected_text = ", ".join(expected.required_checks)
    elif expected.require_status_checks is True:
        if actual_checks:
            status = "match"
        elif unknown_reason:
            status = "unknown"
        else:
            status = "drift"
        follow_up = (
            "Document exact hosted check names for stricter comparison."
            if actual_checks
            else f"Retry completed, but hosted required-check state is still unavailable: {unknown_reason}."
            if status == "unknown"
            else "Require the hosted validation checks declared by source-of-truth governance docs through an enforcement mechanism."
        )
        expected_text = "hosted required status checks are explicitly required; exact names are not documented"
    elif expected.require_status_checks is False:
        if actual_checks:
            status = "drift"
        elif unknown_reason:
            status = "unknown"
        else:
            status = "match"
        follow_up = (
            f"Retry completed, but hosted required-check state is still unavailable: {unknown_reason}."
            if status == "unknown"
            else "Align hosted required checks with the explicit source-of-truth governance declaration."
        )
        expected_text = "no hosted required status checks"
    else:
        status = "unknown"
        follow_up = "unknown_policy: document required hosted checks before treating check configuration as drift."
        expected_text = "unknown_policy: no required-check expectation found"

    return AuditItem(
        setting="required status checks",
        status=status,
        expected=expected_text,
        actual=(
            f"hosted required status checks unavailable ({unknown_reason})"
            if status == "unknown" and unknown_reason
            else ", ".join(actual_checks)
            if actual_checks
            else "no required hosted status checks detected"
        ),
        source=_source(remote),
        follow_up=follow_up,
    )


def _pull_request_item(remote: RemoteSnapshot, state: HostedState, expected: ExpectedSettings) -> AuditItem:
    actual = _pull_request_required(state)
    unknown_reason = _hosted_rule_unknown_reason(state) if actual is None else ""
    return AuditItem(
        setting="required pull requests",
        status=_compare_bool_if_known(expected.required_prs, actual),
        expected=(
            f"pull requests before merge: {_enabled_disabled(expected.required_prs)}"
            if expected.required_prs is not None
            else "unknown_policy: no pull-request requirement found in source-of-truth docs"
        ),
        actual=(
            f"required pull-request state unavailable ({unknown_reason})"
            if unknown_reason
            else _yes_no_unknown(actual)
        ),
        source=_source(remote),
        follow_up=(
            f"Retry completed, but hosted pull-request state is still unavailable: {unknown_reason}."
            if unknown_reason
            else "Require pull requests through hosted enforcement only after explicit human approval."
            if expected.required_prs and actual is False
            else "unknown_policy: document pull-request expectations before treating this setting as drift."
        ),
    )


def _review_admin_item(remote: RemoteSnapshot, state: HostedState, expected: ExpectedSettings) -> AuditItem:
    actual_reviews = _required_approving_reviews(state)
    actual_admin_bypass = _admin_bypass_enabled(state)
    unknown_reason = _review_admin_unknown_reason(state, actual_reviews, actual_admin_bypass)
    expected_parts: list[tuple[str, object | None, object | None]] = [
        ("required approving reviews", expected.required_approving_reviews, actual_reviews),
        ("administrator bypass", expected.admin_bypass, actual_admin_bypass),
    ]
    known_parts = [part for part in expected_parts if part[1] is not None]
    if known_parts and any(actual is None for _, _, actual in known_parts):
        status = "unknown"
    elif known_parts:
        status = "match" if all(expected_value == actual for _, expected_value, actual in known_parts) else "drift"
    else:
        status = "unknown"
    return AuditItem(
        setting="review and administrator policy",
        status=status,
        expected=(
            "; ".join(_review_admin_part(label, value) for label, value, _ in known_parts)
            if known_parts
            else "unknown_policy: no review/admin expectation found in source-of-truth docs"
        ),
        actual=(
            f"required approving reviews: {_unknown_or_value(actual_reviews)}; "
            f"administrator bypass: {_enabled_disabled_unknown(actual_admin_bypass)}"
            f"{f' ({unknown_reason})' if status == 'unknown' and unknown_reason else ''}"
        ),
        source=_source(remote),
        follow_up=(
            "Align hosted review/admin settings or source-of-truth governance docs."
            if status == "drift"
            else f"Retry completed, but hosted review/admin state is still unavailable: {unknown_reason}."
            if status == "unknown" and unknown_reason
            else "unknown_policy: document review-count and administrator-bypass expectations before treating these settings as drift."
        ),
    )


def _strict_checks_item(remote: RemoteSnapshot, state: HostedState, expected: ExpectedSettings) -> AuditItem:
    actual = _strict_status_checks(state)
    unknown_reason = _hosted_rule_unknown_reason(state) if actual is None else ""
    return AuditItem(
        setting="branch up-to-date requirement",
        status=_compare_bool_if_known(expected.strict_checks, actual),
        expected=(
            f"branches up to date before merge: {_enabled_disabled(expected.strict_checks)}"
            if expected.strict_checks is not None
            else "unknown_policy: no up-to-date requirement found in source-of-truth docs"
        ),
        actual=(
            f"up-to-date requirement unavailable ({unknown_reason})"
            if unknown_reason
            else _yes_no_unknown(actual)
        ),
        source=_source(remote),
        follow_up=(
            "Enable strict required checks through an explicit hosted-settings change."
            if expected.strict_checks and actual is False
            else f"Retry completed, but hosted up-to-date state is still unavailable: {unknown_reason}."
            if unknown_reason
            else "unknown_policy: document whether branches must be up to date before merge."
        ),
    )


def _force_delete_item(remote: RemoteSnapshot, state: HostedState, expected: ExpectedSettings) -> AuditItem:
    force_allowed = _force_pushes_allowed(state)
    delete_allowed = _deletions_allowed(state)
    unknown_reason = _force_delete_unknown_reason(state, force_allowed, delete_allowed)
    expected_parts: list[tuple[str, bool | None, bool | None]] = [
        ("force pushes allowed", expected.force_pushes_allowed, force_allowed),
        ("deletions allowed", expected.deletions_allowed, delete_allowed),
    ]
    known_parts = [part for part in expected_parts if part[1] is not None]
    if known_parts and any(actual is None for _, _, actual in known_parts):
        status = "unknown"
    elif known_parts:
        status = "match" if all(expected_value == actual for _, expected_value, actual in known_parts) else "drift"
    else:
        status = "unknown"
    return AuditItem(
        setting="force-push and deletion restrictions",
        status=status,
        expected=(
            "; ".join(
                f"{label}: {_enabled_disabled(value)}"
                for label, value, _ in known_parts
                if value is not None
            )
            if known_parts
            else "unknown_policy: no force-push/deletion restriction expectation found"
        ),
        actual=(
            f"force pushes allowed: {_yes_no_unknown(force_allowed)}; "
            f"deletions allowed: {_yes_no_unknown(delete_allowed)}"
            f"{f' ({unknown_reason})' if status == 'unknown' and unknown_reason else ''}"
        ),
        source=_source(remote),
        follow_up=(
            "Review hosted effective force-push/deletion restrictions manually; this audit is report-only."
            if status == "drift"
            else f"Retry completed, but hosted force-push/deletion state is still unavailable: {unknown_reason}."
            if status == "unknown" and unknown_reason
            else "unknown_policy: document force-push/deletion expectations before treating this as drift."
        ),
    )


def _actions_item(remote: RemoteSnapshot, state: HostedState) -> AuditItem:
    hosted = _workflow_actual(state)
    missing = [path for path in remote.workflow_paths if path not in hosted]
    inactive = [path for path in remote.workflow_paths if hosted.get(path) not in (None, "active")]
    if state.workflows is None:
        status = "unknown"
        actual = f"hosted workflow state unavailable ({state.workflows_error})" if state.workflows_error else "hosted workflow state unavailable"
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
    dependabot = expected.dependabot
    config = _dependabot_config(remote)
    if config.error:
        return AuditItem(
            setting="Dependabot config presence",
            status="unknown",
            expected=_describe_expected_dependabot(dependabot),
            actual=f"{remote.dependabot_path} could not be parsed ({config.error})",
            source=_source(remote),
            follow_up="unknown_unavailable: fix or inspect Dependabot config before treating dependency-update policy as drift.",
        )
    if not dependabot.enabled:
        status = "match" if not remote.dependabot_path else "drift"
        return AuditItem(
            setting="Dependabot config presence",
            status=status,
            expected="Dependabot disabled by central repo settings policy",
            actual=remote.dependabot_path or "not present in source-of-truth ref",
            source=_source(remote),
            follow_up=(
                "Remove or justify Dependabot config through a central repo policy override."
                if status == "drift"
                else "Dependabot is intentionally disabled for this repository."
            ),
        )
    if not dependabot.ecosystems:
        return AuditItem(
            setting="Dependabot config presence",
            status="match",
            expected=f"not applicable: no supported ecosystems detected ({dependabot.reason})",
            actual=remote.dependabot_path or "not present in source-of-truth ref",
            source=_source(remote),
            follow_up="No Dependabot config is required until a supported ecosystem is added.",
        )
    missing = _missing_dependabot_ecosystems(dependabot, config)
    wrong_schedule = _wrong_dependabot_schedules(dependabot, config)
    status = "match" if remote.dependabot_path and not missing and not wrong_schedule else "drift"
    actual_parts = []
    if remote.dependabot_path:
        actual_parts.append(_describe_dependabot_config(config))
    else:
        actual_parts.append("not present in source-of-truth ref")
    if missing:
        actual_parts.append(f"missing ecosystems: {', '.join(missing)}")
    if wrong_schedule:
        actual_parts.append(f"non-weekly schedules: {', '.join(wrong_schedule)}")
    return AuditItem(
        setting="Dependabot config presence",
        status=status,
        expected=_describe_expected_dependabot(dependabot),
        actual="; ".join(actual_parts),
        source=_source(remote),
        follow_up=(
            "Add or update Dependabot config in a normal PR so supported ecosystems update weekly."
            if status == "drift"
            else "Dependabot config covers the supported baseline ecosystems."
        ),
    )


def _merge_methods_item(remote: RemoteSnapshot, state: HostedState, expected: ExpectedSettings) -> AuditItem:
    actual = _merge_methods_actual(state)
    known = expected.merge_methods
    if known:
        status = "match" if all(actual.get(method) == value for method, value in known.items()) else "drift"
    else:
        status = "unknown"
    return AuditItem(
        setting="merge method settings",
        status=status,
        expected=(
            _describe_expected_merge_methods(known)
            if known
            else
            "source-of-truth docs mention merge methods, but no concrete expected settings are parsed"
            if expected.merge_methods_documented
            else "no concrete merge method expectation found in source-of-truth docs"
        ),
        actual=_describe_merge_methods(state),
        source=_source(remote),
        follow_up=(
            "Align hosted merge methods with explicit source-of-truth governance docs."
            if status == "drift"
            else
            "Compare allowed merge methods manually if the policy is prose-only, or document concrete merge-method expectations before enforcing this check."
            if expected.merge_methods_documented
            else "Document concrete merge-method expectations before treating these hosted settings as drift."
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
    protection_endpoint = f"/repos/{repo}/branches/{branch}/protection"
    protection_fetch = _fetch_optional_object(runner, protection_endpoint, errors)
    protection = protection_fetch.value
    protection_retried = protection_fetch.retried
    protection_incomplete: tuple[str, ...] = ()
    if protection is not None:
        protection, protection_incomplete, read_depth_retried = _retry_incomplete_branch_protection(
            runner,
            protection_endpoint,
            protection,
            errors,
        )
        protection_retried = protection_retried or read_depth_retried
    rulesets_fetch = _fetch_rulesets(runner, repo, errors)
    workflows_fetch = _fetch_optional_object(runner, f"/repos/{repo}/actions/workflows", errors)
    workflows = None
    workflows_error = workflows_fetch.error
    if workflows_fetch.value is not None:
        items = workflows_fetch.value.get("workflows")
        if isinstance(items, list):
            workflows = tuple(item for item in items if isinstance(item, dict))
        else:
            workflows_error = "unknown_unavailable: actions workflows response did not include a workflows list"
            errors.append(workflows_error)
    return HostedState(
        repo=repo_data,
        branch_protection=protection,
        rulesets=rulesets_fetch.value,
        workflows=workflows,
        branch_protection_error=protection_fetch.error,
        branch_protection_incomplete=protection_incomplete,
        branch_protection_retried=protection_retried,
        rulesets_error=rulesets_fetch.error,
        rulesets_retried=rulesets_fetch.retried,
        workflows_error=workflows_error,
        workflows_retried=workflows_fetch.retried,
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


def _fetch_org_repositories(org: str, runner: Runner) -> tuple[tuple[str, ...], list[str]]:
    command = runner(("gh", "repo", "list", org, "--json", "nameWithOwner", "--limit", "1000"))
    if command.returncode != 0:
        detail = (command.stderr or command.stdout or f"exit {command.returncode}").splitlines()[0]
        raise RuntimeError(f"gh repo list {org} failed: {detail}")
    try:
        data = json.loads(command.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh repo list {org} returned invalid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise RuntimeError(f"gh repo list {org} did not return a repository list")
    repositories: list[str] = []
    errors: list[str] = []
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("nameWithOwner"), str):
            errors.append("gh repo list entry did not include nameWithOwner")
            continue
        repositories.append(entry["nameWithOwner"])
    return tuple(sorted(set(repositories), key=str.lower)), errors


def _org_local_repo_root(repo: str, workspace_root: Path | None) -> Path | None:
    if workspace_root is None:
        return None
    return workspace_root / repo.split("/", 1)[-1]


def _fetch_optional_object(
    runner: Runner,
    endpoint: str,
    errors: list[str],
) -> OptionalObjectResult:
    result = _fetch_optional_object_once(runner, endpoint)
    if not result.error:
        return result
    retry = _fetch_optional_object_once(runner, endpoint)
    if not retry.error:
        return OptionalObjectResult(
            value=retry.value,
            retried=True,
            absent=retry.absent,
        )
    error = f"unknown_after_retry: {endpoint} {retry.error}"
    errors.append(error)
    return OptionalObjectResult(error=error, retried=True)


def _fetch_optional_object_once(
    runner: Runner,
    endpoint: str,
) -> OptionalObjectResult:
    command = runner(("gh", "api", endpoint))
    if command.returncode != 0:
        detail = (command.stderr or command.stdout or f"exit {command.returncode}").splitlines()[0]
        if _not_found(detail):
            return OptionalObjectResult(absent=True)
        return OptionalObjectResult(error=f"inaccessible: {detail}")
    try:
        data = json.loads(command.stdout or "{}")
    except json.JSONDecodeError as exc:
        return OptionalObjectResult(error=f"returned invalid JSON: {exc}")
    if not isinstance(data, dict):
        return OptionalObjectResult(error="did not return an object")
    return OptionalObjectResult(value=data)


def _fetch_optional_collection(
    runner: Runner,
    endpoint: str,
    errors: list[str],
) -> OptionalCollectionResult:
    result = _fetch_optional_collection_once(runner, endpoint)
    if not result.error:
        return result
    retry = _fetch_optional_collection_once(runner, endpoint)
    if not retry.error:
        return OptionalCollectionResult(
            value=retry.value,
            retried=True,
            absent=retry.absent,
        )
    error = f"unknown_after_retry: {endpoint} {retry.error}"
    errors.append(error)
    return OptionalCollectionResult(error=error, retried=True)


def _fetch_optional_collection_once(
    runner: Runner,
    endpoint: str,
) -> OptionalCollectionResult:
    command = runner(("gh", "api", endpoint))
    if command.returncode != 0:
        detail = (command.stderr or command.stdout or f"exit {command.returncode}").splitlines()[0]
        if _not_found(detail):
            return OptionalCollectionResult(value=(), absent=True)
        return OptionalCollectionResult(error=f"inaccessible: {detail}")
    try:
        data = json.loads(command.stdout or "[]")
    except json.JSONDecodeError as exc:
        return OptionalCollectionResult(error=f"returned invalid JSON: {exc}")
    if not isinstance(data, list):
        return OptionalCollectionResult(error="did not return a list")
    return OptionalCollectionResult(value=tuple(item for item in data if isinstance(item, dict)))


def _fetch_rulesets(
    runner: Runner,
    repo: str,
    errors: list[str],
) -> OptionalCollectionResult:
    endpoint = f"/repos/{repo}/rulesets?targets=branch"
    listed = _fetch_optional_collection(runner, endpoint, errors)
    if listed.value is None:
        return listed
    details: list[dict[str, object]] = []
    retried = listed.retried
    detail_errors: list[str] = []
    for ruleset in listed.value:
        ruleset_id = ruleset.get("id")
        if not isinstance(ruleset_id, int):
            details.append(ruleset)
            continue
        detail_endpoint = f"/repos/{repo}/rulesets/{ruleset_id}"
        detail = _fetch_optional_object(runner, detail_endpoint, errors)
        retried = retried or detail.retried
        if detail.value is not None:
            details.append(detail.value)
        else:
            details.append(ruleset)
            if detail.error:
                detail_errors.append(detail.error)
    if detail_errors:
        return OptionalCollectionResult(
            value=tuple(details),
            error="; ".join(detail_errors),
            retried=retried,
        )
    return OptionalCollectionResult(
        value=tuple(details),
        retried=retried,
        absent=listed.absent,
    )


def _retry_incomplete_branch_protection(
    runner: Runner,
    endpoint: str,
    protection: dict[str, object],
    errors: list[str],
) -> tuple[dict[str, object], tuple[str, ...], bool]:
    missing = _missing_branch_protection_read_depth_fields(protection)
    if not missing:
        return protection, (), False
    retry = _fetch_optional_object_once(runner, endpoint)
    if not retry.error and retry.value is not None:
        missing = _missing_branch_protection_read_depth_fields(retry.value)
        if missing:
            errors.append(
                f"unknown_after_retry: {endpoint} response missing {', '.join(missing)} after retry"
            )
        return retry.value, missing, True
    errors.append(
        f"unknown_after_retry: {endpoint} response missing {', '.join(missing)}; "
        f"retry {retry.error or 'did not return branch protection'}"
    )
    return protection, missing, True


def _missing_branch_protection_read_depth_fields(protection: dict[str, object]) -> tuple[str, ...]:
    fields = ("allow_deletions", "allow_force_pushes", "enforce_admins")
    return tuple(field for field in fields if field not in protection)


def _not_found(detail: str) -> bool:
    return "404" in detail or "Not Found" in detail


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
    return (
        path in LOCAL_GOVERNANCE_NAMES
        or path in SOURCE_METADATA_NAMES
        or any(path.startswith(prefix) for prefix in LOCAL_GOVERNANCE_PREFIXES)
    )


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


DeclarationMap = dict[str, tuple[str, ...]]


def _central_policy(repo: str) -> CentralPolicy:
    data = _read_central_policy()
    baseline = _policy_object(data.get("baseline"), "baseline")
    repositories = _policy_object(data.get("repositories", {}), "repositories")
    override = _policy_object(repositories.get(repo, {}), f"repositories.{repo}")
    merged = {**baseline, **override}
    if isinstance(baseline.get("dependabot"), dict) or isinstance(override.get("dependabot"), dict):
        merged["dependabot"] = {
            **(baseline.get("dependabot") if isinstance(baseline.get("dependabot"), dict) else {}),
            **(override.get("dependabot") if isinstance(override.get("dependabot"), dict) else {}),
        }
    return CentralPolicy(
        visibility=_policy_string(merged, "visibility"),
        default_branch=_policy_string(merged, "default_branch"),
        required_checks=_policy_required_checks(merged.get("required_checks", ())),
        require_status_checks=_policy_bool(merged.get("require_status_checks")),
        required_prs=_policy_bool(merged.get("require_pull_requests")),
        strict_checks=_policy_bool(merged.get("strict_required_checks")),
        force_pushes_allowed=_enabled_policy_bool(merged.get("force_pushes")),
        deletions_allowed=_enabled_policy_bool(merged.get("branch_deletions")),
        required_approving_reviews=_policy_int(merged.get("required_approving_reviews")),
        admin_bypass=_enabled_policy_bool(merged.get("administrator_bypass")),
        merge_methods=_policy_merge_methods(merged),
        dependabot=_dependabot_policy(merged.get("dependabot")),
    )


def _read_central_policy() -> dict[str, object]:
    try:
        data = json.loads(CENTRAL_POLICY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"central repo settings policy not found: {CENTRAL_POLICY_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"central repo settings policy is invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("central repo settings policy must be a JSON object")
    return data


def _policy_object(value: object, label: str) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    raise RuntimeError(f"central repo settings policy field {label} must be an object")


def _policy_string(policy: dict[str, object], key: str) -> str:
    value = policy.get(key)
    return value if isinstance(value, str) else ""


def _policy_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _policy_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _policy_required_checks(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    checks = []
    for item in value:
        clean = _clean_check_name(item)
        if clean:
            checks.append(clean)
    return tuple(checks)


def _enabled_policy_bool(value: object) -> bool | None:
    if value == "enabled":
        return True
    if value == "disabled":
        return False
    if isinstance(value, bool):
        return value
    return None


def _policy_merge_methods(policy: dict[str, object]) -> dict[str, bool]:
    methods: dict[str, bool] = {}
    if policy.get("merge_policy") == "squash-only":
        methods.update(
            {
                "allow_squash_merge": True,
                "allow_merge_commit": False,
                "allow_rebase_merge": False,
            }
        )
    auto_merge = _enabled_policy_bool(policy.get("auto_merge"))
    if auto_merge is not None:
        methods["allow_auto_merge"] = auto_merge
    delete_branch = _enabled_policy_bool(policy.get("delete_branch_on_merge"))
    if delete_branch is not None:
        methods["delete_branch_on_merge"] = delete_branch
    return methods


def _dependabot_policy(value: object) -> DependabotPolicy:
    if not isinstance(value, dict):
        return DependabotPolicy(enabled="disabled", ecosystems=(), schedule="")
    enabled = value.get("enabled")
    if enabled is True:
        enabled_value = "enabled"
    elif enabled is False:
        enabled_value = "disabled"
    elif enabled in ("auto", "enabled", "disabled"):
        enabled_value = str(enabled)
    else:
        enabled_value = "disabled"
    ecosystems = value.get("ecosystems")
    schedule = value.get("schedule")
    return DependabotPolicy(
        enabled=enabled_value,
        ecosystems=tuple(str(item) for item in ecosystems if isinstance(item, str)) if isinstance(ecosystems, list) else (),
        schedule=schedule if isinstance(schedule, str) else "",
    )


def _override_bool(base: bool | None, override: bool | None) -> bool | None:
    return base if override is None else override


def _override_int(base: int | None, override: int | None) -> int | None:
    return base if override is None else override


def _expected_dependabot(policy: DependabotPolicy, remote: RemoteSnapshot) -> ExpectedDependabot:
    if policy.enabled == "disabled":
        return ExpectedDependabot(
            enabled=False,
            ecosystems=(),
            schedule=policy.schedule,
            reason="disabled by central policy",
        )
    supported = _supported_dependabot_ecosystems(remote)
    configured = tuple(ecosystem for ecosystem in policy.ecosystems if ecosystem in supported)
    if policy.enabled == "enabled":
        configured = policy.ecosystems
    return ExpectedDependabot(
        enabled=True,
        ecosystems=configured,
        schedule=policy.schedule,
        reason=", ".join(_dependabot_support_reasons(remote)) or "no supported ecosystem files",
    )


def _supported_dependabot_ecosystems(remote: RemoteSnapshot) -> tuple[str, ...]:
    ecosystems = []
    if remote.workflow_paths:
        ecosystems.append("github-actions")
    if any(path in remote.files for path in PYTHON_PACKAGE_METADATA_PATHS):
        ecosystems.append("pip")
    return tuple(ecosystems)


def _dependabot_support_reasons(remote: RemoteSnapshot) -> tuple[str, ...]:
    reasons = []
    if remote.workflow_paths:
        reasons.append(".github/workflows/*.yml")
    python_metadata = [path for path in PYTHON_PACKAGE_METADATA_PATHS if path in remote.files]
    if python_metadata:
        reasons.append(", ".join(python_metadata))
    return tuple(reasons)


def _explicit_declarations(text: str) -> DeclarationMap:
    declarations: dict[str, list[str]] = {}
    in_fenced_block = False
    for line in text.splitlines():
        if _is_fence(line):
            in_fenced_block = not in_fenced_block
            continue
        if in_fenced_block or ":" not in line:
            continue
        stripped = line.strip().lstrip("-*").strip()
        key, value = stripped.split(":", 1)
        normalized_key = _normalize_declaration_key(key)
        if normalized_key not in _supported_declaration_keys():
            continue
        clean_value = _clean_declaration_value(value)
        if not clean_value:
            continue
        declarations.setdefault(normalized_key, []).append(clean_value)
    return {key: tuple(values) for key, values in declarations.items()}


def _supported_declaration_keys() -> set[str]:
    return {
        "administrator bypass",
        "default branch",
        "deletions on main",
        "force pushes on main",
        "merge commits",
        "merge methods",
        "merge policy",
        "pull requests before merge",
        "rebase merge",
        "repository visibility",
        "require branches up to date before merge",
        "require pull requests before merge",
        "require status checks before merge",
        "required approving reviews",
        "squash merge",
        "solo-operator review policy",
        "visibility",
    }


def _normalize_declaration_key(key: str) -> str:
    return re.sub(r"\s+", " ", key.strip().lower())


def _clean_declaration_value(value: str) -> str:
    return value.strip().strip("`").strip().rstrip(".;").strip().strip("`").lower()


def _last_declaration(declarations: DeclarationMap, *keys: str) -> str:
    for key in keys:
        values = declarations.get(key)
        if values:
            return values[-1]
    return ""


def _documented_visibility(declarations: DeclarationMap) -> str:
    value = _last_declaration(declarations, "repository visibility", "visibility")
    return value if value in {"public", "private"} else ""


def _documented_default_branch(declarations: DeclarationMap) -> str:
    return _last_declaration(declarations, "default branch").removeprefix("origin/")


def _documented_required_prs(declarations: DeclarationMap) -> bool | None:
    return _bool_declaration(declarations, "require pull requests before merge", "pull requests before merge")


def _documented_require_status_checks(declarations: DeclarationMap) -> bool | None:
    return _bool_declaration(declarations, "require status checks before merge")


def _documented_strict_checks(declarations: DeclarationMap) -> bool | None:
    return _bool_declaration(declarations, "require branches up to date before merge")


def _documented_merge_methods(declarations: DeclarationMap) -> dict[str, bool]:
    policy = _last_declaration(declarations, "merge policy", "merge methods")
    methods: dict[str, bool] = {}
    if policy == "squash-only":
        methods.update(
            {
                "allow_squash_merge": True,
                "allow_merge_commit": False,
                "allow_rebase_merge": False,
            }
        )
    squash = _bool_declaration(declarations, "squash merge")
    merge_commit = _bool_declaration(declarations, "merge commits")
    rebase = _bool_declaration(declarations, "rebase merge")
    if squash is not None:
        methods["allow_squash_merge"] = squash
    if merge_commit is not None:
        methods["allow_merge_commit"] = merge_commit
    if rebase is not None:
        methods["allow_rebase_merge"] = rebase
    return methods


def _bool_declaration(declarations: DeclarationMap, *keys: str) -> bool | None:
    value = _last_declaration(declarations, *keys)
    if value in {"yes", "true", "enabled", "required", "require"}:
        return True
    if value in {"no", "false", "disabled", "not required"}:
        return False
    return None


def _int_declaration(declarations: DeclarationMap, key: str) -> int | None:
    value = _last_declaration(declarations, key)
    if not re.fullmatch(r"\d+", value):
        return None
    return int(value)


def _documented_required_checks(text: str) -> tuple[str, ...]:
    checks: list[str] = []
    lines = text.splitlines()
    in_fenced_block = False
    scoped_section = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_fence(line):
            in_fenced_block = not in_fenced_block
            index += 1
            continue
        if not in_fenced_block and _is_heading(line):
            scoped_section = _is_required_check_section_scope(line)
        if in_fenced_block or not _is_required_check_declaration(line, scoped_section):
            index += 1
            continue

        block_checks, next_index = _collect_required_check_declaration(lines, index)
        checks.extend(block_checks)
        index = next_index
    return tuple(sorted(set(checks)))


def _is_fence(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def _is_required_check_declaration(line: str, scoped_section: bool) -> bool:
    normalized = line.strip().lower()
    if not normalized:
        return False
    if "require these status checks" in normalized:
        return True
    if _is_heading(line) and "required status check" in normalized:
        return True
    if "required status check" in normalized and _looks_like_declaration(normalized):
        return True
    if scoped_section and _scoped_required_checks_declaration(normalized):
        return True
    return False


def _looks_like_declaration(normalized: str) -> bool:
    return (
        ":" in normalized
        or normalized.endswith(" is")
        or normalized.endswith(" are")
        or " name is" in normalized
        or " names are" in normalized
    )


def _is_required_check_section_scope(line: str) -> bool:
    normalized = line.strip("# ").lower()
    return any(marker in normalized for marker in ("governance", "branch protection", "ruleset"))


def _scoped_required_checks_declaration(normalized: str) -> bool:
    stripped = normalized.lstrip("-* ").strip()
    return stripped.startswith("required checks:") or stripped.startswith("require checks:")


def _collect_required_check_declaration(lines: list[str], declaration_index: int) -> tuple[list[str], int]:
    checks = _declaration_check_values(lines[declaration_index])
    declaration_indent = _indent_width(lines[declaration_index])
    declaration_is_bullet = lines[declaration_index].strip().startswith(("-", "*"))
    index = declaration_index + 1
    while index < len(lines):
        line = lines[index]
        if _is_fence(line):
            break
        if not line.strip():
            if checks:
                break
            index += 1
            continue
        if _is_heading(line):
            break
        line_indent = _indent_width(line)
        if line_indent < declaration_indent:
            break
        if declaration_is_bullet and line_indent <= declaration_indent:
            break

        values = _structured_check_values(line)
        if values:
            checks.extend(values)
            index += 1
            continue

        if checks:
            break
        index += 1
    return checks, index


def _is_heading(line: str) -> bool:
    return line.lstrip().startswith("#")


def _structured_check_values(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith(("-", "*")):
        bullet_value = stripped[1:].strip()
        if re.fullmatch(r"`[^`]+`[.;,]?", bullet_value):
            return _check_values(bullet_value)
        return []
    if re.fullmatch(r"`[^`]+`[.;,]?", stripped):
        return _check_values(line)
    return []


def _indent_width(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _check_values(line: str) -> list[str]:
    values: list[str] = []
    for value in re.findall(r"`([^`]+)`", line):
        clean = _clean_check_name(value)
        if clean:
            values.append(clean)
    return values


def _declaration_check_values(line: str) -> list[str]:
    lower = line.lower()
    for marker in ("require these status checks", "required status check", "required checks", "require checks"):
        marker_index = lower.find(marker)
        if marker_index == -1:
            continue
        declaration_tail = line[marker_index:]
        colon_index = declaration_tail.find(":")
        if colon_index != -1:
            declaration_tail = declaration_tail[colon_index + 1 :]
        return _check_values(declaration_tail)
    return []


def _clean_check_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    clean = value.strip()
    if not clean:
        return ""
    if not any(char.isalnum() for char in clean):
        return ""
    return clean


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


def _compare_bool_if_known(expected: bool | None, actual: bool | None) -> str:
    if expected is None or actual is None:
        return "unknown"
    return "match" if expected == actual else "drift"


def _visibility(state: HostedState) -> str:
    return "private" if bool(state.repo.get("private")) else "public"


def _default_branch(state: HostedState) -> str:
    return str(state.repo.get("default_branch") or "")


def _branch_rules_present(state: HostedState) -> bool | None:
    if state.branch_protection is not None or bool(_active_branch_rulesets(state.rulesets)):
        return True
    if _hosted_rule_unknown_reason(state):
        return None
    return False


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
            checks.extend(clean for context in contexts if (clean := _clean_check_name(context)))
        check_items = required.get("checks")
        if isinstance(check_items, list):
            checks.extend(
                clean
                for item in check_items
                if isinstance(item, dict) and (clean := _clean_check_name(item.get("context", "")))
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
                clean
                for item in required_items
                if isinstance(item, dict) and (clean := _clean_check_name(item.get("context", "")))
            )
    return tuple(sorted(set(checks)))


def _pull_request_required(state: HostedState) -> bool | None:
    if state.branch_protection and isinstance(state.branch_protection.get("required_pull_request_reviews"), dict):
        return True
    if "pull_request" in _rule_types(state.rulesets):
        return True
    if _hosted_rule_unknown_reason(state):
        return None
    return False


def _strict_status_checks(state: HostedState) -> bool | None:
    values: list[bool] = []
    unknown = False
    required = state.branch_protection.get("required_status_checks") if state.branch_protection else None
    if isinstance(required, dict):
        if isinstance(required.get("strict"), bool):
            values.append(bool(required["strict"]))
        else:
            unknown = True
    for ruleset in _active_branch_rulesets(state.rulesets):
        rules = ruleset.get("rules")
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
                continue
            parameters = rule.get("parameters")
            if isinstance(parameters, dict) and isinstance(parameters.get("strict_required_status_checks_policy"), bool):
                values.append(bool(parameters["strict_required_status_checks_policy"]))
            else:
                unknown = True
    if any(values):
        return True
    if unknown:
        return None
    if values:
        return False
    if _required_checks(state):
        return None
    if _hosted_rule_unknown_reason(state):
        return None
    return False


def _force_pushes_allowed(state: HostedState) -> bool | None:
    if "non_fast_forward" in _rule_types(state.rulesets):
        return False
    if state.branch_protection:
        value = state.branch_protection.get("allow_force_pushes")
        if isinstance(value, dict) and isinstance(value.get("enabled"), bool):
            return bool(value["enabled"])
        if "allow_force_pushes" in state.branch_protection_incomplete:
            return None
    if _hosted_rule_unknown_reason(state):
        return None
    return True


def _deletions_allowed(state: HostedState) -> bool | None:
    if "deletion" in _rule_types(state.rulesets):
        return False
    if state.branch_protection:
        value = state.branch_protection.get("allow_deletions")
        if isinstance(value, dict) and isinstance(value.get("enabled"), bool):
            return bool(value["enabled"])
        if "allow_deletions" in state.branch_protection_incomplete:
            return None
    if _hosted_rule_unknown_reason(state):
        return None
    return True


def _required_approving_reviews(state: HostedState) -> int | None:
    counts: list[int] = []
    unknown = False
    reviews = state.branch_protection.get("required_pull_request_reviews") if state.branch_protection else None
    if isinstance(reviews, dict):
        count = reviews.get("required_approving_review_count")
        if isinstance(count, int):
            counts.append(count)
        else:
            counts.append(0)
    for ruleset in _active_branch_rulesets(state.rulesets):
        rules = ruleset.get("rules")
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if not isinstance(rule, dict) or rule.get("type") != "pull_request":
                continue
            parameters = rule.get("parameters")
            if isinstance(parameters, dict) and isinstance(parameters.get("required_approving_review_count"), int):
                counts.append(int(parameters["required_approving_review_count"]))
            elif isinstance(parameters, dict):
                counts.append(0)
            else:
                unknown = True
    if counts:
        return max(counts)
    if unknown:
        return None
    pull_requests = _pull_request_required(state)
    if pull_requests is None:
        return None
    return 0


def _admin_bypass_enabled(state: HostedState) -> bool | None:
    values: list[bool] = []
    unknown = False
    if state.branch_protection:
        enforce_admins = state.branch_protection.get("enforce_admins")
        if isinstance(enforce_admins, dict) and isinstance(enforce_admins.get("enabled"), bool):
            values.append(not bool(enforce_admins["enabled"]))
        elif "enforce_admins" in state.branch_protection_incomplete:
            unknown = True
    rulesets = _active_branch_rulesets(state.rulesets)
    for ruleset in rulesets:
        value = _ruleset_admin_bypass_enabled(ruleset)
        if value is None:
            unknown = True
        else:
            values.append(value)
    if any(value is False for value in values):
        return False
    if values and not unknown:
        return True
    if unknown:
        return None
    if _hosted_rule_unknown_reason(state):
        return None
    return True


def _ruleset_admin_bypass_enabled(ruleset: dict[str, object]) -> bool | None:
    current_user = ruleset.get("current_user_can_bypass")
    if current_user in ("always", "pull_requests_only"):
        return True
    if current_user == "never":
        return False
    bypass_actors = ruleset.get("bypass_actors")
    if isinstance(bypass_actors, list):
        return bool(bypass_actors)
    return None


def _hosted_rule_unknown_reason(state: HostedState) -> str:
    reasons = []
    if state.branch_protection is None and state.branch_protection_error:
        reasons.append(state.branch_protection_error)
    if state.rulesets_error:
        reasons.append(state.rulesets_error)
    return "; ".join(reasons)


def _branch_protection_field_unknown_reason(state: HostedState, fields: Iterable[str]) -> str:
    missing = [field for field in fields if field in state.branch_protection_incomplete]
    if not missing:
        return ""
    return f"unknown_after_retry: branch protection response missing {', '.join(missing)} after retry"


def _review_admin_unknown_reason(
    state: HostedState,
    actual_reviews: int | None,
    actual_admin_bypass: bool | None,
) -> str:
    reasons = []
    if actual_reviews is None:
        reason = _hosted_rule_unknown_reason(state)
        if reason:
            reasons.append(reason)
    if actual_admin_bypass is None:
        reason = _branch_protection_field_unknown_reason(state, ("enforce_admins",))
        if reason:
            reasons.append(reason)
        elif bool(_active_branch_rulesets(state.rulesets)):
            reasons.append(
                "unknown_unavailable: branch ruleset detail did not expose "
                "current_user_can_bypass or bypass_actors"
            )
        else:
            hosted_reason = _hosted_rule_unknown_reason(state)
            if hosted_reason:
                reasons.append(hosted_reason)
    return "; ".join(dict.fromkeys(reasons))


def _force_delete_unknown_reason(
    state: HostedState,
    force_allowed: bool | None,
    delete_allowed: bool | None,
) -> str:
    reasons = []
    if force_allowed is None:
        reason = _branch_protection_field_unknown_reason(state, ("allow_force_pushes",))
        if reason:
            reasons.append(reason)
    if delete_allowed is None:
        reason = _branch_protection_field_unknown_reason(state, ("allow_deletions",))
        if reason:
            reasons.append(reason)
    if (force_allowed is None or delete_allowed is None) and _hosted_rule_unknown_reason(state):
        reasons.append(_hosted_rule_unknown_reason(state))
    return "; ".join(dict.fromkeys(reasons))


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
    if state.branch_protection:
        protection = "branch protection present"
        if state.branch_protection_incomplete:
            protection += f" (partial after retry: missing {', '.join(state.branch_protection_incomplete)})"
        if state.branch_protection_retried:
            protection += " (retried)"
        parts = [protection]
    elif state.branch_protection_error:
        parts = [f"branch protection unknown ({state.branch_protection_error})"]
    else:
        parts = ["branch protection absent"]
    rulesets = _active_branch_rulesets(state.rulesets)
    if rulesets:
        names = ", ".join(str(ruleset.get("name") or ruleset.get("id")) for ruleset in rulesets)
        retry_note = " (retried)" if state.rulesets_retried else ""
        parts.append(f"active branch rulesets{retry_note}: {names}")
    elif state.rulesets is None:
        parts.append(f"active branch rulesets: unknown ({state.rulesets_error})")
    else:
        retry_note = " (retried)" if state.rulesets_retried else ""
        parts.append(f"active branch rulesets{retry_note}: none detected")
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


def _dependabot_config(remote: RemoteSnapshot) -> DependabotConfig:
    path = remote.dependabot_path
    if not path:
        return DependabotConfig(updates=())
    text = remote.files.get(path, "")
    return _parse_dependabot_config(text)


def _parse_dependabot_config(text: str) -> DependabotConfig:
    updates_started = False
    current: dict[str, str] | None = None
    updates: list[DependabotUpdate] = []
    in_schedule = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("updates:"):
            if stripped != "updates:":
                return DependabotConfig(updates=(), error="unknown_unavailable: unsupported inline updates declaration")
            updates_started = True
            in_schedule = False
            continue
        if not updates_started:
            continue
        if not line.startswith((" ", "-")):
            break
        if stripped.startswith("- "):
            if current is not None:
                updates.extend(_dependabot_update_from_mapping(current))
            current = {}
            in_schedule = False
            tail = stripped[2:].strip()
            if tail:
                key, value, error = _dependabot_key_value(tail)
                if error:
                    return DependabotConfig(updates=(), error=error)
                if key:
                    current[key] = value
            continue
        if current is None:
            return DependabotConfig(updates=(), error="unknown_unavailable: updates entry is not a list item")
        key, value, error = _dependabot_key_value(stripped)
        if error:
            return DependabotConfig(updates=(), error=error)
        if not key:
            continue
        if key == "schedule":
            in_schedule = True
            continue
        if in_schedule and key == "interval":
            current["interval"] = value
        elif key == "package-ecosystem":
            current["package-ecosystem"] = value
        else:
            in_schedule = False if not line.startswith("      ") else in_schedule
    if current is not None:
        updates.extend(_dependabot_update_from_mapping(current))
    return DependabotConfig(updates=tuple(updates))


def _dependabot_key_value(line: str) -> tuple[str, str, str]:
    if ":" not in line:
        return "", "", ""
    key, value = line.split(":", 1)
    key = key.strip()
    value = _clean_yaml_scalar(value)
    if value in ("[", "{"):
        return "", "", f"unknown_unavailable: unsupported Dependabot YAML value for {key}"
    return key, value, ""


def _clean_yaml_scalar(value: str) -> str:
    cleaned = value.strip()
    if " #" in cleaned:
        cleaned = cleaned.split(" #", 1)[0].strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
        cleaned = cleaned[1:-1]
    return cleaned


def _dependabot_update_from_mapping(mapping: dict[str, str]) -> tuple[DependabotUpdate, ...]:
    ecosystem = mapping.get("package-ecosystem", "")
    if not ecosystem:
        return ()
    return (DependabotUpdate(ecosystem=ecosystem, interval=mapping.get("interval", "")),)


def _missing_dependabot_ecosystems(
    expected: ExpectedDependabot,
    config: DependabotConfig,
) -> tuple[str, ...]:
    configured = {update.ecosystem for update in config.updates}
    return tuple(ecosystem for ecosystem in expected.ecosystems if ecosystem not in configured)


def _wrong_dependabot_schedules(
    expected: ExpectedDependabot,
    config: DependabotConfig,
) -> tuple[str, ...]:
    wrong = []
    for update in config.updates:
        if update.ecosystem in expected.ecosystems and update.interval != expected.schedule:
            wrong.append(f"{update.ecosystem} ({update.interval or 'missing interval'})")
    return tuple(wrong)


def _describe_expected_dependabot(expected: ExpectedDependabot) -> str:
    if not expected.enabled:
        return "Dependabot disabled"
    if not expected.ecosystems:
        return f"Dependabot not applicable: no supported ecosystems detected ({expected.reason})"
    return (
        f"Dependabot updates expected {expected.schedule or 'with documented schedule'} "
        f"for {', '.join(expected.ecosystems)} "
        f"({expected.reason})"
    )


def _describe_dependabot_config(config: DependabotConfig) -> str:
    if not config.updates:
        return "Dependabot config has no parsed update entries"
    return ", ".join(
        f"{update.ecosystem} ({update.interval or 'missing interval'})"
        for update in config.updates
    )


def _describe_merge_methods(state: HostedState) -> str:
    fields = (
        ("merge commits", "allow_merge_commit"),
        ("squash merges", "allow_squash_merge"),
        ("rebase merges", "allow_rebase_merge"),
        ("auto-merge", "allow_auto_merge"),
        ("delete branch on merge", "delete_branch_on_merge"),
    )
    return "; ".join(f"{label}: {_yes_no_unknown(state.repo.get(field))}" for label, field in fields)


def _merge_methods_actual(state: HostedState) -> dict[str, bool]:
    methods: dict[str, bool] = {}
    for field in (
        "allow_merge_commit",
        "allow_squash_merge",
        "allow_rebase_merge",
        "allow_auto_merge",
        "delete_branch_on_merge",
    ):
        value = state.repo.get(field)
        if isinstance(value, bool):
            methods[field] = value
    return methods


def _describe_expected_merge_methods(methods: dict[str, bool]) -> str:
    labels = {
        "allow_merge_commit": "merge commits",
        "allow_squash_merge": "squash merges",
        "allow_rebase_merge": "rebase merges",
        "allow_auto_merge": "auto-merge",
        "delete_branch_on_merge": "delete branch on merge",
    }
    return "; ".join(f"{labels[key]}: {_enabled_disabled(value)}" for key, value in sorted(methods.items()))


def _review_admin_part(label: str, value: object) -> str:
    if isinstance(value, bool):
        return f"{label}: {_enabled_disabled(value)}"
    return f"{label}: {value}"


def _unknown_or_value(value: object) -> str:
    return "unknown" if value is None else str(value)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _yes_no_unknown(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "unknown"


def _enabled_disabled(value: bool | None) -> str:
    if value is True:
        return "enabled"
    if value is False:
        return "disabled"
    return "unknown"


def _enabled_disabled_unknown(value: bool | None) -> str:
    return _enabled_disabled(value)


def _source(remote: RemoteSnapshot) -> str:
    return f"central repo-settings policy + GitHub {remote.ref} ({remote.sha})"


def _local_source_items(items: Iterable[AuditItem]) -> tuple[AuditItem, ...]:
    return tuple(item for item in items if item.setting in LOCAL_SOURCE_SETTINGS)


def _hosted_governance_items(items: Iterable[AuditItem]) -> tuple[AuditItem, ...]:
    return tuple(item for item in items if item.setting not in LOCAL_SOURCE_SETTINGS)


def _org_summary(reports: Iterable[RepoSettingsReport], *, hosted: bool) -> dict[str, int]:
    items = []
    for report in reports:
        selected = _hosted_governance_items(report.items) if hosted else _local_source_items(report.items)
        items.extend(selected)
    return _summary(items)


def _has_hosted_drift(report: RepoSettingsReport | OrgRepoSettingsReport) -> bool:
    if isinstance(report, RepoSettingsReport):
        return any(item.status == "drift" for item in _hosted_governance_items(report.items))
    return any(_has_hosted_drift(repo_report) for repo_report in report.reports)


def _has_runtime_errors(report: RepoSettingsReport | OrgRepoSettingsReport) -> bool:
    if report.errors:
        return True
    if isinstance(report, OrgRepoSettingsReport):
        return any(_has_runtime_errors(repo_report) for repo_report in report.reports)
    return False


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
