"""One fixed, fail-closed DAG for issue-owned Codex prompt delivery.

This module deliberately implements one workflow.  It is not a graph builder,
workflow framework, scheduler, retry system, or transport abstraction.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Callable, Protocol
from urllib.parse import urlparse
import uuid

from enforcement.artifact_store_integrity import (
    DropboxClient,
    ProviderError,
    dropbox_content_hash,
)


NODE_DEPENDENCIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("freeze_input", ()),
    ("validate_scope", ("freeze_input",)),
    ("upload_prompt", ("validate_scope",)),
    ("verify_artifact", ("upload_prompt",)),
    ("mint_download_link", ("verify_artifact",)),
    ("render_handoff", ("mint_download_link",)),
)

ZERO_AUTHORITY = (
    "This receipt, prompt, artifact, link, validation, and execution result "
    "grant zero authority and perform no lifecycle transition."
)
TEMPORARY_LINK_SECONDS = 4 * 60 * 60


class PromptDeliveryProvider(Protocol):
    def get_current_account(self) -> dict[str, object]: ...

    def upload_absent(self, path: str, content: bytes) -> dict[str, object]: ...

    def get_metadata(self, path: str) -> dict[str, object]: ...

    def get_temporary_link(self, path: str) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class PromptMaterial:
    content: bytes = field(repr=False)
    size: int
    sha256: str
    dropbox_content_hash: str

    @classmethod
    def freeze(cls, content: bytes) -> "PromptMaterial":
        return cls(
            content=bytes(content),
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            dropbox_content_hash=dropbox_content_hash(content),
        )


@dataclass(frozen=True, slots=True)
class Bootstrap:
    checkout: str
    acquisition_posture: str = "existing_checkout_required"


@dataclass(frozen=True, slots=True)
class DeliveryRequest:
    issue_id: str
    destination: str
    recipient: str
    acting_email: str
    expected_size: int
    expected_sha256: str
    expected_dropbox_content_hash: str
    non_secret_confirmed: bool
    bootstrap: Bootstrap
    attempt_id: str


@dataclass(frozen=True, slots=True)
class VerifiedArtifact:
    file_id: str
    path: str
    revision: str
    size: int
    sha256: str
    dropbox_content_hash: str


@dataclass(frozen=True, slots=True)
class ReceiverLink:
    url: str = field(repr=False)
    expires_after_seconds: int = TEMPORARY_LINK_SECONDS
    single_use: bool = False


@dataclass(frozen=True, slots=True)
class NodeResult:
    node_id: str
    dependencies: tuple[str, ...]
    status: str
    code: str
    checks: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "dependencies": list(self.dependencies),
            "status": self.status,
            "code": self.code,
            "checks": list(self.checks),
        }


@dataclass(frozen=True, slots=True)
class ReceiptContext:
    issue_id: str
    destination: str
    acting_email: str
    prompt_size: int
    prompt_sha256: str
    prompt_dropbox_content_hash: str
    prompt_identity_observed: bool
    utf8: bool | None
    bom_present: bool | None
    lf_only: bool | None
    final_newline: bool | None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    attempt_id: str
    terminal_status: str
    nodes: tuple[NodeResult, ...]
    context: ReceiptContext
    artifact: VerifiedArtifact | None
    handoff: str | None = field(default=None, repr=False)

    def durable_receipt(self) -> dict[str, object]:
        artifact = None
        if self.artifact is not None:
            artifact = {
                "file_id": self.artifact.file_id,
                "path": self.artifact.path,
                "revision": self.artifact.revision,
                "size": self.artifact.size,
                "sha256": self.artifact.sha256,
                "dropbox_content_hash": self.artifact.dropbox_content_hash,
            }
        link_created = any(
            node.node_id == "mint_download_link" and node.status == "SUCCESS"
            for node in self.nodes
        )
        return {
            "schema_version": 1,
            "workflow": "cak-207-fixed-prompt-delivery-dag",
            "attempt_id": self.attempt_id,
            "terminal_status": self.terminal_status,
            "scope": {
                "issue_id": self.context.issue_id,
                "destination": self.context.destination,
                "acting_email": self.context.acting_email,
            },
            "frozen_prompt": {
                "size": self.context.prompt_size,
                "sha256": self.context.prompt_sha256,
                "dropbox_content_hash": self.context.prompt_dropbox_content_hash,
                "observed": self.context.prompt_identity_observed,
                "text_format": {
                    "utf8": self.context.utf8,
                    "bom_present": self.context.bom_present,
                    "lf_only": self.context.lf_only,
                    "final_newline": self.context.final_newline,
                },
            },
            "node_results": [node.as_dict() for node in self.nodes],
            "artifact": artifact,
            "receiver_link": {
                "created": link_created,
                "url_retained": False,
                "expires_after_seconds": TEMPORARY_LINK_SECONDS if link_created else None,
                "single_use": False if link_created else None,
            },
            "acting_identity_verified": any(
                node.node_id == "validate_scope" and node.status == "SUCCESS"
                for node in self.nodes
            ),
            "authority": ZERO_AUTHORITY,
        }


class DeliveryBlocked(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class FixedPromptDeliveryDAG:
    """Execute the six CAK-207 nodes once, in their fixed dependency order."""

    def __init__(self, provider: PromptDeliveryProvider) -> None:
        self._provider = provider

    def execute(self, content: bytes, request: DeliveryRequest) -> ExecutionResult:
        results: list[NodeResult] = []
        statuses: dict[str, str] = {}
        material: PromptMaterial | None = None
        upload_metadata: dict[str, object] | None = None
        verified: VerifiedArtifact | None = None
        receiver_link: ReceiverLink | None = None
        handoff: str | None = None
        context = ReceiptContext(
            issue_id=request.issue_id,
            destination=request.destination,
            acting_email=request.acting_email,
            prompt_size=request.expected_size,
            prompt_sha256=request.expected_sha256,
            prompt_dropbox_content_hash=request.expected_dropbox_content_hash,
            prompt_identity_observed=False,
            utf8=None,
            bom_present=None,
            lf_only=None,
            final_newline=None,
        )

        def run(
            node_id: str,
            dependencies: tuple[str, ...],
            action: Callable[[], tuple[object | None, tuple[str, ...]]],
        ) -> object | None:
            if any(statuses.get(dependency) != "SUCCESS" for dependency in dependencies):
                statuses[node_id] = "NOT_RUN"
                results.append(NodeResult(node_id, dependencies, "NOT_RUN", "predecessor_blocked"))
                return None
            try:
                value, checks = action()
            except DeliveryBlocked as exc:
                statuses[node_id] = "BLOCKED"
                results.append(NodeResult(node_id, dependencies, "BLOCKED", exc.code))
                return None
            except ProviderError as exc:
                code = "destination_collision" if exc.kind == "collision" else f"provider_{exc.kind}"
                statuses[node_id] = "BLOCKED"
                results.append(NodeResult(node_id, dependencies, "BLOCKED", code))
                return None
            except Exception as exc:  # fail closed without retaining arbitrary exception text
                statuses[node_id] = "BLOCKED"
                results.append(NodeResult(node_id, dependencies, "BLOCKED", f"internal_{type(exc).__name__}"))
                return None
            statuses[node_id] = "SUCCESS"
            results.append(NodeResult(node_id, dependencies, "SUCCESS", "ok", checks))
            return value

        frozen_input = run(
            "freeze_input",
            (),
            lambda: (
                self._freeze_with_context(content, request),
                ("exact_bytes_frozen", "size_computed", "sha256_computed", "dropbox_content_hash_computed"),
            ),
        )
        assert frozen_input is None or (
            isinstance(frozen_input, tuple)
            and len(frozen_input) == 2
            and isinstance(frozen_input[0], PromptMaterial)
            and isinstance(frozen_input[1], ReceiptContext)
        )
        if frozen_input is not None:
            material, context = frozen_input

        run(
            "validate_scope",
            ("freeze_input",),
            lambda: (None, self._validate(material, request)),
        )

        upload_metadata = run(
            "upload_prompt",
            ("validate_scope",),
            lambda: (
                self._provider.upload_absent(request.destination, material.content),
                ("mode_add", "strict_conflict", "overwrite_disabled", "autorename_disabled", "single_upload"),
            ),
        )
        assert upload_metadata is None or isinstance(upload_metadata, dict)

        verified = run(
            "verify_artifact",
            ("upload_prompt",),
            lambda: self._verify(material, request, upload_metadata),
        )
        assert verified is None or isinstance(verified, VerifiedArtifact)

        receiver_link = run(
            "mint_download_link",
            ("verify_artifact",),
            lambda: self._mint_link(verified),
        )
        assert receiver_link is None or isinstance(receiver_link, ReceiverLink)

        handoff = run(
            "render_handoff",
            ("mint_download_link",),
            lambda: (
                render_handoff(verified, receiver_link, request.recipient, request.bootstrap),
                ("byte_blind_input", "metadata_only_render", "raw_url_excluded_from_receipt"),
            ),
        )
        assert handoff is None or isinstance(handoff, str)

        terminal = "SUCCESS" if statuses.get("render_handoff") == "SUCCESS" else "BLOCKED"
        return ExecutionResult(
            attempt_id=request.attempt_id,
            terminal_status=terminal,
            nodes=tuple(results),
            context=context,
            artifact=verified,
            handoff=handoff,
        )

    @staticmethod
    def _freeze_with_context(
        content: bytes,
        request: DeliveryRequest,
    ) -> tuple[PromptMaterial, ReceiptContext]:
        material = PromptMaterial.freeze(content)
        return material, ReceiptContext(
            issue_id=request.issue_id,
            destination=request.destination,
            acting_email=request.acting_email,
            prompt_size=material.size,
            prompt_sha256=material.sha256,
            prompt_dropbox_content_hash=material.dropbox_content_hash,
            prompt_identity_observed=True,
            utf8=_is_utf8(material.content),
            bom_present=material.content.startswith(b"\xef\xbb\xbf"),
            lf_only=b"\r" not in material.content,
            final_newline=material.content.endswith(b"\n"),
        )

    def _validate(self, material: PromptMaterial | None, request: DeliveryRequest) -> tuple[str, ...]:
        if material is None:
            raise DeliveryBlocked("missing_frozen_input", "frozen prompt material is unavailable")
        try:
            material.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DeliveryBlocked("invalid_utf8", "prompt is not valid UTF-8") from exc
        if material.content.startswith(b"\xef\xbb\xbf"):
            raise DeliveryBlocked("utf8_bom", "prompt must not contain a UTF-8 BOM")
        if b"\r" in material.content:
            raise DeliveryBlocked("non_lf_line_endings", "prompt must use LF line endings")
        if not material.content.endswith(b"\n"):
            raise DeliveryBlocked("missing_final_newline", "prompt must end with one LF newline")
        if not request.non_secret_confirmed:
            raise DeliveryBlocked("non_secret_unconfirmed", "caller did not confirm non-secret content")
        if re.fullmatch(r"CAK-[1-9][0-9]*", request.issue_id) is None:
            raise DeliveryBlocked("invalid_issue", "issue must be a CAK identifier")
        self._validate_destination(request.issue_id, request.destination)
        if request.recipient != "codex":
            raise DeliveryBlocked("unsupported_recipient", "v1 supports only the Codex recipient")
        if request.bootstrap.acquisition_posture != "existing_checkout_required":
            raise DeliveryBlocked("invalid_acquisition_posture", "v1 requires an existing checkout")
        if not PurePosixPath(request.bootstrap.checkout).is_absolute():
            raise DeliveryBlocked("invalid_checkout", "checkout must be an absolute path")
        expected = (
            ("size", request.expected_size, material.size),
            ("sha256", request.expected_sha256, material.sha256),
            ("dropbox_content_hash", request.expected_dropbox_content_hash, material.dropbox_content_hash),
        )
        for name, supplied, computed in expected:
            if supplied != computed:
                raise DeliveryBlocked(f"{name}_mismatch", f"supplied {name} does not match frozen bytes")
        account = self._provider.get_current_account()
        if account.get("email") != request.acting_email:
            raise DeliveryBlocked("acting_identity_mismatch", "Dropbox acting identity does not match")
        return (
            "utf8",
            "no_bom",
            "lf_line_endings",
            "final_newline",
            "non_secret_attested",
            "issue_valid",
            "destination_contained_and_versioned",
            "recipient_codex",
            "integrity_inputs_match",
            "acting_identity_match",
        )

    @staticmethod
    def _validate_destination(issue_id: str, destination: str) -> None:
        path = PurePosixPath(destination)
        expected_parent = PurePosixPath("/issues") / issue_id
        if not destination.startswith("/") or ".." in path.parts or "\\" in destination:
            raise DeliveryBlocked("invalid_destination", "destination must be a contained Dropbox path")
        if path.parent != expected_parent:
            raise DeliveryBlocked("destination_outside_issue", "destination is outside the governing issue")
        if re.search(r"-v[1-9][0-9]*-[0-9]{4}-[0-9]{2}-[0-9]{2}\.[A-Za-z0-9]+$", path.name) is None:
            raise DeliveryBlocked("destination_not_versioned", "destination must have a versioned dated name")

    def _verify(
        self,
        material: PromptMaterial | None,
        request: DeliveryRequest,
        upload_metadata: dict[str, object] | None,
    ) -> tuple[VerifiedArtifact, tuple[str, ...]]:
        if material is None or upload_metadata is None:
            raise DeliveryBlocked("missing_upload_evidence", "upload evidence is unavailable")
        file_id = upload_metadata.get("id")
        if not isinstance(file_id, str) or not file_id.startswith("id:"):
            raise DeliveryBlocked("missing_file_id", "upload omitted Dropbox file ID")
        observed = self._provider.get_metadata(file_id)
        fields = {
            "file_id": (upload_metadata.get("id"), observed.get("id")),
            "path": (request.destination, observed.get("path_display")),
            "revision": (upload_metadata.get("rev"), observed.get("rev")),
            "size": (material.size, observed.get("size")),
            "dropbox_content_hash": (material.dropbox_content_hash, observed.get("content_hash")),
        }
        for name, (expected, actual) in fields.items():
            if expected is None or actual is None:
                raise DeliveryBlocked(f"missing_{name}", f"Dropbox omitted required {name} evidence")
            if expected != actual:
                raise DeliveryBlocked(f"{name}_mismatch", f"Dropbox {name} evidence mismatched")
        for name in ("path_display", "rev", "size", "content_hash"):
            if upload_metadata.get(name) != observed.get(name):
                raise DeliveryBlocked(f"upload_{name}_mismatch", f"upload and re-observed {name} mismatched")
        artifact = VerifiedArtifact(
            file_id=file_id,
            path=request.destination,
            revision=str(observed["rev"]),
            size=material.size,
            sha256=material.sha256,
            dropbox_content_hash=material.dropbox_content_hash,
        )
        return artifact, (
            "upload_identity_bound",
            "metadata_reobserved",
            "path_match",
            "revision_match",
            "size_match",
            "dropbox_content_hash_match",
            "sha256_kept_distinct",
        )

    def _mint_link(self, artifact: VerifiedArtifact | None) -> tuple[ReceiverLink, tuple[str, ...]]:
        if artifact is None:
            raise DeliveryBlocked("missing_verified_artifact", "verified artifact is unavailable")
        result = self._provider.get_temporary_link(artifact.file_id)
        link = result.get("link")
        metadata = result.get("metadata")
        if not isinstance(link, str) or urlparse(link).scheme != "https":
            raise DeliveryBlocked("invalid_receiver_link", "Dropbox omitted a valid HTTPS temporary link")
        if not isinstance(metadata, dict):
            raise DeliveryBlocked("missing_link_metadata", "Dropbox link response omitted metadata")
        comparisons = {
            "file_id": (artifact.file_id, metadata.get("id")),
            "path": (artifact.path, metadata.get("path_display")),
            "revision": (artifact.revision, metadata.get("rev")),
            "size": (artifact.size, metadata.get("size")),
            "dropbox_content_hash": (artifact.dropbox_content_hash, metadata.get("content_hash")),
        }
        for name, (expected, actual) in comparisons.items():
            if expected != actual:
                raise DeliveryBlocked(f"link_{name}_mismatch", f"temporary-link {name} evidence mismatched")
        return ReceiverLink(link), (
            "single_link_created",
            "link_metadata_matches_verified_artifact",
            "expires_after_four_hours",
            "not_claimed_single_use",
        )


def render_handoff(
    artifact: VerifiedArtifact,
    link: ReceiverLink,
    recipient: str,
    bootstrap: Bootstrap,
) -> str:
    """Render the transient handoff from byte-free inputs only."""
    return "\n".join(
        (
            f"Recipient: {recipient}",
            f"Receiver URL (perform one download for this attempt): {link.url}",
            f"Dropbox path: {artifact.path}",
            f"Dropbox file ID: {artifact.file_id}",
            f"Provider revision: {artifact.revision}",
            f"Expected bytes: {artifact.size}",
            f"Expected whole-file SHA-256: {artifact.sha256}",
            f"Dropbox content hash: {artifact.dropbox_content_hash}",
            f"Required checkout: {bootstrap.checkout}",
            f"Acquisition posture: {bootstrap.acquisition_posture}",
            "Verify every identity and digest before executing the complete downloaded prompt.",
            "The link is a Dropbox temporary download link that expires after four hours; it is not claimed to be single-use.",
            ZERO_AUTHORITY,
        )
    )


def _is_utf8(content: bytes) -> bool:
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the fixed CAK-207 prompt-delivery DAG once.")
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--recipient", required=True, choices=("codex",))
    parser.add_argument("--acting-email", required=True)
    parser.add_argument("--namespace-id", required=True)
    parser.add_argument("--access-token-env", default="DROPBOX_ACCESS_TOKEN")
    parser.add_argument("--expected-size", type=int, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--expected-dropbox-content-hash", required=True)
    parser.add_argument("--non-secret", action="store_true", required=True)
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--attempt-id", default=None)
    parser.add_argument("--receipt-file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = os.environ.get(args.access_token_env, "")
    if not token:
        print(f"prompt-delivery: access token environment variable is unset: {args.access_token_env}", file=sys.stderr)
        return 2
    try:
        content = args.prompt_file.read_bytes()
    except OSError as exc:
        print(f"prompt-delivery: cannot read prompt file: {type(exc).__name__}", file=sys.stderr)
        return 2
    request_value = DeliveryRequest(
        issue_id=args.issue,
        destination=args.destination,
        recipient=args.recipient,
        acting_email=args.acting_email,
        expected_size=args.expected_size,
        expected_sha256=args.expected_sha256,
        expected_dropbox_content_hash=args.expected_dropbox_content_hash,
        non_secret_confirmed=args.non_secret,
        bootstrap=Bootstrap(checkout=args.checkout),
        attempt_id=args.attempt_id or str(uuid.uuid4()),
    )
    try:
        with args.receipt_file.open("x", encoding="utf-8", errors="strict") as stream:
            result = FixedPromptDeliveryDAG(DropboxClient(token, args.namespace_id)).execute(content, request_value)
            receipt_body = json.dumps(result.durable_receipt(), indent=2, sort_keys=True) + "\n"
            stream.write(receipt_body)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        print("prompt-delivery: receipt destination already exists", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"prompt-delivery: cannot create receipt: {type(exc).__name__}", file=sys.stderr)
        return 2
    if result.terminal_status == "SUCCESS":
        print(result.handoff)
        return 0
    blocked = next(node for node in result.nodes if node.status == "BLOCKED")
    print(f"prompt-delivery BLOCKED at {blocked.node_id}: {blocked.code}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
