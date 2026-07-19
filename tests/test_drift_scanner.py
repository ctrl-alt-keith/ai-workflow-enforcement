from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

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

    def test_agents_alignment_does_not_require_interaction_mode_vocabulary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            playbook_root = workspace / "ai-workflow-playbook"
            playbook = playbook_root / "docs"
            repo = workspace / "demo"
            notes = root / "notes"
            notes.mkdir()
            playbook.mkdir(parents=True)
            repo.mkdir()
            (notes / "note.md").write_text("temporary note", encoding="utf-8")
            (playbook / "repo-readiness.md").write_text(
                "Before acting determine the interaction mode. Use implementation mode, "
                "review/audit mode, or orchestration mode. For ordinary repository operations, "
                "use direct git, gh, make, python, and repo-local scripts. Before choosing "
                "wrapper shells such as zsh -lc, bash -lc, or sh -c, check whether a direct "
                "form exists. Prefer native argv execution and disable implicit shell or login "
                "shell behavior for git and gh where supported.",
                encoding="utf-8",
            )
            (repo / "AGENTS.md").write_text(
                "# AGENTS.md\n\n"
                "Follow ai-workflow-playbook/docs/start-here.md for reusable workflow guidance.\n",
                encoding="utf-8",
            )

            result = scan(
                ScannerConfig(
                    notes_roots=(notes,),
                    playbook_roots=(playbook,),
                    workspace_root=workspace,
                    organization_repositories=("ctrl-alt-keith/demo",),
                )
            )

        self.assertEqual(0, len(result.advisory_findings))

    def test_agents_alignment_reports_missing_canonical_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            playbook_root = workspace / "ai-workflow-playbook"
            playbook = playbook_root / "docs"
            repo = workspace / "demo"
            notes = root / "notes"
            notes.mkdir()
            playbook.mkdir(parents=True)
            repo.mkdir()
            (notes / "note.md").write_text("temporary note", encoding="utf-8")
            (playbook / "start-here.md").write_text("ai-workflow-playbook guidance", encoding="utf-8")
            (repo / "AGENTS.md").write_text(
                "# AGENTS.md\n\nRepository-specific execution guidance.\n",
                encoding="utf-8",
            )

            result = scan(
                ScannerConfig(
                    notes_roots=(notes,),
                    playbook_roots=(playbook,),
                    workspace_root=workspace,
                    organization_repositories=("ctrl-alt-keith/demo",),
                )
            )

        kinds = {finding.kind for finding in result.advisory_findings}
        self.assertEqual({"agents_missing_canonical_playbook_reference"}, kinds)

    def test_agents_alignment_does_not_require_command_form_vocabulary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            playbook_root = workspace / "ai-workflow-playbook"
            playbook = playbook_root / "docs"
            repo = workspace / "demo"
            notes = root / "notes"
            notes.mkdir()
            playbook.mkdir(parents=True)
            repo.mkdir()
            (notes / "note.md").write_text("temporary note", encoding="utf-8")
            (playbook / "start-here.md").write_text("ai-workflow-playbook guidance", encoding="utf-8")
            (repo / "AGENTS.md").write_text(
                "# AGENTS.md\n\n"
                "This repo uses ai-workflow-playbook as canonical guidance.\n"
                "Select the interaction mode: implementation, review/audit, or orchestration.\n"
                "Run the repository validation entrypoint before opening a pull request.\n",
                encoding="utf-8",
            )

            result = scan(
                ScannerConfig(
                    notes_roots=(notes,),
                    playbook_roots=(playbook,),
                    workspace_root=workspace,
                    organization_repositories=("ctrl-alt-keith/demo",),
                )
            )

        self.assertEqual(0, len(result.advisory_findings))

    def test_agents_alignment_flags_large_canonical_duplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            playbook_root = workspace / "ai-workflow-playbook"
            playbook = playbook_root / "docs"
            repo = workspace / "demo"
            notes = root / "notes"
            notes.mkdir()
            playbook.mkdir(parents=True)
            repo.mkdir()
            repeated_policy = (
                "Before acting determine the interaction mode and preserve implementation "
                "review audit orchestration boundaries with direct command execution for "
                "git gh make python and repo-local scripts before choosing wrapper shells. "
            ) * 50
            (notes / "note.md").write_text("temporary note", encoding="utf-8")
            (playbook / "repo-readiness.md").write_text(repeated_policy, encoding="utf-8")
            (repo / "AGENTS.md").write_text(
                "# AGENTS.md\n\n"
                "This repo uses ai-workflow-playbook as canonical guidance.\n"
                "Select the interaction mode: implementation, review/audit, or orchestration.\n"
                "Use direct command execution for git, gh, make, python, and repo-local scripts.\n"
                "Before using wrapper shells such as zsh -lc, bash -lc, or sh -c, check whether direct form exists.\n\n"
                f"{repeated_policy}",
                encoding="utf-8",
            )

            result = scan(
                ScannerConfig(
                    notes_roots=(notes,),
                    playbook_roots=(playbook,),
                    workspace_root=workspace,
                    organization_repositories=("ctrl-alt-keith/demo",),
                )
            )

        kinds = {finding.kind for finding in result.advisory_findings}
        self.assertIn("agents_large_canonical_duplication", kinds)

    def test_noncanonical_authority_and_stronger_rules_are_advisory_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "runtime.md").write_text(
                "This runtime artifact is the source of truth.\n"
                "Agents must provide complete self-contained output.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        kinds = [finding.kind for finding in result.advisory_findings]
        self.assertIn("noncanonical_authority_language", kinds)
        self.assertIn("staged_rule_stronger_than_playbook", kinds)

    def test_drop_in_artifact_rules_are_advisory_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "runtime.md").write_text(
                "Agents must provide full drop-in prompts that are copy/paste-safe.\n"
                "Agents must not provide delta-only targeted edits.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        staged_findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "staged_rule_stronger_than_playbook"
        ]
        self.assertEqual(2, len(staged_findings))
        self.assertTrue(any("complete output" in finding.reasons[0] for finding in staged_findings))
        self.assertTrue(any("partial prompt prohibitions" in finding.reasons[0] for finding in staged_findings))

    def test_sandbox_writable_roots_exhaustive_claim_is_advisory_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "codex.md").write_text(
                "The sandbox_workspace_write.writable_roots list is the only writable root set.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "sandbox_writable_roots_exhaustive_claim"
        ]
        self.assertEqual(1, len(findings))
        self.assertEqual(1, findings[0].line)

    def test_sandbox_writable_roots_complete_effective_claim_is_advisory_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "codex.md").write_text(
                "`writable_roots` is the complete effective writable root set.\n"
                "`writable_roots` is the only effective writable root set.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "sandbox_writable_roots_exhaustive_claim"
        ]
        self.assertEqual(
            [
                "`writable_roots` is the complete effective writable root set.",
                "`writable_roots` is the only effective writable root set.",
            ],
            [finding.snippet for finding in findings],
        )

    def test_sandbox_writable_roots_corrective_wording_is_not_advisory_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "codex.md").write_text(
                "`writable_roots` is not the full effective writable root set.\n"
                "Effective writable roots may also include implicit temp roots.\n"
                "Effective writable roots can also include the current project root.\n"
                "Do not assume `writable_roots` is exhaustive.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "sandbox_writable_roots_exhaustive_claim"
        ]
        self.assertEqual(0, len(findings))

    def test_sandbox_writable_roots_corrective_context_is_not_advisory_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "codex.md").write_text(
                "In workspace-write mode, do not assume\n"
                "`[sandbox_workspace_write].writable_roots` is the complete effective writable\n"
                "root list. Codex may also have implicit writable roots.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "sandbox_writable_roots_exhaustive_claim"
        ]
        self.assertEqual(0, len(findings))

    def test_sandbox_writable_roots_detector_description_is_not_advisory_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "scanner.md").write_text(
                "The scanner reports Codex sandbox docs/examples that imply "
                "`writable_roots` is the exhaustive effective writable root set.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "sandbox_writable_roots_exhaustive_claim"
        ]
        self.assertEqual(0, len(findings))

    def test_authority_language_skips_noncanonical_disclaimers_and_external_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "staging.md").write_text(
                "This is not a canonical source for reusable guidance.\n"
                "Tracked examples here are not the authoritative source for live runtime state.\n"
                "## Source of truth\n\n"
                "GitHub issues and PRs remain authoritative for implementation work.\n"
                "Public API behavior claims require authoritative official documentation.\n"
                "A prompt can ask for canonical source use.\n"
                "Runtime artifacts can appear authoritative when copied widely.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        authority_findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "noncanonical_authority_language"
        ]
        self.assertEqual(0, len(authority_findings))

    def test_authority_language_flags_current_line_claim_next_to_disclaimer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "staging.md").write_text(
                "This is not a canonical source for reusable guidance.\n"
                "This runtime prompt is canonical.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        authority_findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "noncanonical_authority_language"
        ]
        self.assertEqual(1, len(authority_findings))
        self.assertEqual(2, authority_findings[0].line)
        self.assertEqual("This runtime prompt is canonical.", authority_findings[0].snippet)

    def test_authority_language_flags_indirect_authority_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "staging.md").write_text(
                "This runtime note provides authoritative guidance.\n"
                "This document governs the workflow.\n"
                "This document defines the workflow.\n"
                "Treat this as the source of truth.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        authority_findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "noncanonical_authority_language"
        ]
        self.assertEqual(
            [
                "This runtime note provides authoritative guidance.",
                "This document governs the workflow.",
                "This document defines the workflow.",
                "Treat this as the source of truth.",
            ],
            [finding.snippet for finding in authority_findings],
        )

    def test_authority_language_flags_playbook_authority_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "staging.md").write_text(
                "This playbook is canonical.\n"
                "This playbook provides authoritative guidance.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        authority_findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "noncanonical_authority_language"
        ]
        self.assertEqual(
            [
                "This playbook is canonical.",
                "This playbook provides authoritative guidance.",
            ],
            [finding.snippet for finding in authority_findings],
        )

    def test_authority_language_flags_layered_disclaimer_and_claim_on_same_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "staging.md").write_text(
                "This is not a canonical source; however, this runtime prompt is definitive instructions.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        authority_findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "noncanonical_authority_language"
        ]
        self.assertEqual(1, len(authority_findings))
        self.assertEqual(1, authority_findings[0].line)

    def test_authority_language_flags_unfamiliar_authoritative_phrasing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "staging.md").write_text(
                "Use this as the canonical workflow.\n"
                "This file is the primary operational reference.\n"
                "This note is the official workflow definition.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        authority_findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "noncanonical_authority_language"
        ]
        self.assertEqual(
            [
                "Use this as the canonical workflow.",
                "This file is the primary operational reference.",
                "This note is the official workflow definition.",
            ],
            [finding.snippet for finding in authority_findings],
        )

    def test_authority_language_keeps_ambiguous_context_unflagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "staging.md").write_text(
                "This staging note is not a canonical source for reusable guidance.\n"
                "It mentions canonical wording for comparison.\n"
                "The phrase source of truth appears only as language to avoid.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertNotIn(
            "noncanonical_authority_language",
            {finding.kind for finding in result.advisory_findings},
        )

    def test_authority_language_preserves_external_source_false_positive_protection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "staging.md").write_text(
                "Official documentation remains authoritative guidance for public API behavior.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertNotIn(
            "noncanonical_authority_language",
            {finding.kind for finding in result.advisory_findings},
        )

    def test_authority_language_skips_source_first_evidence_discussion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "staging.md").write_text(
                "The assistant treated summaries as sufficiently authoritative.\n"
                "Nearby derived state can outrank live retrievable\n"
                "authoritative state. Stale repo summaries can become shadow authority surfaces.\n"
                "- authoritative retrievable state: live repository, PR, issue, branch, CI, or source artifact state.\n"
                "Stale uploaded operational summaries can become shadow authoritative surfaces.\n"
                "Use live retrieval when authoritative sources are available.\n"
                "Older notes were absorbed into canonical guidance or narrowed to provenance.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertNotIn(
            "noncanonical_authority_language",
            {finding.kind for finding in result.advisory_findings},
        )

    def test_authority_language_skips_named_playbook_reference_explanations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "staging.md").write_text(
                "`ai-workflow-playbook` is the canonical reusable workflow-policy source.\n"
                "`ctrl-alt-keith/ai-workflow-playbook`: GitHub playbook repository and canonical source.\n"
                "Use `ai-workflow-playbook/docs/context-refresh.md` as the canonical reference.\n"
                "Canonical guidance lives in `ai-workflow-playbook` after repeated evidence.\n"
                "A separate Playbook Update task updates canonical guidance if warranted.\n"
                "Audit drift between staging notes and canonical guidance.\n"
                "Review role-mode confusion, canonical-source evidence, and validation records.\n"
                "Reference canonical guidance and repo-local execution rules.\n"
                "Audit findings distinguish canonical guidance ownership from local hygiene.\n"
                "Expectations are not yet fully promoted into\n"
                "canonical guidance.\n"
                "- What would become canonical if promoted: a narrow review caution.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertNotIn(
            "noncanonical_authority_language",
            {finding.kind for finding in result.advisory_findings},
        )

    def test_authority_language_flags_playbook_replacement_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "staging.md").write_text(
                "This runtime document replaces ai-workflow-playbook as the canonical source.\n"
                "Treat this note, not ai-workflow-playbook, as the canonical workflow reference.\n"
                "This prompt supersedes ai-workflow-playbook as canonical guidance.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        authority_findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "noncanonical_authority_language"
        ]
        self.assertEqual(
            [
                "This runtime document replaces ai-workflow-playbook as the canonical source.",
                "Treat this note, not ai-workflow-playbook, as the canonical workflow reference.",
                "This prompt supersedes ai-workflow-playbook as canonical guidance.",
            ],
            [finding.snippet for finding in authority_findings],
        )

    def test_shell_wrapper_examples_skip_negative_examples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "prompt.md").write_text(
                "Run `bash -lc 'make check'` before review.\n"
                "Incorrect: `zsh -lc 'git status'`.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Use direct command form.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        wrapper_findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "ordinary_repo_command_shell_wrapper_example"
        ]
        self.assertEqual(1, len(wrapper_findings))
        self.assertIn("make check", wrapper_findings[0].reasons[0])

    def test_shell_wrapper_prompt_examples_flag_ordinary_repo_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "prompt.md").write_text(
                "Prompt: run `zsh -lc 'git status'` and then `bash -lc 'make check'` before review.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Use direct command form.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        wrapper_reasons = [
            finding.reasons[0] for finding in result.advisory_findings
            if finding.kind == "ordinary_repo_command_shell_wrapper_example"
        ]
        self.assertEqual(
            [
                "wrapper shell example contains ordinary repo command: git status",
                "wrapper shell example contains ordinary repo command: make check",
            ],
            wrapper_reasons,
        )

    def test_shell_wrapper_negative_example_is_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "guidance.md").write_text(
                "Incorrect: `zsh -lc 'git status'`. Use `git status` instead.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Use direct command form.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertNotIn(
            "ordinary_repo_command_shell_wrapper_example",
            {finding.kind for finding in result.advisory_findings},
        )

    def test_shell_wrapper_real_shell_syntax_is_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "example.md").write_text(
                "Use `bash -lc 'make check > scratch/check.log'` when redirecting output.\n"
                "Use `sh -c 'git status | tee scratch/status.txt'` when piping output.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Use direct command form.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertNotIn(
            "ordinary_repo_command_shell_wrapper_example",
            {finding.kind for finding in result.advisory_findings},
        )

    def test_shell_wrapper_explanatory_drift_discussion_is_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "evidence.md").write_text(
                "This note discusses shell-wrapper drift evidence, not guidance.\n"
                "Observed example: `zsh -lc 'git status'` appeared in a transcript.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Use direct command form.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertNotIn(
            "ordinary_repo_command_shell_wrapper_example",
            {finding.kind for finding in result.advisory_findings},
        )

    def test_shell_wrapper_runtime_policy_evidence_is_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            policy = notes / "runtime-artifacts" / "codex-local-policy"
            policy.mkdir(parents=True)
            playbook.mkdir()
            (policy / "README.md").write_text(
                "Shell-wrapped validation examples:\n\n"
                "- `/bin/zsh -lc 'git worktree remove .worktrees/example'` -> no rule-layer\n"
                "  allow; hook does not auto-allow the raw wrapper command\n\n"
                "Copyable validation:\n\n"
                "```sh\n"
                "codex execpolicy check --pretty --rules custom.rules -- /bin/bash -lc 'git worktree remove .worktrees/example'\n"
                "```\n",
                encoding="utf-8",
            )
            (policy / "runtime-enforcement-matrix.md").write_text(
                "| Surface | Requested command | Hook payload command | Static policy | Runtime behavior |\n"
                "| --- | --- | --- | --- | --- |\n"
                "| interactive Codex runtime | `/bin/zsh -lc 'git worktree remove .worktrees/example'` | none logged | `matchedRules: []` | Executed. |\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Use direct command form.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertNotIn(
            "ordinary_repo_command_shell_wrapper_example",
            {finding.kind for finding in result.advisory_findings},
        )

    def test_shell_wrapper_runtime_behavior_guidance_is_flagged_outside_policy_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "prompt.md").write_text(
                "Runtime behavior: run `bash -lc 'make check'` before review.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Use direct command form.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        wrapper_findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "ordinary_repo_command_shell_wrapper_example"
        ]
        self.assertEqual(1, len(wrapper_findings))
        self.assertIn("make check", wrapper_findings[0].reasons[0])

    def test_worktree_creation_without_inspection_signal_is_advisory_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "prompt.md").write_text(
                "For same-repo implementation work, create a new worktree under `.worktrees/` "
                "and make the change there.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "worktree_creation_without_inspection_signal"
        ]
        self.assertEqual(1, len(findings))
        self.assertIn("create a new worktree", findings[0].snippet)

    def test_worktree_selection_order_guidance_is_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "guidance.md").write_text(
                "Before creating a worktree, inspect `git worktree list` and inspect repo-local `.worktrees/`.\n"
                "Before implementation changes, select, reuse, or create a dedicated repo-local worktree.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        worktree_kinds = {
            finding.kind for finding in result.advisory_findings
            if "worktree" in finding.kind
        }
        self.assertEqual(set(), worktree_kinds)

    def test_worktree_adapter_pointer_after_required_worktree_rule_is_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "guidance.md").write_text(
                "Use the implementation-isolation rule: one repository, one branch, "
                "one dedicated repo-local worktree, and one PR per change.\n"
                "For Codex-specific worktree creation, reuse, cleanup, and parallel-batch "
                "handling, follow the Codex adapter.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertNotIn(
            "worktree_creation_without_inspection_signal",
            {finding.kind for finding in result.advisory_findings},
        )

    def test_worktree_commands_run_records_are_not_guidance_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "audit.md").write_text(
                "## Commands Run\n\n"
                "- `git fetch origin main`\n"
                "- `git worktree add .worktrees/example -b audit/example origin/main`\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertNotIn(
            "worktree_creation_without_inspection_signal",
            {finding.kind for finding in result.advisory_findings},
        )

    def test_branch_only_implementation_guidance_is_flagged_without_required_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "guidance.md").write_text(
                "Use worktrees for parallel same-repo work. "
                "Normal branches are fine for single-task sequential work when safe.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "implementation_work_without_required_worktree"
        ]
        self.assertEqual(1, len(findings))
        self.assertIn("Normal branches are fine", findings[0].snippet)

    def test_worktree_cleanup_and_ignore_guidance_is_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "cleanup.md").write_text(
                "Ignore repo-local `.worktrees/` directories and remove stale worktree metadata during cleanup.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertNotIn(
            "worktree_creation_without_inspection_signal",
            {finding.kind for finding in result.advisory_findings},
        )

    def test_workspace_scope_uses_optional_manifest_and_organization_intersection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            playbook_root = workspace / "ai-workflow-playbook"
            playbook = playbook_root / "docs"
            inventory_dir = root / "inventory"
            inventory_path = inventory_dir / "workspace-inventory.txt"
            included = workspace / "included"
            local_only = workspace / "local-only"
            notes = root / "notes"
            notes.mkdir()
            playbook.mkdir(parents=True)
            inventory_dir.mkdir()
            included.mkdir()
            local_only.mkdir()
            (notes / "note.md").write_text("temporary note", encoding="utf-8")
            (playbook / "baseline.md").write_text("workflow guidance", encoding="utf-8")
            inventory_path.write_text(
                "ctrl-alt-keith/included\nctrl-alt-keith/manifest-only\n",
                encoding="utf-8",
            )
            (included / "AGENTS.md").write_text("# AGENTS.md\n\nThin file.\n", encoding="utf-8")
            (local_only / "AGENTS.md").write_text(
                "# AGENTS.md\n\nPrefer direct git and gh commands.\n",
                encoding="utf-8",
            )

            result = scan(
                ScannerConfig(
                    notes_roots=(notes,),
                    playbook_roots=(playbook,),
                    workspace_root=workspace,
                    workspace_manifest=inventory_path,
                    organization_repositories=("ctrl-alt-keith/included", "ctrl-alt-keith/org-only"),
                )
            )

        finding_paths = {finding.path.name for finding in result.advisory_findings}
        snippets = {finding.snippet for finding in result.advisory_findings}
        self.assertIn("ctrl-alt-keith/org-only", snippets)
        self.assertIn("AGENTS.md", finding_paths)
        self.assertNotIn("local-only", {finding.path.parent.name for finding in result.advisory_findings})

    def test_workspace_scope_prefers_organization_enumeration_with_explicit_narrowing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            playbook = root / "playbook"
            included = workspace / "included"
            local_only = workspace / "local-only"
            notes = root / "notes"
            notes.mkdir()
            playbook.mkdir()
            included.mkdir(parents=True)
            local_only.mkdir()
            (notes / "note.md").write_text("temporary note", encoding="utf-8")
            (playbook / "baseline.md").write_text("workflow guidance", encoding="utf-8")
            (included / "AGENTS.md").write_text("# AGENTS.md\n\nThin file.\n", encoding="utf-8")
            (local_only / "AGENTS.md").write_text(
                "# AGENTS.md\n\nPrefer direct git and gh commands.\n",
                encoding="utf-8",
            )
            completed = subprocess.CompletedProcess(
                args=(),
                returncode=0,
                stdout=(
                    '[{"nameWithOwner":"ctrl-alt-keith/included"},'
                    '{"nameWithOwner":"ctrl-alt-keith/org-only"}]'
                ),
                stderr="",
            )

            with patch("enforcement.drift_scanner.subprocess.run", return_value=completed) as run:
                result = scan(
                    ScannerConfig(
                        notes_roots=(notes,),
                        playbook_roots=(playbook,),
                        workspace_root=workspace,
                        organization="ctrl-alt-keith",
                        organization_repositories=("ctrl-alt-keith/included", "ctrl-alt-keith/not-visible"),
                    )
                )

        run.assert_called_once()
        snippets = {finding.snippet for finding in result.advisory_findings}
        finding_paths = {finding.path.name for finding in result.advisory_findings}
        self.assertIn("ctrl-alt-keith/not-visible", snippets)
        self.assertIn("AGENTS.md", finding_paths)
        self.assertNotIn("local-only", {finding.path.parent.name for finding in result.advisory_findings})
        self.assertNotIn("org-only", {finding.path.parent.name for finding in result.advisory_findings})

    def test_workspace_scope_reports_unavailable_organization_inventory_without_filesystem_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            repo = workspace / "local-only"
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            repo.mkdir(parents=True)
            (notes / "note.md").write_text("temporary note", encoding="utf-8")
            (playbook / "baseline.md").write_text("workflow guidance", encoding="utf-8")
            (repo / "AGENTS.md").write_text("# AGENTS.md\n\nPrefer direct git and gh commands.\n", encoding="utf-8")

            with patch("enforcement.drift_scanner.subprocess.run", side_effect=FileNotFoundError("gh")):
                result = scan(
                    ScannerConfig(
                        notes_roots=(notes,),
                        playbook_roots=(playbook,),
                        workspace_root=workspace,
                        organization="ctrl-alt-keith",
                    )
                )

        self.assertEqual(("workspace_scope_inventory_unavailable",), tuple(f.kind for f in result.advisory_findings))
        self.assertEqual("ctrl-alt-keith", result.advisory_findings[0].snippet)
        self.assertNotIn("local-only", {finding.path.parent.name for finding in result.advisory_findings})


if __name__ == "__main__":
    unittest.main()
