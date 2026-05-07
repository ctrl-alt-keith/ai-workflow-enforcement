from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from enforcement.cli import main


class CliTests(unittest.TestCase):
    def test_success_path_is_advisory_exit_zero_without_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes, playbook = _make_roots(root)
            (notes / "note.md").write_text("temporary working note", encoding="utf-8")
            (playbook / "guide.md").write_text("canonical durable guidance", encoding="utf-8")

            code, stdout, stderr = _run_cli(
                "--notes-root",
                str(notes),
                "--playbook-root",
                str(playbook),
            )

        self.assertEqual(0, code)
        self.assertIn("No overlap candidates found.", stdout)
        self.assertEqual("", stderr)

    def test_error_path_returns_two_and_reports_to_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            playbook = root / "playbook"
            playbook.mkdir()

            code, stdout, stderr = _run_cli(
                "--notes-root",
                str(root / "missing-notes"),
                "--playbook-root",
                str(playbook),
            )

        self.assertEqual(2, code)
        self.assertEqual("", stdout)
        self.assertIn("error: configured root does not exist:", stderr)

    def test_fail_on_candidates_is_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes, playbook = _make_roots(root)
            text = "small scoped changes use the canonical make check validation path"
            (notes / "thread.md").write_text(f"# Validation Path\n\n{text}\n\n{text}\n", encoding="utf-8")
            (playbook / "baseline.md").write_text(f"# Validation Path\n\n{text}\n\n{text}\n", encoding="utf-8")
            args = (
                "--notes-root",
                str(notes),
                "--playbook-root",
                str(playbook),
                "--min-heading-matches",
                "1",
                "--min-phrase-words",
                "5",
                "--min-phrase-matches",
                "1",
            )

            advisory_code, advisory_stdout, advisory_stderr = _run_cli(*args)
            failing_code, failing_stdout, failing_stderr = _run_cli(*args, "--fail-on-candidates")

        self.assertEqual(0, advisory_code)
        self.assertEqual(1, failing_code)
        self.assertIn("Overlap candidates: 1", advisory_stdout)
        self.assertIn("Overlap candidates: 1", failing_stdout)
        self.assertEqual("", advisory_stderr)
        self.assertEqual("", failing_stderr)

    def test_output_format_json_emits_machine_readable_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes, playbook = _make_roots(root)
            text = "small scoped changes use the canonical make check validation path"
            (notes / "thread.md").write_text(f"# Validation Path\n\n{text}\n\n{text}\n", encoding="utf-8")
            (playbook / "baseline.md").write_text(f"# Validation Path\n\n{text}\n\n{text}\n", encoding="utf-8")

            code, stdout, stderr = _run_cli(
                "--notes-root",
                str(notes),
                "--playbook-root",
                str(playbook),
                "--min-heading-matches",
                "1",
                "--min-phrase-words",
                "5",
                "--min-phrase-matches",
                "1",
                "--output-format",
                "json",
            )

        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        self.assertNotIn("Notes vs playbook drift scan", stdout)
        data = json.loads(stdout)
        self.assertEqual("notes_playbook_drift_scan", data["report_type"])
        self.assertIs(True, data["advisory"])
        self.assertEqual(1, data["summary"]["candidate_count"])
        self.assertEqual(
            {
                "canonical_reference_present",
                "note_path",
                "playbook_path",
                "reasons",
                "repeated_headings",
                "repeated_phrases",
                "similarity",
                "suggested_direction",
            },
            set(data["candidates"][0]),
        )


def _make_roots(root: Path) -> tuple[Path, Path]:
    notes = root / "notes"
    playbook = root / "playbook"
    notes.mkdir()
    playbook.mkdir()
    return notes, playbook


def _run_cli(*args: str) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(list(args))
    return code, stdout.getvalue(), stderr.getvalue()


if __name__ == "__main__":
    unittest.main()
