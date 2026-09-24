"""Consequential boundaries for the local agent reviewer."""

import json
from pathlib import Path
import tempfile
import unittest

from enforcement.agent_review import review, write_record
from enforcement.agent_review_cli import main


class AgentReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.codex = self.root / "codex"
        self.claude = self.root / "claude"
        self.project = self.root / "project"
        for path in (self.codex, self.claude, self.project / ".claude", self.root / "records"):
            path.mkdir(parents=True)
        self.enrollment = {"agents": [
            {"id": "codex", "kind": "codex", "version": "fixture-1", "support": "official-doc-fixture",
             "launch_context": "fixture", "config_root": str(self.codex), "projects": [str(self.project)]},
            {"id": "claude", "kind": "claude-code", "version": "fixture-1", "support": "official-doc-fixture",
             "launch_context": "fixture", "config_root": str(self.claude), "projects": [str(self.project)]},
        ]}

    def test_distinct_providers_and_shared_source_without_mutation(self):
        shared = self.project / "CLAUDE.md"
        shared.write_text("# Shared\nSynthetic instruction.\n", encoding="utf-8")
        codex_config = self.codex / "config.toml"
        codex_config.write_text('approval_policy = "on-request"\nmodel = "test"\n', encoding="utf-8")
        settings = self.claude / "settings.json"
        settings.write_text(json.dumps({"permissions": {"deny": ["Bash(rm:*)"], "allow": ["Bash(git:*)"]},
                                        "apiKey": "synthetic-secret"}), encoding="utf-8")
        before = {p: p.read_bytes() for p in (shared, codex_config, settings)}
        report = review(self.enrollment)
        self.assertEqual(report["result"], "OBSERVED")
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertTrue(any(u["product"] == "codex" and u["disposition"] == "KEEP_GUARDRAIL"
                            for u in report["units"]))
        self.assertTrue(any(u["product"] == "claude-code" and u["surface"] == "permission"
                            for u in report["units"]))
        serialized = json.dumps(report)
        self.assertNotIn("synthetic-secret", serialized)
        self.assertNotIn("Bash(git:*)", serialized)
        self.assertNotIn(str(self.root), serialized)

    def test_generic_unknown_loading_and_comparison(self):
        instruction = self.root / "personal.md"
        instruction.write_text("# Guide\nDo the good thing.\n", encoding="utf-8")
        enrollment = {"agents": [{"id": "other", "kind": "file-backed", "version": "fixture-1",
                                  "support": "unverified", "launch_context": "fixture", "root": str(self.root),
                                  "files": [{"path": str(instruction), "surface": "instruction"}]}]}
        first = review(enrollment)
        self.assertTrue(all(u["loading"] == "UNKNOWN" for u in first["units"]))
        self.assertEqual(first["previous"]["status"], "unavailable")
        self.assertEqual(review(enrollment, first)["previous"]["new"], [])
        instruction.write_text("# Guide\nA changed synthetic instruction.\n", encoding="utf-8")
        changed = review(enrollment, first)
        self.assertTrue(changed["previous"]["new"])
        self.assertTrue(changed["previous"]["resolved"])
        shifted = dict(enrollment)
        shifted["record_retention"] = "changed local contract"
        self.assertEqual(review(shifted, first)["previous"]["status"], "scope_changed")

    def test_parse_failure_is_partial_and_record_is_exclusive(self):
        (self.claude / "settings.json").write_text("{invalid", encoding="utf-8")
        report = review(self.enrollment)
        self.assertEqual(report["result"], "PARTIAL")
        self.assertTrue(any(u["disposition"] == "UNKNOWN" and u["reason"] == "parse failure"
                            for u in report["units"]))
        destination = self.root / "records" / "20260924T000000Z-fixture.json"
        write_record(report, destination)
        with self.assertRaises(FileExistsError):
            write_record(report, destination)

    def test_symlink_is_not_followed(self):
        secret = self.root / "secret.json"
        secret.write_text('{"token": "do-not-read"}', encoding="utf-8")
        (self.claude / "settings.json").symlink_to(secret)
        report = review(self.enrollment)
        self.assertEqual(report["result"], "PARTIAL")
        self.assertNotIn("do-not-read", json.dumps(report))
        self.assertTrue(any(c["status"] == "symlink not followed" for c in report["coverage"]))

    def test_codex_rule_guards_and_duplicates_are_distinct(self):
        rules = self.codex / "rules"
        rules.mkdir()
        (rules / "fixture.rules").write_text(
            'prefix_rule(pattern=["git", "status"], decision="allow")\n'
            'prefix_rule(pattern=["git", "status"], decision="allow")\n'
            'prefix_rule(pattern=["rm"], decision="forbidden")\n', encoding="utf-8")
        report = review(self.enrollment)
        relevant = [u for u in report["units"] if u["source"].startswith("user/rules/")]
        self.assertEqual([u["disposition"] for u in relevant],
                         ["UNKNOWN", "PRUNE_CANDIDATE_REDUNDANT", "KEEP_GUARDRAIL"])
        self.assertNotIn("git", json.dumps(report))

    def test_claude_import_is_unknown_and_sensitive_field_is_excluded(self):
        (self.claude / "CLAUDE.md").write_text("Prelude\n# Rule\nSee @other.md\n", encoding="utf-8")
        (self.claude / "settings.json").write_text(
            '{"env":{"SECRET_TOKEN":"synthetic-secret"},"permissions":{"allow":["Read","Read"]}}',
            encoding="utf-8")
        report = review(self.enrollment)
        self.assertTrue(any(u["locator"] == "import-1" and u["disposition"] == "UNKNOWN"
                            for u in report["units"]))
        self.assertTrue(any(u["reason"] == "sensitive field excluded from inspection"
                            for u in report["units"]))
        self.assertTrue(any(u["disposition"] == "PRUNE_CANDIDATE_REDUNDANT"
                            for u in report["units"]))
        self.assertNotIn("synthetic-secret", json.dumps(report))

    def test_cli_requires_declared_destination_and_exclusive_record(self):
        self.enrollment["record_root"] = str(self.root / "records")
        self.enrollment["record_retention"] = "fixture retention"
        manifest = self.root / "enrollment.json"
        manifest.write_text(json.dumps(self.enrollment), encoding="utf-8")
        destination = self.root / "records" / "20260924T000000Z-fixture.json"
        self.assertEqual(main(["--enrollment", str(manifest), "--record", str(destination)]), 0)
        self.assertEqual(main(["--enrollment", str(manifest), "--record", str(destination)]), 2)
        self.assertEqual(main(["--enrollment", str(manifest), "--record", str(self.root / "bad.json")]), 2)


if __name__ == "__main__":
    unittest.main()
