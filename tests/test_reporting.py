from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from enforcement.drift_scanner import OverlapCandidate, ScanResult
from enforcement.reporting import render_report


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


if __name__ == "__main__":
    unittest.main()
