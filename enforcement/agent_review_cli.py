"""Explicit local entrypoint for a read-only agent inventory run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from .agent_review import refresh_provider_docs, review, write_record


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect explicitly enrolled local agent files without changing them.")
    parser.add_argument("--enrollment", type=Path, required=True, help="Operator-owned local JSON enrollment.")
    parser.add_argument("--record", type=Path, required=True, help="Declared new immutable local record path.")
    parser.add_argument("--previous", type=Path, help="Previous successful local record.")
    parser.add_argument("--baseline", type=Path, help="Separately human-accepted local baseline record.")
    args = parser.parse_args(argv)
    try:
        enrollment = _load(args.enrollment)
        record_root = enrollment.get("record_root")
        retention = enrollment.get("record_retention")
        if not isinstance(record_root, str) or not Path(record_root).is_absolute() or not retention:
            raise ValueError("enrollment requires declared record_root and record_retention")
        if args.record.parent != Path(record_root) or not re.fullmatch(
                r"[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9_-]+\.json", args.record.name):
            raise ValueError("record must use a dated name directly under declared record_root")
        destination = Path(record_root).resolve()
        if not destination.is_dir() or args.record.exists():
            raise ValueError("declared record destination is unavailable or already exists")
        implementation_root = Path(__file__).resolve().parents[1]
        if destination == implementation_root or implementation_root in destination.parents:
            raise ValueError("operational records cannot be stored in the implementation repository")
        for agent in enrollment.get("agents", []):
            if not isinstance(agent, dict):
                continue
            root = agent.get("config_root", agent.get("root"))
            if isinstance(root, str) and Path(root).is_absolute():
                inspected_root = Path(root).resolve()
                if destination == inspected_root or inspected_root in destination.parents:
                    raise ValueError("operational records cannot be stored under an inspected agent root")
            for project in agent.get("projects", []):
                if isinstance(project, str) and Path(project).is_absolute():
                    inspected_project = Path(project).resolve()
                    if destination == inspected_project or inspected_project in destination.parents:
                        raise ValueError("operational records cannot be stored under an enrolled project")
        previous = _load(args.previous) if args.previous else None
        baseline = _load(args.baseline) if args.baseline else None
        docs = refresh_provider_docs(enrollment)
        report = review(enrollment, previous, baseline, docs)
        write_record(report, args.record)
    except (OSError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        print(f"agent review blocked: {type(exc).__name__}", file=sys.stderr)
        return 2
    changes = ("new", "resolved", "source_new", "source_resolved", "provider_docs_changed")
    changed = any(report[name]["status"] == "scope_changed" or
                  any(report[name][key] for key in changes)
                  for name in ("previous", "accepted_baseline"))
    first_actionable = previous is None and any(
        unit["disposition"] in {"PRUNE_CANDIDATE_REDUNDANT", "PRUNE_CANDIDATE_STALE",
                                "PRUNE_CANDIDATE_UNSUPPORTED", "REVIEW_NARROWING", "REVIEW_RATIONALE"}
        for unit in report["units"])
    attested_count = sum(item["status"] == "operator_attested" for item in report["coverage"])
    if report["result"] != "OBSERVED" or changed or first_actionable or (previous is None and attested_count):
        print(json.dumps({"result": report["result"], "record": "created",
                          "baseline_status": report["baseline_status"],
                          "attested_context_domains": attested_count,
                          "new_findings": len(report["previous"]["new"]),
                          "resolved_findings": len(report["previous"]["resolved"]),
                          "new_source_observations": len(report["previous"]["source_new"]),
                          "resolved_source_observations": len(report["previous"]["source_resolved"]),
                          "provider_docs_changed": len(report["previous"]["provider_docs_changed"]),
                          "baseline_new": len(report["accepted_baseline"]["new"]),
                          "baseline_resolved": len(report["accepted_baseline"]["resolved"]),
                          "baseline_source_changes": len(report["accepted_baseline"]["source_new"]) +
                              len(report["accepted_baseline"]["source_resolved"]),
                          "baseline_provider_docs_changed": len(
                              report["accepted_baseline"]["provider_docs_changed"])}, sort_keys=True))
    return 0 if report["result"] == "OBSERVED" and report["baseline_status"] != "drift" else 1


if __name__ == "__main__":
    raise SystemExit(main())
