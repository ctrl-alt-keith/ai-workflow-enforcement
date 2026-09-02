"""Normal local invocation boundary for the fixed prompt-delivery DAG.

This module removes manual digest assembly from the operator path and owns the
final handoff emission.  It does not copy the graph, select another transport,
retry a provider effect, or become a general workflow runner.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import TextIO
import uuid

from enforcement.artifact_store_integrity import DropboxClient
from enforcement.prompt_delivery_dag import (
    Bootstrap,
    DeliveryRequest,
    ExecutionResult,
    FixedPromptDeliveryDAG,
    PromptMaterial,
    ZERO_AUTHORITY,
)
from enforcement.prompt_handoff_emission import emit_handoff


WORKFLOW = "cak-208-normal-prompt-delivery-invocation"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deliver one exact issue-owned Codex prompt through the fixed DAG."
    )
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--recipient", required=True, choices=("codex",))
    parser.add_argument("--acting-email", required=True)
    parser.add_argument("--namespace-id", required=True)
    parser.add_argument("--access-token-env", default="DROPBOX_ACCESS_TOKEN")
    parser.add_argument("--non-secret", action="store_true", required=True)
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--attempt-id", default=None)
    parser.add_argument("--receipt-file", type=Path, required=True)
    return parser


def _receipt(
    *,
    args: argparse.Namespace,
    attempt_id: str,
    material: PromptMaterial,
    credential_present: bool,
    terminal_status: str,
    code: str,
    dag_result: ExecutionResult | None,
    emission_attempted: bool,
    emission_observed: bool,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "workflow": WORKFLOW,
        "attempt_id": attempt_id,
        "terminal_status": terminal_status,
        "code": code,
        "scope": {
            "issue_id": args.issue,
            "destination": args.destination,
            "recipient": args.recipient,
            "acting_email": args.acting_email,
            "namespace_id": args.namespace_id,
            "checkout": args.checkout,
        },
        "frozen_prompt": {
            "size": material.size,
            "sha256": material.sha256,
            "dropbox_content_hash": material.dropbox_content_hash,
            "body_retained": False,
            "text_format": {
                "utf8": _is_utf8(material.content),
                "bom_present": material.content.startswith(b"\xef\xbb\xbf"),
                "lf_only": b"\r" not in material.content,
                "final_newline": material.content.endswith(b"\n"),
            },
        },
        "credential": {
            "environment_variable": args.access_token_env,
            "present": credential_present,
            "value_retained": False,
        },
        "dag": dag_result.durable_receipt() if dag_result is not None else None,
        "handoff_emission": {
            "attempted": emission_attempted,
            "observed": emission_observed,
            "body_retained": False,
            "raw_receiver_url_retained": False,
        },
        "authority": ZERO_AUTHORITY,
    }


def _is_utf8(content: bytes) -> bool:
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _write_receipt(stream: TextIO, receipt: dict[str, object]) -> None:
    stream.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    output = stdout if stdout is not None else sys.stdout
    errors = stderr if stderr is not None else sys.stderr
    attempt_id = args.attempt_id or str(uuid.uuid4())

    try:
        content = args.prompt_file.read_bytes()
        material = PromptMaterial.freeze(content)
    except OSError as exc:
        print(f"prompt-delivery invocation: cannot read prompt file: {type(exc).__name__}", file=errors)
        return 2

    try:
        receipt_stream = args.receipt_file.open("x", encoding="utf-8", errors="strict")
    except FileExistsError:
        print("prompt-delivery invocation: receipt destination already exists", file=errors)
        return 2
    except OSError as exc:
        print(f"prompt-delivery invocation: cannot create receipt: {type(exc).__name__}", file=errors)
        return 2

    token = os.environ.get(args.access_token_env, "")
    credential_present = bool(token)
    dag_result: ExecutionResult | None = None
    terminal_status = "BLOCKED"
    code = "credential_unavailable"
    emission_attempted = False
    emission_observed = False

    try:
        if credential_present:
            request = DeliveryRequest(
                issue_id=args.issue,
                destination=args.destination,
                recipient=args.recipient,
                acting_email=args.acting_email,
                expected_size=material.size,
                expected_sha256=material.sha256,
                expected_dropbox_content_hash=material.dropbox_content_hash,
                non_secret_confirmed=args.non_secret,
                bootstrap=Bootstrap(checkout=args.checkout),
                attempt_id=attempt_id,
            )
            provider = DropboxClient(token, args.namespace_id)
            dag_result = FixedPromptDeliveryDAG(provider).execute(content, request)
            if dag_result.terminal_status == "SUCCESS":
                emission_attempted = True
                try:
                    emit_handoff(dag_result.handoff or "", output)
                except Exception:  # fail closed on any ordinary output-stream failure
                    code = "handoff_emission_failed"
                else:
                    terminal_status = "SUCCESS"
                    code = "ok"
                    emission_observed = True
            else:
                blocked = next(node for node in dag_result.nodes if node.status == "BLOCKED")
                code = f"dag_{blocked.node_id}_{blocked.code}"

        receipt = _receipt(
            args=args,
            attempt_id=attempt_id,
            material=material,
            credential_present=credential_present,
            terminal_status=terminal_status,
            code=code,
            dag_result=dag_result,
            emission_attempted=emission_attempted,
            emission_observed=emission_observed,
        )
        _write_receipt(receipt_stream, receipt)
    except OSError as exc:
        print(f"prompt-delivery invocation: cannot finalize receipt: {type(exc).__name__}", file=errors)
        return 2
    finally:
        receipt_stream.close()

    if terminal_status == "SUCCESS":
        return 0
    print(f"prompt-delivery invocation BLOCKED: {code}", file=errors)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
