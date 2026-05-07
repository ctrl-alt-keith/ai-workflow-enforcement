from __future__ import annotations

import json
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "skills" / "drift_review" / "manifest.json"

REQUIRED_BOUNDARIES = {
    "advisory_only",
    "human_reviewed_classification",
    "non_executable_manifest",
    "no_orchestration_runtime",
    "no_automatic_execution",
    "no_automatic_remediation",
    "no_workflow_state_machine",
    "no_persistent_workflow_state",
    "no_github_or_ci_integration",
    "no_agent_coordination",
    "no_skill_marketplace_or_discovery",
}

FORBIDDEN_EXECUTION_KEYS = {
    "entrypoint",
    "execute",
    "executor",
    "hook",
    "hooks",
    "schedule",
    "scheduler",
    "trigger",
    "triggers",
    "workflow_state",
}


class DriftReviewSkillManifestTests(unittest.TestCase):
    def test_manifest_declares_existing_contracts(self) -> None:
        manifest = _load_manifest()

        self.assertEqual("portable_skill_capability", manifest["manifest_type"])
        self.assertEqual(1, manifest["manifest_version"])
        self.assertEqual("drift_review", manifest["name"])

        task_envelope = manifest["supported_task_envelope"]
        self.assertEqual("drift_review", task_envelope["task_type"])
        self.assertEqual(1, task_envelope["schema_version"])

        attestation = manifest["supported_attestation"]
        self.assertEqual("drift_review_result", attestation["attestation_type"])
        self.assertEqual("drift_review", attestation["source_task_type"])
        self.assertEqual(1, attestation["schema_version"])

    def test_manifest_references_existing_files(self) -> None:
        manifest = _load_manifest()

        referenced_paths = [
            manifest["supported_task_envelope"]["schema"],
            manifest["supported_task_envelope"]["example"],
            manifest["supported_attestation"]["schema"],
            manifest["supported_attestation"]["example"],
        ]
        referenced_paths.extend(tool["path"] for tool in manifest["referenced_tooling"])

        for relative_path in referenced_paths:
            with self.subTest(path=relative_path):
                self.assertTrue((REPO_ROOT / relative_path).exists())

    def test_manifest_preserves_non_executable_boundaries(self) -> None:
        manifest = _load_manifest()

        self.assertEqual("repository_root", manifest["path_reference_base"])
        self.assertTrue(REQUIRED_BOUNDARIES.issubset(set(manifest["operational_boundaries"])))
        self.assertFalse(FORBIDDEN_EXECUTION_KEYS.intersection(_all_keys(manifest)))

    def test_manifest_uses_canonical_validation_entrypoint(self) -> None:
        manifest = _load_manifest()

        self.assertIn(
            "Run make check from the repository root.",
            manifest["validation_expectations"],
        )


def _load_manifest() -> dict[str, object]:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AssertionError("skill manifest must be a JSON object")
    return data


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for nested in value.values():
            keys.update(_all_keys(nested))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_all_keys(item))
        return keys
    return set()


if __name__ == "__main__":
    unittest.main()
