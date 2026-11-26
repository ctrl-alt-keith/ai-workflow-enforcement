"""Synthetic Git fixtures; no cloud settings, credentials or provider calls."""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from enforcement import cloud_setup_source as source


@unittest.skipUnless(shutil.which("git"), "Git required for source alignment fixtures")
class CloudSetupSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.seed = self.root / "seed"
        self.seed.mkdir()
        self.remote = self.root / "remote.git"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.repo = self.workspace / "ai-workflow-enforcement"
        self.git_binary = shutil.which("git")
        self.env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(self.root),
                    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_TERMINAL_PROMPT": "0"}
        self.git(self.seed, "init", "-q", "-b", "main")
        (self.seed / "input").write_text("old build\n")
        self.old = self.commit()
        self.git(self.root, "clone", "-q", "--bare", str(self.seed), str(self.remote))
        self.git(self.root, "clone", "-q", str(self.remote), str(self.repo))
        self.git(self.repo, "remote", "set-url", "origin",
                 "https://github.com/ctrl-alt-keith/ai-workflow-enforcement.git")
        self.catalog = self.workspace / "catalog.json"
        self.write_catalog(self.old)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        # Substitute transport only. Repository status, identity readback, object
        # access, archive and ref operations still use actual Git.
        wrapper = self.bin / "git"
        remotes = {self.repo.name: str(self.remote), "secrets-lifecycle": str(self.root / "lifecycle.git")}
        wrapper.write_text(
            f"#!{sys.executable}\nimport os,sys\nargs=sys.argv[1:]\n"
            "if 'ls-remote' in args or 'fetch' in args:\n"
            " checkout=args[args.index('-C')+1] if '-C' in args else os.getcwd()\n"
            f" remote={remotes!r}[os.path.basename(checkout)]\n"
            " args=[remote if x=='origin' else x for x in args]\n"
            f"os.execv({self.git_binary!r},[{self.git_binary!r},*args])\n")
        wrapper.chmod(0o755)
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env["PATH"]

    def git(self, repo, *args):
        return subprocess.check_output(
            [self.git_binary, "-c", "core.hooksPath=/dev/null", *args],
            cwd=repo, env=self.env, stderr=subprocess.DEVNULL, text=True,
        ).strip()

    def commit(self):
        self.git(self.seed, "add", ".")
        self.git(self.seed, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        return self.git(self.seed, "rev-parse", "HEAD")

    def advance(self):
        (self.seed / "input").write_text("new build\n")
        head = self.commit()
        self.git(self.seed, "push", "-q", str(self.remote), "HEAD:refs/heads/main")
        self.git(self.repo, "fetch", "-q", str(self.remote), head)
        return head

    def write_catalog(self, commit):
        self.catalog.write_text(json.dumps([{
            "name": source.REPOSITORY, "host": "github.com",
            "mount_path": "ai-workflow-enforcement", "commit": commit,
        }]))

    def verify(self, mode="pinned", selected=None, reviewed=None, main=None):
        args = dict(catalog=self.catalog, workspace_root=self.workspace,
                    selection_mode=mode, selected_commit=selected or self.old)
        if mode == "pinned":
            args.update(reviewed_commit=reviewed or self.old, reviewed_main=main or self.old)
        with patch.dict(os.environ, self.env, clear=True):
            return source.verify_source(**args)

    def test_explicit_pin_remains_authoritative_after_main_advances(self):
        current = self.advance()
        before = self.catalog.read_bytes()
        report = self.verify(main=current)
        self.assertEqual(report["source_commit"], self.old)
        self.assertEqual(report["current_main"], current)
        self.assertEqual(report["main_changes"], ["M\tinput"])
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.old)
        self.assertEqual(self.catalog.read_bytes(), before)

    def test_saved_selection_must_match_explicit_reviewed_pin(self):
        with self.assertRaisesRegex(source.SetupSourceError, "saved selection differs"):
            self.verify(reviewed="a" * 40)

    def test_unmerged_or_divergent_pin_blocks_despite_matching_local_identities(self):
        (self.repo / "unmerged").write_text("not on main\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "unmerged fixture")
        pin = self.git(self.repo, "rev-parse", "HEAD")
        self.write_catalog(pin)
        for diverged in (False, True):
            current = self.advance() if diverged else self.old
            with self.subTest(main=current), self.assertRaisesRegex(
                source.SetupSourceError, "not verified as merged"
            ):
                self.verify(selected=pin, reviewed=pin, main=current)

    def test_catalog_mismatch_blocks_even_with_matching_checkout_and_selection(self):
        self.write_catalog("a" * 40)
        with self.assertRaisesRegex(source.SetupSourceError, "selection and generated catalog differ"):
            self.verify()

    def test_checkout_mismatch_and_dirty_checkout_block(self):
        current = self.advance()
        self.git(self.repo, "checkout", "-q", "--detach", current)
        with self.assertRaisesRegex(source.SetupSourceError, "catalog and checkout commit differ"):
            self.verify(main=current)
        self.git(self.repo, "checkout", "-q", "--detach", self.old)
        (self.repo / "unrelated-edit").write_text("preserve this\n")
        with self.assertRaisesRegex(source.SetupSourceError, "contains changes"):
            self.verify(main=current)
        self.assertEqual((self.repo / "unrelated-edit").read_text(), "preserve this\n")

    def test_main_mode_requires_current_main_and_aligned_catalog(self):
        self.assertEqual(self.verify(mode="main")["status"], "aligned")
        current = self.advance()
        # Cached origin/main is still old; the live ref controls the outcome.
        self.assertEqual(self.git(self.repo, "rev-parse", "origin/main"), self.old)
        with self.assertRaisesRegex(source.SetupSourceError, "behind freshly observed"):
            self.verify(mode="main")
        self.git(self.repo, "checkout", "-q", "--detach", current)
        self.write_catalog(current)
        self.assertEqual(self.verify(mode="main", selected=current)["source_commit"], current)

    def test_main_build_checks_both_sources_without_rewriting_restored_catalog(self):
        lifecycle_name = "ctrl-alt-keith/secrets-lifecycle"
        lifecycle = self.workspace / "secrets-lifecycle"
        lifecycle_remote = self.root / "lifecycle.git"
        self.git(self.root, "clone", "-q", "--bare", str(self.seed), str(lifecycle_remote))
        self.git(self.root, "clone", "-q", str(lifecycle_remote), str(lifecycle))
        self.git(lifecycle, "remote", "set-url", "origin", f"https://github.com/{lifecycle_name}.git")

        args = dict(catalog=self.catalog, workspace_root=self.workspace)
        with patch.dict(os.environ, self.env, clear=True), self.assertRaisesRegex(
            source.SetupSourceError, "required build mount missing.*secrets-lifecycle"
        ):
            source.verify_main_build(**args)

        entries = json.loads(self.catalog.read_text())
        entries.append({"name": lifecycle_name, "host": "github.com",
                        "mount_path": lifecycle.name, "commit": self.old})
        baseline = json.dumps(entries).encode()
        self.catalog.write_bytes(baseline)
        current_enforcement = self.advance()
        self.git(self.repo, "merge", "-q", "--ff-only", current_enforcement)
        (self.seed / "input").write_text("new lifecycle build\n")
        current_lifecycle = self.commit()
        self.git(self.seed, "push", "-q", str(lifecycle_remote), "HEAD:refs/heads/main")
        self.git(lifecycle, "fetch", "-q", str(lifecycle_remote), current_lifecycle)

        with patch.dict(os.environ, self.env, clear=True), self.assertRaisesRegex(
            source.SetupSourceError, "does not match hosted main.*secrets-lifecycle"
        ):
            source.verify_main_build(**args)
        self.git(lifecycle, "merge", "-q", "--ff-only", current_lifecycle)
        with patch.dict(os.environ, self.env, clear=True):
            result = source.verify_main_build(**args)

        self.assertEqual(result["sources"], {
            source.REPOSITORY: {"catalog_commit": self.old, "source_commit": current_enforcement},
            lifecycle_name: {"catalog_commit": self.old, "source_commit": current_lifecycle},
        })
        self.assertEqual(self.catalog.read_bytes(), baseline)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), current_enforcement)
        self.assertEqual(self.git(lifecycle, "rev-parse", "HEAD"), current_lifecycle)

    def test_main_advancement_invalidates_the_reviewed_comparison(self):
        self.advance()
        with self.assertRaisesRegex(source.SetupSourceError, "separately reviewed comparison"):
            self.verify()

    def test_named_branch_build_keeps_main_checkouts_and_refuses_drift(self):
        lifecycle = self.workspace / "secrets-lifecycle"
        lifecycle_remote = self.root / "lifecycle.git"
        lifecycle_name = "ctrl-alt-keith/secrets-lifecycle"
        self.git(self.root, "clone", "-q", "--bare", str(self.seed), str(lifecycle_remote))
        self.git(self.root, "clone", "-q", str(lifecycle_remote), str(lifecycle))
        self.git(lifecycle, "remote", "set-url", "origin", f"https://github.com/{lifecycle_name}.git")
        entries = json.loads(self.catalog.read_text())
        entries.append({"name": lifecycle_name, "host": "github.com",
                        "mount_path": lifecycle.name, "commit": self.old})
        catalog = json.dumps(entries)
        self.catalog.write_text(catalog)

        for name in (*source.BRANCH_STARTUP_FILES, "cloud-environments/openai/install.sh", "enforcement/op_runtime.py"):
            path = self.seed / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("main fixture\n")
        shared = self.commit()
        branch = "build/router"
        self.git(self.seed, "switch", "-qc", branch, shared)
        (self.seed / "enforcement/op_runtime.py").write_text("branch fixture\n")
        tip = self.commit()
        self.git(self.seed, "push", "-q", str(self.remote), f"HEAD:refs/heads/{branch}")
        self.git(self.repo, "fetch", "-q", str(self.remote), tip)
        self.git(self.seed, "switch", "-q", "main")
        main = self.advance()
        self.git(self.repo, "merge", "-q", "--ff-only", main)
        self.assertEqual(self.git(self.repo, "merge-base", main, tip), shared)
        args = dict(catalog=self.catalog, workspace_root=self.workspace)
        options = dict(build_branch=branch, reviewed_branch_tip=tip)

        with patch.dict(os.environ, self.env, clear=True):
            report = source.verify_main_build(**args, **options)
            self.assertEqual(report["selection_mode"], "main")
            self.assertEqual(report["build_mode"], "branch")
            self.assertEqual(report["build_ref"], f"refs/heads/{branch}")
            self.assertEqual(report["sources"][source.REPOSITORY]["source_commit"], tip)
            self.assertEqual(report["artifact_sha256"]["op-runtime.py"],
                             hashlib.sha256(b"branch fixture\n").hexdigest())
            self.assertEqual(report["sources"][lifecycle_name]["source_commit"], self.old)
            self.assertEqual(source.verify_main_build(**args)["build_mode"], "main")
            with self.assertRaisesRegex(source.SetupSourceError, "unavailable from origin"):
                source.verify_main_build(**args, build_branch="build/absent", reviewed_branch_tip=tip)
            with self.assertRaisesRegex(source.SetupSourceError, "differs from the reviewed tip"):
                source.verify_main_build(**args, build_branch=branch, reviewed_branch_tip=shared)

            actual_tip = source._branch_tip
            observations = iter((tip, shared))
            with patch.object(source, "_branch_tip", side_effect=lambda repo, name:
                              next(observations) if name == branch else actual_tip(repo, name)):
                with self.assertRaisesRegex(source.SetupSourceError, "moved during source verification"):
                    source.verify_main_build(**args, **options)

        self.git(self.seed, "switch", "-q", branch)
        (self.seed / source.BRANCH_STARTUP_FILES[1]).write_text("incompatible startup\n")
        incompatible = self.commit()
        self.git(self.seed, "push", "-q", str(self.remote), f"HEAD:refs/heads/{branch}")
        self.git(self.repo, "fetch", "-q", str(self.remote), incompatible)
        with patch.dict(os.environ, self.env, clear=True), self.assertRaisesRegex(
            source.SetupSourceError, "changes the main-tracking startup contract"
        ):
            source.verify_main_build(**args, build_branch=branch, reviewed_branch_tip=incompatible)

        self.assertEqual(self.catalog.read_text(), catalog)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), main)
        self.assertEqual(self.git(lifecycle, "rev-parse", "HEAD"), self.old)

    def test_movement_during_checks_and_catalog_drift_block(self):
        with patch.object(source, "_main", side_effect=[self.old, "a" * 40]):
            with self.assertRaisesRegex(source.SetupSourceError, "moved during"):
                self.verify()
        original = source._main
        calls = 0
        def drift(repo):
            nonlocal calls
            calls += 1
            result = original(repo)
            if calls == 2:
                self.catalog.write_text(self.catalog.read_text() + "\n")
            return result
        with patch.object(source, "_main", side_effect=drift):
            with self.assertRaisesRegex(source.SetupSourceError, "catalog changed"):
                self.verify()

    def test_wrong_origin_and_invalid_catalog_block(self):
        self.git(self.repo, "remote", "set-url", "origin", "https://github.com/other/repository.git")
        with self.assertRaisesRegex(source.SetupSourceError, "remote does not match"):
            self.verify()
        self.catalog.write_text("[]")
        with self.assertRaisesRegex(source.SetupSourceError, "catalog unavailable or invalid"):
            self.verify()

    def test_new_procedure_retains_duplicate_key_rejection_for_old_catalogs(self):
        text = self.catalog.read_text().replace('"commit":', '"commit": "' + "a" * 40 + '", "commit":')
        self.catalog.write_text(text)
        with self.assertRaisesRegex(source.SetupSourceError, "catalog unavailable or invalid"):
            self.verify()

    def test_modes_do_not_accept_ambiguous_authority(self):
        common = dict(catalog=self.catalog, workspace_root=self.workspace, selected_commit=self.old)
        for kwargs in ({"selection_mode": "pinned"},
                       {"selection_mode": "main", "reviewed_commit": self.old},
                       {"selection_mode": "other"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(source.SetupSourceError):
                source.verify_source(**common, **kwargs)

    def test_CLI_valid_main_and_invalid_pin_have_controlled_JSON_results(self):
        args = ["--catalog", str(self.catalog), "--workspace-root", str(self.workspace),
                "--selected-commit", self.old]
        for mode, expected in (("main", 0), ("pinned", 1)):
            output = io.StringIO()
            with patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stdout(output):
                status = source.main([*args, "--selection-mode", mode])
            self.assertEqual(status, expected)
            self.assertEqual(json.loads(output.getvalue())["status"], "aligned" if expected == 0 else "blocked")

    def prepare_procedure(self, *, merged=True):
        package = Path(source.__file__).parent
        shutil.copytree(package, self.seed / "enforcement",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        procedure = self.commit()
        destination = "main" if merged else "procedure-not-merged"
        self.git(self.seed, "push", "-q", str(self.remote), f"HEAD:refs/heads/{destination}")
        return procedure

    def acquisition_blocks(self, procedure, reviewed_main):
        # Execute the owning guide's actual commands, not a second implementation.
        guide = (Path(source.__file__).parents[1] /
                 "cloud-environments/openai/README.md").read_text()
        section = guide.split("### Acquire a newer procedure from an older mount", 1)[1]
        blocks = re.findall(r"```bash\n(.*?)```", section, re.S)
        replacements = {
            "<selected-enforcement-checkout>": str(self.repo),
            "<reviewed-merged-full-commit>": procedure,
            "<separately-reviewed-current-main-full-commit>": reviewed_main,
            "<workspace-root>": str(self.workspace),
            "<generated-catalog-path>": str(self.catalog),
            "<saved-editor-full-commit>": self.old,
            "<reviewed-build-full-commit>": self.old,
        }
        for placeholder, value in replacements.items():
            blocks = [block.replace("'" + placeholder + "'", shlex.quote(value)) for block in blocks]
        return blocks

    def run_acquisition(self, blocks, env=None):
        return subprocess.run(["bash", "-c", "\n".join(blocks)],
                              cwd=self.root, env=env or self.env,
                              capture_output=True, text=True, timeout=30)

    def test_archived_procedure_runs_without_changing_old_mount_or_catalog(self):
        procedure = self.prepare_procedure()
        catalog_before = self.catalog.read_bytes()
        inode_before = self.repo.stat().st_ino
        # An inherited import path must not select another copy of enforcement,
        # even with Python's safe-path mode enabled. Nothing touches the mount.
        shadow = self.root / "shadow"
        (shadow / "enforcement").mkdir(parents=True)
        (shadow / "enforcement/__init__.py").write_text("")
        marker = self.root / "wrong-module-loaded"
        (shadow / "enforcement/cloud_setup_source.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(99)\n")
        scratch = self.root / "private exports"
        scratch.mkdir()
        env = {**self.env, "PYTHONSAFEPATH": "1", "PYTHONPATH": str(shadow), "TMPDIR": str(scratch)}
        result = self.run_acquisition(self.acquisition_blocks(procedure, procedure), env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["source_commit"], self.old)
        self.assertFalse(marker.exists())
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.old)
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(self.repo.stat().st_ino, inode_before)
        self.assertEqual(self.catalog.read_bytes(), catalog_before)

    def test_acquisition_stops_before_module_execution_for_unmerged_procedure(self):
        procedure = self.prepare_procedure(merged=False)
        scratch = self.root / "exports"
        scratch.mkdir()
        result = self.run_acquisition(self.acquisition_blocks(procedure, self.old),
                                      {**self.env, "TMPDIR": str(scratch)})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.git(self.repo, "rev-parse", f"{procedure}^{{commit}}"), procedure)
        self.assertTrue(any(scratch.glob("*/enforcement/cloud_setup_source.py")))
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.old)
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), "")

    def test_second_block_rejects_missing_or_empty_retained_shell_values(self):
        second = self.acquisition_blocks(self.old, self.old)[1]
        values = {"setup_checkout": str(self.repo), "setup_procedure_commit": self.old,
                  "setup_procedure_dir": str(self.root)}
        for name in values:
            for empty in (False, True):
                env = {**self.env, **values}
                if empty:
                    env[name] = ""
                else:
                    env.pop(name)
                with self.subTest(variable=name, empty=empty):
                    result = self.run_acquisition([second], env)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
