from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from enforcement.task_envelope import (
    EnvelopeValidationError,
    load_task_envelope,
    main,
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

    def test_cli_reports_valid_envelope_contract(self) -> None:
        code, stdout, stderr = _run_cli(str(EXAMPLE_ENVELOPE))

        self.assertEqual(0, code)
        self.assertEqual("valid drift_review envelope schema_version 1\n", stdout)
        self.assertEqual("", stderr)

    def test_cli_invalid_envelope_returns_two_and_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "envelope.json"
            envelope = _example()
            envelope["schema_version"] = "1"
            path.write_text(json.dumps(envelope), encoding="utf-8")

            code, stdout, stderr = _run_cli(str(path))

        self.assertEqual(2, code)
        self.assertEqual("", stdout)
        self.assertIn("error: unsupported schema_version: '1'", stderr)


def _example() -> dict[str, object]:
    return copy.deepcopy(load_task_envelope(EXAMPLE_ENVELOPE))


def _run_cli(*args: str) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(list(args))
    return code, stdout.getvalue(), stderr.getvalue()


if __name__ == "__main__":
    unittest.main()
