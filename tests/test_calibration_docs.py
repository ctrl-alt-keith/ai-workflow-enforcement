from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from enforcement.config import ScannerConfig
from enforcement.drift_scanner import scan


ROOT = Path(__file__).resolve().parents[1]
CALIBRATION_DOC = ROOT / "docs" / "drift-review-calibration.md"
README = ROOT / "README.md"


class CalibrationDocsTests(unittest.TestCase):
    def test_calibration_doc_names_lightweight_review_categories(self) -> None:
        text = CALIBRATION_DOC.read_text(encoding="utf-8").lower()
        normalized = " ".join(text.split())

        for category in (
            "confirmed drift",
            "acceptable duplication",
            "intentional staging overlap",
            "historical residue",
            "false positive",
        ):
            self.assertIn(category, text)

        self.assertIn("candidate is a review prompt, not a finding", normalized)
        self.assertIn("leave category assignment to maintainers", normalized)

    def test_readme_points_reviewers_to_calibration_guidance(self) -> None:
        text = README.read_text(encoding="utf-8")

        self.assertIn("docs/drift-review-calibration.md", text)
        self.assertIn("without changing scanner behavior", text)

    def test_calibration_doc_does_not_trigger_its_worktree_guidance_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / CALIBRATION_DOC.name).write_text(
                CALIBRATION_DOC.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text(
                "Reusable workflow guidance lives here.\n",
                encoding="utf-8",
            )

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertNotIn(
            "worktree_creation_without_inspection_signal",
            {finding.kind for finding in result.advisory_findings},
        )


if __name__ == "__main__":
    unittest.main()
