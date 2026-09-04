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
        self.assertTrue(candidate.reasons)

    def test_frozen_historical_overlap_remains_visible_with_calibrated_direction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            shared = (
                "Accepted with modification, reasoned decline, superseded, or verified externally "
                "against the exact reviewed artifact identity."
            )
            (notes / "frozen-review.md").write_text(
                "# Frozen Review Record\n\n"
                "This frozen proposal records how ai-workflow-playbook guidance was applied "
                "at the historical review boundary.\n\n"
                f"{shared}\n\n{shared}\n",
                encoding="utf-8",
            )
            (notes / "active-guidance.md").write_text(
                f"# Active Guidance\n\n{shared}\n\n{shared}\n",
                encoding="utf-8",
            )
            (playbook / "review-packet.md").write_text(
                f"# Review Packet\n\n{shared}\n\n{shared}\n",
                encoding="utf-8",
            )

            result = scan(
                ScannerConfig(
                    notes_roots=(notes,),
                    playbook_roots=(playbook,),
                    min_phrase_words=6,
                    min_phrase_matches=2,
                )
            )

        candidates = {candidate.note_path.name: candidate for candidate in result.candidates}
        self.assertEqual({"active-guidance.md", "frozen-review.md"}, set(candidates))
        self.assertNotEqual(
            candidates["frozen-review.md"].reasons,
            candidates["active-guidance.md"].reasons,
        )
        self.assertNotEqual(
            candidates["frozen-review.md"].suggested_direction,
            candidates["active-guidance.md"].suggested_direction,
        )

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

    def test_incubator_confirmed_authority_false_positive_regression_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            false_positive_lines = (
                "Do not turn generated rollups into source of truth for repository state.",
                "Risk: shadow-canon emergence can become an informal canonical reference.",
                "When the operator asserts authoritative state, record the verification target.",
                "No inferred state is treated as canonical.",
                "Autonomous agents help when the source of truth is verifiable.",
                "The GitHub authoritative scan covered the visible repositories.",
                "Repo guidance names make check as canonical local validation.",
                "Authoritative-source and public-safety checks are rollout families.",
                "Best candidates include authoritative-source checks.",
                "Adopt the authoritative-source check for provider claim repositories.",
                "Treat the receipt as a claim rather than as authoritative current state.",
                "Provider work exposed collection lineage as canonical concepts.",
                "Review the authoritative local source graph with read-only tools.",
                "## Verified authoritative baseline",
                "Repository proof remains authoritative for its content, but historical status does not override current state.",
                "Promotion criteria: keep it explicitly non-authoritative.",
                "Do not imply any generated view is source of truth.",
                "Do not treat descriptor output as canonical docs.",
                "Exact workspace scope must come from authoritative inventory.",
                "Add truncation detection before calling the result authoritative.",
            )
            (notes / "confirmed-false-positives.md").write_text(
                "\n".join(false_positive_lines) + "\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertNotIn(
            "noncanonical_authority_language",
            {finding.kind for finding in result.advisory_findings},
        )

    def test_incubator_confirmed_genuine_authority_drift_regression_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            genuine_drift_lines = (
                "Synchronization: lane 2.A merges first because it was the canonical authority.",
                "GitHub state was used as authoritative source via git metadata.",
                "The proposal was verified against current authoritative sources.",
                "List the authoritative sources inspected before trusting recommendations.",
                "Retain this as the canonical worked example for this incubation concept.",
                "GitHub is the authoritative source of truth for repository and review state.",
            )
            (notes / "active-note.md").write_text(
                "\n".join(genuine_drift_lines) + "\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        authority_findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "noncanonical_authority_language"
        ]
        self.assertEqual(list(genuine_drift_lines), [finding.snippet for finding in authority_findings])

    def test_incubator_genuine_authority_wording_is_suppressed_in_frozen_historical_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "frozen-review.md").write_text(
                "# Frozen Review Record\n\n"
                "context: frozen historical evidence artifact\n"
                "role: completed retrospective record\n\n"
                "The preserved review quoted these prior findings:\n"
                "- Synchronization: lane 2.A merges first because it was the canonical authority.\n"
                "- GitHub state was used as authoritative source via git metadata.\n"
                "- GitHub is the authoritative source of truth for repository and review state.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertNotIn(
            "noncanonical_authority_language",
            {finding.kind for finding in result.advisory_findings},
        )

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

        wrapper_findings = [
            finding for finding in result.advisory_findings
            if finding.kind == "ordinary_repo_command_shell_wrapper_example"
        ]
        self.assertEqual(2, len(wrapper_findings))
        for command in ("git status", "make check"):
            self.assertTrue(any(command in finding.reasons[0] for finding in wrapper_findings))

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

    def test_worktree_history_observations_and_stopped_attempts_are_not_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes = root / "notes"
            playbook = root / "playbook"
            notes.mkdir()
            playbook.mkdir()
            (notes / "history.md").write_text(
                "Worktree creation also became a visible operational signal that a run had entered setup.\n"
                "Record worktree creation as an observed signal, not a semantic requirement.\n"
                "Worktree creation was an observed signal and remains repository policy.\n"
                "The first Stage 2 dry launch stopped before branch/worktree creation and before evidence collection.\n"
                "The second attempt then stopped before branch/worktree creation and before evidence collection.\n"
                "The amendment passed integrity gates, then stopped before branch/worktree creation and before collection.\n"
                "The history records that execution stopped before branch or worktree creation and before evidence collection.\n"
                "The retrospective analyzes whether creating worktrees too early caused churn.\n",
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
