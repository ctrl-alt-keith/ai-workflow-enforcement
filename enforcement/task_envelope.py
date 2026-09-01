"""Lightweight validation for explicit workflow task envelopes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


KNOWN_TASK_TYPES = ("drift_review",)
SUPPORTED_SCHEMA_VERSIONS = (1,)
REQUIRED_FIELDS = (
    "task_type",
    "schema_version",
    "inputs",
    "constraints",
    "expected_outputs",
    "validation_expectations",
)
STRUCTURED_FIELDS = (
    "inputs",
    "constraints",
    "expected_outputs",
    "validation_expectations",
)


class EnvelopeValidationError(ValueError):
    """Raised when a task envelope does not match the supported contract."""


def load_task_envelope(path: Path) -> dict[str, Any]:
    """Load a task envelope JSON object from disk."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EnvelopeValidationError(f"invalid task envelope JSON: {exc.msg}") from exc

    if not isinstance(data, dict):
        raise EnvelopeValidationError("task envelope must be a JSON object")
    return data


def validate_task_envelope(envelope: dict[str, Any]) -> None:
    """Validate the small supported task-envelope contract."""
    missing = [field for field in REQUIRED_FIELDS if field not in envelope]
    if missing:
        raise EnvelopeValidationError(f"missing required fields: {', '.join(missing)}")

    unexpected = sorted(set(envelope) - set(REQUIRED_FIELDS))
    if unexpected:
        raise EnvelopeValidationError(f"unexpected fields: {', '.join(unexpected)}")

    task_type = envelope["task_type"]
    if not isinstance(task_type, str) or task_type not in KNOWN_TASK_TYPES:
        raise EnvelopeValidationError(
            f"unknown task_type: {task_type!r}; expected one of: {', '.join(KNOWN_TASK_TYPES)}"
        )

    schema_version = envelope["schema_version"]
    if type(schema_version) is not int or schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        supported = ", ".join(str(version) for version in SUPPORTED_SCHEMA_VERSIONS)
        raise EnvelopeValidationError(
            f"unsupported schema_version: {schema_version!r}; expected one of: {supported}"
        )

    for field in STRUCTURED_FIELDS:
        if not isinstance(envelope[field], dict):
            raise EnvelopeValidationError(f"{field} must be an object")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a small explicit workflow task envelope.",
    )
    parser.add_argument("path", type=Path, help="Task envelope JSON file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        envelope = load_task_envelope(args.path)
        validate_task_envelope(envelope)
    except (OSError, EnvelopeValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"valid {envelope['task_type']} envelope schema_version {envelope['schema_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
