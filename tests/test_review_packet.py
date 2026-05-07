from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from enforcement.review_packet import main, render_review_packet


class ReviewPacketTests(unittest.TestCase):
    def test_valid_json_input_renders_markdown_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan.json"
            path.write_text(json.dumps(_scan(candidates=[])), encoding="utf-8")

            code, stdout, stderr = _run_cli("--input", str(path))

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        self.assertIn("# Drift Scan Review Packet", stdout)
        self.assertIn("- Report type: notes_playbook_drift_scan", stdout)
        self.assertIn("- Advisory input: yes", stdout)

    def test_valid_json_stdin_renders_markdown_packet(self) -> None:
        payload = json.dumps(_scan(candidates=[]))

        with patch("sys.stdin", StringIO(payload)):
            code, stdout, stderr = _run_cli()

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        self.assertIn("# Drift Scan Review Packet", stdout)
        self.assertIn("- Candidate count: 0", stdout)

    def test_zero_candidate_output_keeps_handoff_advisory(self) -> None:
        packet = render_review_packet(_scan(candidates=[]))

        self.assertIn("- Candidate count: 0", packet)
        self.assertIn("No overlap candidates were present in the scan JSON.", packet)
        self.assertIn("final classification remains human-reviewed", packet)
        self.assertIn("This packet does not infer final classifications", packet)

    def test_candidate_output_groups_evidence_for_review(self) -> None:
        packet = render_review_packet(_scan(candidates=[_candidate()]))

        self.assertIn("- Candidate count: 1", packet)
        self.assertIn("### Candidate 1", packet)
        self.assertIn("- Note: notes/thread.md", packet)
        self.assertIn("- Possible canonical target: playbook/baseline.md", packet)
        self.assertIn("- Scanner reasons: repeated heading, missing canonical reference", packet)
        self.assertIn("- Token similarity: 0.7245", packet)
        self.assertIn("- Canonical reference present: no", packet)
        self.assertIn("Repeated headings:", packet)
        self.assertIn("- operating model", packet)
        self.assertIn("Repeated phrases:", packet)
        self.assertIn("- small scoped changes with canonical validation", packet)
        self.assertIn("## Suggested Reviewer Questions", packet)

    def test_invalid_input_handling_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan.json"
            path.write_text("{not-json", encoding="utf-8")

            code, stdout, stderr = _run_cli("--input", str(path))

        self.assertEqual(2, code)
        self.assertEqual("", stdout)
        self.assertIn("error: invalid drift scan JSON:", stderr)

    def test_invalid_shape_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan.json"
            path.write_text(json.dumps({"report_type": "other"}), encoding="utf-8")

            code, stdout, stderr = _run_cli("--input", str(path))

        self.assertEqual(2, code)
        self.assertEqual("", stdout)
        self.assertIn("error: drift scan JSON report_type must be notes_playbook_drift_scan", stderr)


def _scan(*, candidates: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "report_type": "notes_playbook_drift_scan",
        "advisory": True,
        "summary": {
            "notes_files_scanned": 2,
            "playbook_files_scanned": 3,
            "ignored_path_count": 1,
            "candidate_count": len(candidates),
        },
        "candidates": candidates,
    }


def _candidate() -> dict[str, object]:
    return {
        "canonical_reference_present": False,
        "note_path": "notes/thread.md",
        "playbook_path": "playbook/baseline.md",
        "reasons": ["repeated heading", "missing canonical reference"],
        "repeated_headings": ["operating model"],
        "repeated_phrases": ["small scoped changes with canonical validation"],
        "similarity": 0.72451,
        "suggested_direction": "Consider replacing repeated guidance with a short canonical playbook reference.",
    }


def _run_cli(*args: str) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(list(args))
    return code, stdout.getvalue(), stderr.getvalue()


if __name__ == "__main__":
    unittest.main()
