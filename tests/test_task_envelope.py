from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from enforcement.task_envelope import (
    EnvelopeValidationError,
    load_task_envelope,
    validate_task_envelope,
)


EXAMPLE_ENVELOPE = Path(__file__).resolve().parents[1] / "examples" / "drift-review-envelope.json"


class TaskEnvelopeTests(unittest.TestCase):
    def test_valid_envelope_loads_and_validates(self) -> None:
        envelope = load_task_envelope(EXAMPLE_ENVELOPE)

        validate_task_envelope(envelope)

        self.assertEqual("drift_review", envelope["task_type"])
        self.assertEqual(1, envelope["schema_version"])

    def test_invalid_task_type_is_rejected(self) -> None:
        envelope = _example()
        envelope["task_type"] = "generic_workflow"

        with self.assertRaisesRegex(EnvelopeValidationError, "unknown task_type"):
            validate_task_envelope(envelope)

    def test_missing_required_fields_are_rejected(self) -> None:
        envelope = _example()
        del envelope["expected_outputs"]

        with self.assertRaisesRegex(
            EnvelopeValidationError,
            "missing required fields: expected_outputs",
        ):
            validate_task_envelope(envelope)

    def test_invalid_schema_version_is_rejected(self) -> None:
        envelope = _example()
        envelope["schema_version"] = 2

        with self.assertRaisesRegex(EnvelopeValidationError, "unsupported schema_version"):
            validate_task_envelope(envelope)

    def test_loader_requires_json_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "envelope.json"
            path.write_text(json.dumps(["drift_review"]), encoding="utf-8")

            with self.assertRaisesRegex(
                EnvelopeValidationError,
                "task envelope must be a JSON object",
            ):
                load_task_envelope(path)


def _example() -> dict[str, object]:
    return copy.deepcopy(load_task_envelope(EXAMPLE_ENVELOPE))


if __name__ == "__main__":
    unittest.main()
