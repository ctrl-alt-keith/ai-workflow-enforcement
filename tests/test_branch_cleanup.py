from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from enforcement import branch_cleanup
from enforcement.branch_cleanup import (
    BranchCleanupConfig,
    RepoTarget,
    StaleApproval,
    cleanup_branches,
    cleanup_branches_with_retries,
    load_config,
    report_to_dict,
    remote_branch_name,
    resolve_branch_cleanup_scope,
)
from enforcement.github_org_repositories import (
    OrganizationRepository,
    OrganizationRepositoryEnumeration,
)


class BranchCleanupTests(unittest.TestCase):
    def test_provider_scope_discovers_active_public_and_private_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = _write_provider_config(root)
            enumeration = _organization_enumeration(
                _organization_repository("existing"),
                _organization_repository("new-private", repository_id=2, private=True),
            )
            with mock.patch.object(
                branch_cleanup,
                "enumerate_organization_repositories",
                return_value=enumeration,
            ):
                config = resolve_branch_cleanup_scope(load_config(config_path))

        self.assertEqual(("existing", "new-private"), tuple(item.name for item in config.repositories))
        self.assertEqual("complete", config.scope_reconciliation.completeness)
        self.assertTrue(config.scope_reconciliation.resolved_targets[1].private)

    def test_provider_scope_excludes_archived_members_and_drops_former_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = _write_provider_config(
                root,
                overrides={"ctrl-alt-keith/transferred-out": {"path": "old-location"}},
            )
            enumeration = _organization_enumeration(
                _organization_repository("active"),
                _organization_repository("archived", repository_id=2, archived=True),
            )
            with mock.patch.object(
                branch_cleanup,
                "enumerate_organization_repositories",
                return_value=enumeration,
            ):
                config = resolve_branch_cleanup_scope(load_config(config_path))

        scope = config.scope_reconciliation
        self.assertEqual(("active",), tuple(item.name for item in config.repositories))
        self.assertEqual(("ctrl-alt-keith/archived",), scope.archived_members)
        self.assertEqual(("ctrl-alt-keith/transferred-out",), scope.unmatched_overrides)

    def test_provider_scope_applies_explicit_exclusion_with_reason_and_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = _write_provider_config(
                root,
                exclusions=[
                    {
                        "repository": "ctrl-alt-keith/excluded",
                        "reason": "Authoritative exception",
                        "authority": "CAK-999",
                    }
                ],
            )
            enumeration = _organization_enumeration(
                _organization_repository("included"),
                _organization_repository("excluded", repository_id=2),
            )
            with mock.patch.object(
                branch_cleanup,
                "enumerate_organization_repositories",
                return_value=enumeration,
            ):
                config = resolve_branch_cleanup_scope(load_config(config_path))

        self.assertEqual(("included",), tuple(item.name for item in config.repositories))
        exclusion = config.scope_reconciliation.exclusions[0]
        self.assertEqual("Authoritative exception", exclusion.reason)
        self.assertEqual("CAK-999", exclusion.authority)

    def test_provider_scope_rejects_malformed_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_provider_config(
                Path(tmp),
                exclusions=[{"repository": "ctrl-alt-keith/excluded", "reason": "missing authority"}],
            )

            with self.assertRaisesRegex(ValueError, "requires an authority"):
                load_config(config_path)

    def test_incomplete_provider_scope_blocks_apply_before_repository_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(_write_provider_config(Path(tmp)))
            enumeration = _organization_enumeration(
                _organization_repository("partial"),
                complete=False,
                errors=("authenticated organization-owner membership could not be verified",),
            )
            with (
                mock.patch.object(
                    branch_cleanup,
                    "enumerate_organization_repositories",
                    return_value=enumeration,
                ),
                mock.patch.object(branch_cleanup, "_cleanup_repo") as cleanup_repo,
            ):
                report = cleanup_branches(config, apply=True)

        cleanup_repo.assert_not_called()
        self.assertTrue(report.dry_run)
        self.assertTrue(report.requested_apply)
        self.assertEqual("unknown", report.scope.completeness)
        self.assertIn("complete provider-backed candidate scope", report.mutation_blocked)

    def test_incomplete_provider_scope_cli_returns_nonzero_with_json_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_provider_config(Path(tmp))
            enumeration = _organization_enumeration(
                _organization_repository("partial"),
                complete=False,
                errors=("visibility is unproven",),
            )
            stdout = io.StringIO()
            with (
                mock.patch.object(
                    branch_cleanup,
                    "enumerate_organization_repositories",
                    return_value=enumeration,
                ),
                mock.patch("sys.stdout", stdout),
            ):
                code = branch_cleanup.main(
                    ["--config", str(config_path), "--apply", "--output-format", "json"]
                )

        data = json.loads(stdout.getvalue())
        self.assertEqual(1, code)
        self.assertEqual("unknown", data["scope"]["completeness"])
        self.assertIn("complete provider-backed candidate scope", data["mutation_blocked"])
        self.assertEqual([], data["repositories"])

    def test_provider_scope_reports_missing_checkout_without_changing_membership(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            enumeration = _organization_enumeration(_organization_repository("missing"))
            with mock.patch.object(
                branch_cleanup,
                "enumerate_organization_repositories",
                return_value=enumeration,
            ):
                report = cleanup_branches(load_config(_write_provider_config(root)))

        self.assertEqual(("ctrl-alt-keith/missing",), report.scope.candidates)
        self.assertEqual("repository path does not exist", report.repos[0].skipped)

    def test_provider_scope_uses_nonstandard_path_override_as_narrow_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            enumeration = _organization_enumeration(_organization_repository("sample"))
            config_path = _write_provider_config(
                root,
                overrides={"ctrl-alt-keith/sample": {"path": "nested/nonstandard"}},
            )
            with mock.patch.object(
                branch_cleanup,
                "enumerate_organization_repositories",
                return_value=enumeration,
            ):
                config = resolve_branch_cleanup_scope(load_config(config_path))

        self.assertEqual((root / "nested/nonstandard").resolve(), config.repositories[0].path)

    def test_legacy_repository_inventory_is_explicit_compatibility_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "branch-cleanup.json"
            config_path.write_text(
                json.dumps({"repositories": [{"name": "sample", "path": "repo"}]}),
                encoding="utf-8",
            )
            config = resolve_branch_cleanup_scope(load_config(config_path))

        self.assertEqual("legacy_explicit_compatibility", config.scope_reconciliation.mode)
        self.assertEqual("explicit_compatibility", config.scope_reconciliation.completeness)

    def test_protected_refs_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "branch", "release", "main")
            config = BranchCleanupConfig(
                repositories=(RepoTarget("sample", repo),),
                protected_branches=("main", "release"),
            )

            report = cleanup_branches(config)

        release = _action(report, "release", "local", "normal_cleanup")
        self.assertEqual("preserved", release.action)
        self.assertEqual("protected branch", release.reason)

    def test_configured_protected_refs_cannot_unprotect_builtin_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "branch", "master", "main")
            config = BranchCleanupConfig(
                repositories=(RepoTarget("sample", repo),),
                protected_branches=("release",),
            )

            report = cleanup_branches(config)

        master = _action(report, "master", "local", "normal_cleanup")
        self.assertIn("release", config.protected_branches)
        self.assertIn("master", config.protected_branches)
        self.assertEqual("preserved", master.action)
        self.assertEqual("protected branch", master.reason)

    def test_loaded_config_adds_protected_refs_to_builtin_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            _git(repo, "branch", "trunk", "main")
            config_path = root / "branch-cleanup.json"
            config_path.write_text(
                """
{
  "repositories": [{"name": "sample", "path": "repo"}],
  "protected_branches": ["release"]
}
""",
                encoding="utf-8",
            )

            config = load_config(config_path)
            report = cleanup_branches(config)

        trunk = _action(report, "trunk", "local", "normal_cleanup")
        self.assertIn("release", config.protected_branches)
        self.assertIn("trunk", config.protected_branches)
        self.assertEqual("preserved", trunk.action)
        self.assertEqual("protected branch", trunk.reason)

    def test_symbolic_refs_are_not_remote_delete_names(self) -> None:
        self.assertIsNone(remote_branch_name("refs/remotes/origin/HEAD"))
        self.assertIsNone(remote_branch_name("refs/remotes/upstream/topic"))
        self.assertEqual("feature/deep/name", remote_branch_name("refs/remotes/origin/feature/deep/name"))

    def test_merged_branch_with_clean_worktree_is_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")

            report = cleanup_branches(_config(repo), apply=True)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")
            linked_exists = linked.exists()

        action = _action(report, "done", "local", "normal_cleanup")
        worktree = _worktree(report, linked)
        self.assertEqual("deleted", action.action)
        self.assertEqual("apply_policy_authorized", action.worktree_cleanup_authority)
        self.assertIn("worktree=", action.evidence[1])
        self.assertEqual("removed_clean_linked_worktree", worktree.cleanup_classification)
        self.assertEqual("apply_policy_authorized", worktree.cleanup_authority)
        self.assertEqual("succeeded", worktree.action_result)
        self.assertEqual("removed_verified", worktree.final_verification_state)
        self.assertFalse(linked_exists)
        self.assertNotEqual(0, ref_check.returncode)

    def test_worktree_with_uncommitted_changes_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            (linked / "README.md").write_text("changed\n", encoding="utf-8")

            report = cleanup_branches(_config(repo), apply=True)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")
            linked_exists = linked.exists()

        action = _action(report, "done", "local", "normal_cleanup")
        worktree = _worktree(report, linked)
        self.assertEqual("preserved", action.action)
        self.assertIn("worktree has uncommitted changes", action.reason)
        self.assertEqual("dirty_worktree_blocked", worktree.cleanup_classification)
        self.assertEqual("preserve_policy", worktree.cleanup_authority)
        self.assertEqual("dirty", worktree.cleanliness)
        self.assertTrue(linked_exists)
        self.assertEqual(0, ref_check.returncode)

    def test_worktree_with_untracked_files_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            (linked / "scratch.txt").write_text("pending\n", encoding="utf-8")

            report = cleanup_branches(_config(repo), apply=True)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")
            linked_exists = linked.exists()

        action = _action(report, "done", "local", "normal_cleanup")
        worktree = _worktree(report, linked)
        self.assertEqual("preserved", action.action)
        self.assertIn("worktree has untracked files", action.reason)
        self.assertEqual("dirty_worktree_blocked", worktree.cleanup_classification)
        self.assertEqual("untracked", worktree.cleanliness)
        self.assertTrue(linked_exists)
        self.assertEqual(0, ref_check.returncode)

    def test_missing_worktree_path_metadata_is_pruned_without_deleting_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            shutil.rmtree(linked)

            report = cleanup_branches(_config(repo), apply=True)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")
            listed = _git(repo, "worktree", "list", "--porcelain", "--expire", "now").stdout

        action = _action(report, "done", "local", "normal_cleanup")
        worktree = _worktree(report, linked)
        self.assertEqual("preserved", action.action)
        self.assertIn("metadata requires pruning", action.reason)
        self.assertEqual("pruned_stale_worktree_metadata", worktree.cleanup_classification)
        self.assertEqual("apply_policy_authorized", worktree.cleanup_authority)
        self.assertTrue(worktree.stale_metadata_pruned)
        self.assertEqual("metadata_absent_verified", worktree.final_verification_state)
        self.assertNotIn(str(linked), listed)
        self.assertEqual(0, ref_check.returncode)

    def test_locked_worktree_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            _git(repo, "worktree", "lock", "--reason", "manual hold", str(linked))

            report = cleanup_branches(_config(repo), apply=True)

        action = _action(report, "done", "local", "normal_cleanup")
        worktree = _worktree(report, linked)
        self.assertEqual("preserved", action.action)
        self.assertIn("locked", action.reason)
        self.assertEqual("locked_worktree_preserved", worktree.cleanup_classification)
        self.assertEqual("preserve_policy", worktree.cleanup_authority)
        self.assertEqual("manual hold", worktree.lock_reason)

    def test_primary_worktree_is_always_reported_and_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))

            report = cleanup_branches(_config(repo), apply=True)

        worktree = _worktree(report, repo)
        self.assertTrue(worktree.primary)
        self.assertEqual("primary_worktree_preserved", worktree.cleanup_classification)
        self.assertEqual("present_verified", worktree.final_verification_state)

    def test_protected_branch_worktree_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _git(repo, "branch", "release", "main")
            _git(repo, "worktree", "add", str(linked), "release")
            config = BranchCleanupConfig(
                repositories=(RepoTarget("sample", repo),),
                protected_branches=("release",),
            )

            report = cleanup_branches(config, apply=True)
            linked_exists = linked.exists()

        worktree = _worktree(report, linked)
        self.assertEqual("protected_branch_worktree_preserved", worktree.cleanup_classification)
        self.assertTrue(linked_exists)

    def test_failed_branch_deletion_revalidation_preserves_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")

            with mock.patch.object(
                branch_cleanup,
                "_remove_worktree_for_branch",
                return_value=branch_cleanup._WorktreeRemovalDecision(
                    path=str(linked),
                    error="branch is no longer proven merged into refs/remotes/origin/main",
                ),
            ):
                report = cleanup_branches(_config(repo), apply=True)
            linked_exists = linked.exists()

        action = _action(report, "done", "local", "normal_cleanup")
        worktree = _worktree(report, linked)
        self.assertEqual("failed", action.action)
        self.assertEqual("preserve_policy", action.worktree_cleanup_authority)
        self.assertEqual("branch_deletion_failed_worktree_preserved", worktree.cleanup_classification)
        self.assertEqual("preserve_policy", worktree.cleanup_authority)
        self.assertTrue(linked_exists)

    def test_branch_delete_command_failure_restores_clean_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            real_git = branch_cleanup._git

            def fail_branch_delete(cwd: Path, *argv: str) -> branch_cleanup.GitCommand:
                if argv[:3] == ("branch", "-d", "--"):
                    return branch_cleanup.GitCommand(("git", *argv), 1, "", "simulated branch deletion failure")
                return real_git(cwd, *argv)

            with mock.patch.object(branch_cleanup, "_git", side_effect=fail_branch_delete):
                report = cleanup_branches(_config(repo), apply=True)

            linked_exists = linked.exists()
            linked_branch = _git(linked, "branch", "--show-current").stdout.strip()
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")

        action = _action(report, "done", "local", "normal_cleanup")
        worktree = _worktree(report, linked)
        self.assertEqual("failed", action.action)
        self.assertIn("clean linked worktree restored", action.reason)
        self.assertEqual("branch_deletion_failed_worktree_preserved", worktree.cleanup_classification)
        self.assertTrue(linked_exists)
        self.assertEqual("done", linked_branch)
        self.assertEqual(0, ref_check.returncode)

    def test_occupied_original_path_blocks_worktree_restoration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            original_path = branch_cleanup._worktree_branches(repo)["done"]
            real_git = branch_cleanup._git
            calls: list[tuple[str, ...]] = []
            race: dict[str, object] = {"delete_attempts": 0}

            def fail_branch_delete(cwd: Path, *argv: str) -> branch_cleanup.GitCommand:
                calls.append(argv)
                if argv[:3] == ("branch", "-d", "--"):
                    race["delete_attempts"] = int(race["delete_attempts"]) + 1
                    if race["delete_attempts"] == 1:
                        race["original_path_removed"] = not linked.exists()
                        worktree_list = real_git(cwd, "worktree", "list", "--porcelain")
                        race["original_registration_removed"] = (
                            f"worktree {original_path}\n" not in worktree_list.stdout
                        )
                        linked.mkdir()
                        race["occupant_inode"] = linked.stat().st_ino
                        race["occupant_device"] = linked.stat().st_dev
                        race["occupant_entries"] = tuple(linked.iterdir())
                        race["occupant_is_symlink"] = linked.is_symlink()
                    return branch_cleanup.GitCommand(
                        ("git", *argv),
                        1,
                        "",
                        "simulated branch deletion failure",
                    )
                return real_git(cwd, *argv)

            with mock.patch.object(branch_cleanup, "_git", side_effect=fail_branch_delete):
                first_report = cleanup_branches(_config(repo), apply=True)
                second_report = cleanup_branches(_config(repo), apply=True)

            occupant_stat = linked.stat()
            occupant_entries = tuple(linked.iterdir())
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")
            final_worktree_list = _git(repo, "worktree", "list", "--porcelain").stdout

        first_action = _action(first_report, "done", "local", "normal_cleanup")
        first_worktree = _worktree(first_report, linked)
        second_action = _action(second_report, "done", "local", "normal_cleanup")
        self.assertTrue(race["original_path_removed"])
        self.assertTrue(race["original_registration_removed"])
        self.assertEqual(race["occupant_inode"], occupant_stat.st_ino)
        self.assertEqual(race["occupant_device"], occupant_stat.st_dev)
        self.assertEqual(race["occupant_entries"], occupant_entries)
        self.assertEqual((), occupant_entries)
        self.assertFalse(race["occupant_is_symlink"])
        self.assertFalse(any("--force" in argv for argv in calls))
        self.assertFalse(any(argv[:2] == ("worktree", "add") for argv in calls))
        self.assertEqual(0, ref_check.returncode)
        self.assertNotIn(f"worktree {original_path}\n", final_worktree_list)
        self.assertEqual("failed", first_action.action)
        self.assertIn(
            f"worktree restoration failed: original worktree path is occupied: {original_path}",
            first_action.reason,
        )
        self.assertEqual(original_path, first_worktree.path)
        self.assertEqual("done", first_worktree.branch)
        self.assertEqual("branch_deletion_failed_after_worktree_removal", first_worktree.cleanup_classification)
        self.assertEqual("failed", first_worktree.action_result)
        self.assertTrue(first_worktree.path_exists)
        self.assertEqual("verification_failed", first_worktree.final_verification_state)
        self.assertTrue(first_worktree.residual_manual_action)
        self.assertEqual("failed", second_action.action)

    def test_detached_clean_worktree_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _git(repo, "worktree", "add", "--detach", str(linked), "main")

            report = cleanup_branches(_config(repo), apply=True)
            linked_exists = linked.exists()

        worktree = _worktree(report, linked)
        self.assertEqual("ambiguous_detached_worktree_preserved", worktree.cleanup_classification)
        self.assertEqual("human_approval_required", worktree.cleanup_authority)
        self.assertTrue(worktree.detached_commit)
        self.assertTrue(linked_exists)

    def test_live_worktree_is_not_mistaken_for_stale_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_branch(repo, "active", "active.txt", "active\n")
            _git(repo, "worktree", "add", str(linked), "active")

            report = cleanup_branches(_config(repo), apply=True)
            linked_exists = linked.exists()

        worktree = _worktree(report, linked)
        self.assertFalse(worktree.prunable)
        self.assertFalse(worktree.stale_metadata_pruned)
        self.assertEqual("active_worktree_preserved", worktree.cleanup_classification)
        self.assertTrue(linked_exists)

    def test_git_operation_in_progress_blocks_worktree_removal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            merge_head = Path(_git(linked, "rev-parse", "--path-format=absolute", "--git-path", "MERGE_HEAD").stdout.strip())
            merge_head.write_text(_git(repo, "rev-parse", "main").stdout.strip() + "\n", encoding="utf-8")

            report = cleanup_branches(_config(repo), apply=True)
            linked_exists = linked.exists()

        worktree = _worktree(report, linked)
        self.assertEqual("active_operation_worktree_preserved", worktree.cleanup_classification)
        self.assertIn("merge", worktree.operation_state)
        self.assertTrue(linked_exists)

    def test_stale_audit_does_not_promote_worktree_with_active_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_branch(repo, "stale", "stale.txt", "stale\n")
            _git(repo, "worktree", "add", str(linked), "stale")
            merge_head = Path(_git(linked, "rev-parse", "--path-format=absolute", "--git-path", "MERGE_HEAD").stdout.strip())
            merge_head.write_text(_git(repo, "rev-parse", "main").stdout.strip() + "\n", encoding="utf-8")

            report = cleanup_branches(_config(repo), audit_stale=True, audit_github_prs=True)

        action = _action(report, "stale", "local", "needs_human_review")
        self.assertEqual("report_only", action.action)
        self.assertIn("operation in progress", action.reason)

    def test_worktree_removal_failure_is_reported_without_branch_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            real_git = branch_cleanup._git

            def fail_remove(cwd: Path, *argv: str) -> branch_cleanup.GitCommand:
                if argv[:2] == ("worktree", "remove"):
                    return branch_cleanup.GitCommand(("git", *argv), 1, "", "simulated removal failure")
                return real_git(cwd, *argv)

            with mock.patch.object(branch_cleanup, "_git", side_effect=fail_remove):
                report = cleanup_branches(_config(repo), apply=True)

            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")
            linked_exists = linked.exists()

        worktree = _worktree(report, linked)
        self.assertEqual("worktree_removal_failed", worktree.cleanup_classification)
        action = _action(report, "done", "local", "normal_cleanup")
        self.assertEqual("apply_policy_authorized", action.worktree_cleanup_authority)
        self.assertEqual("apply_policy_authorized", worktree.cleanup_authority)
        self.assertEqual("failed", worktree.action_result)
        self.assertEqual(1, report_to_dict(report)["worktree_summary"]["failed_removals"])
        self.assertEqual(0, ref_check.returncode)
        self.assertTrue(linked_exists)

    def test_post_removal_verification_failure_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")

            with mock.patch.object(
                branch_cleanup,
                "_verify_worktree_removed",
                return_value=f"worktree removal verification failed: metadata remains for {linked}",
            ):
                report = cleanup_branches(_config(repo), apply=True)

        worktree = _worktree(report, linked)
        self.assertEqual("worktree_removal_verification_failed", worktree.cleanup_classification)
        self.assertEqual("apply_policy_authorized", worktree.cleanup_authority)
        self.assertEqual("verification_failed", worktree.final_verification_state)
        self.assertEqual(1, report_to_dict(report)["worktree_summary"]["verification_failures"])

    def test_report_only_mode_does_not_remove_worktree_or_prune_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            stale = root / "stale"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            _git(repo, "worktree", "add", "--detach", str(stale), "main")
            shutil.rmtree(stale)

            report = cleanup_branches(_config(repo))
            listed = _git(repo, "worktree", "list", "--porcelain", "--expire", "now").stdout
            linked_exists = linked.exists()

        self.assertEqual("clean_linked_worktree_would_remove", _worktree(report, linked).cleanup_classification)
        self.assertEqual("stale_worktree_metadata_prunable", _worktree(report, stale).cleanup_classification)
        self.assertTrue(linked_exists)
        self.assertIn(str(stale), listed)

    def test_json_report_contains_stable_worktree_disposition_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            report = cleanup_branches(_config(repo))

        data = report_to_dict(report)
        worktree = data["repositories"][0]["worktrees"][0]
        self.assertEqual(4, data["schema_version"])
        for field in (
            "repo",
            "path",
            "primary",
            "branch",
            "detached_commit",
            "cleanliness",
            "operation_state",
            "locked",
            "related_branch_classification",
            "related_branch_outcome",
            "cleanup_classification",
            "cleanup_authority",
            "action_attempted",
            "action_result",
            "preservation_or_blocker_reason",
            "stale_metadata_pruned",
            "final_verification_state",
            "residual_manual_action",
        ):
            self.assertIn(field, worktree)

    def test_apply_removes_safe_worktree_without_confirmation_or_stdin_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            config_path = root / "branch-cleanup.json"
            config_path.write_text(
                json.dumps(
                    {
                        "repositories": [
                            {
                                "name": "sample",
                                "path": str(repo),
                                "remote": "origin",
                                "default_branch": "main",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()

            with (
                mock.patch("builtins.input", side_effect=AssertionError("unexpected confirmation prompt")),
                mock.patch.object(sys, "stdin", _FailOnReadStdin()),
                mock.patch.object(sys, "stdout", output),
            ):
                exit_code = branch_cleanup.main(
                    ["--config", str(config_path), "--apply", "--output-format", "json"]
                )

            payload = json.loads(output.getvalue())
            worktree = next(
                item
                for item in payload["repositories"][0]["worktrees"]
                if Path(item["path"]).resolve(strict=False) == linked.resolve(strict=False)
            )
            linked_exists = linked.exists()

        self.assertEqual(0, exit_code)
        self.assertFalse(linked_exists)
        self.assertEqual("removed_clean_linked_worktree", worktree["cleanup_classification"])
        self.assertEqual("apply_policy_authorized", worktree["cleanup_authority"])
        self.assertEqual(1, payload["worktree_summary"]["automatically_removed_safe_worktrees"])
        self.assertNotIn("unexpected_confirmation_requests", payload["worktree_summary"])
        self.assertNotIn("Yes, delete the safe worktrees.", output.getvalue())

    def test_apply_succeeds_with_closed_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            config_path = root / "branch-cleanup.json"
            config_path.write_text(
                json.dumps({"repositories": [{"name": "sample", "path": str(repo)}]}),
                encoding="utf-8",
            )
            closed_stdin = io.StringIO()
            closed_stdin.close()

            with (
                mock.patch.object(sys, "stdin", closed_stdin),
                mock.patch.object(sys, "stdout", io.StringIO()),
            ):
                exit_code = branch_cleanup.main(["--config", str(config_path), "--apply"])
            linked_exists = linked.exists()

        self.assertEqual(0, exit_code)
        self.assertFalse(linked_exists)

    def test_child_commands_are_explicitly_noninteractive(self) -> None:
        completed = subprocess.CompletedProcess(("git", "status"), 0, "", "")
        with mock.patch.object(branch_cleanup.subprocess, "run", return_value=completed) as run:
            branch_cleanup._git(Path("."), "status")

        kwargs = run.call_args.kwargs
        self.assertEqual(subprocess.DEVNULL, kwargs["stdin"])
        self.assertFalse(kwargs["shell"])
        self.assertEqual("0", kwargs["env"]["GIT_TERMINAL_PROMPT"])
        self.assertEqual("1", kwargs["env"]["GH_PROMPT_DISABLED"])

    def test_cli_has_no_generic_confirmation_bypass_options(self) -> None:
        option_strings = {
            option
            for action in branch_cleanup.build_parser()._actions
            for option in action.option_strings
        }
        self.assertTrue({"--yes", "--force", "--assume-yes"}.isdisjoint(option_strings))

    def test_end_to_end_unattended_apply_removes_only_safe_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            safe = root / "safe"
            dirty = root / "dirty"
            locked = root / "locked"
            for branch in ("safe", "dirty", "locked"):
                _commit_and_merge_branch(repo, branch)
            _git(repo, "worktree", "add", str(safe), "safe")
            _git(repo, "worktree", "add", str(dirty), "dirty")
            _git(repo, "worktree", "add", str(locked), "locked")
            (dirty / "scratch.txt").write_text("preserve me\n", encoding="utf-8")
            _git(repo, "worktree", "lock", "--reason", "manual hold", str(locked))

            with (
                mock.patch("builtins.input", side_effect=AssertionError("unexpected confirmation prompt")),
                mock.patch.object(sys, "stdin", _FailOnReadStdin()),
            ):
                first = cleanup_branches(_config(repo), apply=True)
                second = cleanup_branches(_config(repo), apply=True)
            first_json = report_to_dict(first)
            first_text = branch_cleanup.render_text_report(first)

            safe_exists = safe.exists()
            dirty_exists = dirty.exists()
            locked_exists = locked.exists()

        self.assertFalse(safe_exists)
        self.assertTrue(dirty_exists)
        self.assertTrue(locked_exists)
        self.assertEqual("apply_policy_authorized", _worktree(first, safe).cleanup_authority)
        self.assertEqual("preserve_policy", _worktree(first, dirty).cleanup_authority)
        self.assertEqual("preserve_policy", _worktree(first, locked).cleanup_authority)
        self.assertEqual(1, first_json["worktree_summary"]["automatically_removed_safe_worktrees"])
        self.assertEqual(1, first_json["worktree_summary"]["preserved_worktrees_by_reason"]["dirty_worktree_blocked"])
        self.assertEqual(1, first_json["worktree_summary"]["preserved_worktrees_by_reason"]["locked_worktree_preserved"])
        self.assertIn("automatically_removed_safe=1", first_text)
        self.assertIn("authority: apply_policy_authorized", first_text)
        self.assertNotIn("unexpected_confirmation_requests", first_text)
        self.assertFalse(any(item.cleanup_classification == "removed_clean_linked_worktree" for item in second.repos[0].worktrees))

    def test_repeated_apply_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")

            first = cleanup_branches(_config(repo), apply=True)
            second = cleanup_branches(_config(repo), apply=True)

        self.assertEqual("removed_clean_linked_worktree", _worktree(first, linked).cleanup_classification)
        self.assertFalse(any(item.cleanup_classification == "removed_clean_linked_worktree" for item in second.repos[0].worktrees))
        self.assertFalse(second.repos[0].worktree_inspection_error)
        self.assertFalse(second.repos[0].worktree_prune_error)

    def test_worktree_remove_revalidates_branch_is_still_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "worktree", "add", str(linked), "done")
            real_worktree_branches = branch_cleanup._worktree_branches
            calls = 0

            def worktree_branches_with_late_commit(path: Path) -> dict[str, str]:
                nonlocal calls
                calls += 1
                if calls == 2:
                    (linked / "late.txt").write_text("late\n", encoding="utf-8")
                    _git(linked, "add", "late.txt")
                    _git(linked, "commit", "-m", "Late branch change")
                return real_worktree_branches(path)

            with mock.patch.object(
                branch_cleanup,
                "_worktree_branches",
                side_effect=worktree_branches_with_late_commit,
            ):
                report = cleanup_branches(_config(repo), apply=True)

            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")
            linked_exists = linked.exists()

        action = _action(report, "done", "local", "normal_cleanup")
        self.assertEqual("failed", action.action)
        self.assertIn("branch is no longer proven merged", action.reason)
        self.assertTrue(linked_exists)
        self.assertEqual(0, ref_check.returncode)

    def test_normal_merged_branch_cleanup_still_works_without_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_and_merge_branch(repo, "done")

            report = cleanup_branches(_config(repo), apply=True)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")

        action = _action(report, "done", "local", "normal_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertNotEqual(0, ref_check.returncode)

    def test_retry_normal_cleanup_deletes_remote_exposed_after_worktree_removal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_and_merge_branch(repo, "done")
            _git(repo, "push", "origin", "done")
            _git(repo, "fetch", "origin")
            _git(repo, "worktree", "add", str(linked), "done")

            report = cleanup_branches_with_retries(_config(repo), apply=True, max_apply_passes=3)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/done")
            remote_check = _git(repo, "ls-remote", "--heads", "origin", "done")

        self.assertEqual("no normal_cleanup would_delete refs remain", report.stopped_reason)
        self.assertGreaterEqual(len(report.reports), 5)
        self.assertEqual("deleted", _action(report.reports[1], "done", "local", "normal_cleanup").action)
        self.assertEqual("deleted", _action(report.reports[3], "done", "remote", "normal_cleanup").action)
        self.assertNotEqual(0, ref_check.returncode)
        self.assertEqual("", remote_check.stdout.strip())

    def test_retry_normal_cleanup_preserves_approved_stale_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="PR merged and local branch is stale",
                evidence={
                    "kind": "github_merged_pr",
                    "pr_number": 456,
                    "state": "MERGED",
                    "merged_at": "2026-05-08T00:00:00Z",
                    "head_oid": oid,
                },
            )

            report = cleanup_branches_with_retries(
                BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                apply=True,
                max_apply_passes=2,
            )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")

        action = _action(report.reports[1], "stale", "local", "stale_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertIn("stale cleanup requires single-pass --apply", action.reason)
        self.assertEqual(0, ref_check.returncode)

    def test_ambiguous_ref_names_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "branch", "ambiguous", "main")
            _git(repo, "tag", "ambiguous", "main")

            report = cleanup_branches(_config(repo))

        action = _action(report, "ambiguous", "local", "normal_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertEqual("ambiguous ref name", action.reason)

    def test_ambiguous_remote_ref_names_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "branch", "remote-ambiguous", "main")
            _git(repo, "push", "origin", "remote-ambiguous")
            _git(repo, "fetch", "origin")
            _git(repo, "tag", "remote-ambiguous", "main")
            _git(repo, "push", "origin", "refs/tags/remote-ambiguous")
            _git(repo, "tag", "-d", "remote-ambiguous")

            report = cleanup_branches(_config(repo))

        action = _action(report, "remote-ambiguous", "remote", "normal_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertEqual("ambiguous remote ref name", action.reason)

    def test_dot_github_participates_in_normal_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp), name=".github")
            _commit_and_merge_branch(repo, "done")

            report = cleanup_branches(BranchCleanupConfig(repositories=(RepoTarget(".github", repo),)))

        action = _action(report, "done", "local", "normal_cleanup")
        self.assertEqual("", report.repos[0].skipped)
        self.assertEqual("would_delete", action.action)

    def test_dirty_repo_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            (repo / "untracked.txt").write_text("pending\n", encoding="utf-8")

            report = cleanup_branches(_config(repo))

        self.assertEqual("dirty working tree", report.repos[0].skipped)

    def test_apply_fetch_prune_skip_reports_first_error_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            real_git = branch_cleanup._git

            def git_with_fetch_failure(cwd: Path, *argv: str) -> branch_cleanup.GitCommand:
                if argv == ("fetch", "origin", "--prune"):
                    return branch_cleanup.GitCommand(
                        ("git",) + argv,
                        128,
                        "",
                        "first actionable line\nsecond line",
                    )
                return real_git(cwd, *argv)

            with mock.patch.object(branch_cleanup, "_git", side_effect=git_with_fetch_failure):
                report = cleanup_branches(_config(repo), apply=True)

        self.assertEqual("fetch/prune failed: first actionable line", report.repos[0].skipped)

    def test_github_pr_evidence_must_match_branch_tip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="merged elsewhere",
                evidence={
                    "kind": "github_merged_pr",
                    "pr_number": 123,
                    "state": "MERGED",
                    "merged_at": "2026-05-08T00:00:00Z",
                    "head_oid": "0" * 40,
                },
            )

            report = cleanup_branches(
                BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,))
            )

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertEqual("stale approval evidence is incomplete or mismatched", action.reason)
        self.assertIn("does not match", action.evidence[0])

    def test_non_ancestor_stale_refs_without_live_evidence_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")

            report = cleanup_branches(_config(repo))

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertEqual("non-ancestor ref lacks live GitHub exact-head or patch-equivalence evidence", action.reason)

    def test_valid_approval_gates_stale_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="PR merged and remote stale ref remains",
                evidence={
                    "kind": "github_merged_pr",
                    "pr_number": 456,
                    "state": "MERGED",
                    "merged_at": "2026-05-08T00:00:00Z",
                    "head_oid": oid,
                },
            )

            report = cleanup_branches(
                BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                apply=True,
            )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertNotEqual(0, ref_check.returncode)

    def test_valid_approval_gates_remote_stale_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "remote-stale", "remote-stale.txt", "unmerged\n")
            _git(repo, "push", "origin", "remote-stale")
            oid = _git(repo, "rev-parse", "refs/remotes/origin/remote-stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="remote",
                branch="remote-stale",
                approved_by="keith",
                reason="remote PR branch is stale after merge",
                evidence={
                    "kind": "github_merged_pr",
                    "pr_number": 789,
                    "state": "MERGED",
                    "merged_at": "2026-05-08T00:00:00Z",
                    "head_oid": oid,
                },
            )

            report = cleanup_branches(
                BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                apply=True,
            )
            remote_check = _git(repo, "ls-remote", "--heads", "origin", "remote-stale")

        action = _action(report, "remote-stale", "remote", "stale_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertEqual("", remote_check.stdout.strip())

    def test_patch_equivalent_deletes_without_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale-patch", "patch.txt", "same change\n")
            _git(repo, "switch", "main")
            (repo / "patch.txt").write_text("same change\n", encoding="utf-8")
            _git(repo, "add", "patch.txt")
            _git(repo, "commit", "-m", "Add equivalent patch")
            _git(repo, "push", "origin", "main")
            report = cleanup_branches(
                _config(repo),
                apply=True,
            )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale-patch")

        action = _action(report, "stale-patch", "local", "stale_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertNotEqual(0, ref_check.returncode)

    def test_stale_audit_reports_patch_equivalent_candidate_without_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale-patch", "patch.txt", "same change\n")
            _git(repo, "switch", "main")
            (repo / "patch.txt").write_text("same change\n", encoding="utf-8")
            _git(repo, "add", "patch.txt")
            _git(repo, "commit", "-m", "Add equivalent patch")
            _git(repo, "push", "origin", "main")

            report = cleanup_branches(_config(repo), audit_stale=True)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale-patch")

        action = _action(report, "stale-patch", "local", "stale_candidate_patch_equivalent")
        self.assertEqual("report_only", action.action)
        self.assertIn("git cherry", "\n".join(action.evidence))
        self.assertEqual(0, ref_check.returncode)

    def test_stale_audit_reports_dirty_worktree_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            _git(repo, "worktree", "add", str(linked), "stale")
            (linked / "scratch.txt").write_text("pending\n", encoding="utf-8")

            report = cleanup_branches(_config(repo), audit_stale=True)

        action = _action(report, "stale", "local", "blocked_dirty_worktree")
        self.assertEqual("report_only", action.action)
        self.assertIn("untracked", action.reason)

    def test_stale_audit_reports_merged_pr_exact_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()

            with mock.patch.object(
                branch_cleanup,
                "_gh",
                return_value=branch_cleanup.GitCommand(
                    ("gh",),
                    0,
                    f'[{{"number": 1, "state": "MERGED", "mergedAt": "2026-05-08T00:00:00Z", "headRefOid": "{oid}", "title": "Merged", "url": "https://example.test/pr/1"}}]',
                    "",
                ),
            ):
                report = cleanup_branches(_config(repo), audit_stale=True, audit_github_prs=True)

        action = _action(report, "stale", "local", "stale_candidate_merged_pr_exact_head")
        self.assertEqual("report_only", action.action)
        self.assertIn("head SHA matches", action.reason)

    def test_merged_pr_exact_head_is_would_delete_without_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()

            with mock.patch.object(branch_cleanup, "_gh", return_value=_merged_pr_command(oid)):
                report = cleanup_branches(_config(repo), audit_stale=True, audit_github_prs=True)

        cleanup = _action(report, "stale", "local", "stale_cleanup")
        audit = _action(report, "stale", "local", "stale_candidate_merged_pr_exact_head")
        self.assertEqual("would_delete", cleanup.action)
        self.assertIn("eligible because GitHub merged PR", cleanup.reason)
        self.assertEqual("report_only", audit.action)
        self.assertIn("head SHA matches", audit.reason)

    def test_merged_pr_exact_head_live_approval_is_would_delete_in_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            with mock.patch.object(branch_cleanup, "_gh", return_value=_merged_pr_command(oid)):
                report = cleanup_branches(
                    BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                    audit_stale=True,
                    audit_github_prs=True,
                )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("would_delete", action.action)
        self.assertIn("eligible because GitHub merged PR", action.reason)
        self.assertIn("GitHub PR #1 state=MERGED", "\n".join(action.evidence))
        self.assertEqual(0, ref_check.returncode)

    def test_merged_pr_exact_head_deletes_without_approval_in_apply_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()
            with mock.patch.object(branch_cleanup, "_gh", return_value=_merged_pr_command(oid)):
                report = cleanup_branches(
                    _config(repo),
                    apply=True,
                    audit_stale=True,
                    audit_github_prs=True,
                )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertIn("eligible because GitHub merged PR", action.reason)
        self.assertNotEqual(0, ref_check.returncode)

    def test_approved_stale_local_branch_in_clean_linked_worktree_is_would_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            _git(repo, "worktree", "add", str(linked), "stale")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            with mock.patch.object(branch_cleanup, "_gh", return_value=_merged_pr_command(oid)):
                report = cleanup_branches(
                    BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                    audit_stale=True,
                    audit_github_prs=True,
                )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("would_delete", action.action)
        self.assertIn("eligible because GitHub merged PR", action.reason)
        self.assertIn("worktree=", "\n".join(action.evidence))
        self.assertIn("apply will remove linked worktree", "\n".join(action.evidence))
        self.assertEqual(0, ref_check.returncode)

    def test_approved_stale_local_branch_in_clean_linked_worktree_apply_removes_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            _git(repo, "worktree", "add", str(linked), "stale")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            with (
                mock.patch.object(branch_cleanup, "_gh", return_value=_merged_pr_command(oid)),
                mock.patch("builtins.input", side_effect=AssertionError("unexpected confirmation prompt")),
                mock.patch.object(sys, "stdin", _FailOnReadStdin()),
            ):
                report = cleanup_branches(
                    BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                    apply=True,
                    audit_stale=True,
                    audit_github_prs=True,
                )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")
            linked_exists = linked.exists()

        action = _action(report, "stale", "local", "stale_cleanup")
        worktree = _worktree(report, linked)
        self.assertEqual("deleted", action.action)
        self.assertEqual("apply_policy_authorized", worktree.cleanup_authority)
        self.assertFalse(linked_exists)
        self.assertNotEqual(0, ref_check.returncode)

    def test_stale_worktree_apply_revalidates_branch_tip_before_removal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            _git(repo, "worktree", "add", str(linked), "stale")
            oid = _git(repo, "rev-parse", "refs/heads/stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )
            real_worktree_branches = branch_cleanup._worktree_branches
            calls = 0

            def worktree_branches_with_late_commit(path: Path) -> dict[str, str]:
                nonlocal calls
                calls += 1
                if calls == 2:
                    (linked / "late.txt").write_text("late\n", encoding="utf-8")
                    _git(linked, "add", "late.txt")
                    _git(linked, "commit", "-m", "Late stale branch change")
                return real_worktree_branches(path)

            with (
                mock.patch.object(branch_cleanup, "_gh", return_value=_merged_pr_command(oid)),
                mock.patch.object(
                    branch_cleanup,
                    "_worktree_branches",
                    side_effect=worktree_branches_with_late_commit,
                ),
            ):
                report = cleanup_branches(
                    BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                    apply=True,
                    audit_stale=True,
                    audit_github_prs=True,
                )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")
            linked_exists = linked.exists()

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("failed", action.action)
        self.assertIn("branch tip changed since stale approval planning", action.reason)
        self.assertTrue(linked_exists)
        self.assertEqual(0, ref_check.returncode)

    def test_live_exact_head_cleanup_requires_github_pr_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            report = cleanup_branches(
                _config(repo),
                audit_stale=True,
                audit_github_prs=False,
            )

        action = _action(report, "stale", "local", "stale_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertEqual("non-ancestor ref lacks live GitHub exact-head or patch-equivalence evidence", action.reason)

    def test_stale_audit_preserves_closed_unmerged_pr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")

            with mock.patch.object(
                branch_cleanup,
                "_gh",
                return_value=branch_cleanup.GitCommand(
                    ("gh",),
                    0,
                    '[{"number": 2, "state": "CLOSED", "mergedAt": null, "headRefOid": "abc", "title": "Closed", "url": "https://example.test/pr/2"}]',
                    "",
                ),
            ):
                report = cleanup_branches(_config(repo), audit_stale=True, audit_github_prs=True)

        action = _action(report, "stale", "local", "closed_unmerged_preserve")
        self.assertEqual("report_only", action.action)
        self.assertIn("closed without merge", action.reason)

    def test_closed_unmerged_live_approval_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="reviewed stale branch",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            with mock.patch.object(
                branch_cleanup,
                "_gh",
                return_value=branch_cleanup.GitCommand(
                    ("gh",),
                    0,
                    '[{"number": 2, "state": "CLOSED", "mergedAt": null, "headRefOid": "abc", "title": "Closed", "url": "https://example.test/pr/2"}]',
                    "",
                ),
            ):
                report = cleanup_branches(
                    BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                    audit_stale=True,
                    audit_github_prs=True,
                )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")

        cleanup = _action(report, "stale", "local", "stale_cleanup")
        audit = _action(report, "stale", "local", "closed_unmerged_preserve")
        self.assertEqual("preserved", cleanup.action)
        self.assertIn("stale approval evidence", cleanup.reason)
        self.assertEqual("report_only", audit.action)
        self.assertEqual(0, ref_check.returncode)

    def test_dirty_worktree_stale_ref_is_preserved_even_with_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_branch(repo, "stale", "stale.txt", "unmerged\n")
            _git(repo, "worktree", "add", str(linked), "stale")
            (linked / "scratch.txt").write_text("pending\n", encoding="utf-8")
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="stale",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            report = cleanup_branches(
                BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                audit_stale=True,
                audit_github_prs=True,
            )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/stale")

        cleanup = _action(report, "stale", "local", "stale_cleanup")
        blocker = _action(report, "stale", "local", "blocked_dirty_worktree")
        self.assertEqual("preserved", cleanup.action)
        self.assertIn("worktree has untracked files", cleanup.reason)
        self.assertEqual("report_only", blocker.action)
        self.assertEqual(0, ref_check.returncode)

    def test_remote_stale_ref_with_matching_clean_linked_worktree_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            linked = root / "linked"
            _commit_branch(repo, "remote-stale", "remote-stale.txt", "unmerged\n")
            _git(repo, "push", "origin", "remote-stale")
            _git(repo, "fetch", "origin")
            _git(repo, "worktree", "add", str(linked), "remote-stale")
            oid = _git(repo, "rev-parse", "refs/remotes/origin/remote-stale").stdout.strip()
            approval = StaleApproval(
                repo="sample",
                scope="remote",
                branch="remote-stale",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            with mock.patch.object(branch_cleanup, "_gh", return_value=_merged_pr_command(oid)):
                report = cleanup_branches(
                    BranchCleanupConfig(repositories=(RepoTarget("sample", repo),), stale_approvals=(approval,)),
                    audit_stale=True,
                    audit_github_prs=True,
                )
            remote_check = _git(repo, "ls-remote", "--heads", "origin", "remote-stale")
            linked_exists = linked.exists()

        cleanup = _action(report, "remote-stale", "remote", "stale_cleanup")
        audit = _action(report, "remote-stale", "remote", "stale_candidate_merged_pr_exact_head")
        self.assertEqual("preserved", cleanup.action)
        self.assertIn("branch is checked out in worktree", cleanup.reason)
        self.assertEqual("report_only", audit.action)
        self.assertTrue(linked_exists)
        self.assertIn("remote-stale", remote_check.stdout)

    def test_protected_stale_ref_is_preserved_even_with_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _commit_branch(repo, "release", "release.txt", "unmerged\n")
            approval = StaleApproval(
                repo="sample",
                scope="local",
                branch="release",
                approved_by="keith",
                reason="merged PR exact-head evidence reviewed",
                evidence={"kind": "github_merged_pr_exact_head"},
            )

            report = cleanup_branches(
                BranchCleanupConfig(
                    repositories=(RepoTarget("sample", repo),),
                    protected_branches=("release",),
                    stale_approvals=(approval,),
                ),
                audit_stale=True,
                audit_github_prs=True,
            )
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", "refs/heads/release")

        action = _action(report, "release", "local", "stale_cleanup")
        self.assertEqual("preserved", action.action)
        self.assertEqual("protected branch", action.reason)
        self.assertEqual(0, ref_check.returncode)

    def test_remote_branch_names_starting_with_dash_are_deleted_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _make_repo(root)
            remote = root / "repo.git"
            oid = _git(repo, "rev-parse", "main").stdout.strip()
            _git(root, "--git-dir", str(remote), "update-ref", "refs/heads/--remote-stale", oid)
            _git(repo, "fetch", "origin")

            report = cleanup_branches(_config(repo), apply=True)
            remote_check = _git(repo, "ls-remote", "--heads", "origin", "refs/heads/--remote-stale")

        action = _action(report, "--remote-stale", "remote", "normal_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertEqual("", remote_check.stdout.strip())

    def test_branch_names_with_shell_metacharacters_are_direct_argv_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            branch = "feature/cleanup;touch"
            _git(repo, "branch", branch, "main")

            report = cleanup_branches(_config(repo), apply=True)
            ref_check = _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")

            self.assertFalse((repo / "touch").exists())

        action = _action(report, branch, "local", "normal_cleanup")
        self.assertEqual("deleted", action.action)
        self.assertNotEqual(0, ref_check.returncode)

    def test_invalid_stale_approval_scope_fails_config_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root)
            config_path = root / "branch-cleanup.json"
            config_path.write_text(
                """
{
  "repositories": [{"name": "sample", "path": "repo"}],
  "stale_approvals": [
    {
      "repo": "sample",
      "scope": "global",
      "branch": "stale",
      "approved_by": "keith",
      "reason": "invalid",
      "evidence": {"kind": "patch_equivalent"}
    }
  ]
}
""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "scope must be 'local' or 'remote'"):
                load_config(config_path)

    def test_default_branch_fallback_reports_missing_remote_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(Path(tmp))
            _git(repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")

            report = cleanup_branches(BranchCleanupConfig(repositories=(RepoTarget("sample", repo),)))

        self.assertEqual("main", report.repos[0].default_branch)
        self.assertEqual("remote HEAD missing for origin; fell back to main", report.repos[0].default_branch_evidence)


class _FailOnReadStdin:
    def read(self, *_args, **_kwargs):
        raise AssertionError("branch cleanup attempted to read stdin")

    def readline(self, *_args, **_kwargs):
        raise AssertionError("branch cleanup attempted to read stdin")

    def isatty(self) -> bool:
        return False


def _config(repo: Path) -> BranchCleanupConfig:
    return BranchCleanupConfig(repositories=(RepoTarget("sample", repo),))


def _write_provider_config(
    root: Path,
    *,
    exclusions: list[dict[str, str]] | None = None,
    overrides: dict[str, dict[str, str]] | None = None,
) -> Path:
    path = root / "branch-cleanup.json"
    path.write_text(
        json.dumps(
            {
                "scope": {
                    "provider": "github_organization",
                    "organization": "ctrl-alt-keith",
                    "workspace_root": str(root),
                    "exclusions": exclusions or [],
                    "repository_overrides": overrides or {},
                },
                "protected_branches": ["main", "master", "trunk", "develop"],
            }
        ),
        encoding="utf-8",
    )
    return path


def _organization_repository(
    name: str,
    *,
    repository_id: int = 1,
    archived: bool = False,
    private: bool = False,
) -> OrganizationRepository:
    return OrganizationRepository(
        repository_id=repository_id,
        name=name,
        full_name=f"ctrl-alt-keith/{name}",
        archived=archived,
        private=private,
        default_branch="main",
    )


def _organization_enumeration(
    *repositories: OrganizationRepository,
    complete: bool = True,
    errors: tuple[str, ...] = (),
) -> OrganizationRepositoryEnumeration:
    return OrganizationRepositoryEnumeration(
        organization="ctrl-alt-keith",
        repositories=tuple(repositories),
        complete=complete,
        detail="complete" if complete else "unknown",
        errors=errors,
    )


def _action(report, branch: str, scope: str, phase: str):
    for repo in report.repos:
        for action in repo.actions:
            if action.branch == branch and action.scope == scope and action.phase == phase:
                return action
    raise AssertionError(f"missing action for {phase} {scope} {branch}")


def _worktree(report, path: Path):
    expected = path.resolve(strict=False)
    for repo in report.repos:
        for worktree in repo.worktrees:
            if Path(worktree.path).resolve(strict=False) == expected:
                return worktree
    raise AssertionError(f"missing worktree disposition for {path}")


def _make_repo(root: Path, name: str = "repo") -> Path:
    repo = root / name
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "Tests")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Initial commit")
    _git(repo, "branch", "-M", "main")
    remote = root / f"{name}.git"
    _git(root, "init", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "main")
    _git(root, "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main")
    _git(repo, "fetch", "origin")
    _git(repo, "remote", "set-head", "origin", "-a")
    return repo


def _commit_branch(repo: Path, branch: str, filename: str, content: str) -> None:
    _git(repo, "switch", "-c", branch)
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", f"Add {filename}")
    _git(repo, "switch", "main")


def _commit_and_merge_branch(repo: Path, branch: str) -> None:
    filename = f"{branch.replace('/', '-')}.txt"
    _commit_branch(repo, branch, filename, "merged\n")
    _git(repo, "merge", "--ff-only", branch)
    _git(repo, "push", "origin", "main")
    _git(repo, "fetch", "origin")


def _git(cwd: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ("git",) + tuple(argv),
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    if result.returncode != 0 and "--quiet" not in argv:
        raise AssertionError(
            f"git {' '.join(argv)} failed in {cwd}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def _merged_pr_command(oid: str) -> branch_cleanup.GitCommand:
    return branch_cleanup.GitCommand(
        ("gh",),
        0,
        (
            '[{"number": 1, "state": "MERGED", '
            '"mergedAt": "2026-05-08T00:00:00Z", '
            f'"headRefOid": "{oid}", '
            '"title": "Merged", "url": "https://example.test/pr/1"}]'
        ),
        "",
    )


if __name__ == "__main__":
    unittest.main()
