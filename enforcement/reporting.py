"""Human-readable drift scan reports."""

from __future__ import annotations

import os
from pathlib import Path

from .drift_scanner import OverlapCandidate, ScanResult


def render_report(result: ScanResult, *, base_dir: Path | None = None) -> str:
    base = base_dir or Path.cwd()
    lines: list[str] = [
        "Notes vs playbook drift scan",
        "",
        f"Notes files scanned: {result.notes_files_scanned}",
        f"Playbook files scanned: {result.playbook_files_scanned}",
        f"Ignored paths: {len(result.ignored_paths)}",
        "",
    ]

    if not result.candidates:
        lines.append("No overlap candidates found.")
        return "\n".join(lines)

    lines.append(f"Overlap candidates: {len(result.candidates)}")
    for index, candidate in enumerate(result.candidates, start=1):
        lines.extend(_render_candidate(index, candidate, base))
    return "\n".join(lines)


def _render_candidate(index: int, candidate: OverlapCandidate, base: Path) -> list[str]:
    lines = [
        "",
        f"{index}. Possible overlap",
        f"   Note: {_rel(candidate.note_path, base)}",
        f"   Possible canonical target: {_rel(candidate.playbook_path, base)}",
        f"   Reasons: {', '.join(candidate.reasons)}",
        f"   Token similarity: {candidate.similarity:.2f}",
        f"   Canonical reference present: {'yes' if candidate.has_canonical_reference else 'no'}",
        f"   Suggested direction: {candidate.suggested_direction}",
    ]
    if candidate.repeated_headings:
        lines.append(f"   Repeated headings: {_join_preview(candidate.repeated_headings)}")
    if candidate.repeated_phrases:
        lines.append("   Repeated phrases:")
        for phrase in candidate.repeated_phrases:
            lines.append(f"     - {phrase}")
    return lines


def _rel(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return Path(os.path.relpath(path, base)).as_posix()


def _join_preview(values: tuple[str, ...], limit: int = 5) -> str:
    shown = values[:limit]
    suffix = "" if len(values) <= limit else f" (+{len(values) - limit} more)"
    return ", ".join(shown) + suffix
