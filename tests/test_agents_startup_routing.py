from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from enforcement.stewardship.agents_startup_routing import (
    APPROVED_BLOCK,
    AgentsStartupRoutingContext,
    AgentsStartupRoutingStrategy,
)


class AgentsStartupRoutingStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.strategy = AgentsStartupRoutingStrategy()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, content: str):
        (self.root / "AGENTS.md").write_text(content, encoding="utf-8")
        return self.strategy.run(
            AgentsStartupRoutingContext(repository_root=self.root)
        )

    def test_compliant_active_route_is_no_change(self) -> None:
        original = (
            "# Instructions\n\n"
            "Start with `ai-workflow-playbook/docs/start-here.md` before work.\n"
        )

        result = self._run(original)

        self.assertEqual("no_change", result.outcome)
        self.assertEqual(original, (self.root / "AGENTS.md").read_text(encoding="utf-8"))

    def test_wrapped_active_route_is_no_change(self) -> None:
        result = self._run(
            "# Instructions\n\n"
            "Read\n"
            "`ai-workflow-playbook/docs/start-here.md` before repository or\n"
            "software work.\n"
        )

        self.assertEqual("no_change", result.outcome)

    def test_absent_route_appends_exact_fixed_block_and_preserves_prefix(self) -> None:
        original = b"# Instructions\r\n\r\nRepository-specific guidance."
        agents = self.root / "AGENTS.md"
        agents.write_bytes(original)

        result = self.strategy.run(
            AgentsStartupRoutingContext(repository_root=self.root)
        )

        observed = agents.read_bytes()
        self.assertEqual("changed", result.outcome)
        self.assertEqual(("AGENTS.md",), result.changed_paths)
        self.assertEqual(original + APPROVED_BLOCK.encode("utf-8"), observed)
        self.assertTrue(observed.startswith(original))

    def test_fenced_example_is_ignored_and_fixed_block_is_appended(self) -> None:
        original = (
            "# Instructions\n\n"
            "```markdown\n"
            "## Shared Workflow Entry Point\n\n"
            "Start with `ai-workflow-playbook/docs/start-here.md`.\n"
            "```\n"
        )

        result = self._run(original)

        self.assertEqual("changed", result.outcome)
        self.assertEqual(
            original + APPROVED_BLOCK,
            (self.root / "AGENTS.md").read_text(encoding="utf-8"),
        )

    def test_negative_or_historical_mentions_are_blocked(self) -> None:
        examples = (
            "Do not start with `ai-workflow-playbook/docs/start-here.md`.\n",
            "Historically, read `ai-workflow-playbook/docs/start-here.md` first.\n",
            "For example, start from `ai-workflow-playbook/docs/start-here.md`.\n",
            "## Examples\n\nStart with `ai-workflow-playbook/docs/start-here.md`.\n",
        )
        for content in examples:
            with self.subTest(content=content):
                result = self._run(content)
                self.assertEqual("blocked", result.outcome)
                self.assertEqual(
                    content,
                    (self.root / "AGENTS.md").read_text(encoding="utf-8"),
                )

    def test_conflicting_reserved_heading_is_blocked(self) -> None:
        result = self._run(
            "# Instructions\n\n"
            "## Shared Workflow Entry Point\n\n"
            "Use repository-local guidance.\n"
        )

        self.assertEqual("blocked", result.outcome)

    def test_missing_agents_is_blocked(self) -> None:
        result = self.strategy.run(
            AgentsStartupRoutingContext(repository_root=self.root)
        )

        self.assertEqual("blocked", result.outcome)
        self.assertFalse((self.root / "AGENTS.md").exists())

    def test_symlinked_agents_is_blocked(self) -> None:
        target = self.root / "instructions.md"
        target.write_text("# Instructions\n", encoding="utf-8")
        (self.root / "AGENTS.md").symlink_to(target)

        result = self.strategy.run(
            AgentsStartupRoutingContext(repository_root=self.root)
        )

        self.assertEqual("blocked", result.outcome)
        self.assertEqual("# Instructions\n", target.read_text(encoding="utf-8"))

    def test_non_utf8_agents_is_blocked(self) -> None:
        original = b"# Instructions\n\xff\xfe"
        agents = self.root / "AGENTS.md"
        agents.write_bytes(original)

        result = self.strategy.run(
            AgentsStartupRoutingContext(repository_root=self.root)
        )

        self.assertEqual("blocked", result.outcome)
        self.assertEqual(original, agents.read_bytes())

    def test_mutation_does_not_change_any_other_file(self) -> None:
        other = self.root / "README.md"
        other.write_bytes(b"unchanged\r\n")

        result = self._run("# Instructions\n")

        self.assertEqual("changed", result.outcome)
        self.assertEqual(("AGENTS.md",), result.changed_paths)
        self.assertEqual(b"unchanged\r\n", other.read_bytes())


if __name__ == "__main__":
    unittest.main()
