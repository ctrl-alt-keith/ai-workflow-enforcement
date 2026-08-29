from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "Makefile"
README = ROOT / "README.md"
POLICY_DOC = ROOT / "docs" / "codex-safe-rm.md"
RULE_TEMPLATE = ROOT / "examples" / "codex-safe-rm.rules"
PATH_TOKEN = "__CODEX_SAFE_RM_ABSOLUTE_PATH__"


class CodexSafeRmOwnershipTests(unittest.TestCase):
    def test_enforcement_does_not_own_executable_or_installer_sources(self) -> None:
        self.assertFalse((ROOT / "enforcement" / "safe_rm.py").exists())
        self.assertFalse((ROOT / "enforcement" / "install_safe_rm.py").exists())
        implementation_references = []
        for path in (ROOT / "enforcement").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "codex-safe-rm" in text or "install_safe_rm" in text:
                implementation_references.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], implementation_references)

    def test_makefile_has_no_safe_rm_install_lifecycle(self) -> None:
        makefile = MAKEFILE.read_text(encoding="utf-8")
        self.assertNotRegex(makefile, r"(?m)^(?:install|verify-install|uninstall)\s*:")
        for variable in ("INSTALL_BIN", "INSTALL_FORCE_ARG", "INSTALL_DIRTY_ARG"):
            self.assertNotIn(variable, makefile)
        self.assertNotIn("codex-safe-rm", makefile)
        self.assertRegex(makefile, r"(?m)^check:\s*##")

    def test_consumer_guidance_routes_implementation_ownership_to_playbook(self) -> None:
        guidance = "\n".join(
            (README.read_text(encoding="utf-8"), POLICY_DOC.read_text(encoding="utf-8"))
        )
        self.assertIn("ai-workflow-playbook", guidance)
        self.assertIn("scripts/codex-safe-rm", guidance)
        self.assertIn("scripts/install-codex-safe-rm", guidance)
        self.assertNotIn("enforcement/safe_rm.py", guidance)
        self.assertNotIn("enforcement/install_safe_rm.py", guidance)
        for stale_instruction in (
            "make install",
            "make verify-install",
            "make uninstall",
            "ALLOW_DIRTY",
            "FORCE=1",
        ):
            self.assertNotIn(stale_instruction, guidance)

    def test_rule_template_preserves_prompt_gate_and_exact_delegated_prefix(self) -> None:
        rules = RULE_TEMPLATE.read_text(encoding="utf-8")
        self.assertRegex(
            rules,
            r'pattern=\["rm"\],\s*\n\s*decision="prompt"',
        )
        self.assertIn(
            f'pattern=["{PATH_TOKEN}", "-rf", "--"]',
            rules,
        )
        self.assertNotIn(f'pattern=["{PATH_TOKEN}"]', rules)

    def test_rule_template_requires_rendered_absolute_identity(self) -> None:
        rules = RULE_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("exact resolved absolute path", rules)
        self.assertIn("unresolved token is not a valid active rule", rules)
        self.assertNotIn("/Users/keith", rules)
        self.assertNotIn("~/.local", rules)
        self.assertNotIn("$HOME", rules)
        self.assertNotRegex(rules, re.compile(r'pattern=\["codex-safe-rm"'))


if __name__ == "__main__":
    unittest.main()
