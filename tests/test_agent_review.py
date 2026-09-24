"""Consequential boundaries for the local agent reviewer."""

import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest import mock

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
        (self.codex / "config.toml").write_text('model = "fixture"\n', encoding="utf-8")
        (self.claude / "settings.json").write_text("{}", encoding="utf-8")
        self.enrollment = {"agents": [
            {"id": "codex", "kind": "codex", "version": "fixture-1", "support": "official-doc-fixture",
             "launch_context": "fixture", "config_root": str(self.codex), "projects": [str(self.project)],
             "context_evidence": {"managed_and_system": "verified:fixture-checked", "profile_trust_and_invocation": "verified:fixture-checked",
                                  "nested_and_fallback_instructions": "verified:fixture-checked"}},
            {"id": "claude", "kind": "claude-code", "version": "fixture-1", "support": "official-doc-fixture",
             "launch_context": "fixture", "config_root": str(self.claude), "projects": [str(self.project)],
             "context_evidence": {"managed": "verified:fixture-checked", "ancestor_and_nested_instructions": "verified:fixture-checked",
                                  "environment_and_invocation": "verified:fixture-checked"}},
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
                                  "files": [{"path": str(instruction), "surface": "instruction", "ownership": "user"}]}]}
        first = review(enrollment)
        self.assertTrue(all(u["loading"] == "UNKNOWN" for u in first["units"]))
        self.assertEqual(first["previous"]["status"], "unavailable")
        self.assertEqual(review(enrollment, first)["previous"]["new"], [])
        instruction.write_text("# Guide\nA changed synthetic instruction.\n", encoding="utf-8")
        changed = review(enrollment, first)
        self.assertTrue(changed["previous"]["new"])
        self.assertTrue(changed["previous"]["resolved"])
        shifted = dict(enrollment)
        shifted["agents"] = [dict(enrollment["agents"][0],
                                  files=[*enrollment["agents"][0]["files"],
                                         {"path": str(self.root / "other.md"), "surface": "instruction",
                                          "ownership": "user"}])]
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
        (self.claude / "settings.json").unlink()
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
        self.assertCountEqual([u["disposition"] for u in relevant],
                              ["UNKNOWN", "PRUNE_CANDIDATE_REDUNDANT", "KEEP_GUARDRAIL"])
        self.assertNotIn("git", json.dumps(report))

    def test_claude_import_is_unknown_and_sensitive_field_is_excluded(self):
        (self.claude / "CLAUDE.md").write_text("Prelude\n# Rule\nSee @other.md\n", encoding="utf-8")
        (self.claude / "settings.json").write_text(
            '{"env":{"SECRET_TOKEN":"synthetic-secret"},"permissions":{"allow":["Read","Read"]}}',
            encoding="utf-8")
        report = review(self.enrollment)
        self.assertTrue(any(u["locator"].startswith("import-") and u["disposition"] == "UNKNOWN"
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
        with mock.patch("enforcement.agent_review_cli.refresh_provider_docs", return_value={"official-doc-fixture": "a" * 64}):
            self.assertEqual(main(["--enrollment", str(manifest), "--record", str(destination)]), 0)
            self.assertEqual(main(["--enrollment", str(manifest), "--record", str(destination)]), 2)
            self.assertEqual(main(["--enrollment", str(manifest), "--record", str(self.root / "bad.json")]), 2)

    def test_missing_root_blocks_and_missing_context_is_partial(self):
        self.enrollment["agents"][0]["config_root"] = str(self.root / "missing")
        with self.assertRaises(ValueError):
            review(self.enrollment)
        self.enrollment["agents"][0]["config_root"] = str(self.codex)
        del self.enrollment["agents"][0]["context_evidence"]
        report = review(self.enrollment)
        self.assertEqual(report["result"], "PARTIAL")
        self.assertTrue(any(c["status"] == "unverified_context" for c in report["coverage"]))

    def test_codex_shadowing_and_permissive_config(self):
        (self.codex / "AGENTS.override.md").write_text("# Override\n", encoding="utf-8")
        (self.codex / "AGENTS.md").write_text("# Shadowed\n", encoding="utf-8")
        (self.codex / "config.toml").write_text(
            'approval_policy = "never"\nsandbox_mode = "danger-full-access"\n', encoding="utf-8")
        report = review(self.enrollment)
        shadowed = [u for u in report["units"] if u["source"] == "user/AGENTS.md"]
        self.assertTrue(shadowed and all(u["disposition"] == "UNKNOWN" for u in shadowed))
        self.assertTrue(all("shadowed" in u["loading"] for u in shadowed))
        config = [u for u in report["units"] if u["source"] == "user/config.toml"]
        self.assertEqual([u["disposition"] for u in config], ["UNKNOWN", "UNKNOWN"])
        self.assertTrue(all("config-reference" in u["provider_reference"] for u in config))
        self.assertTrue(all("matching rules" not in u["precedence"] for u in config))

    def test_claude_project_memory_and_symlinked_rule_directory(self):
        (self.project / ".claude/CLAUDE.md").write_text("# Project memory\n", encoding="utf-8")
        target = self.root / "outside-rules"
        target.mkdir()
        (target / "hidden.md").write_text("# Do not read\n", encoding="utf-8")
        (self.claude / "rules").mkdir()
        (self.claude / "rules" / "linked").symlink_to(target, target_is_directory=True)
        report = review(self.enrollment)
        self.assertTrue(any(u["source"] == "project-0/.claude/CLAUDE.md" for u in report["units"]))
        self.assertEqual(report["result"], "PARTIAL")
        self.assertTrue(any(c["status"] == "symlink not followed" for c in report["coverage"]))

    def test_position_insertion_keeps_existing_fingerprints(self):
        instructions = self.claude / "CLAUDE.md"
        instructions.write_text("# First\nA\n# Second\nB\n", encoding="utf-8")
        first = review(self.enrollment)
        instructions.write_text("# New\nX\n# First\nA\n# Second\nB\n", encoding="utf-8")
        second = review(self.enrollment, first)
        self.assertEqual(len(second["previous"]["new"]), 1)
        self.assertEqual(second["previous"]["resolved"], [])

    def test_partial_reference_is_rejected_and_reference_is_identified(self):
        first = review(self.enrollment)
        partial = dict(first, result="PARTIAL")
        with self.assertRaises(ValueError):
            review(self.enrollment, partial)
        compared = review(self.enrollment, first)
        self.assertEqual(compared["previous"]["status"], "compared")
        self.assertEqual(len(compared["previous"]["reference_sha256"]), 64)

    def test_changed_provider_document_is_comparison_change(self):
        first = review(self.enrollment, provider_docs={"official-doc": "a" * 64})
        second = review(self.enrollment, first, provider_docs={"official-doc": "b" * 64})
        self.assertEqual(len(second["previous"]["new"]), 1)
        self.assertEqual(len(second["previous"]["resolved"]), 1)

    def test_codex_mcp_secrets_are_excluded(self):
        (self.codex / "config.toml").write_text(
            '[mcp_servers.private]\ncommand="synthetic-secret-command"\nheaders={Authorization="synthetic-secret"}\n',
            encoding="utf-8")
        report = review(self.enrollment)
        self.assertNotIn("synthetic-secret", json.dumps(report))
        self.assertTrue(any(u["reason"] == "sensitive field excluded from inspection"
                            for u in report["units"]))

    def test_rule_and_permission_insertion_keep_existing_fingerprints(self):
        rules = self.codex / "rules"
        rules.mkdir()
        rule_file = rules / "fixture.rules"
        rule_file.write_text('prefix_rule(pattern=["git"], decision="prompt")\n', encoding="utf-8")
        settings = self.claude / "settings.json"
        settings.write_text('{"permissions":{"allow":["Read","Grep"]}}', encoding="utf-8")
        first = review(self.enrollment)
        rule_file.write_text('prefix_rule(pattern=["ls"], decision="allow")\n'
                             'prefix_rule(pattern=["git"], decision="prompt")\n', encoding="utf-8")
        settings.write_text('{"permissions":{"allow":["Glob","Read","Grep"]}}', encoding="utf-8")
        second = review(self.enrollment, first)
        self.assertEqual(len(second["previous"]["new"]), 2)
        self.assertEqual(second["previous"]["resolved"], [])

    def test_baseline_drift_is_not_quiet(self):
        self.enrollment["record_root"] = str(self.root / "records")
        self.enrollment["record_retention"] = "fixture"
        first = review(self.enrollment, provider_docs={"official-doc-fixture": "a" * 64})
        (self.claude / "settings.json").write_text('{"permissions":{"allow":["Read"]}}', encoding="utf-8")
        current = review(self.enrollment, first, first, {"official-doc-fixture": "a" * 64})
        manifest = self.root / "enrollment.json"
        previous = self.root / "previous.json"
        baseline = self.root / "baseline.json"
        manifest.write_text(json.dumps(self.enrollment), encoding="utf-8")
        previous.write_text(json.dumps(current), encoding="utf-8")
        baseline.write_text(json.dumps(first), encoding="utf-8")
        output = io.StringIO()
        with mock.patch("enforcement.agent_review_cli.refresh_provider_docs", return_value={"official-doc-fixture": "a" * 64}):
            with redirect_stdout(output):
                code = main(["--enrollment", str(manifest), "--record",
                             str(self.root / "records/20260924T000000Z-baseline.json"),
                             "--previous", str(previous), "--baseline", str(baseline)])
        self.assertEqual(code, 1)
        self.assertIn('"baseline_new": 1', output.getvalue())

    def test_record_under_enrolled_project_is_rejected(self):
        records = self.project / "records"
        records.mkdir()
        self.enrollment["record_root"] = str(records)
        self.enrollment["record_retention"] = "fixture"
        manifest = self.root / "enrollment.json"
        manifest.write_text(json.dumps(self.enrollment), encoding="utf-8")
        destination = records / "20260924T000000Z-fixture.json"
        self.assertEqual(main(["--enrollment", str(manifest), "--record", str(destination)]), 2)
        self.assertFalse(destination.exists())

    def test_accepted_invariant_drift_is_partial(self):
        config = self.codex / "config.toml"
        config.write_text('sandbox_mode = "read-only"\n', encoding="utf-8")
        first = review(self.enrollment)
        selected = next(unit for unit in first["units"] if unit["source"] == "user/config.toml")
        self.enrollment["invariants"] = [{"agent": "codex", "source": "user/config.toml",
                                         "locator": selected["locator"],
                                         "expected_content_sha256": selected["content_sha256"],
                                         "evidence": "accepted fixture decision"}]
        self.assertEqual(review(self.enrollment)["invariants"][0]["status"], "present")
        config.write_text('sandbox_mode = "danger-full-access"\n', encoding="utf-8")
        changed = review(self.enrollment)
        self.assertEqual(changed["result"], "PARTIAL")
        self.assertEqual(changed["invariants"][0]["status"], "drift")

    def test_explicit_managed_context_is_inspected_as_context_only(self):
        managed = self.root / "managed-settings.json"
        managed.write_text('{"permissions":{"deny":["Bash(rm:*)"]}}', encoding="utf-8")
        self.enrollment["agents"][1]["context_files"] = [{"path": str(managed.resolve()),
                                                              "surface": "config", "ownership": "managed"}]
        report = review(self.enrollment)
        managed_units = [u for u in report["units"] if u["source"] == "context-file-0"]
        self.assertTrue(managed_units)
        self.assertTrue(all(u["ownership"] == "managed" for u in managed_units))
        self.assertTrue(all(u["disposition"] != "PRUNE_CANDIDATE_REDUNDANT" for u in managed_units))

    def test_shared_physical_instruction_retains_agent_context(self):
        shared = self.project / "CLAUDE.md"
        shared.write_text("# Shared\nfixture guidance\n", encoding="utf-8")
        generic = {"id": "other", "kind": "file-backed", "version": "fixture-1",
                   "support": "unverified", "launch_context": "generic", "root": str(self.project),
                   "files": [{"path": str(shared), "surface": "instruction", "ownership": "shared"}]}
        self.enrollment["agents"].append(generic)
        report = review(self.enrollment)
        sharing = [u for u in report["units"] if u["content_sha256"] ==
                   next(item["content_sha256"] for item in report["units"]
                        if item["source"] == "project-0/CLAUDE.md")]
        self.assertEqual({u["agent"] for u in sharing}, {"claude", "other"})
        self.assertEqual({u["loading"] for u in sharing},
                         {"ancestor instruction layer; conflicts need judgment", "UNKNOWN"})

    def test_missing_enrolled_files_and_empty_provider_roots_are_partial(self):
        self.enrollment["agents"][0]["context_files"] = [{"path": str(self.root.resolve() / "missing.md"),
                                                              "surface": "instruction", "ownership": "user"}]
        self.enrollment["agents"].append({"id": "other", "kind": "file-backed", "version": "fixture-1",
                                           "support": "unverified", "launch_context": "fixture",
                                           "root": str(self.root),
                                           "files": [{"path": str(self.root / "missing-file.md"),
                                                      "surface": "instruction", "ownership": "user"}]})
        report = review(self.enrollment)
        self.assertEqual(report["result"], "PARTIAL")
        self.assertEqual(sum(c["status"] == "missing enrolled file" for c in report["coverage"]), 2)
        (self.codex / "config.toml").unlink()
        (self.claude / "settings.json").unlink()
        report = review(self.enrollment)
        self.assertEqual(sum(c["status"] == "no user source inspected" for c in report["coverage"]), 2)

    def test_invariant_rejects_moved_or_shadowed_unit(self):
        settings = self.claude / "settings.json"
        settings.write_text('{"permissions":{"deny":["Bash(rm:*)"]}}', encoding="utf-8")
        unit = next(u for u in review(self.enrollment)["units"] if u["source"] == "user/settings.json")
        self.enrollment["invariants"] = [{"agent": "claude", "source": unit["source"],
                                         "locator": unit["locator"], "expected_content_sha256": unit["content_sha256"],
                                         "evidence": "fixture"}]
        self.assertEqual(review(self.enrollment)["invariants"][0]["status"], "present")
        settings.write_text('{"permissions":{"allow":["Bash(rm:*)"]}}', encoding="utf-8")
        self.assertEqual(review(self.enrollment)["invariants"][0]["status"], "drift")
        del self.enrollment["invariants"]
        memory = self.codex / "AGENTS.md"
        memory.write_text("# Fixture\nGuarded text\n", encoding="utf-8")
        unit = next(u for u in review(self.enrollment)["units"] if u["source"] == "user/AGENTS.md")
        self.enrollment["invariants"] = [{"agent": "codex", "source": unit["source"],
                                         "locator": unit["locator"], "expected_content_sha256": unit["content_sha256"],
                                         "evidence": "fixture"}]
        (self.codex / "AGENTS.override.md").write_text("# Override\n", encoding="utf-8")
        self.assertEqual(review(self.enrollment)["invariants"][0]["status"], "drift")

    def test_intermediate_symlink_not_followed(self):
        external = self.root / "external"
        external.mkdir()
        (external / "CLAUDE.md").write_text("synthetic secret content", encoding="utf-8")
        (self.project / ".claude").rmdir()
        (self.project / ".claude").symlink_to(external, target_is_directory=True)
        report = review(self.enrollment)
        self.assertEqual(report["result"], "PARTIAL")
        self.assertNotIn("synthetic secret content", json.dumps(report))
        self.assertTrue(any(c["status"] == "symlink not followed" for c in report["coverage"]))

    def test_invariant_addition_does_not_change_comparison_scope(self):
        first = review(self.enrollment)
        unit = next(u for u in first["units"] if u["source"] == "user/config.toml")
        self.enrollment["invariants"] = [{"agent": "codex", "source": unit["source"],
                                         "locator": unit["locator"], "expected_content_sha256": unit["content_sha256"],
                                         "evidence": "fixture"}]
        self.assertEqual(review(self.enrollment, first)["previous"]["status"], "compared")

    def test_invalid_context_attestation_stays_unknown(self):
        self.enrollment["agents"][0]["context_evidence"]["managed_and_system"] = "fixture-checked"
        report = review(self.enrollment)
        self.assertEqual(report["result"], "PARTIAL")
        self.assertTrue(any(c["source"] == "context/managed_and_system" and c["status"] == "unverified_context"
                            for c in report["coverage"]))

    def test_empty_user_root_is_partial_even_with_project_file(self):
        (self.project / "CLAUDE.md").write_text("# Project fixture\n", encoding="utf-8")
        (self.codex / "config.toml").unlink()
        (self.claude / "settings.json").unlink()
        report = review(self.enrollment)
        self.assertEqual(report["result"], "PARTIAL")
        self.assertTrue(any(c["agent"] == "claude" and c["source"] == "project-0/CLAUDE.md"
                            and c["status"] == "inspected" for c in report["coverage"]))
        self.assertEqual({c["agent"] for c in report["coverage"] if c["status"] == "no user source inspected"},
                         {"codex", "claude"})

    def test_context_identity_changes_comparison_scope(self):
        first = review(self.enrollment)
        self.enrollment["agents"][0]["context_evidence"]["managed_and_system"] = "verified:other-evidence"
        self.assertEqual(review(self.enrollment, first)["previous"]["status"], "scope_changed")
        self.enrollment["agents"][0]["launch_context"] = "another-fixture"
        self.assertEqual(review(self.enrollment, first)["previous"]["status"], "scope_changed")

    def test_file_backed_ownership_is_explicit(self):
        instruction = self.root / "generic.md"
        instruction.write_text("# Fixture\n", encoding="utf-8")
        generic = {"agents": [{"id": "other", "kind": "file-backed", "version": "fixture-1",
                               "support": "unverified", "launch_context": "fixture", "root": str(self.root),
                               "files": [{"path": str(instruction), "surface": "instruction"}]}]}
        with self.assertRaises(ValueError):
            review(generic)
        generic["agents"][0]["files"][0]["ownership"] = "shared"
        report = review(generic)
        self.assertEqual({u["ownership"] for u in report["units"]}, {"shared"})

    def test_nested_hyphenated_secret_fields_are_excluded(self):
        (self.claude / "settings.json").write_text(
            '{"hooks":[{"X-Api-Key":"synthetic-secret"}]}', encoding="utf-8")
        report = review(self.enrollment)
        self.assertNotIn("synthetic-secret", json.dumps(report))
        self.assertTrue(any(u["reason"] == "sensitive field excluded from inspection"
                            for u in report["units"]))

    def test_context_file_intermediate_symlink_is_not_followed(self):
        external = self.root.resolve() / "external-context"
        external.mkdir()
        (external / "settings.json").write_text('{"token":"synthetic-secret"}', encoding="utf-8")
        link = self.root.resolve() / "linked-context"
        link.symlink_to(external, target_is_directory=True)
        self.enrollment["agents"][0]["context_files"] = [{"path": str(link / "settings.json"),
                                                              "surface": "config", "ownership": "user"}]
        report = review(self.enrollment)
        self.assertEqual(report["result"], "PARTIAL")
        self.assertTrue(any(c["source"] == "context-file-0" and c["status"] == "symlink not followed"
                            for c in report["coverage"]))


if __name__ == "__main__":
    unittest.main()
