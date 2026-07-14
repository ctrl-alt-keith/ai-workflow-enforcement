from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from enforcement.safe_rm import CONTROL_NAME, CONTROL_VERSION, SafeRmError, main, remove_directories, validate_operand


class SafeRmTests(unittest.TestCase):
    def test_accepts_generic_literal_relative_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp).resolve()
            for operand in (
                "pytest-of-root", ".pytest_cache", "build", "dist", "docs/_build",
                "knowledge-adapters", "foo/bar.baz_qux-1",
            ):
                with self.subTest(operand=operand):
                    self.assertTrue(validate_operand(operand, cwd))

    def test_rejects_dangerous_and_non_literal_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp).resolve()
            rejected = (
                "*", ".", "./", "..", "../foo", "/tmp/foo", "~/foo", '"$DIR"',
                "${DIR}", "$(pwd)", "`pwd`", "**/*", "foo/../bar", "foo//bar",
                "foo/", "build;whoami", "build|tee", "build>output", "path with spaces",
            )
            for operand in rejected:
                with self.subTest(operand=operand):
                    with self.assertRaises(SafeRmError):
                        validate_operand(operand, cwd)

    def test_rejects_git_metadata_at_any_depth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp).resolve()
            for operand in (".git", ".GIT", ".git/objects", "repo/.git", "repo/.git/objects"):
                with self.subTest(operand=operand):
                    with self.assertRaisesRegex(SafeRmError, "metadata"):
                        validate_operand(operand, cwd)

    def test_rejects_target_resolving_to_cwd_or_outside(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            cwd = root / "repo"
            outside = root / "outside"
            cwd.mkdir()
            outside.mkdir()
            (cwd / "self").symlink_to(cwd, target_is_directory=True)
            (cwd / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(SafeRmError, "invocation cwd"):
                validate_operand("self", cwd)
            with self.assertRaisesRegex(SafeRmError, "outside"):
                validate_operand("escape/child", cwd)

    def test_removes_multiple_trees_and_ignores_missing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp).resolve()
            (cwd / "build" / "nested").mkdir(parents=True)
            (cwd / "build" / "nested" / "file.txt").write_text("data\n", encoding="utf-8")
            (cwd / "dist").mkdir()
            remove_directories(["build", "dist", "missing"], cwd=cwd)
            self.assertFalse((cwd / "build").exists())
            self.assertFalse((cwd / "dist").exists())

    def test_internal_symlink_is_unlinked_without_traversing_outside(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            cwd = root / "repo"
            outside = root / "outside"
            (cwd / "build").mkdir(parents=True)
            outside.mkdir()
            protected = outside / "protected.txt"
            protected.write_text("keep\n", encoding="utf-8")
            (cwd / "build" / "outside-link").symlink_to(outside, target_is_directory=True)
            remove_directories(["build"], cwd=cwd)
            self.assertTrue(protected.is_file())
            self.assertFalse((cwd / "build").exists())

    def test_rejects_top_level_symlink_and_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp).resolve()
            real = cwd / "real"
            real.mkdir()
            (cwd / "link").symlink_to(real, target_is_directory=True)
            (cwd / "file.txt").write_text("keep\n", encoding="utf-8")
            for operand in ("link", "file.txt"):
                with self.subTest(operand=operand):
                    with self.assertRaisesRegex(SafeRmError, "not a real directory"):
                        remove_directories([operand], cwd=cwd)

    def test_validates_all_targets_before_deleting_anything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp).resolve()
            (cwd / "build").mkdir()
            with self.assertRaises(SafeRmError):
                remove_directories(["build", "../outside"], cwd=cwd)
            self.assertTrue((cwd / "build").is_dir())

    def test_rejects_overlapping_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp).resolve()
            (cwd / "build" / "nested").mkdir(parents=True)
            with self.assertRaisesRegex(SafeRmError, "overlapping"):
                remove_directories(["build", "build/nested"], cwd=cwd)

    def test_fails_closed_without_symlink_resistant_runtime(self) -> None:
        with mock.patch.object(shutil.rmtree, "avoids_symlink_attacks", False):
            with self.assertRaisesRegex(SafeRmError, "symlink-resistant"):
                remove_directories(["missing"], cwd=Path.cwd())

    def test_cli_requires_exact_fixed_flags_and_operands(self) -> None:
        for args in ([], ["-r", "--", "build"], ["-rf", "build"], ["-rf", "--"]):
            with self.subTest(args=args):
                code, _, stderr = _run_main(args)
                self.assertEqual(2, code)
                self.assertIn("usage:", stderr)

    def test_cli_version(self) -> None:
        code, stdout, stderr = _run_main(["--version"])
        self.assertEqual(0, code)
        self.assertEqual(f"{CONTROL_NAME} {CONTROL_VERSION}\n", stdout)
        self.assertEqual("", stderr)


def _run_main(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()


if __name__ == "__main__":
    unittest.main()
