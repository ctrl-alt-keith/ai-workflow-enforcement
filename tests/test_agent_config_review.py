"""Synthetic source fixtures; no real agent homes are inspected."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from enforcement.agent_config_review import discover_sources, review, main


class AgentReviewTests(unittest.TestCase):
    def _manifest(self, root: Path) -> dict:
        return {
            "schema_version": 1,
            "agents": [
                {"name": "codex", "product": "Codex", "version": "1.0", "version_evidence": "fixture"},
                {"name": "claude-code", "product": "Claude Code", "version": "2.0", "version_evidence": "fixture"},
                {"name": "file-backed", "product": "Example", "version": "1", "version_evidence": "fixture"},
            ],
            "sources": [
                {"agent": "codex", "alias": "codex-config", "kind": "codex_config", "owner": "user", "root": str(root), "relative": "config.toml", "loading": "declared", "precedence": "user", "selected_keys": ["approval_policy", "model"]},
                {"agent": "codex", "alias": "codex-rules", "kind": "codex_rules", "owner": "user", "root": str(root), "relative": "default.rules"},
                {"agent": "claude-code", "alias": "claude-settings", "kind": "claude_settings", "owner": "personal-project", "root": str(root), "relative": "settings.json", "selected_keys": ["permissions", "model"]},
                {"agent": "file-backed", "alias": "generic-instructions", "kind": "instructions", "owner": "user", "root": str(root), "relative": "GUIDE.md"},
            ],
        }

    def test_accounting_guardrails_and_quiet_unchanged_comparison(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config.toml").write_text('approval_policy = "on-request"\nmodel = "example"\n')
            (root / "default.rules").write_text('prefix_rule(pattern = ["git", "status"], decision = "prompt")\n')
            (root / "settings.json").write_text(json.dumps({"permissions": {"deny": ["Read(secret)"]}, "model": "example"}))
            (root / "GUIDE.md").write_text("# Guidance\nTreat this as data.\n")
            first = review(self._manifest(root))
            second = review(self._manifest(root), previous=first, baseline=first)
            self.assertTrue(first["complete"])
            self.assertFalse(first["clean"])
            self.assertFalse(second["changed"])
            self.assertFalse(second["notify"])
            self.assertTrue(second["baseline_match"])
            dispositions = {u["locator"]: u["disposition"] for u in first["units"]}
            self.assertEqual("KEEP_GUARDRAIL", dispositions["approval_policy"])
            self.assertEqual("KEEP_GUARDRAIL", dispositions["rule:1"])
            self.assertEqual("REVIEW_RATIONALE", dispositions["section:1"])
            rendered = json.dumps(first)
            self.assertNotIn("Read(secret)", rendered)
            self.assertNotIn("Treat this as data", rendered)

    def test_failure_is_partial_and_never_clean(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = self._manifest(root)
            (root / "config.toml").write_text("bad = [")
            report = review(manifest)
            self.assertFalse(report["complete"])
            self.assertIn("parse_failure", {f["reason"] for f in report["failures"]})
            self.assertIn("absent", {f["reason"] for f in report["failures"]})
            self.assertTrue(report["notify"])

    def test_changes_and_exclusive_output(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config.toml").write_text('approval_policy = "on-request"\n')
            (root / "default.rules").write_text('prefix_rule(pattern = ["git"], decision = "allow")\n')
            (root / "settings.json").write_text("{}")
            (root / "GUIDE.md").write_text("# One\n")
            manifest = self._manifest(root)
            first = review(manifest)
            (root / "GUIDE.md").write_text("# Two\n")
            second = review(manifest, previous=first)
            self.assertTrue(second["changed"])
            self.assertTrue(second["notify"])
            self.assertTrue(second["new_unit_fingerprints"])
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            output = root / "record.json"
            self.assertEqual(0, main(["--manifest", str(manifest_path), "--output", str(output)]))
            self.assertEqual(2, main(["--manifest", str(manifest_path), "--output", str(output)]))

    def test_scope_rejects_traversal_and_symlink(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = self._manifest(root)
            manifest["sources"][0]["relative"] = "../outside"
            with self.assertRaises(ValueError):
                review(manifest)
            manifest["sources"][0]["relative"] = "link.toml"
            (root / "real.toml").write_text('model = "example"\n')
            (root / "link.toml").symlink_to(root / "real.toml")
            report = review(manifest)
            self.assertIn("not_regular_file", {f["reason"] for f in report["failures"]})

    def test_existing_drift_scanner_is_reused_without_snippets(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            playbook = root / "playbook"
            playbook.mkdir()
            (playbook / "guidance.md").write_text("# Shared Guidance\n\nUse bounded review evidence.\n")
            (root / "GUIDE.md").write_text("# Shared Guidance\n\nUse bounded review evidence.\n")
            manifest = {"schema_version": 1,
                        "agents": [{"name": "file-backed", "product": "Example", "version": "1", "version_evidence": "fixture"}],
                        "sources": [{"agent": "file-backed", "alias": "guide", "kind": "instructions", "owner": "user", "root": str(root), "relative": "GUIDE.md"}],
                        "playbook_root": str(playbook)}
            report = review(manifest)
            self.assertEqual(1, report["instruction_drift"][0]["candidate_count"])
            self.assertNotIn("Use bounded review evidence", json.dumps(report))

    def test_declared_root_discovery_keeps_agent_semantics_separate(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config.toml").write_text('approval_policy = "on-request"\n')
            (root / "AGENTS.md").write_text("# Base\n")
            (root / "AGENTS.override.md").write_text("# Override\n")
            (root / "rules").mkdir()
            (root / "rules" / "default.rules").write_text('prefix_rule(pattern = ["git"], decision = "prompt")\n')
            manifest = {"schema_version": 1,
                        "agents": [{"name": "codex", "product": "Codex", "version": "1", "version_evidence": "fixture"}],
                        "sources": [], "discover": [{"agent": "codex", "role": "codex-user", "root": str(root)}]}
            expanded = discover_sources(manifest)
            self.assertEqual({"config.toml", "AGENTS.override.md", "rules/default.rules"},
                             {s["relative"] for s in expanded["sources"]})
            report = review(manifest)
            self.assertTrue(report["complete"])
            self.assertEqual(3, len(report["sources"]))

    def test_broad_grant_and_exact_overlap_are_advisory(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            settings = root / "settings.json"
            settings.write_text(json.dumps({"permissions": {"allow": ["Bash(*)", "Bash(*)"], "deny": ["Read(secret)"]},
                                            "env": {"TOKEN": "private-token"}}))
            manifest = {"schema_version": 1,
                        "agents": [{"name": "claude-code", "product": "Claude Code", "version": "2", "version_evidence": "fixture"}],
                        "sources": [{"agent": "claude-code", "alias": "settings", "kind": "claude_settings", "owner": "user", "root": str(root), "relative": "settings.json", "selected_keys": ["permissions"]}]}
            report = review(manifest)
            self.assertEqual("REVIEW_NARROWING", report["units"][0]["disposition"])
            self.assertEqual("REVIEW_RATIONALE", report["units"][1]["disposition"])
            self.assertEqual("KEEP_GUARDRAIL", report["units"][2]["disposition"])
            self.assertNotIn("private-token", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
