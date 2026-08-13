from __future__ import annotations

from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from enforcement import install_safe_rm
from enforcement.install_safe_rm import InstallError, build_parser, install, metadata_path, uninstall, verify


class InstallSafeRmTests(unittest.TestCase):
    def test_cli_rejects_options_that_do_not_apply_to_the_action(self) -> None:
        parser = build_parser()
        for args in (
            ("verify", "--force"),
            ("verify", "--allow-dirty"),
            ("uninstall", "--allow-dirty"),
        ):
            with self.subTest(args=args), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(args)

    def test_clean_install_is_executable_recorded_and_verifiable(self) -> None:
        with _installation_fixture() as fixture:
            metadata = install(fixture.destination, source=fixture.source, repo_root=fixture.repo)
            self.assertTrue(fixture.destination.is_file())
            self.assertNotEqual(0, fixture.destination.stat().st_mode & stat.S_IXUSR)
            self.assertEqual(fixture.source.read_bytes(), fixture.destination.read_bytes())
            self.assertFalse(metadata["source_dirty"])
            self.assertEqual(metadata, verify(fixture.destination, source=fixture.source, repo_root=fixture.repo))

    def test_clean_install_uses_pinned_git_blob_if_worktree_changes_after_status(self) -> None:
        with _installation_fixture() as fixture:
            committed_bytes = fixture.source.read_bytes()
            real_status = install_safe_rm._git_status

            def clean_status_then_modify_source(repo_root: Path) -> str:
                status = real_status(repo_root)
                self.assertEqual("", status)
                fixture.source.write_text("#!/usr/bin/env python3\nprint('uncommitted')\n", encoding="utf-8")
                return status

            with mock.patch.object(
                install_safe_rm,
                "_git_status",
                side_effect=clean_status_then_modify_source,
            ):
                metadata = install(
                    fixture.destination,
                    source=fixture.source,
                    repo_root=fixture.repo,
                )

            self.assertNotEqual(committed_bytes, fixture.source.read_bytes())
            self.assertEqual(committed_bytes, fixture.destination.read_bytes())
            self.assertFalse(metadata["source_dirty"])

    def test_install_is_idempotent_for_owned_consistent_destination(self) -> None:
        with _installation_fixture() as fixture:
            first = install(fixture.destination, source=fixture.source, repo_root=fixture.repo)
            second = install(fixture.destination, source=fixture.source, repo_root=fixture.repo)
            self.assertEqual(first, second)

    def test_dirty_installation_is_refused_even_with_force(self) -> None:
        with _installation_fixture() as fixture:
            (fixture.repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(InstallError, "ALLOW_DIRTY=1"):
                install(fixture.destination, source=fixture.source, repo_root=fixture.repo, force=True)

    def test_dirty_installation_requires_separate_opt_in_and_records_provenance(self) -> None:
        with _installation_fixture() as fixture:
            (fixture.repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            metadata = install(
                fixture.destination, source=fixture.source, repo_root=fixture.repo, allow_dirty=True,
            )
            self.assertTrue(metadata["source_dirty"])
            recorded = json.loads(metadata_path(fixture.destination).read_text(encoding="utf-8"))
            self.assertTrue(recorded["source_dirty"])

    def test_dirty_installation_hashes_the_exact_worktree_snapshot(self) -> None:
        with _installation_fixture() as fixture:
            dirty_bytes = b"#!/usr/bin/env python3\nprint('dirty snapshot')\n"
            fixture.source.write_bytes(dirty_bytes)
            metadata = install(
                fixture.destination,
                source=fixture.source,
                repo_root=fixture.repo,
                allow_dirty=True,
            )
            self.assertEqual(dirty_bytes, fixture.destination.read_bytes())
            self.assertTrue(metadata["source_dirty"])
            self.assertEqual(metadata["source_sha256"], metadata["installed_sha256"])

    def test_unrelated_destination_is_refused(self) -> None:
        with _installation_fixture() as fixture:
            fixture.destination.parent.mkdir(parents=True)
            fixture.destination.write_text("unrelated\n", encoding="utf-8")
            with self.assertRaisesRegex(InstallError, "FORCE=1"):
                install(fixture.destination, source=fixture.source, repo_root=fixture.repo)
            self.assertEqual("unrelated\n", fixture.destination.read_text(encoding="utf-8"))

    def test_force_replaces_destination_but_does_not_change_source_policy(self) -> None:
        with _installation_fixture() as fixture:
            fixture.destination.parent.mkdir(parents=True)
            fixture.destination.write_text("unrelated\n", encoding="utf-8")
            metadata = install(
                fixture.destination, source=fixture.source, repo_root=fixture.repo, force=True,
            )
            self.assertFalse(metadata["source_dirty"])
            self.assertEqual(fixture.source.read_bytes(), fixture.destination.read_bytes())

    def test_verify_fails_on_executable_drift(self) -> None:
        with _installation_fixture() as fixture:
            install(fixture.destination, source=fixture.source, repo_root=fixture.repo)
            fixture.destination.write_text("modified\n", encoding="utf-8")
            with self.assertRaisesRegex(InstallError, "digest mismatch"):
                verify(fixture.destination, source=fixture.source, repo_root=fixture.repo)

    def test_verify_fails_on_metadata_drift_or_missing_pair_member(self) -> None:
        with _installation_fixture() as fixture:
            install(fixture.destination, source=fixture.source, repo_root=fixture.repo)
            record = metadata_path(fixture.destination)
            metadata = json.loads(record.read_text(encoding="utf-8"))
            metadata["control_version"] = "different"
            record.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(InstallError, "version mismatch"):
                verify(fixture.destination, source=fixture.source, repo_root=fixture.repo)
            record.unlink()
            with self.assertRaisesRegex(InstallError, "metadata is missing"):
                verify(fixture.destination, source=fixture.source, repo_root=fixture.repo)

    def test_verify_fails_when_executable_is_missing(self) -> None:
        with _installation_fixture() as fixture:
            install(fixture.destination, source=fixture.source, repo_root=fixture.repo)
            fixture.destination.unlink()
            with self.assertRaisesRegex(InstallError, "executable is missing"):
                verify(fixture.destination, source=fixture.source, repo_root=fixture.repo)

    def test_verify_fails_on_provenance_drift(self) -> None:
        with _installation_fixture() as fixture:
            install(fixture.destination, source=fixture.source, repo_root=fixture.repo)
            record = metadata_path(fixture.destination)
            metadata = json.loads(record.read_text(encoding="utf-8"))
            metadata["source_commit"] = "0" * 40
            record.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(InstallError, "provenance"):
                verify(fixture.destination, source=fixture.source, repo_root=fixture.repo)

    def test_uninstall_requires_owned_consistent_pair(self) -> None:
        with _installation_fixture() as fixture:
            install(fixture.destination, source=fixture.source, repo_root=fixture.repo)
            uninstall(fixture.destination)
            self.assertFalse(fixture.destination.exists())
            self.assertFalse(metadata_path(fixture.destination).exists())
            uninstall(fixture.destination)
            fixture.destination.parent.mkdir(parents=True, exist_ok=True)
            fixture.destination.write_text("unrelated\n", encoding="utf-8")
            with self.assertRaisesRegex(InstallError, "FORCE=1"):
                uninstall(fixture.destination)
            self.assertTrue(fixture.destination.exists())
            uninstall(fixture.destination, force=True)
            self.assertFalse(fixture.destination.exists())


class _Fixture:
    def __init__(self, root: Path):
        self.repo = root / "repo"
        self.destination = root / "bin" / "codex-safe-rm"
        self.source = self.repo / "enforcement" / "safe_rm.py"

    def prepare(self) -> "_Fixture":
        self.repo.mkdir()
        (self.repo / "enforcement").mkdir()
        real_source = Path(__file__).resolve().parents[1] / "enforcement" / "safe_rm.py"
        self.source.write_bytes(real_source.read_bytes())
        _git(self.repo, "init")
        _git(self.repo, "config", "user.email", "tests@example.com")
        _git(self.repo, "config", "user.name", "Tests")
        _git(self.repo, "config", "commit.gpgsign", "false")
        _git(self.repo, "add", "enforcement/safe_rm.py")
        _git(self.repo, "commit", "-m", "Add reviewed source")
        return self


class _InstallationContext:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()

    def __enter__(self) -> _Fixture:
        return _Fixture(Path(self.temp.name)).prepare()

    def __exit__(self, *args: object) -> None:
        self.temp.cleanup()


def _installation_fixture() -> _InstallationContext:
    return _InstallationContext()


def _git(cwd: Path, *args: str) -> None:
    result = subprocess.run(
        ("git", *args), cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr or result.stdout}")


if __name__ == "__main__":
    unittest.main()
