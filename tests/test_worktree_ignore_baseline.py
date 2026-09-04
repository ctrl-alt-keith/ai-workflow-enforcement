from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from enforcement.stewardship.worktree_ignore_baseline import (
    REQUIRED_RULE,
    WorktreeIgnoreBaselineContext,
    WorktreeIgnoreBaselineStrategy,
)


class WorktreeIgnoreBaselineStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.gitignore = self.root / ".gitignore"
        self.strategy = WorktreeIgnoreBaselineStrategy()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, content: bytes):
        self.gitignore.write_bytes(content)
        return self.strategy.run(
            WorktreeIgnoreBaselineContext(repository_root=self.root)
        )

    def test_exact_lf_rule_is_no_change(self) -> None:
        original = b"*.pyc\n.worktrees/\n"
        result = self._run(original)
        self.assertEqual("no_change", result.outcome)
        self.assertEqual(original, self.gitignore.read_bytes())

    def test_absent_token_with_trailing_lf_appends_exact_suffix(self) -> None:
        original = b"*.pyc\n"
        result = self._run(original)
        self.assertEqual("changed", result.outcome)
        self.assertEqual((".gitignore",), result.changed_paths)
        self.assertEqual(original + b".worktrees/\n", self.gitignore.read_bytes())

    def test_ambiguous_worktrees_forms_are_blocked(self) -> None:
        examples = (
            b"# .worktrees/\n",
            b"!.worktrees/\n",
            b".worktrees\n",
            b"/.worktrees/\n",
            b".worktrees/**\n",
            b"*.worktrees\n",
            b"\\.worktrees/\n",
            b" .worktrees/\n",
            b".worktrees/ \n",
            b".worktrees/\n/.worktrees/\n",
            b".worktrees/\n# .worktrees/\n",
            ".worktrees/\u2028*.pyc\n".encode("utf-8"),
        )
        for original in examples:
            with self.subTest(original=original):
                result = self._run(original)
                self.assertEqual("blocked", result.outcome)
                self.assertEqual(original, self.gitignore.read_bytes())

    def test_mixed_newline_convention_is_blocked_before_mutation(self) -> None:
        original = b"*.pyc\r\n.cache\n"
        result = self._run(original)
        self.assertEqual("blocked", result.outcome)
        self.assertEqual(original, self.gitignore.read_bytes())

    def test_missing_file_is_blocked_and_never_created(self) -> None:
        result = self.strategy.run(
            WorktreeIgnoreBaselineContext(repository_root=self.root)
        )
        self.assertEqual("blocked", result.outcome)
        self.assertFalse(self.gitignore.exists())

    def test_symlink_is_blocked_without_changing_target(self) -> None:
        target = self.root / "ignore-target"
        target.write_bytes(b"*.pyc\n")
        self.gitignore.symlink_to(target)
        result = self.strategy.run(
            WorktreeIgnoreBaselineContext(repository_root=self.root)
        )
        self.assertEqual("blocked", result.outcome)
        self.assertEqual(b"*.pyc\n", target.read_bytes())

    def test_invalid_utf8_is_blocked(self) -> None:
        original = b"*.pyc\n\xff"
        result = self._run(original)
        self.assertEqual("blocked", result.outcome)
        self.assertEqual(original, self.gitignore.read_bytes())

    def test_post_write_verification_failure_is_blocked(self) -> None:
        original = b"*.pyc\n"
        self.gitignore.write_bytes(original)
        with mock.patch.object(
            Path, "read_bytes", side_effect=(original, b"unexpected")
        ):
            result = self.strategy.run(
                WorktreeIgnoreBaselineContext(repository_root=self.root)
            )
        self.assertEqual("blocked", result.outcome)

    def test_only_gitignore_is_reported_and_other_files_are_unchanged(self) -> None:
        other = self.root / "README.md"
        other.write_bytes(b"unchanged\r\n")
        result = self._run(b"*.pyc\n")
        self.assertEqual("changed", result.outcome)
        self.assertEqual((".gitignore",), result.changed_paths)
        self.assertEqual(b"unchanged\r\n", other.read_bytes())
        self.assertIn(REQUIRED_RULE, self.gitignore.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
