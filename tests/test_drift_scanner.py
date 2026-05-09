from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

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

    def test_agents_alignment_finds_missing_pointers_without_flagging_thin_reinforcement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            playbook_root = workspace / "ai-workflow-playbook"
            playbook = playbook_root / "docs"
            config_dir = playbook_root / "config"
            repo = workspace / "demo"
            notes = root / "notes"
            notes.mkdir()
            playbook.mkdir(parents=True)
            config_dir.mkdir()
            repo.mkdir()
            (notes / "note.md").write_text("temporary note", encoding="utf-8")
            (config_dir / "workspace-repos.txt").write_text("ctrl-alt-keith/demo\n", encoding="utf-8")
            (playbook / "repo-readiness.md").write_text(
                "Before acting determine the interaction mode. Use implementation mode, "
                "review/audit mode, or orchestration mode. For ordinary repository operations, "
                "use direct git, gh, make, python, and repo-local scripts. Before choosing "
                "wrapper shells such as zsh -lc, bash -lc, or sh -c, check whether a direct "
                "form exists.",
                encoding="utf-8",
            )
            (repo / "AGENTS.md").write_text(
                "# AGENTS.md\n\n"
                "This repo uses ai-workflow-playbook as canonical guidance.\n\n"
                "## Startup And Interaction Mode\n\n"
                "- Before acting, select the interaction mode: implementation, review/audit, "
                "or orchestration/prompt-authoring.\n\n"
                "## Local Execution\n\n"
                "- Use direct command execution for ordinary repo commands such as git, gh, "
                "make, python, and repo-local scripts.\n"
                "- Before using zsh, bash, sh, zsh -lc, bash -lc, or sh -c, check whether "
                "the command has a direct form and use that direct form when it does.\n",
                encoding="utf-8",
            )

            result = scan(
                ScannerConfig(
                    notes_roots=(notes,),
                    playbook_roots=(playbook,),
                    workspace_root=workspace,
                    workspace_manifest=config_dir / "workspace-repos.txt",
                    organization_repositories=("ctrl-alt-keith/demo",),
                )
            )

        self.assertEqual(0, len(result.advisory_findings))

    def test_agents_alignment_finds_missing_command_and_interaction_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            playbook_root = workspace / "ai-workflow-playbook"
            playbook = playbook_root / "docs"
            config_dir = playbook_root / "config"
            repo = workspace / "demo"
            notes = root / "notes"
            notes.mkdir()
            playbook.mkdir(parents=True)
            config_dir.mkdir()
            repo.mkdir()
            (notes / "note.md").write_text("temporary note", encoding="utf-8")
            (config_dir / "workspace-repos.txt").write_text("ctrl-alt-keith/demo\n", encoding="utf-8")
            (playbook / "start-here.md").write_text("ai-workflow-playbook guidance", encoding="utf-8")
            (repo / "AGENTS.md").write_text(
                "# AGENTS.md\n\nPrefer direct git and gh commands.\n",
                encoding="utf-8",
            )

            result = scan(
                ScannerConfig(
                    notes_roots=(notes,),
                    playbook_roots=(playbook,),
                    workspace_root=workspace,
                    workspace_manifest=config_dir / "workspace-repos.txt",
                )
            )

        kinds = {finding.kind for finding in result.advisory_findings}
        self.assertIn("agents_missing_interaction_mode_pointer", kinds)
        self.assertIn("agents_missing_command_form_guidance", kinds)
        self.assertIn("agents_missing_canonical_playbook_reference", kinds)
        self.assertIn("weak_command_form_wording", kinds)

    def test_agents_alignment_flags_large_canonical_duplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            playbook_root = workspace / "ai-workflow-playbook"
            playbook = playbook_root / "docs"
            config_dir = playbook_root / "config"
            repo = workspace / "demo"
            notes = root / "notes"
            notes.mkdir()
            playbook.mkdir(parents=True)
            config_dir.mkdir()
            repo.mkdir()
            repeated_policy = (
                "Before acting determine the interaction mode and preserve implementation "
                "review audit orchestration boundaries with direct command execution for "
                "git gh make python and repo-local scripts before choosing wrapper shells. "
            ) * 50
            (notes / "note.md").write_text("temporary note", encoding="utf-8")
            (config_dir / "workspace-repos.txt").write_text("ctrl-alt-keith/demo\n", encoding="utf-8")
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
                    workspace_manifest=config_dir / "workspace-repos.txt",
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
                "Choose between the active checkout, an existing clean worktree, or a new worktree based on task isolation needs.\n",
                encoding="utf-8",
            )
            (playbook / "baseline.md").write_text("Reusable workflow guidance lives here.\n", encoding="utf-8")

            result = scan(ScannerConfig(notes_roots=(notes,), playbook_roots=(playbook,)))

        self.assertNotIn(
            "worktree_creation_without_inspection_signal",
            {finding.kind for finding in result.advisory_findings},
        )

    def test_parallel_worktree_guidance_with_normal_branch_scope_is_not_flagged(self) -> None:
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

        self.assertNotIn(
            "worktree_creation_without_inspection_signal",
            {finding.kind for finding in result.advisory_findings},
        )

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

    def test_workspace_scope_uses_manifest_and_organization_intersection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            playbook_root = workspace / "ai-workflow-playbook"
            playbook = playbook_root / "docs"
            config_dir = playbook_root / "config"
            included = workspace / "included"
            local_only = workspace / "local-only"
            notes = root / "notes"
            notes.mkdir()
            playbook.mkdir(parents=True)
            config_dir.mkdir()
            included.mkdir()
            local_only.mkdir()
            (notes / "note.md").write_text("temporary note", encoding="utf-8")
            (playbook / "baseline.md").write_text("workflow guidance", encoding="utf-8")
            (config_dir / "workspace-repos.txt").write_text(
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
                    workspace_manifest=config_dir / "workspace-repos.txt",
                    organization_repositories=("ctrl-alt-keith/included", "ctrl-alt-keith/org-only"),
                )
            )

        finding_paths = {finding.path.name for finding in result.advisory_findings}
        snippets = {finding.snippet for finding in result.advisory_findings}
        self.assertIn("org-only", snippets)
        self.assertIn("AGENTS.md", finding_paths)
        self.assertNotIn("local-only", {finding.path.parent.name for finding in result.advisory_findings})


if __name__ == "__main__":
    unittest.main()
