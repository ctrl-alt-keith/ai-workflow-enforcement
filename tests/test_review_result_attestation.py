from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from enforcement.review_result_attestation import (
    AttestationValidationError,
    load_review_result_attestation,
    validate_review_result_attestation,
)


EXAMPLE_ATTESTATION = (
    Path(__file__).resolve().parents[1] / "examples" / "drift-review-result-attestation.json"
)


class ReviewResultAttestationTests(unittest.TestCase):
    def test_valid_attestation_loads_and_validates(self) -> None:
        attestation = load_review_result_attestation(EXAMPLE_ATTESTATION)

        validate_review_result_attestation(attestation)

        self.assertEqual("drift_review_result", attestation["attestation_type"])
        self.assertEqual(1, attestation["schema_version"])
        self.assertEqual("drift_review", attestation["source_task_type"])
        self.assertIs(False, attestation["cleanup_required"])

    def test_missing_required_fields_are_rejected(self) -> None:
        attestation = _example()
        del attestation["classification"]

        with self.assertRaises(AttestationValidationError):
            validate_review_result_attestation(attestation)

    def test_unknown_fields_are_rejected_to_match_schema_contract(self) -> None:
        attestation = _example()
        attestation["cleanup_complete"] = True

        with self.assertRaises(AttestationValidationError):
            validate_review_result_attestation(attestation)

    def test_invalid_schema_version_is_rejected(self) -> None:
        attestation = _example()
        attestation["schema_version"] = 2

        with self.assertRaises(AttestationValidationError):
            validate_review_result_attestation(attestation)

    def test_invalid_attestation_type_is_rejected(self) -> None:
        attestation = _example()
        attestation["attestation_type"] = "workflow_state"

        with self.assertRaises(AttestationValidationError):
            validate_review_result_attestation(attestation)

    def test_loader_requires_json_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "attestation.json"
            path.write_text(json.dumps(["drift_review_result"]), encoding="utf-8")

            with self.assertRaises(AttestationValidationError):
                load_review_result_attestation(path)


def _example() -> dict[str, object]:
    return copy.deepcopy(load_review_result_attestation(EXAMPLE_ATTESTATION))


if __name__ == "__main__":
    unittest.main()
