from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from enforcement.drift_scanner import AdvisoryFinding, OverlapCandidate, ScanResult
from enforcement.reporting import render_json_report, render_report


class ReportingTests(unittest.TestCase):
    def test_render_report_with_candidate_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = OverlapCandidate(
                note_path=root / "notes" / "thread.md",
                playbook_path=root / "playbook" / "baseline.md",
                repeated_headings=("operating model",),
                repeated_phrases=("small scoped changes with canonical validation",),
                similarity=0.72,
                has_canonical_reference=False,
                reasons=("repeated heading", "missing canonical reference"),
            )
            result = ScanResult(
                candidates=(candidate,),
                notes_files_scanned=1,
                playbook_files_scanned=1,
                ignored_paths=(),
                advisory_findings=(
                    AdvisoryFinding(
                        kind="weak_command_form_wording",
                        path=root / "notes" / "thread.md",
                        line=4,
                        snippet="Prefer direct git and gh commands.",
                        reasons=("make command mention",),
                        suggested_direction="Strengthen local wording.",
                    ),
                ),
            )

            report = render_report(result, base_dir=root)

        for value in (
            "notes/thread.md",
            "playbook/baseline.md",
            "repeated heading",
            "missing canonical reference",
            "operating model",
            "small scoped changes with canonical validation",
            "weak_command_form_wording",
            "Prefer direct git and gh commands.",
        ):
            self.assertIn(value, report)

    def test_render_json_report_has_stable_advisory_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = OverlapCandidate(
                note_path=root / "notes" / "thread.md",
                playbook_path=root / "playbook" / "baseline.md",
                repeated_headings=("operating model",),
                repeated_phrases=("small scoped changes with canonical validation",),
                similarity=0.72451,
                has_canonical_reference=True,
                reasons=("repeated heading",),
            )
            finding = AdvisoryFinding(
                kind="ordinary_repo_command_shell_wrapper_example",
                path=root / "notes" / "prompt.md",
                line=2,
                snippet="Run `bash -lc 'make check'`.",
                reasons=("wrapper shell example contains ordinary repo command: make check",),
                suggested_direction="Use direct argv form in examples unless shell syntax is actually required.",
            )
            result = ScanResult(
                candidates=(candidate,),
                notes_files_scanned=1,
                playbook_files_scanned=2,
                ignored_paths=(root / "notes" / ".venv" / "ignored.md",),
                advisory_findings=(finding,),
            )

            first_report = render_json_report(result, base_dir=root)
            second_report = render_json_report(result, base_dir=root)

        self.assertEqual(first_report, second_report)
        data = json.loads(first_report)
        self.assertEqual(1, data["schema_version"])
        self.assertEqual("notes_playbook_drift_scan", data["report_type"])
        self.assertIs(True, data["advisory"])
        self.assertEqual(
            {
                "advisory_finding_count": 1,
                "candidate_count": 1,
                "ignored_path_count": 1,
                "notes_files_scanned": 1,
                "playbook_files_scanned": 2,
            },
            data["summary"],
        )
        self.assertEqual(
            {
                "canonical_reference_present": True,
                "note_path": "notes/thread.md",
                "playbook_path": "playbook/baseline.md",
                "reasons": ["repeated heading"],
                "repeated_headings": ["operating model"],
                "repeated_phrases": ["small scoped changes with canonical validation"],
                "similarity": 0.7245,
                "suggested_direction": candidate.suggested_direction,
            },
            data["candidates"][0],
        )
        self.assertEqual(
            {
                "kind": "ordinary_repo_command_shell_wrapper_example",
                "path": "notes/prompt.md",
                "line": 2,
                "snippet": "Run `bash -lc 'make check'`.",
                "reasons": ["wrapper shell example contains ordinary repo command: make check"],
                "suggested_direction": finding.suggested_direction,
            },
            data["advisory_findings"][0],
        )


if __name__ == "__main__":
    unittest.main()
