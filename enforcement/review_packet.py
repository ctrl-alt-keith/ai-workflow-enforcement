"""Markdown review-packet renderer for drift scan JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a local markdown review packet from drift scan JSON.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Drift scan JSON file. Omit or use '-' to read from stdin.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _read_input(args.input)
        packet = render_review_packet(load_scan_json(payload))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(packet)
    return 0


def load_scan_json(payload: str) -> dict[str, Any]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid drift scan JSON: {exc.msg}") from exc

    if not isinstance(data, dict):
        raise ValueError("drift scan JSON must be an object")
    if data.get("report_type") != "notes_playbook_drift_scan":
        raise ValueError("drift scan JSON report_type must be notes_playbook_drift_scan")
    if data.get("advisory") is not True:
        raise ValueError("drift scan JSON must be marked advisory")
    if not isinstance(data.get("summary"), dict):
        raise ValueError("drift scan JSON summary must be an object")
    if not isinstance(data.get("candidates"), list):
        raise ValueError("drift scan JSON candidates must be a list")
    return data


def render_review_packet(scan: dict[str, Any]) -> str:
    summary = scan["summary"]
    candidates = scan["candidates"]
    candidate_count = _int_field(summary, "candidate_count")
    if candidate_count != len(candidates):
        raise ValueError("summary candidate_count must match candidates length")

    lines = [
        "# Drift Scan Review Packet",
        "",
        (
            "This packet is a local handoff artifact built from advisory drift scan "
            "JSON. It is a review aid only; final classification remains "
            "human-reviewed."
        ),
        "",
        "## Scan Summary",
        "",
        f"- Report type: {scan['report_type']}",
        f"- Schema version: {scan.get('schema_version', 'unknown')}",
        "- Advisory input: yes",
        f"- Notes files scanned: {_int_field(summary, 'notes_files_scanned')}",
        f"- Playbook files scanned: {_int_field(summary, 'playbook_files_scanned')}",
        f"- Ignored paths: {_int_field(summary, 'ignored_path_count')}",
        f"- Candidate count: {candidate_count}",
        "",
        "## Candidate Evidence",
        "",
    ]

    if not candidates:
        lines.append("No overlap candidates were present in the scan JSON.")
    else:
        for index, candidate in enumerate(candidates, start=1):
            lines.extend(_render_candidate(index, candidate))

    lines.extend(
        [
            "",
            "## Suggested Reviewer Questions",
            "",
            (
                "- Does each candidate represent confirmed drift, acceptable "
                "duplication, intentional staging overlap, historical residue, or "
                "a false positive?"
            ),
            (
                "- What evidence supports the reviewer classification, and what "
                "uncertainty remains?"
            ),
            (
                "- Should the local note retain only repository-specific evidence "
                "or context while referencing canonical playbook guidance?"
            ),
            "- Is any separate follow-up needed after human review?",
            "",
            "## Human Review Reminder",
            "",
            (
                "The drift scan JSON is advisory signal input. This packet does not "
                "infer final classifications, create work items, clean up content, "
                "or make policy decisions."
            ),
        ]
    )
    return "\n".join(lines)


def _render_candidate(index: int, candidate: object) -> list[str]:
    if not isinstance(candidate, dict):
        raise ValueError("each candidate must be an object")

    lines = [
        f"### Candidate {index}",
        "",
        f"- Note: {_str_field(candidate, 'note_path')}",
        f"- Possible canonical target: {_str_field(candidate, 'playbook_path')}",
        f"- Scanner reasons: {_join_values(candidate, 'reasons')}",
        f"- Token similarity: {_number_field(candidate, 'similarity'):.4f}",
        (
            "- Canonical reference present: "
            f"{'yes' if _bool_field(candidate, 'canonical_reference_present') else 'no'}"
        ),
        f"- Scanner suggested direction: {_str_field(candidate, 'suggested_direction')}",
    ]

    repeated_headings = _list_field(candidate, "repeated_headings")
    if repeated_headings:
        lines.extend(["", "Repeated headings:"])
        lines.extend(f"- {heading}" for heading in repeated_headings)

    repeated_phrases = _list_field(candidate, "repeated_phrases")
    if repeated_phrases:
        lines.extend(["", "Repeated phrases:"])
        lines.extend(f"- {phrase}" for phrase in repeated_phrases)

    lines.append("")
    return lines


def _read_input(path: Path | None) -> str:
    if path is None or path.as_posix() == "-":
        return sys.stdin.read()
    return path.read_text(encoding="utf-8")


def _int_field(data: dict[str, Any], name: str) -> int:
    value = data.get(name)
    if not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _number_field(data: dict[str, Any], name: str) -> float:
    value = data.get(name)
    if not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number")
    return float(value)


def _bool_field(data: dict[str, Any], name: str) -> bool:
    value = data.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _str_field(data: dict[str, Any], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _list_field(data: dict[str, Any], name: str) -> list[str]:
    value = data.get(name)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a list of strings")
    return value


def _join_values(data: dict[str, Any], name: str) -> str:
    values = _list_field(data, name)
    return ", ".join(values) if values else "none"


if __name__ == "__main__":
    raise SystemExit(main())
