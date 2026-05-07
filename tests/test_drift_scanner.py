from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from enforcement.config import ScannerConfig
from enforcement.drift_scanner import scan


class DriftScannerTests(unittest.TestCase):
    def test_detects_likely_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            shared = (
                "Prefer small scoped changes with direct validation through "
                "the repository canonical make check entrypoint before review."
            )
            (notes / "thread.md").write_text(
                f"# Workflow Alignment\n\n{shared}\n\n{shared}\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text(
                f"# Workflow Alignment\n\n{shared}\n\n{shared}\n",
                encoding="utf-8",
            )

            result = scan(
                ScannerConfig(
                    notes_roots=(notes,),
                    playbook_roots=(playbook,),
                    min_heading_matches=1,
                    min_phrase_words=6,
                    min_phrase_matches=2,
                )
            )

        self.assertEqual(1, len(result.candidates))
        candidate = result.candidates[0]
        self.assertIn("workflow alignment", candidate.repeated_headings)
        self.assertIn("repeated normalized phrase", candidate.reasons)
        self.assertIn("missing canonical reference", candidate.reasons)

    def test_ignore_patterns_skip_matching_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            ignored = notes / "archive"
            ignored.mkdir(parents=True)
            playbook.mkdir()
            text = "durable workflow guidance follows bounded evidence supported promotion"
            (ignored / "old.md").write_text(f"# Promotion Lane\n\n{text}\n", encoding="utf-8")
            (playbook / "promotion.md").write_text(f"# Promotion Lane\n\n{text}\n", encoding="utf-8")

            result = scan(
                ScannerConfig(
                    notes_roots=(notes,),
                    playbook_roots=(playbook,),
                    ignore_patterns=("archive/**",),
                    min_phrase_words=5,
                    min_phrase_matches=1,
                )
            )

        self.assertEqual(0, len(result.candidates))
        self.assertEqual(1, len(result.ignored_paths))

    def test_similarity_threshold_controls_candidate_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            note_text = "alpha beta gamma delta epsilon zeta eta theta iota"
            target_text = "alpha beta gamma delta epsilon zeta kappa lambda mu"
            (notes / "note.md").write_text(note_text, encoding="utf-8")
            (playbook / "target.md").write_text(target_text, encoding="utf-8")

            low_threshold = scan(
                ScannerConfig(
                    notes_roots=(notes,),
                    playbook_roots=(playbook,),
                    similarity_threshold=0.45,
                    min_phrase_words=6,
                    min_phrase_matches=3,
                )
            )
            high_threshold = scan(
                ScannerConfig(
                    notes_roots=(notes,),
                    playbook_roots=(playbook,),
                    similarity_threshold=0.9,
                    min_phrase_words=6,
                    min_phrase_matches=3,
                )
            )

        self.assertEqual(1, len(low_threshold.candidates))
        self.assertEqual(0, len(high_threshold.candidates))

    def test_generic_headings_do_not_create_false_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "note.md").write_text("# Purpose\n\nKeep a temporary idea here.\n", encoding="utf-8")
            (playbook / "guide.md").write_text("# Purpose\n\nDefine reusable guidance here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertEqual(0, len(result.candidates))


if __name__ == "__main__":
    unittest.main()
