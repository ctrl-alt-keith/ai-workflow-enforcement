"""Human-invoked, read-only integrity checks for a bounded Dropbox artifact scope."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Callable, Iterable
from urllib import error, request


NOTICE = (
    "This point-in-time report is advisory evidence only. It grants no authority "
    "and makes no claim about sharing, retention, local synchronization, restore "
    "behavior, plan tier, independent backup coverage, or future availability."
)
DEFAULT_MAX_FILES = 100
DEFAULT_MAX_BYTES = 100 * 1024 * 1024
DROPBOX_BLOCK_SIZE = 4 * 1024 * 1024
API_ROOT = "https://api.dropboxapi.com/2/files"
CONTENT_ROOT = "https://content.dropboxapi.com/2/files"


class ManifestError(ValueError):
    """The caller-owned identity or authority manifest is unusable."""


class ProviderError(RuntimeError):
    """A Dropbox observation failed without changing provider state."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Scope:
    kind: str
    value: str


@dataclass(frozen=True)
class Listing:
    entries: tuple[dict[str, object], ...]
    pages: int


class DropboxClient:
    """Narrow Dropbox API client exposing only read operations used by the audit."""

    def __init__(self, access_token: str, namespace_id: str, *, timeout: int = 60) -> None:
        if not access_token:
            raise ValueError("Dropbox access token is empty")
        self._access_token = access_token
        self._namespace_id = namespace_id
        self._timeout = timeout

    def list_folder(self, path: str) -> Listing:
        payload = {
            "path": path,
            "recursive": True,
            "include_deleted": True,
            "include_media_info": False,
            "limit": 1000,
        }
        page = self._rpc("list_folder", payload)
        entries: list[dict[str, object]] = []
        pages = 0
        while True:
            pages += 1
            raw_entries = page.get("entries")
            if not isinstance(raw_entries, list):
                raise ProviderError("unverifiable", "Dropbox list_folder omitted entries")
            entries.extend(entry for entry in raw_entries if isinstance(entry, dict))
            has_more = page.get("has_more")
            if has_more is False:
                return Listing(tuple(entries), pages)
            cursor = page.get("cursor")
            if has_more is not True or not isinstance(cursor, str) or not cursor:
                raise ProviderError("unverifiable", "Dropbox pagination state is incomplete")
            page = self._rpc("list_folder/continue", {"cursor": cursor})

    def get_metadata(self, path: str) -> dict[str, object]:
        return self._rpc("get_metadata", {"path": path, "include_deleted": True})

    def download(self, path: str, *, max_bytes: int) -> tuple[dict[str, object], bytes]:
        api_arg = json.dumps({"path": path}, separators=(",", ":"))
        req = request.Request(
            f"{CONTENT_ROOT}/download",
            data=b"",
            method="POST",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/octet-stream",
                "Dropbox-API-Arg": api_arg,
                "Dropbox-API-Path-Root": self._path_root_header(),
            },
        )
        try:
            with request.urlopen(req, timeout=self._timeout) as response:
                raw_metadata = response.headers.get("Dropbox-API-Result")
                if not raw_metadata:
                    raise ProviderError("unverifiable", "Dropbox download omitted result metadata")
                metadata = json.loads(raw_metadata)
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(min(1024 * 1024, max_bytes - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ProviderError("unverifiable", "download exceeded the declared byte bound")
                    chunks.append(chunk)
        except ProviderError:
            raise
        except error.HTTPError as exc:
            raise _http_provider_error(exc) from exc
        except (error.URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProviderError("unverifiable", f"Dropbox download unavailable: {type(exc).__name__}") from exc
        if not isinstance(metadata, dict):
            raise ProviderError("unverifiable", "Dropbox download metadata is not an object")
        return metadata, b"".join(chunks)

    def _rpc(self, endpoint: str, payload: dict[str, object]) -> dict[str, object]:
        req = request.Request(
            f"{API_ROOT}/{endpoint}",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
                "Dropbox-API-Path-Root": self._path_root_header(),
            },
        )
        try:
            with request.urlopen(req, timeout=self._timeout) as response:
                result = json.load(response)
        except error.HTTPError as exc:
            raise _http_provider_error(exc) from exc
        except (error.URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProviderError("unverifiable", f"Dropbox metadata unavailable: {type(exc).__name__}") from exc
        if not isinstance(result, dict):
            raise ProviderError("unverifiable", "Dropbox returned a non-object response")
        return result

    def _path_root_header(self) -> str:
        return json.dumps(
            {".tag": "namespace_id", "namespace_id": self._namespace_id},
            separators=(",", ":"),
        )


def load_manifest(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest: {type(exc).__name__}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManifestError("manifest must be a JSON object")
    validate_manifest(value)
    return value


def validate_manifest(manifest: dict[str, object]) -> None:
    if manifest.get("schema_version") != 1:
        raise ManifestError("manifest schema_version must be 1")
    if manifest.get("provider") != "dropbox":
        raise ManifestError("manifest provider must be dropbox")
    store = manifest.get("store")
    if not isinstance(store, dict):
        raise ManifestError("manifest store must be an object")
    namespace_id = store.get("namespace_id")
    root = store.get("root")
    if not isinstance(namespace_id, str) or not namespace_id.isdecimal():
        raise ManifestError("store.namespace_id must be a Dropbox numeric namespace ID")
    if not isinstance(root, str) or root == "/" or not _valid_absolute_path(root):
        raise ManifestError("store.root must be a non-root absolute Dropbox path")
    authority = manifest.get("authority")
    if not isinstance(authority, dict) or not isinstance(authority.get("source"), str) or not authority["source"].strip():
        raise ManifestError("authority.source is required; destination authority cannot be inferred")
    objects = manifest.get("objects")
    if not isinstance(objects, list) or not objects:
        raise ManifestError("manifest objects must be a non-empty list")
    seen: set[str] = set()
    for index, item in enumerate(objects):
        if not isinstance(item, dict):
            raise ManifestError(f"objects[{index}] must be an object")
        path = item.get("path")
        if not isinstance(path, str) or not _valid_relative_path(path):
            raise ManifestError(f"objects[{index}].path must be a contained relative file path")
        key = path.casefold()
        if key in seen:
            raise ManifestError(f"duplicate case-insensitive object path: {path}")
        seen.add(key)
        size = item.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ManifestError(f"objects[{index}].size must be a non-negative integer")
        if not _digest(item.get("sha256")):
            raise ManifestError(f"objects[{index}].sha256 must be a lowercase SHA-256")
        if not isinstance(item.get("evidence_source"), str) or not str(item["evidence_source"]).strip():
            raise ManifestError(f"objects[{index}].evidence_source is required")
        for field in ("dropbox_content_hash",):
            if field in item and not _digest(item[field]):
                raise ManifestError(f"objects[{index}].{field} must be a lowercase SHA-256")
        if "file_id" in item and (
            not isinstance(item["file_id"], str)
            or re.fullmatch(r"id:[A-Za-z0-9_-]+", item["file_id"]) is None
        ):
            raise ManifestError(f"objects[{index}].file_id must be a Dropbox file ID")
        if "revision" in item and (not isinstance(item["revision"], str) or not item["revision"]):
            raise ManifestError(f"objects[{index}].revision must be a non-empty string")
        samples = item.get("samples", [])
        if not isinstance(samples, list) or any(not isinstance(value, str) or not value for value in samples):
            raise ManifestError(f"objects[{index}].samples must be a list of non-empty strings")


def verify(
    manifest: dict[str, object],
    scope: Scope,
    client: object,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    clock: Callable[[], str] | None = None,
) -> dict[str, object]:
    """Verify one explicitly bounded scope without mutating the artifact store."""
    validate_manifest(manifest)
    if max_files < 1 or max_bytes < 1:
        raise ManifestError("max-files and max-bytes must be positive")
    selected = _select_objects(manifest["objects"], scope)
    if not selected:
        raise ManifestError(f"scope {scope.kind}={scope.value!r} selects no manifest objects")
    store = manifest["store"]
    root = str(store["root"]).rstrip("/")
    pages = 0
    listed_entries = 0
    discovery: dict[str, dict[str, object]] = {}
    discovery_failure: ProviderError | None = None
    if scope.kind in {"issue", "package"}:
        scope_path = _join(root, scope.value)
        try:
            listing = client.list_folder(scope_path)
            pages = listing.pages
            listed_entries = len(listing.entries)
            discovery = _index_listing(listing.entries, root)
        except ProviderError as exc:
            discovery_failure = exc
    else:
        for item in selected:
            full_path = _join(root, str(item["path"]))
            try:
                metadata = client.get_metadata(full_path)
            except ProviderError as exc:
                metadata = {"_provider_error": exc}
            discovery[str(item["path"]).casefold()] = metadata

    selected_by_path = {str(item["path"]).casefold(): item for item in selected}
    if scope.kind in {"issue", "package"} and discovery_failure is None:
        for key, metadata in discovery.items():
            if metadata.get(".tag") == "file" and key not in selected_by_path:
                relative = _relative_path(metadata, root)
                if relative:
                    selected_by_path[key] = {"path": relative, "_unmanifested": True}
    if len(selected_by_path) > max_files:
        raise ManifestError(
            f"scope contains {len(selected_by_path)} files, exceeding --max-files {max_files}"
        )

    results: list[dict[str, object]] = []
    downloaded_bytes = 0
    for key in sorted(selected_by_path):
        item = selected_by_path[key]
        if discovery_failure is not None:
            results.append(_provider_failure_result(item, discovery_failure))
            continue
        metadata = discovery.get(key)
        if metadata is None:
            results.append(_terminal_result(item, "missing", "object was not returned by the bounded provider observation"))
            continue
        provider_error = metadata.get("_provider_error")
        if isinstance(provider_error, ProviderError):
            results.append(_provider_failure_result(item, provider_error))
            continue
        tag = metadata.get(".tag")
        if tag == "deleted":
            results.append(_terminal_result(item, "deleted", "provider returned a deleted entry"))
            continue
        if tag != "file":
            results.append(_terminal_result(item, "unverifiable", f"expected a file; provider returned {tag or 'unknown metadata'}"))
            continue
        if item.get("_unmanifested"):
            results.append(_terminal_result(item, "unverifiable", "current file has no manifest-bound immutable identity evidence"))
            continue
        remaining = max_bytes - downloaded_bytes
        if remaining < 1:
            results.append(_terminal_result(item, "unverifiable", "run exhausted the declared byte bound"))
            continue
        full_path = _join(root, str(item["path"]))
        try:
            download_metadata, content = client.download(full_path, max_bytes=remaining)
        except ProviderError as exc:
            results.append(_provider_failure_result(item, exc))
            continue
        downloaded_bytes += len(content)
        results.append(_verify_bytes(item, metadata, download_metadata, content))

    counts = {status: sum(result["status"] == status for result in results) for status in (
        "pass", "changed", "missing", "deleted", "inaccessible", "unverifiable"
    )}
    clean = counts["pass"] == len(results)
    now = clock or _utc_now
    return {
        "schema_version": 1,
        "report_type": "artifact_store_integrity",
        "generated_at": now(),
        "mode": "human_invoked_read_only",
        "read_only": True,
        "advisory": True,
        "notice": NOTICE,
        "authority_source": manifest["authority"]["source"],
        "scope": {"kind": scope.kind, "value": scope.value, "max_files": max_files, "max_bytes": max_bytes},
        "store": {"provider": "dropbox", "namespace_id": store["namespace_id"], "root": root},
        "result": "pass" if clean else "non-pass",
        "summary": {"total": len(results), **counts},
        "observation": {
            "pagination_pages": pages,
            "listed_entries": listed_entries,
            "downloaded_files": sum(bool(result.get("observed", {}).get("sha256")) for result in results),
            "downloaded_bytes": downloaded_bytes,
        },
        "objects": results,
    }


def render_summary(report: dict[str, object]) -> str:
    summary = report["summary"]
    scope = report["scope"]
    observation = report["observation"]
    counts = ", ".join(
        f"{summary[name]} {name}" for name in ("pass", "changed", "missing", "deleted", "inaccessible", "unverifiable")
        if summary[name]
    ) or "0 objects"
    return (
        f"artifact-store integrity {str(report['result']).upper()}: "
        f"{scope['kind']}={scope['value']}; {counts}; "
        f"{observation['pagination_pages']} listing page(s), "
        f"{observation['downloaded_files']} file(s)/{observation['downloaded_bytes']} bytes read. "
        "Advisory only; grants no authority."
    )


def dropbox_content_hash(content: bytes) -> str:
    block_hashes = b"".join(
        hashlib.sha256(content[offset : offset + DROPBOX_BLOCK_SIZE]).digest()
        for offset in range(0, len(content), DROPBOX_BLOCK_SIZE)
    )
    return hashlib.sha256(block_hashes).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a bounded, human-invoked Dropbox artifact integrity check.")
    parser.add_argument("--manifest", type=Path, required=True, help="Caller-owned JSON identity and authority manifest.")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--issue", help="Verify one issue directory named in the manifest.")
    scope.add_argument("--package", help="Verify one relative package directory named in the manifest.")
    scope.add_argument("--path", dest="object_path", help="Verify one relative file path named in the manifest.")
    scope.add_argument("--sample", help="Verify one explicit sample tag named in the manifest.")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--access-token-env", default="DROPBOX_ACCESS_TOKEN")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        scope = _scope_from_args(args)
        token = os.environ.get(args.access_token_env, "")
        if not token:
            raise ManifestError(f"access token environment variable is unset: {args.access_token_env}")
        store = manifest["store"]
        report = verify(
            manifest,
            scope,
            DropboxClient(token, str(store["namespace_id"])),
            max_files=args.max_files,
            max_bytes=args.max_bytes,
        )
    except (ManifestError, ValueError) as exc:
        print(f"artifact-store-integrity: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    print(render_summary(report), file=sys.stderr)
    return 0 if report["result"] == "pass" else 1


def _verify_bytes(
    item: dict[str, object],
    listed: dict[str, object],
    downloaded: dict[str, object],
    content: bytes,
) -> dict[str, object]:
    computed_dropbox_hash = dropbox_content_hash(content)
    observed = {
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "dropbox_content_hash": downloaded.get("content_hash"),
        "computed_dropbox_content_hash": computed_dropbox_hash,
        "file_id": downloaded.get("id"),
        "revision": downloaded.get("rev"),
    }
    comparisons: list[dict[str, object]] = []
    for field in ("size", "sha256", "dropbox_content_hash", "file_id", "revision"):
        expected = item.get(field)
        if expected is None:
            comparisons.append(_comparison(field, "cross_time", None, observed.get(field), "not_recorded"))
        else:
            status = "match" if expected == observed.get(field) else "mismatch" if observed.get(field) is not None else "unknown"
            comparisons.append(_comparison(field, "cross_time", expected, observed.get(field), status))
    same_checks = {
        "metadata_size_vs_raw": (downloaded.get("size"), observed["size"]),
        "metadata_content_hash_vs_raw": (downloaded.get("content_hash"), computed_dropbox_hash),
        "listing_file_id_vs_download": (listed.get("id"), downloaded.get("id")),
        "listing_revision_vs_download": (listed.get("rev"), downloaded.get("rev")),
        "listing_size_vs_download": (listed.get("size"), downloaded.get("size")),
        "listing_content_hash_vs_download": (listed.get("content_hash"), downloaded.get("content_hash")),
    }
    for field, (left, right) in same_checks.items():
        status = "unknown" if left is None or right is None else "match" if left == right else "mismatch"
        comparisons.append(_comparison(field, "same_observation", left, right, status))
    statuses = {comparison["status"] for comparison in comparisons}
    status = "changed" if "mismatch" in statuses else "unverifiable" if "unknown" in statuses else "pass"
    reasons = []
    if status == "changed":
        reasons.append("one or more immutable identity comparisons mismatched")
    elif status == "unverifiable":
        reasons.append("one or more required current metadata comparisons were unavailable")
    return {
        "path": item["path"],
        "status": status,
        "evidence_source": item.get("evidence_source"),
        "expected": {field: item.get(field) for field in ("size", "sha256", "dropbox_content_hash", "file_id", "revision")},
        "observed": observed,
        "comparisons": comparisons,
        "reasons": reasons,
    }


def _comparison(field: str, basis: str, expected: object, observed: object, status: str) -> dict[str, object]:
    return {"field": field, "basis": basis, "expected": expected, "observed": observed, "status": status}


def _terminal_result(item: dict[str, object], status: str, reason: str) -> dict[str, object]:
    return {
        "path": item["path"],
        "status": status,
        "evidence_source": item.get("evidence_source"),
        "expected": {field: item.get(field) for field in ("size", "sha256", "dropbox_content_hash", "file_id", "revision")},
        "observed": {},
        "comparisons": [],
        "reasons": [reason],
    }


def _provider_failure_result(item: dict[str, object], failure: ProviderError) -> dict[str, object]:
    status = "inaccessible" if failure.kind == "inaccessible" else "missing" if failure.kind == "missing" else "unverifiable"
    return _terminal_result(item, status, str(failure))


def _select_objects(objects: object, scope: Scope) -> list[dict[str, object]]:
    if not isinstance(objects, list):
        raise ManifestError("manifest objects must be a list")
    value = _validated_scope_value(scope)
    selected: list[dict[str, object]] = []
    for item in objects:
        if not isinstance(item, dict):
            continue
        path = str(item["path"])
        if scope.kind == "issue" and path.split("/", 1)[0].casefold() == value.casefold():
            selected.append(item)
        elif scope.kind == "package" and (path.casefold() == value.casefold() or path.casefold().startswith(value.casefold() + "/")):
            selected.append(item)
        elif scope.kind == "path" and path.casefold() == value.casefold():
            selected.append(item)
        elif scope.kind == "sample" and value in item.get("samples", []):
            selected.append(item)
    return selected


def _validated_scope_value(scope: Scope) -> str:
    if scope.kind not in {"issue", "package", "path", "sample"}:
        raise ManifestError(f"unsupported scope kind: {scope.kind}")
    if scope.kind == "sample":
        if not scope.value.strip():
            raise ManifestError("sample name must not be empty")
        return scope.value
    if not _valid_relative_path(scope.value):
        raise ManifestError(f"{scope.kind} scope must be a contained relative path")
    if scope.kind == "issue" and "/" in scope.value:
        raise ManifestError("issue scope must be one path component")
    return scope.value.rstrip("/")


def _index_listing(entries: Iterable[dict[str, object]], root: str) -> dict[str, dict[str, object]]:
    indexed: dict[str, dict[str, object]] = {}
    for entry in entries:
        relative = _relative_path(entry, root)
        if relative is not None:
            indexed[relative.casefold()] = entry
    return indexed


def _relative_path(metadata: dict[str, object], root: str) -> str | None:
    raw = metadata.get("path_display") or metadata.get("path_lower")
    if not isinstance(raw, str):
        return None
    root_parts = PurePosixPath(root).parts
    path_parts = PurePosixPath(raw).parts
    if len(path_parts) <= len(root_parts):
        return None
    if [part.casefold() for part in path_parts[: len(root_parts)]] != [part.casefold() for part in root_parts]:
        return None
    return "/".join(path_parts[len(root_parts) :])


def _scope_from_args(args: argparse.Namespace) -> Scope:
    if args.issue is not None:
        return Scope("issue", args.issue)
    if args.package is not None:
        return Scope("package", args.package)
    if args.object_path is not None:
        return Scope("path", args.object_path)
    return Scope("sample", args.sample)


def _join(root: str, relative: str) -> str:
    return root.rstrip("/") + "/" + relative.strip("/")


def _valid_absolute_path(value: str) -> bool:
    path = PurePosixPath(value)
    return value.startswith("/") and ".." not in path.parts and "\\" not in value


def _valid_relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not value.startswith("/") and ".." not in path.parts and "\\" not in value and not value.endswith("/")


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _http_provider_error(exc: error.HTTPError) -> ProviderError:
    try:
        body = exc.read(4096).decode("utf-8", errors="replace")
    except OSError:
        body = ""
    if exc.code in {401, 403}:
        return ProviderError("inaccessible", f"Dropbox access denied (HTTP {exc.code})")
    if exc.code == 409 and ("not_found" in body or "path/not_found" in body):
        return ProviderError("missing", "Dropbox path was not found")
    return ProviderError("unverifiable", f"Dropbox request failed (HTTP {exc.code})")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
