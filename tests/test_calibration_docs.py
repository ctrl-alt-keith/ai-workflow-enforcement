from __future__ import annotations

from pathlib import Path
import unittest


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


if __name__ == "__main__":
    unittest.main()
