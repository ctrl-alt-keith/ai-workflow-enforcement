"""Read-only GitHub App policy audit for the workflow-drift App."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Callable


POLICY_ROOT = Path(__file__).resolve().parent.parent / "policy" / "github-apps" / "workflow-drift"
FORBIDDEN_RECEIPT_KEYS = frozenset({"private_key", "webhook_secret", "installation_token", "token", "pem"})


@dataclass(frozen=True)
class AuditItem:
    field: str
    status: str
    expected: object
    actual: object
    reason: str


def audit(policy: dict[str, Any], receipt: dict[str, Any] | None, live: dict[str, Any] | None) -> list[AuditItem]:
    """Compare supported non-secret facts; missing state is never clean."""
    app = policy["app"]
    installation = policy["installation"]
    return [
        _compare("app.owner", app["owner"], _get(live, "app", "owner"), "App JWT /app snapshot required."),
        _compare("app.slug", app["logical_name"], _get(live, "app", "slug"), "App JWT /app snapshot required."),
        _compare("permissions", policy["permissions"], _get(live, "installation", "effective_permissions"), "App JWT installation snapshot required."),
        _compare("events", sorted(policy["events"]), _sorted(_get(live, "installation", "effective_events")), "App JWT installation snapshot required."),
        _compare("installation.repository_selection", installation["expected_repository_selection"], _get(live, "installation", "repository_selection"), "An installation token cannot prove selection mode."),
        _compare("installation.owner", app["owner"], _get(live, "installation", "owner"), "App JWT installation snapshot required."),
        _compare("installation.id", _get(receipt, "installation", "installation_id"), _get(live, "installation", "installation_id"), "No supported live installation identity was supplied."),
        _compare_scope(receipt, live),
        _receipt_item("receipt.app_id", receipt, "app", "app_id"),
        _receipt_item("receipt.client_id", receipt, "app", "client_id"),
        _receipt_item("receipt.key_fingerprint", receipt, "key", "fingerprint"),
        AuditItem("registration.webhook", "unable-to-verify", "manifest bootstrap intent", None, "No safe supported read path is used for registration settings."),
        AuditItem("registration.urls", "unable-to-verify", "manifest bootstrap intent", None, "No safe supported read path is used for registration settings."),
    ]


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def validate_receipt(receipt: dict[str, Any]) -> None:
    forbidden = _forbidden_keys(receipt)
    if forbidden:
        raise ValueError("receipt contains forbidden secret-bearing key(s): " + ", ".join(sorted(forbidden)))
    for path in (("app", "app_id"), ("app", "client_id"), ("app", "owner"), ("app", "slug"), ("installation", "installation_id"), ("installation", "repository_selection"), ("installation", "scope_sha256"), ("installation", "effective_permissions"), ("installation", "effective_events"), ("key", "fingerprint"), ("key", "secret_manager_reference"), ("key", "secret_manager_version"), ("approval", "approved_by"), ("approval", "approved_at"), ("validation_evidence", "representative_run_url"), ("validation_evidence", "captured_at")):
        if _get(receipt, *path) in (None, ""):
            raise ValueError("receipt missing required non-secret field: " + ".".join(path))
    if receipt.get("schema_version") != 1:
        raise ValueError("receipt schema_version must be 1")
    if _get(receipt, "installation", "repository_selection") not in {"all", "selected"}:
        raise ValueError("receipt installation.repository_selection must be all or selected")
    scope_hash = _get(receipt, "installation", "scope_sha256")
    if not isinstance(scope_hash, str) or len(scope_hash) != 64 or any(char not in "0123456789abcdef" for char in scope_hash):
        raise ValueError("receipt installation.scope_sha256 must be a lowercase SHA-256 digest")
    if not isinstance(_get(receipt, "installation", "effective_permissions"), dict) or not isinstance(_get(receipt, "installation", "effective_events"), list):
        raise ValueError("receipt effective permissions and events must use the documented JSON types")


def fetch_installation_repositories(runner: Callable[[tuple[str, ...]], str] | None = None) -> dict[str, Any]:
    run = runner or _gh
    pages = json.loads(run(("gh", "api", "--paginate", "--slurp", "/installation/repositories?per_page=100")))
    if not isinstance(pages, list) or not pages:
        raise ValueError("installation repository API returned no pages")
    totals = {page.get("total_count") for page in pages if isinstance(page, dict)}
    names = sorted({repo.get("full_name") for page in pages if isinstance(page, dict) for repo in page.get("repositories", []) if isinstance(repo, dict) and isinstance(repo.get("full_name"), str)})
    if len(totals) != 1 or None in totals or len(names) != totals.pop():
        raise ValueError("installation repository pagination is incomplete")
    return {"installation": {"visible_repositories": names, "scope_sha256": _scope_hash(names)}}


def render_json(items: list[AuditItem]) -> str:
    summary = {status: sum(item.status == status for item in items) for status in ("match", "drift", "unable-to-verify")}
    return json.dumps({"schema_version": 1, "report_type": "github_app_policy_audit", "read_only": True, "summary": summary, "items": [asdict(item) for item in items]}, indent=2, sort_keys=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only workflow-drift GitHub App policy audit.")
    parser.add_argument("--policy", type=Path, default=POLICY_ROOT / "permissions-policy.json")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--live-state", type=Path, help="Non-secret App/JWT snapshot; this tool never reads an App key.")
    parser.add_argument("--fetch-installation-repositories", action="store_true", help="Read /installation/repositories with the current installation token.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    policy = load_json(args.policy)
    receipt = load_json(args.receipt) if args.receipt else None
    if receipt:
        validate_receipt(receipt)
    live = load_json(args.live_state) if args.live_state else None
    if args.fetch_installation_repositories:
        fetched = fetch_installation_repositories()
        current = _get(live, "installation", default={})
        live = {**(live or {}), "installation": {**current, **fetched["installation"]}}
    print(render_json(audit(policy, receipt, live)))
    return 0


def _compare(field: str, expected: object, actual: object, unavailable_reason: str) -> AuditItem:
    if expected in (None, "") or actual is None:
        return AuditItem(field, "unable-to-verify", expected, actual, unavailable_reason)
    return AuditItem(field, "match" if expected == actual else "drift", expected, actual, "Compared from a non-secret live snapshot.")


def _compare_scope(receipt: dict[str, Any] | None, live: dict[str, Any] | None) -> AuditItem:
    return _compare("installation.visible_repository_scope", _get(receipt, "installation", "scope_sha256"), _get(live, "installation", "scope_sha256"), "Visible repositories describe installation scope only; they do not prove All repositories.")


def _receipt_item(field: str, receipt: dict[str, Any] | None, *path: str) -> AuditItem:
    return AuditItem(field, "unable-to-verify", _get(receipt, *path), None, "Receipt metadata is historical evidence, not proof of current GitHub state.")


def _get(data: dict[str, Any] | None, *path: str, default: object = None) -> Any:
    current: Any = data
    for part in path:
        if not isinstance(current, dict):
            return default
        current = current.get(part)
    return default if current is None else current


def _sorted(value: object) -> object:
    return sorted(value) if isinstance(value, list) else value


def _scope_hash(names: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(names)).encode("utf-8")).hexdigest()


def _forbidden_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        nested = set().union(*(_forbidden_keys(item) for item in value.values())) if value else set()
        return {key for key in value if key.casefold() in FORBIDDEN_RECEIPT_KEYS} | nested
    if isinstance(value, list):
        return set().union(*(_forbidden_keys(item) for item in value)) if value else set()
    return set()


def _gh(argv: tuple[str, ...]) -> str:
    result = subprocess.run(argv, check=False, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "GitHub CLI command failed")
    return result.stdout


if __name__ == "__main__":
    raise SystemExit(main())
