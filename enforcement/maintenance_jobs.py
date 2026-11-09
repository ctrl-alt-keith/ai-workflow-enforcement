"""Read-only validation of versioned weekly maintenance job specifications."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any


class JobValidationError(ValueError):
    """A catalog or its prompt files do not match the supported contract."""


def _fields(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise JobValidationError(f"{label} requires exactly: {', '.join(sorted(expected))}")


def load_catalog(path: Path) -> dict[str, Any]:
    """Validate one catalog and its adjacent prompts without executing them."""
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise JobValidationError("catalog must be UTF-8 JSON") from exc

    _fields(catalog, {"schema_version", "jobs"}, "catalog")
    if type(catalog["schema_version"]) is not int or catalog["schema_version"] != 1:
        raise JobValidationError("schema_version must be 1")
    jobs = catalog["jobs"]
    if not isinstance(jobs, list) or not jobs:
        raise JobValidationError("jobs must be a nonempty array")
    seen: set[str] = set()
    for job in jobs:
        _fields(job, {"id", "title", "cadence", "prompt_file"}, "job")
        job_id = job["id"]
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", job_id):
            raise JobValidationError("job id must be a lowercase hyphenated name")
        if job_id in seen:
            raise JobValidationError(f"duplicate job id: {job_id}")
        seen.add(job_id)
        if not isinstance(job["title"], str) or not job["title"].strip():
            raise JobValidationError(f"{job_id}: title must be nonempty text")
        if job["cadence"] != "weekly":
            raise JobValidationError(f"{job_id}: cadence must be weekly")
        filename = job["prompt_file"]
        if not isinstance(filename, str) or not re.fullmatch(r"[a-z0-9-]+\.md", filename):
            raise JobValidationError(f"{job_id}: prompt_file must be an adjacent Markdown basename")
        prompt_path = path.parent / filename
        if prompt_path.is_symlink() or not prompt_path.is_file():
            raise JobValidationError(f"{job_id}: prompt_file must be an existing regular file")
        try:
            prompt = prompt_path.read_bytes().decode("utf-8")
        except UnicodeError as exc:
            raise JobValidationError(f"{job_id}: prompt must be UTF-8") from exc
        if not prompt.strip() or prompt.startswith("\ufeff") or "\r" in prompt or not prompt.endswith("\n"):
            raise JobValidationError(f"{job_id}: prompt must be nonempty UTF-8 without BOM, with LF and a final newline")
    return catalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Weekly job catalog JSON file.")
    args = parser.parse_args(argv)
    try:
        catalog = load_catalog(args.path)
    except (OSError, JobValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"valid weekly maintenance catalog: {len(catalog['jobs'])} jobs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
