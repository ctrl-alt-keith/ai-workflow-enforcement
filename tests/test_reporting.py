from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from enforcement.drift_scanner import AdvisoryFinding, OverlapCandidate, ScanResult
from enforcement.reporting import render_json_report, render_report


class ReportingTests(unittest.TestCase):
    def test_render_report_with_zero_candidates(self) -> None:
        result = ScanResult(
            candidates=(),
            notes_files_scanned=2,
            playbook_files_scanned=3,
            ignored_paths=(Path("ignored.md"),),
        )

        report = render_report(result)

        self.assertIn("Notes vs playbook drift scan", report)
        self.assertIn("Notes files scanned: 2", report)
        self.assertIn("Playbook files scanned: 3", report)
        self.assertIn("Ignored paths: 1", report)
        self.assertIn("No overlap candidates found.", report)
        self.assertIn("No advisory workflow-policy findings found.", report)
        self.assertNotIn("Overlap candidates:", report)

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

        self.assertIn("Overlap candidates: 1", report)
        self.assertIn("1. Possible overlap", report)
        self.assertIn("Note: notes/thread.md", report)
        self.assertIn("Possible canonical target: playbook/baseline.md", report)
        self.assertIn("Reasons: repeated heading, missing canonical reference", report)
        self.assertIn("Token similarity: 0.72", report)
        self.assertIn("Canonical reference present: no", report)
        self.assertIn("Repeated headings: operating model", report)
        self.assertIn("Repeated phrases:", report)
        self.assertIn("- small scoped changes with canonical validation", report)
        self.assertIn("Advisory workflow-policy findings: 1", report)
        self.assertIn("weak_command_form_wording", report)
        self.assertIn("Line: 4", report)

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
            result = ScanResult(
                candidates=(candidate,),
                notes_files_scanned=1,
                playbook_files_scanned=2,
                ignored_paths=(root / "notes" / ".venv" / "ignored.md",),
                advisory_findings=(
                    AdvisoryFinding(
                        kind="ordinary_repo_command_shell_wrapper_example",
                        path=root / "notes" / "prompt.md",
                        line=2,
                        snippet="Run `bash -lc 'make check'`.",
                        reasons=("wrapper shell example contains ordinary repo command: make check",),
                        suggested_direction="Use direct argv form in examples unless shell syntax is actually required.",
                    ),
                ),
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
                "suggested_direction": (
                    "Review staged note for stale duplicate wording; keep local evidence or context only."
                ),
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
                "suggested_direction": "Use direct argv form in examples unless shell syntax is actually required.",
            },
            data["advisory_findings"][0],
        )


if __name__ == "__main__":
    unittest.main()
