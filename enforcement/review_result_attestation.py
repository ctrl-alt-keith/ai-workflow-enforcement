"""Lightweight validation for completed drift-review attestations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


KNOWN_ATTESTATION_TYPES = ("drift_review_result",)
SUPPORTED_SCHEMA_VERSIONS = (1,)
KNOWN_SOURCE_TASK_TYPES = ("drift_review",)
REQUIRED_FIELDS = (
    "attestation_type",
    "schema_version",
    "source_task_type",
    "classification",
    "cleanup_required",
    "reviewer_type",
    "evidence_summary",
)


class AttestationValidationError(ValueError):
    """Raised when an attestation does not match the supported contract."""


def load_review_result_attestation(path: Path) -> dict[str, Any]:
    """Load a review-result attestation JSON object from disk."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AttestationValidationError(f"invalid review-result attestation JSON: {exc.msg}") from exc

    if not isinstance(data, dict):
        raise AttestationValidationError("review-result attestation must be a JSON object")
    return data


def validate_review_result_attestation(attestation: dict[str, Any]) -> None:
    """Validate the small supported review-result attestation contract."""
    missing = [field for field in REQUIRED_FIELDS if field not in attestation]
    if missing:
        raise AttestationValidationError(f"missing required fields: {', '.join(missing)}")

    attestation_type = attestation["attestation_type"]
    if not isinstance(attestation_type, str) or attestation_type not in KNOWN_ATTESTATION_TYPES:
        raise AttestationValidationError(
            "unknown attestation_type: "
            f"{attestation_type!r}; expected one of: {', '.join(KNOWN_ATTESTATION_TYPES)}"
        )

    schema_version = attestation["schema_version"]
    if type(schema_version) is not int or schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        supported = ", ".join(str(version) for version in SUPPORTED_SCHEMA_VERSIONS)
        raise AttestationValidationError(
            f"unsupported schema_version: {schema_version!r}; expected one of: {supported}"
        )

    source_task_type = attestation["source_task_type"]
    if not isinstance(source_task_type, str) or source_task_type not in KNOWN_SOURCE_TASK_TYPES:
        raise AttestationValidationError(
            "unknown source_task_type: "
            f"{source_task_type!r}; expected one of: {', '.join(KNOWN_SOURCE_TASK_TYPES)}"
        )

    if not isinstance(attestation["classification"], str) or not attestation["classification"].strip():
        raise AttestationValidationError("classification must be a non-empty string")

    if type(attestation["cleanup_required"]) is not bool:
        raise AttestationValidationError("cleanup_required must be a boolean")

    if not isinstance(attestation["reviewer_type"], str) or not attestation["reviewer_type"].strip():
        raise AttestationValidationError("reviewer_type must be a non-empty string")

    if not isinstance(attestation["evidence_summary"], str) or not attestation["evidence_summary"].strip():
        raise AttestationValidationError("evidence_summary must be a non-empty string")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a small completed drift-review attestation.",
    )
    parser.add_argument("path", type=Path, help="Review-result attestation JSON file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        attestation = load_review_result_attestation(args.path)
        validate_review_result_attestation(attestation)
    except (OSError, AttestationValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        "valid "
        f"{attestation['attestation_type']} attestation schema_version {attestation['schema_version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
