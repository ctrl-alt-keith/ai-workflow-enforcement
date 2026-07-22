"""Command-line entrypoint for one hosted stewardship run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from .config import load_config
from .engine import StewardshipEngine
from .github import GitHubGateway
from .models import ENGINE_SCHEMA_VERSION, STRATEGY_ID, STRATEGY_REVISION, StewardshipReceipt


SUCCESS_TERMINALS = {
    "eligible_no_change",
    "dry_run_complete",
    "delivery_succeeded",
    "skipped_existing_pr",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--mode", required=True, choices=("dry-run", "propose"))
    parser.add_argument("--target-ref", default="")
    parser.add_argument("--run-identifier", required=True)
    parser.add_argument("--engine-revision", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/hosted-stewardship.json"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    read_token = os.environ.get("STEWARDSHIP_READ_TOKEN", "")
    write_token = os.environ.get("STEWARDSHIP_WRITE_TOKEN", "")
    gateway = GitHubGateway(read_token=read_token, write_token=write_token)
    os.environ.pop("STEWARDSHIP_READ_TOKEN", None)
    os.environ.pop("STEWARDSHIP_WRITE_TOKEN", None)
    try:
        config = load_config(args.config)
        engine = StewardshipEngine(
            config=config,
            gateway=gateway,
            redactions=(read_token, write_token),
        )
        receipt = engine.run(
            repository=args.repository,
            mode=args.mode,
            target_ref=args.target_ref,
            run_identifier=args.run_identifier,
            engine_revision=args.engine_revision,
            workspace_root=args.workspace,
            evidence_dir=args.evidence_dir,
        )
    except Exception as exc:
        error = str(exc)
        for secret in (read_token, write_token):
            if secret:
                error = error.replace(secret, "[REDACTED]")
        receipt = _failure_receipt(args, error[:2000])
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = args.evidence_dir / "receipt.json"
    receipt_path.write_text(
        json.dumps(receipt.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "final_terminal_state": receipt.final_terminal_state,
                "receipt": str(receipt_path),
                "would_create_pr": receipt.would_create_pr,
            },
            sort_keys=True,
        )
    )
    return 0 if receipt.final_terminal_state in SUCCESS_TERMINALS else 1


def _failure_receipt(args: argparse.Namespace, error: str) -> StewardshipReceipt:
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return StewardshipReceipt(
        schema_version=ENGINE_SCHEMA_VERSION,
        run_identifier=args.run_identifier,
        started_at=timestamp,
        completed_at=timestamp,
        mode=args.mode,
        repository=args.repository,
        requested_target_ref=args.target_ref or None,
        effective_target_ref=None,
        base_branch=None,
        base_sha=None,
        engine_revision=args.engine_revision,
        strategy_identifier=STRATEGY_ID,
        strategy_revision=STRATEGY_REVISION,
        eligibility={
            "decision": "blocked",
            "reason": "Engine initialization failed before eligibility could complete.",
            "controlling_source": "repository-owned engine configuration",
            "evidence": [],
        },
        strategy_result={
            "outcome": "blocked",
            "summary": "Strategy execution did not begin.",
            "changed_paths": [],
            "evidence": [],
            "validation_requirements": [],
        },
        changed_paths=[],
        diff_digest=None,
        patch_artifact=None,
        validation={
            "command": [],
            "reason": "Validation did not run because engine initialization failed.",
            "status": "blocked",
            "exit_code": None,
            "log_artifact": None,
        },
        proposed_branch=None,
        proposed_commit_message=None,
        proposed_pr_title=None,
        proposed_pr_body=None,
        collision={
            "decision": "not_checked",
            "reason": "Collision detection did not run.",
            "existing_pr_url": None,
            "observed_base_sha": None,
        },
        would_create_pr=False,
        would_create_pr_reason="The engine did not construct a deliverable proposal.",
        remote_mutations_attempted=[],
        remote_mutation_results=[],
        final_terminal_state="blocked_before_strategy",
        failure_stage="engine_initialization",
        bounded_error=error,
    )


if __name__ == "__main__":
    raise SystemExit(main())
