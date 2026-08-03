"""On-demand, source-backed, advisory repository preflight reporting."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from typing import Callable
from urllib.parse import urlsplit, urlunsplit


NOTICE = "This report is advisory, stale after capture, and not a source of truth."


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class EvidenceSource:
    name: str
    status: str
    captured_at: str
    source: str
    facts: dict[str, object]
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepoPreflightReport:
    schema_version: int
    report_type: str
    repository: dict[str, str]
    captured_at: str
    advisory: bool
    notice: str
    overall_source_status: str
    sources: tuple[EvidenceSource, ...]


Clock = Callable[[], str]
Runner = Callable[[Path, tuple[str, ...]], CommandResult]


def inspect_repository(
    repository: Path,
    *,
    include_hosted: bool = False,
    clock: Clock | None = None,
    runner: Runner | None = None,
) -> RepoPreflightReport:
    """Capture direct evidence for one local Git repository without mutation."""
    now = clock or _utc_now
    run = runner or _run
    root = repository.expanduser().resolve()
    if not root.exists():
        raise ValueError(f"repository path does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"repository path is not a directory: {root}")
    probe = run(root, ("git", "rev-parse", "--show-toplevel"))
    if probe.returncode:
        raise ValueError(f"not a Git repository: {root}")
    top = Path(probe.stdout.strip()).resolve()
    if top != root:
        raise ValueError(f"path is inside a Git repository but is not its root: {root}")

    sources = [
        _agents_source(root, now()),
        _make_source(root, now()),
        _git_source(root, now(), run),
    ]
    if include_hosted:
        sources.append(_hosted_source(root, now(), run, sources[-1]))
    status = _overall_status(sources)
    captured_at = now()
    return RepoPreflightReport(
        schema_version=1,
        report_type="repository_preflight",
        repository={"name": root.name, "path": str(root)},
        captured_at=captured_at,
        advisory=True,
        notice=NOTICE,
        overall_source_status=status,
        sources=tuple(sources),
    )


def render_json(report: RepoPreflightReport) -> str:
    return json.dumps(asdict(report), indent=2, sort_keys=True)


def render_markdown(report: RepoPreflightReport) -> str:
    lines = [
        "# Repository Preflight",
        "",
        f"> {report.notice}",
        "",
        f"- Schema version: `{report.schema_version}`",
        f"- Report type: `{report.report_type}`",
        f"- Repository: `{report.repository['name']}`",
        f"- Path: `{report.repository['path']}`",
        f"- Captured at: `{report.captured_at}`",
        f"- Overall source status: `{report.overall_source_status}`",
        "",
    ]
    for source in report.sources:
        lines.extend([
            f"## {source.name}", "",
            f"- Status: `{source.status}`",
            f"- Source: `{source.source}`",
            f"- Captured at: `{source.captured_at}`",
        ])
        if source.errors:
            lines.extend(["- Errors/unavailable reasons:", *[f"  - {error}" for error in source.errors]])
        lines.extend(["", "```json", json.dumps(source.facts, indent=2, sort_keys=True), "```", ""])
    return "\n".join(lines).rstrip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report current source-backed evidence for one repository.")
    parser.add_argument("repository", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--output-format", choices=("markdown", "json"), default="markdown")
    parser.add_argument(
        "--include-hosted",
        action="store_true",
        help="Opt in to read-only GitHub metadata using the existing gh CLI authentication.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = inspect_repository(args.repository, include_hosted=args.include_hosted)
    except (OSError, ValueError) as exc:
        print(f"repo-preflight: {exc}", file=__import__("sys").stderr)
        return 2
    print(render_json(report) if args.output_format == "json" else render_markdown(report))
    return 0


def _agents_source(root: Path, captured_at: str) -> EvidenceSource:
    path = root / "AGENTS.md"
    if not path.is_file():
        return EvidenceSource("repo_local_agents", "unavailable", captured_at, str(path), {"exists": False}, ("AGENTS.md not found",))
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return EvidenceSource("repo_local_agents", "unavailable", captured_at, str(path), {"exists": True}, (f"{type(exc).__name__}: {exc}",))
    headings = [line.lstrip("#").strip() for line in content.splitlines() if re.match(r"^#{1,6}\s+\S", line)]
    return EvidenceSource("repo_local_agents", "available", captured_at, str(path), {"exists": True, "headings": headings})


def _make_source(root: Path, captured_at: str) -> EvidenceSource:
    path = root / "Makefile"
    if not path.is_file():
        return EvidenceSource("validation_tooling", "unavailable", captured_at, str(path), {"makefile_exists": False, "targets": []}, ("Makefile not found; equivalent tooling was not inferred",))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return EvidenceSource("validation_tooling", "unavailable", captured_at, str(path), {"makefile_exists": True, "targets": []}, (f"{type(exc).__name__}: {exc}",))
    targets = []
    for line_number, line in enumerate(lines, 1):
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*):(?:[^=]|$)", line)
        if match and not match.group(1).startswith("."):
            targets.append({"name": match.group(1), "command": f"make {match.group(1)}", "line": line_number})
    return EvidenceSource("validation_tooling", "available", captured_at, str(path), {"makefile_exists": True, "targets": targets})


def _git_source(root: Path, captured_at: str, run: Runner) -> EvidenceSource:
    commands = {
        "current_branch": ("git", "branch", "--show-current"),
        "remotes": ("git", "remote", "-v"),
        "working_tree": ("git", "status", "--porcelain"),
        "default_branch": ("git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"),
    }
    facts: dict[str, object] = {}
    errors: list[str] = []
    for name, command in commands.items():
        result = run(root, command)
        if result.returncode:
            facts[name] = "unknown"
            errors.append(f"{' '.join(command)}: {(result.stderr or 'unavailable').strip()}")
        elif name == "working_tree":
            facts[name] = "dirty" if result.stdout else "clean"
        elif name == "remotes":
            facts[name] = _parse_remotes(result.stdout)
        elif name == "default_branch":
            facts[name] = result.stdout.strip().removeprefix("origin/") or "unknown"
        else:
            facts[name] = result.stdout.strip() or "unknown"
    status = "available" if not errors else "partial" if any(value != "unknown" for value in facts.values()) else "unavailable"
    source = "; ".join(" ".join(command) for command in commands.values())
    return EvidenceSource("git_metadata", status, captured_at, source, facts, tuple(errors))


def _hosted_source(root: Path, captured_at: str, run: Runner, git_source: EvidenceSource) -> EvidenceSource:
    repo = _github_repo_identity(git_source.facts.get("remotes", []))
    if repo is None:
        return EvidenceSource("hosted_repository", "unavailable", captured_at, "configured Git remotes", {}, ("no unambiguous GitHub repository identity",))
    command = ("gh", "api", f"repos/{repo}")
    result = run(root, command)
    if result.returncode:
        return EvidenceSource("hosted_repository", "unavailable", captured_at, " ".join(command), {"repository": repo}, ((result.stderr or "hosted metadata unavailable").strip(),))
    try:
        payload = json.loads(result.stdout)
        facts = {"repository": repo, "visibility": payload["visibility"], "default_branch": payload["default_branch"], "archived": payload["archived"]}
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        return EvidenceSource("hosted_repository", "unavailable", captured_at, " ".join(command), {"repository": repo}, (f"unsupported response: {exc}",))
    return EvidenceSource("hosted_repository", "available", captured_at, " ".join(command), facts)


def _parse_remotes(output: str) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            kind = parts[2].strip("()")
            found[(parts[0], kind)] = {"name": parts[0], "url": _sanitize_remote_url(parts[1]), "kind": kind}
    return [found[key] for key in sorted(found)]


def _sanitize_remote_url(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    sanitized_netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parsed.scheme, sanitized_netloc, parsed.path, "", ""))


def _github_repo_identity(remotes: object) -> str | None:
    if not isinstance(remotes, list):
        return None
    repos = set()
    for remote in remotes:
        if not isinstance(remote, dict):
            continue
        url = str(remote.get("url", ""))
        match = re.match(r"(?:git@github\.com:|https://github\.com/)([^/]+/[^/]+?)(?:\.git)?$", url)
        if match:
            repos.add(match.group(1))
    return repos.pop() if len(repos) == 1 else None


def _overall_status(sources: list[EvidenceSource]) -> str:
    statuses = {source.status for source in sources}
    if statuses == {"available"}:
        return "available"
    if "available" in statuses or "partial" in statuses:
        return "partial"
    return "unavailable"


def _run(cwd: Path, argv: tuple[str, ...]) -> CommandResult:
    result = subprocess.run(argv, cwd=cwd, check=False, capture_output=True, text=True, shell=False)
    return CommandResult(result.returncode, result.stdout, result.stderr)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
