"""Capture one admitted issue-owned artifact through a verified Dropbox folder."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Protocol

from enforcement.artifact_store_integrity import DropboxClient, ProviderError, dropbox_content_hash


MAX_BYTES = 10 * 1024 * 1024


class Provider(Protocol):
    def get_current_account(self) -> dict[str, object]: ...
    def get_metadata(self, path: str) -> dict[str, object]: ...
    def upload_absent(self, path: str, content: bytes) -> dict[str, object]: ...


class CaptureBlocked(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _folder(metadata: dict[str, object], path: str, identity: str | None = None) -> str:
    folder_id = metadata.get("id")
    if (metadata.get(".tag") != "folder" or not isinstance(folder_id, str)
            or not folder_id.startswith("id:") or metadata.get("path_lower") != path.casefold()
            or metadata.get("path_display") != path
            or (identity is not None and folder_id != identity)):
        raise CaptureBlocked("folder_identity_mismatch")
    return folder_id


def capture(*, provider: Provider, issue: str, source: Path, name: str,
            acting_email: str, authority: str) -> dict[str, object]:
    """Read local bytes, then require provider folder identity before any upload."""
    if re.fullmatch(r"CAK-[1-9][0-9]*", issue) is None:
        raise CaptureBlocked("invalid_issue")
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*-v[1-9][0-9]*-[0-9]{4}-[0-9]{2}-[0-9]{2}\.[A-Za-z0-9]+", name)
            or not authority.strip()):
        raise CaptureBlocked("invalid_scope")
    try:
        with source.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    except OSError as exc:
        raise CaptureBlocked("source_unavailable") from exc
    if len(data) > MAX_BYTES:
        raise CaptureBlocked("source_too_large")
    size = len(data)
    sha256 = hashlib.sha256(data).hexdigest()
    content_hash = dropbox_content_hash(data)
    folder_path = f"/issues/{issue}"
    destination = f"{folder_path}/{name}"

    try:
        account = provider.get_current_account()
        if account.get("email") != acting_email:
            raise CaptureBlocked("acting_identity_mismatch")
        folder_id = _folder(provider.get_metadata(folder_path), folder_path)
        _folder(provider.get_metadata(folder_id), folder_path, folder_id)
    except ProviderError as exc:
        raise CaptureBlocked("folder_unverifiable") from exc

    # From this point a provider error may have followed a successful write.
    try:
        uploaded = provider.upload_absent(destination, data)
    except ProviderError as exc:
        raise CaptureBlocked("upload_collision" if exc.kind == "collision" else "upload_outcome_unknown") from exc
    file_id = uploaded.get("id")
    if not isinstance(file_id, str) or not file_id.startswith("id:"):
        raise CaptureBlocked("upload_identity_unverifiable")
    try:
        observed = provider.get_metadata(file_id)
        _folder(provider.get_metadata(folder_id), folder_path, folder_id)
    except ProviderError as exc:
        raise CaptureBlocked("uploaded_file_unverifiable") from exc
    if observed.get(".tag") != "file":
        raise CaptureBlocked("uploaded_file_identity_mismatch")
    required = {"id": file_id, "path_lower": destination.casefold(),
                "size": size, "content_hash": content_hash}
    if any(uploaded.get(key) != value or observed.get(key) != value for key, value in required.items()):
        raise CaptureBlocked("uploaded_file_identity_mismatch")
    if (not isinstance(uploaded.get("rev"), str) or not uploaded["rev"]
            or observed.get("rev") != uploaded["rev"]
            or uploaded.get("path_display") != destination
            or observed.get("path_display") != destination):
        raise CaptureBlocked("uploaded_file_identity_mismatch")
    return {
        "status": "verified", "issue": issue, "authority": authority,
        "folder": {"path": folder_path, "id": folder_id},
        "file": {"path": observed["path_display"], "id": file_id,
                 "rev": observed["rev"], "size": size, "sha256": sha256,
                 "dropbox_content_hash": content_hash},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture one admitted issue-owned artifact in Dropbox")
    parser.add_argument("--source-file", type=Path, required=True)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--name", required=True, help="Unique dated or versioned file name")
    parser.add_argument("--authority", required=True, help="Storage-admission authority reference")
    parser.add_argument("--acting-email", required=True)
    parser.add_argument("--namespace-id", required=True)
    parser.add_argument("--receipt-file", type=Path, required=True)
    parser.add_argument("--access-token-env", default="DROPBOX_ACCESS_TOKEN")
    args = parser.parse_args(argv)
    if not args.namespace_id.isdecimal():
        print("issue-artifact capture BLOCKED: invalid_namespace", file=sys.stderr)
        return 1
    try:
        receipt = args.receipt_file.open("x", encoding="utf-8")
    except OSError:
        print("issue-artifact capture BLOCKED: receipt_unavailable", file=sys.stderr)
        return 1
    token = os.environ.get(args.access_token_env, "")
    try:
        if not token:
            raise CaptureBlocked("credential_unavailable")
        result = capture(provider=DropboxClient(token, args.namespace_id), issue=args.issue,
                         source=args.source_file, name=args.name,
                         acting_email=args.acting_email, authority=args.authority)
    except CaptureBlocked as exc:
        result = {"status": "blocked", "code": exc.code, "issue": args.issue,
                  "authority": args.authority,
                  "provider_effect": (
                      "unknown_after_attempt" if (exc.code.startswith("upload_")
                      and exc.code != "upload_collision") or exc.code.startswith("uploaded_file_")
                      else "collision_no_create" if exc.code == "upload_collision" else "not_attempted"
                  )}
    result["namespace_id"] = args.namespace_id
    result["acting_email"] = args.acting_email
    try:
        receipt.write(json.dumps(result, sort_keys=True) + "\n")
        receipt.flush()
        os.fsync(receipt.fileno())
        receipt.close()
    except OSError:
        print("issue-artifact capture BLOCKED: receipt_write_failed", file=sys.stderr)
        return 1
    if result["status"] != "verified":
        print(f"issue-artifact capture BLOCKED: {result['code']}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
