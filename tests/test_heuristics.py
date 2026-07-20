from __future__ import annotations

from collections import Counter
import unittest

from enforcement import heuristics


class HeuristicsTests(unittest.TestCase):
    def test_normalized_headings_excludes_generic_and_normalizes_specific_headings(self) -> None:
        text = "\n".join(
            [
                "# Overview",
                "## Validation Path",
                "### Context",
                "#### Canonical Make Check",
            ]
        )

        self.assertEqual(
            heuristics.normalized_headings(text),
            {"validation path", "canonical make check"},
        )

    def test_normalized_phrases_skips_low_signal_repetition(self) -> None:
        text = "the the the the the the the"

        self.assertEqual(heuristics.normalized_phrases(text, 5), Counter())

    def test_has_canonical_reference_detects_start_here_reference(self) -> None:
        text = "See docs/start-here.md before changing repo workflow behavior."

        self.assertTrue(heuristics.has_canonical_reference(text))

    def test_token_similarity_returns_zero_for_empty_input(self) -> None:
        self.assertEqual(heuristics.token_similarity("", "canonical validation"), 0.0)
        self.assertEqual(heuristics.token_similarity("notes only", ""), 0.0)


if __name__ == "__main__":
    unittest.main()
