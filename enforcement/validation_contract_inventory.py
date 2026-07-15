"""Report-only inventory of repository-local validation contracts."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date
import json
from pathlib import Path
import re
import sys
from typing import Callable, Iterable

from .repo_preflight import inspect_repository

NOTICE = "This inventory is advisory, report-only, and preserves repository-local authority."
CLASSIFICATIONS = ("Match", "Mismatch", "Unclear", "Not applicable")
_MAKE_COMMAND = re.compile(
    r"`make\s+([A-Za-z0-9][A-Za-z0-9_.-]*)[^`]*`|(?:^\s*|\b(?:run|use)\s+)make\s+([A-Za-z0-9][A-Za-z0-9_.-]*)\b",
    re.I,
)
_AMBIGUOUS_VALIDATION = re.compile(r"\b(?:run|runs|running|use|uses)\b.{0,48}\b(?:tests?|validation|checks?)\b", re.I)


@dataclass(frozen=True)
class Evidence:
    source_type: str
    path: str
    line: int | None
    target: str | None
    command: str | None
    snippet: str


@dataclass(frozen=True)
class ContractFinding:
    repository: str
    classification: str
    claimed_validation: str
    observed_evidence: str
    evidence_source: tuple[Evidence, ...]
    capture_date: str
    confidence: str
    recommendation: str | None


@dataclass(frozen=True)
class RepositoryReview:
    repository: str
    path: str
    classification: str
    contracts_evaluated: int
    findings: tuple[ContractFinding, ...]


@dataclass(frozen=True)
class ValidationContractInventory:
    schema_version: int
    report_type: str
    advisory: bool
    persistent_inventory: bool
    notice: str
    capture_date: str
    repositories: tuple[RepositoryReview, ...]
    classification_counts: dict[str, int]


Clock = Callable[[], str]


def inventory_validation_contracts(repositories: Iterable[Path], *, clock: Clock | None = None) -> ValidationContractInventory:
    """Compare directly stated Make validation claims with local Makefile targets."""
    capture_date = (clock or _today)()
    reviews = tuple(_review_repository(path, capture_date) for path in sorted(repositories, key=str))
    counts = {classification: 0 for classification in CLASSIFICATIONS}
    for review in reviews:
        for finding in review.findings:
            counts[finding.classification] += 1
    return ValidationContractInventory(1, "validation_contract_inventory", True, False, NOTICE, capture_date, reviews, counts)


def render_json(report: ValidationContractInventory) -> str:
    return json.dumps(asdict(report), indent=2, sort_keys=True)


def render_markdown(report: ValidationContractInventory) -> str:
    lines = ["# Validation Contract Inventory", "", f"> {report.notice}", "", "## Executive summary", "",
             f"- Capture date: `{report.capture_date}`", f"- Repositories reviewed: {len(report.repositories)}",
             f"- Contracts evaluated: {sum(review.contracts_evaluated for review in report.repositories)}",
             *[f"- {name}: {report.classification_counts[name]}" for name in CLASSIFICATIONS],
             "- Repository scoring: not performed", "", "## Repositories reviewed", ""]
    for review in report.repositories:
        lines.append(f"- `{review.repository}` — {review.classification}; {review.contracts_evaluated} contract(s)")
    for heading, classification in (("Matching contracts", "Match"), ("Mismatches", "Mismatch"),
                                    ("Unclear contracts", "Unclear"), ("Not-applicable repositories", "Not applicable")):
        lines.extend(["", f"## {heading}", ""])
        findings = [finding for review in report.repositories for finding in review.findings if finding.classification == classification]
        if not findings:
            lines.append("None.")
        for finding in findings:
            lines.extend(_render_finding(finding))
    lines.extend(["", "## Repository-local recommendations", ""])
    recommendations = [finding for review in report.repositories for finding in review.findings if finding.recommendation]
    if not recommendations:
        lines.append("None.")
    for finding in recommendations:
        lines.append(f"- `{finding.repository}`: {finding.recommendation}")
    return "\n".join(lines).rstrip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report repository-local validation contract evidence.")
    parser.add_argument("repositories", nargs="+", type=Path, help="Local Git repository roots to review.")
    parser.add_argument("--output-format", choices=("markdown", "json"), default="markdown")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = inventory_validation_contracts(args.repositories)
    except (OSError, ValueError) as exc:
        print(f"validation-contract-inventory: {exc}", file=sys.stderr)
        return 2
    print(render_json(report) if args.output_format == "json" else render_markdown(report))
    return 0


def _review_repository(path: Path, capture_date: str) -> RepositoryReview:
    root = path.expanduser().resolve()
    preflight = inspect_repository(root)
    make_source = next(source for source in preflight.sources if source.name == "validation_tooling")
    target_rows = make_source.facts.get("targets", [])
    targets = {str(row["name"]): row for row in target_rows if isinstance(row, dict) and "name" in row}
    claims, ambiguous = _collect_claims(root)
    findings: list[ContractFinding] = []
    for command, claim_evidence in claims:
        target = command.split(maxsplit=1)[1]
        row = targets.get(target)
        evidence = list(claim_evidence)
        if row is not None:
            evidence.append(Evidence("implementation", "Makefile", int(row["line"]), target, command, f"target `{target}`"))
            values = ("Match", f"Makefile defines target `{target}`.", "High", None)
        elif make_source.facts.get("makefile_exists"):
            evidence.append(Evidence("implementation", "Makefile", None, target, command, f"target `{target}` not found"))
            values = ("Mismatch", f"Makefile exists but does not define target `{target}`.", "High",
                      f"Reconcile the repository-local `{command}` claim with its Makefile target surface.")
        else:
            evidence.append(Evidence("implementation", "Makefile", None, target, command, "Makefile not found; equivalent tooling not inferred"))
            values = ("Unclear", "No Makefile was found; equivalent validation tooling was not inferred.", "Medium",
                      f"Clarify the observable implementation for the repository-local `{command}` claim.")
        findings.append(ContractFinding(root.name, values[0], command, values[1], tuple(evidence), capture_date, values[2], values[3]))
    for evidence in ambiguous:
        findings.append(ContractFinding(root.name, "Unclear", evidence.snippet,
            "The claim does not identify a directly comparable validation command or target.", (evidence,), capture_date, "Low",
            "Name the repository-local validation command if this prose is intended as an executable contract."))
    if not findings and targets:
        evidence = tuple(Evidence("implementation", "Makefile", int(row["line"]), str(row["name"]), str(row["command"]), f"target `{row['name']}`") for row in target_rows)
        findings.append(ContractFinding(root.name, "Unclear", "No explicit repository-local validation claim found.",
            "Makefile targets exist, but documentation does not identify which target is the validation contract.", evidence, capture_date, "Medium",
            "Document the repository-local validation entrypoint if one of the observed targets is canonical."))
    if not findings:
        findings.append(ContractFinding(root.name, "Not applicable", "No repository-local validation claim found.",
            "No Makefile validation surface was found.",
            (Evidence("documentation", "AGENTS.md / README / docs", None, None, None, "no validation claim found"),),
            capture_date, "Medium", None))
    classification = next(name for name in ("Mismatch", "Unclear", "Match", "Not applicable") if any(f.classification == name for f in findings))
    return RepositoryReview(root.name, str(root), classification, len(findings), tuple(findings))


def _collect_claims(root: Path) -> tuple[list[tuple[str, tuple[Evidence, ...]]], list[Evidence]]:
    explicit: dict[str, list[Evidence]] = {}
    ambiguous: list[Evidence] = []
    for path in _documentation_paths(root):
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            matches = list(_MAKE_COMMAND.finditer(line))
            for match in matches:
                target = match.group(1) or match.group(2)
                if not any(term in target.lower() for term in ("check", "test", "lint", "valid")):
                    continue
                command = f"make {target}"
                explicit.setdefault(command, []).append(Evidence("claim", relative, line_number, target, command, line.strip()))
            if not matches and _AMBIGUOUS_VALIDATION.search(line):
                ambiguous.append(Evidence("claim", relative, line_number, None, None, line.strip()))
    claims = [(command, tuple(explicit[command])) for command in sorted(explicit)]
    if claims:
        ambiguous = []
    return claims, sorted(ambiguous, key=lambda item: (item.path, item.line or 0, item.snippet))


def _documentation_paths(root: Path) -> tuple[Path, ...]:
    paths = {root / name for name in ("AGENTS.md", "README.md", "README") if (root / name).is_file()}
    docs = root / "docs"
    if docs.is_dir():
        paths.update(path for path in docs.rglob("*.md") if any(term in path.name.lower() for term in ("valid", "develop", "contribut", "workflow")))
    return tuple(sorted(paths))


def _render_finding(finding: ContractFinding) -> list[str]:
    lines = [f"### `{finding.repository}` — {finding.claimed_validation}", "", f"- Classification: {finding.classification}",
             f"- Observed evidence: {finding.observed_evidence}", f"- Capture date: `{finding.capture_date}`",
             f"- Confidence: {finding.confidence}", "- Evidence source:"]
    for evidence in finding.evidence_source:
        location = evidence.path + (f":{evidence.line}" if evidence.line is not None else "")
        details = [f"`{location}`", evidence.source_type]
        if evidence.target:
            details.append(f"target `{evidence.target}`")
        if evidence.command:
            details.append(f"command `{evidence.command}`")
        details.append(evidence.snippet)
        lines.append(f"  - {' — '.join(details)}")
    if finding.recommendation:
        lines.append(f"- Recommendation: {finding.recommendation}")
    lines.append("")
    return lines


def _today() -> str:
    return date.today().isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
