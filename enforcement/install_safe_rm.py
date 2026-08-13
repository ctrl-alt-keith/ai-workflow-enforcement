"""Install, verify, and uninstall the reviewed codex-safe-rm control."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

from .safe_rm import CONTROL_NAME, CONTROL_VERSION


SCHEMA_VERSION = 1
SOURCE_REPOSITORY = "ctrl-alt-keith/ai-workflow-enforcement"
SOURCE_RELATIVE_PATH = "enforcement/safe_rm.py"
DEFAULT_DESTINATION = Path.home() / ".local" / "bin" / CONTROL_NAME


class InstallError(Exception):
    """An installation ownership, provenance, or integrity error."""


def metadata_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.install.json")


def install(
    destination: Path,
    *,
    source: Path | None = None,
    repo_root: Path | None = None,
    force: bool = False,
    allow_dirty: bool = False,
) -> dict[str, object]:
    source_path, root = _source_context(source, repo_root)
    source_bytes, commit, dirty = _capture_install_source(
        source_path,
        root,
        allow_dirty=allow_dirty,
    )
    source_digest = _sha256(source_bytes)
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "control": CONTROL_NAME,
        "control_version": CONTROL_VERSION,
        "source_repository": SOURCE_REPOSITORY,
        "source_path": SOURCE_RELATIVE_PATH,
        "source_commit": commit,
        "source_dirty": dirty,
        "source_sha256": source_digest,
        "installed_sha256": source_digest,
    }

    destination.parent.mkdir(parents=True, exist_ok=True)
    record = metadata_path(destination)
    if destination.exists() or destination.is_symlink() or record.exists():
        try:
            _verify_owned_pair(destination, record)
        except InstallError:
            if not force:
                raise InstallError(
                    f"refusing to replace unrecognized or inconsistent destination {destination}; use FORCE=1 explicitly"
                )

    executable_tmp = _write_temp(destination.parent, source_bytes, 0o755)
    metadata_bytes = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
    metadata_tmp = _write_temp(destination.parent, metadata_bytes, 0o644)
    try:
        os.replace(executable_tmp, destination)
        os.replace(metadata_tmp, record)
        _fsync_directory(destination.parent)
    finally:
        _unlink_if_present(executable_tmp)
        _unlink_if_present(metadata_tmp)
    return metadata


def verify(
    destination: Path,
    *,
    source: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, object]:
    source_path, root = _source_context(source, repo_root)
    record = metadata_path(destination)
    metadata = _verify_owned_pair(destination, record)
    installed_digest = _sha256(destination.read_bytes())
    source_digest = _sha256(source_path.read_bytes())
    if installed_digest != source_digest or metadata.get("source_sha256") != source_digest:
        raise InstallError("installed executable does not match the current reviewed enforcement source")
    commit, dirty = _source_provenance(root)
    if metadata.get("source_commit") != commit or metadata.get("source_dirty") != dirty:
        raise InstallError("installed provenance does not match the current enforcement checkout")
    mode = destination.stat().st_mode
    if not stat.S_ISREG(mode) or mode & 0o111 == 0:
        raise InstallError("installed control is not a regular executable file")
    result = subprocess.run(
        (str(destination), "--version"),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    expected = f"{CONTROL_NAME} {CONTROL_VERSION}"
    if result.returncode != 0 or result.stdout.strip() != expected or result.stderr:
        raise InstallError("installed control version check failed")
    return metadata


def uninstall(destination: Path, *, force: bool = False) -> None:
    record = metadata_path(destination)
    if not destination.exists() and not destination.is_symlink() and not record.exists():
        return
    try:
        _verify_owned_pair(destination, record)
    except InstallError:
        if not force:
            raise InstallError(
                f"refusing to remove unrecognized or inconsistent destination {destination}; use FORCE=1 explicitly"
            )
    _unlink_if_present(destination)
    _unlink_if_present(record)
    _fsync_directory(destination.parent)


def _source_context(source: Path | None, repo_root: Path | None) -> tuple[Path, Path]:
    root = (repo_root or Path(__file__).resolve().parent.parent).resolve()
    source_path = (source or root / SOURCE_RELATIVE_PATH).resolve()
    if not source_path.is_file():
        raise InstallError(f"reviewed source is missing: {source_path}")
    return source_path, root


def _source_provenance(repo_root: Path) -> tuple[str, bool]:
    commit = _git_text(repo_root, "rev-parse", "HEAD")
    status = _git_status(repo_root)
    return commit.strip(), bool(status)


def _capture_install_source(
    source_path: Path,
    repo_root: Path,
    *,
    allow_dirty: bool,
) -> tuple[bytes, str, bool]:
    """Capture bytes whose digest and dirty state are tied to a pinned commit."""
    commit = _git_text(repo_root, "rev-parse", "HEAD").strip()
    committed_bytes = _git_bytes(repo_root, "show", f"{commit}:{SOURCE_RELATIVE_PATH}")
    if not allow_dirty:
        if _git_status(repo_root):
            raise InstallError(
                "source checkout is dirty; rerun with ALLOW_DIRTY=1 only after explicit review"
            )
        # Do not read the working-tree source on the clean path. Even if the
        # checkout changes after the status check, the installed bytes are the
        # immutable blob addressed by the recorded commit.
        return committed_bytes, commit, False

    working_bytes = source_path.read_bytes()
    status = _git_status(repo_root)
    final_commit = _git_text(repo_root, "rev-parse", "HEAD").strip()
    if final_commit != commit:
        raise InstallError("source HEAD changed while dirty installation bytes were captured")
    dirty = bool(status) or working_bytes != committed_bytes
    return working_bytes, commit, dirty


def _git_status(repo_root: Path) -> str:
    return _git_text(repo_root, "status", "--porcelain=v1", "--untracked-files=all")


def _git_text(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise InstallError(f"cannot determine source provenance: git {' '.join(args)}: {detail}")
    return result.stdout


def _git_bytes(repo_root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ("git", *args),
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip() or f"exit {result.returncode}"
        raise InstallError(f"cannot read committed source: git {' '.join(args)}: {detail}")
    return result.stdout


def _verify_owned_pair(destination: Path, record: Path) -> dict[str, object]:
    if not destination.exists() or destination.is_symlink():
        raise InstallError("installed executable is missing or is a symlink")
    if not record.is_file() or record.is_symlink():
        raise InstallError("installation metadata is missing or is a symlink")
    try:
        metadata = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallError("installation metadata is unreadable or invalid") from exc
    expected_identity = {
        "schema_version": SCHEMA_VERSION,
        "control": CONTROL_NAME,
        "source_repository": SOURCE_REPOSITORY,
        "source_path": SOURCE_RELATIVE_PATH,
    }
    for key, expected in expected_identity.items():
        if metadata.get(key) != expected:
            raise InstallError(f"installation metadata ownership mismatch: {key}")
    if metadata.get("control_version") != CONTROL_VERSION:
        raise InstallError("installation metadata version mismatch")
    digest = _sha256(destination.read_bytes())
    if metadata.get("installed_sha256") != digest or metadata.get("source_sha256") != digest:
        raise InstallError("installed executable and metadata digest mismatch")
    if not isinstance(metadata.get("source_commit"), str) or not metadata["source_commit"]:
        raise InstallError("installation metadata source commit is missing")
    if not isinstance(metadata.get("source_dirty"), bool):
        raise InstallError("installation metadata dirty-source provenance is invalid")
    return metadata


def _write_temp(directory: Path, content: bytes, mode: int) -> Path:
    fd, raw_path = tempfile.mkstemp(prefix=f".{CONTROL_NAME}.", dir=directory)
    path = Path(raw_path)
    fd_open = True
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            fd_open = False
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if fd_open:
            os.close(fd)
        _unlink_if_present(path)
        raise
    return path


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(directory, flags)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        os.close(fd)


def _unlink_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the reviewed codex-safe-rm installation.")
    actions = parser.add_subparsers(dest="action", required=True)

    install_parser = actions.add_parser("install")
    install_parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    install_parser.add_argument("--force", action="store_true", help="Replace an existing destination.")
    install_parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Install from a dirty source checkout and record source_dirty=true.",
    )

    verify_parser = actions.add_parser("verify")
    verify_parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)

    uninstall_parser = actions.add_parser("uninstall")
    uninstall_parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    uninstall_parser.add_argument("--force", action="store_true", help="Remove an existing destination.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "install":
            metadata = install(args.destination, force=args.force, allow_dirty=args.allow_dirty)
            print(
                f"installed {CONTROL_NAME} {metadata['control_version']} at {args.destination} "
                f"from {metadata['source_commit']} (source_dirty={str(metadata['source_dirty']).lower()})"
            )
        elif args.action == "verify":
            metadata = verify(args.destination)
            print(
                f"verified {CONTROL_NAME} {metadata['control_version']} at {args.destination} "
                f"sha256={metadata['installed_sha256']}"
            )
        else:
            uninstall(args.destination, force=args.force)
            print(f"uninstalled {CONTROL_NAME} from {args.destination}")
    except (InstallError, OSError) as exc:
        print(f"{CONTROL_NAME} installer: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
