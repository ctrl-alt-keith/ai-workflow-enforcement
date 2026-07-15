"""Report open GitHub pull requests and issues across an organization."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import subprocess
import sys
from typing import Callable, Iterable


DEFAULT_ORG = "ctrl-alt-keith"
AUTOMATION_ID = "org-pr-issue-scan"
AUTOMATION_DISPLAY_NAME = "🔎 Org PR and Issue Scan"


@dataclass(frozen=True)
class GhCommand:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Repository:
    name: str
    full_name: str
    url: str


@dataclass(frozen=True)
class WorkItem:
    repo: str
    number: int
    title: str
    url: str
    author: str
    labels: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    updated_at: str = ""


@dataclass
class RepositoryWork:
    name: str
    full_name: str
    url: str
    pull_requests: list[WorkItem] = field(default_factory=list)
    issues: list[WorkItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OrgWorkReport:
    schema_version: int
    report_type: str
    automation_id: str
    automation_display_name: str
    org: str
    selected_repositories: tuple[str, ...]
    started_at: str
    finished_at: str
    repositories: tuple[RepositoryWork, ...]
    errors: tuple[str, ...] = ()


Runner = Callable[[tuple[str, ...]], GhCommand]


def scan_org_work(
    org: str = DEFAULT_ORG,
    *,
    selected_repos: Iterable[str] = (),
    runner: Runner | None = None,
) -> OrgWorkReport:
    """Collect current open pull requests and issues for all visible org repos."""
    started = _utc_now()
    gh = runner or _gh
    repositories, errors = _fetch_repositories(org, gh)
    selected = _repo_selection_names(org, selected_repos)
    if selected and not errors:
        repositories = _select_repositories(org, repositories, selected)
    repo_reports = tuple(_scan_repository(org, repo, gh) for repo in sorted(repositories, key=lambda item: item.name.lower()))
    finished = _utc_now()
    return OrgWorkReport(
        schema_version=1,
        report_type="org_pr_issue_scan",
        automation_id=AUTOMATION_ID,
        automation_display_name=AUTOMATION_DISPLAY_NAME,
        org=org,
        selected_repositories=selected,
        started_at=started,
        finished_at=finished,
        repositories=repo_reports,
        errors=errors,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report open pull requests and issues across a GitHub organization.")
    parser.add_argument("--org", default=DEFAULT_ORG, help=f"GitHub organization to scan. Default: {DEFAULT_ORG}.")
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        help="Repository name to scan after org enumeration. May be repeated; accepts name or org/name.",
    )
    parser.add_argument(
        "--output-format",
        choices=("text", "json"),
        default="text",
        help="Report format. Default is human-readable text.",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Exit 1 if repository enumeration or per-repository collection reports incomplete coverage.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = scan_org_work(args.org, selected_repos=args.repo)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.output_format == "json":
        print(render_json_report(report))
    else:
        print(render_text_report(report))
    if args.fail_on_error and _has_runtime_errors(report):
        return 1
    return 0


def render_text_report(report: OrgWorkReport) -> str:
    total_prs = sum(len(repo.pull_requests) for repo in report.repositories)
    total_issues = sum(len(repo.issues) for repo in report.repositories)
    skipped = [repo for repo in report.repositories if repo.skipped]
    lines = [
        report.automation_display_name,
        f"Automation ID: {report.automation_id}",
        f"Organization: {report.org}",
        *([f"Repository filter: {', '.join(report.selected_repositories)}"] if report.selected_repositories else []),
        f"Started: {report.started_at}",
        f"Finished: {report.finished_at}",
        f"Repositories scanned: {len(report.repositories)}",
        f"Open pull requests: {total_prs}",
        f"Open issues: {total_issues}",
        f"Skipped or partial repositories: {len(skipped)}",
        "",
    ]
    for error in report.errors:
        lines.append(f"ERROR: {error}")
    if report.errors:
        lines.append("")

    if not report.repositories:
        lines.append("No repositories scanned.")
        return "\n".join(lines).rstrip()

    for repo in report.repositories:
        lines.append(f"{repo.name}:")
        if repo.url:
            lines.append(f"  url: {repo.url}")
        for reason in repo.skipped:
            lines.append(f"  skipped: {reason}")
        lines.append("  Pull requests:")
        _append_items(lines, repo.pull_requests)
        lines.append("  Issues:")
        _append_items(lines, repo.issues)
        lines.append("")
    return "\n".join(lines).rstrip()


def render_json_report(report: OrgWorkReport) -> str:
    return json.dumps(report_to_dict(report), indent=2, sort_keys=True)


def report_to_dict(report: OrgWorkReport) -> dict[str, object]:
    """Return the structured representation used by the JSON report."""
    return {
        "schema_version": report.schema_version,
        "report_type": report.report_type,
        "automation_id": report.automation_id,
        "automation_display_name": report.automation_display_name,
        "org": report.org,
        "selected_repositories": list(report.selected_repositories),
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "errors": list(report.errors),
        "summary": {
            "repository_count": len(report.repositories),
            "open_pull_request_count": sum(len(repo.pull_requests) for repo in report.repositories),
            "open_issue_count": sum(len(repo.issues) for repo in report.repositories),
            "skipped_repository_count": sum(1 for repo in report.repositories if repo.skipped),
        },
        "repositories": [
            {
                "name": repo.name,
                "full_name": repo.full_name,
                "url": repo.url,
                "skipped": list(repo.skipped),
                "pull_requests": [_item_to_json(item) for item in repo.pull_requests],
                "issues": [_item_to_json(item) for item in repo.issues],
            }
            for repo in report.repositories
        ],
    }


def _scan_repository(org: str, repo: Repository, runner: Runner) -> RepositoryWork:
    report = RepositoryWork(name=repo.name, full_name=repo.full_name, url=repo.url)
    pr_endpoint = f"/repos/{org}/{repo.name}/pulls?state=open&per_page=100"
    issue_endpoint = f"/repos/{org}/{repo.name}/issues?state=open&per_page=100"

    prs, pr_error = _fetch_collection(pr_endpoint, runner)
    if pr_error:
        report.skipped.append(f"pull requests inaccessible: {pr_error}")
    else:
        report.pull_requests.extend(_work_item(repo.name, item) for item in prs)

    issues, issue_error = _fetch_collection(issue_endpoint, runner)
    if issue_error:
        report.skipped.append(f"issues inaccessible: {issue_error}")
    else:
        report.issues.extend(_work_item(repo.name, item) for item in issues if "pull_request" not in item)

    return report


def _fetch_repositories(org: str, runner: Runner) -> tuple[tuple[Repository, ...], tuple[str, ...]]:
    endpoint = f"/orgs/{org}/repos?type=all&per_page=100"
    payload, error = _fetch_collection(endpoint, runner)
    if error:
        return (), (f"repository enumeration failed: {error}",)
    repositories = tuple(
        Repository(
            name=str(item.get("name", "")),
            full_name=str(item.get("full_name", "")),
            url=str(item.get("html_url", "")),
        )
        for item in payload
        if item.get("name")
    )
    return repositories, ()


def _repo_selection_names(org: str, selected_repos: Iterable[str]) -> tuple[str, ...]:
    names: list[str] = []
    for raw in selected_repos:
        value = raw.strip()
        if not value:
            continue
        if "/" in value:
            owner, repo_name = value.split("/", 1)
            if owner == org and repo_name and "/" not in repo_name:
                value = repo_name
        if value not in names:
            names.append(value)
    return tuple(names)


def _select_repositories(
    org: str,
    repositories: tuple[Repository, ...],
    selected_names: tuple[str, ...],
) -> tuple[Repository, ...]:
    by_name = {repo.name: repo for repo in repositories}
    unknown = tuple(name for name in selected_names if name not in by_name)
    if unknown:
        raise ValueError(f"selected repositories not found in {org}: {', '.join(unknown)}")
    return tuple(by_name[name] for name in selected_names)


def _fetch_collection(endpoint: str, runner: Runner) -> tuple[tuple[dict[str, object], ...], str]:
    result = runner(("gh", "api", "--paginate", "--slurp", endpoint))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"exit {result.returncode}").splitlines()[0]
        return (), detail
    try:
        data = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        return (), f"invalid JSON from gh api: {exc}"
    return tuple(_flatten_collection(data)), ""


def _flatten_collection(data: object) -> Iterable[dict[str, object]]:
    if isinstance(data, dict):
        yield data
        return
    if not isinstance(data, list):
        return
    for item in data:
        if isinstance(item, list):
            yield from _flatten_collection(item)
        elif isinstance(item, dict):
            yield item


def _work_item(repo: str, item: dict[str, object]) -> WorkItem:
    return WorkItem(
        repo=repo,
        number=int(item.get("number", 0)),
        title=str(item.get("title", "")),
        url=str(item.get("html_url", "")),
        author=_login(item.get("user")),
        labels=_labels(item.get("labels")),
        assignees=_assignees(item.get("assignees")),
        updated_at=str(item.get("updated_at", "")),
    )


def _labels(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item.get("name", "")) for item in value if isinstance(item, dict) and item.get("name"))


def _assignees(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(_login(item) for item in value if _login(item))


def _login(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("login", ""))


def _append_items(lines: list[str], items: list[WorkItem]) -> None:
    if not items:
        lines.append("    none")
        return
    for item in items:
        labels = ", ".join(item.labels) if item.labels else "none"
        assignees = ", ".join(item.assignees) if item.assignees else "none"
        lines.append(f"    - #{item.number} {item.title}")
        lines.append(f"      url: {item.url}")
        lines.append(f"      author: {item.author or 'unknown'}")
        lines.append(f"      labels: {labels}")
        lines.append(f"      assignees: {assignees}")
        lines.append(f"      updated_at: {item.updated_at or 'unknown'}")


def _item_to_json(item: WorkItem) -> dict[str, object]:
    return {
        "repo": item.repo,
        "number": item.number,
        "title": item.title,
        "url": item.url,
        "author": item.author,
        "labels": list(item.labels),
        "assignees": list(item.assignees),
        "updated_at": item.updated_at,
    }


def _has_runtime_errors(report: OrgWorkReport) -> bool:
    return bool(report.errors) or any(repo.skipped for repo in report.repositories)


def _gh(argv: tuple[str, ...]) -> GhCommand:
    process = subprocess.run(
        argv,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    return GhCommand(
        argv=argv,
        returncode=process.returncode,
        stdout=process.stdout.strip(),
        stderr=process.stderr.strip(),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
