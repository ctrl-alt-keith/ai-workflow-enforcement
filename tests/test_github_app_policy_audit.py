from __future__ import annotations

import json
from pathlib import Path
import unittest

from enforcement.github_app_policy_audit import audit, fetch_installation_repositories, load_json, validate_receipt


ROOT = Path(__file__).resolve().parents[1]
POLICY = load_json(ROOT / "policy/github-apps/workflow-drift/permissions-policy.json")
MANIFEST = load_json(ROOT / "policy/github-apps/workflow-drift/manifest.json")
RECEIPT_SCHEMA = load_json(ROOT / "policy/github-apps/workflow-drift/receipt.schema.json")


def receipt(selection: str = "all") -> dict:
    return {"schema_version": 1, "app": {"app_id": 7, "client_id": "Iv1.example", "owner": "ctrl-alt-keith", "slug": "workflow-drift-audit"}, "installation": {"installation_id": 9, "repository_selection": selection, "scope_sha256": "a" * 64, "effective_permissions": {"contents": "read", "metadata": "read"}, "effective_events": []}, "key": {"fingerprint": "SHA256:example", "secret_manager_reference": "pending://approved-adoption", "secret_manager_version": "pending"}, "approval": {"approved_by": "operator", "approved_at": "2026-08-12T00:00:00Z"}, "validation_evidence": {"representative_run_url": "https://github.com/ctrl-alt-keith/ai-workflow-enforcement/actions/runs/1", "captured_at": "2026-08-12T00:00:00Z"}}


class GitHubAppPolicyAuditTests(unittest.TestCase):
    def test_policy_artifacts_are_non_secret_and_consistent(self) -> None:
        self.assertEqual(MANIFEST["name"], POLICY["app"]["logical_name"])
        self.assertEqual(MANIFEST["default_permissions"], POLICY["permissions"])
        self.assertIn("private_key", RECEIPT_SCHEMA["not"]["anyOf"][0]["required"])

    def test_matching_supported_live_state_and_registration_gap(self) -> None:
        live = {"app": {"owner": "ctrl-alt-keith", "slug": "workflow-drift-audit"}, "installation": {"installation_id": 9, "owner": "ctrl-alt-keith", "repository_selection": "all", "effective_permissions": {"contents": "read", "metadata": "read"}, "effective_events": [], "scope_sha256": "a" * 64}}
        results = {item.field: item for item in audit(POLICY, receipt(), live)}
        self.assertEqual("match", results["permissions"].status)
        self.assertEqual("unable-to-verify", results["registration.webhook"].status)

    def test_detects_drift(self) -> None:
        live = {"app": {"owner": "wrong", "slug": "workflow-drift-audit"}, "installation": {"repository_selection": "selected", "effective_permissions": {"contents": "write"}, "effective_events": ["push"]}}
        results = {item.field: item.status for item in audit(POLICY, receipt(), live)}
        self.assertEqual("drift", results["app.owner"])
        self.assertEqual("drift", results["installation.repository_selection"])
        self.assertEqual("drift", results["permissions"])

    def test_selected_scope_is_not_all_and_visible_scope_is_not_proof(self) -> None:
        results = {item.field: item for item in audit(POLICY, receipt("selected"), {"installation": {"visible_repositories": ["ctrl-alt-keith/a"]}})}
        self.assertEqual("unable-to-verify", results["installation.repository_selection"].status)
        self.assertEqual("unable-to-verify", results["installation.visible_repository_scope"].status)

    def test_receipt_rejects_secret_bearing_inputs(self) -> None:
        unsafe = receipt()
        unsafe["key"]["private_key"] = "not allowed"
        with self.assertRaises(ValueError):
            validate_receipt(unsafe)

    def test_receipt_rejects_malformed_scope_hash(self) -> None:
        malformed = receipt()
        malformed["installation"]["scope_sha256"] = "not-a-sha"
        with self.assertRaises(ValueError):
            validate_receipt(malformed)

    def test_fetches_only_installation_visible_repositories(self) -> None:
        pages = json.dumps([{"total_count": 2, "repositories": [{"full_name": "ctrl-alt-keith/b"}, {"full_name": "ctrl-alt-keith/a"}]}])
        live = fetch_installation_repositories(lambda argv: pages)
        self.assertEqual(["ctrl-alt-keith/a", "ctrl-alt-keith/b"], live["installation"]["visible_repositories"])


if __name__ == "__main__":
    unittest.main()
