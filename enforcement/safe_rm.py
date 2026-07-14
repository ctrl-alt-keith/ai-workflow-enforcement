#!/usr/bin/env python3
"""Safely remove literal relative directory trees beneath the invocation cwd."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import os
from pathlib import Path
import re
import shutil
import stat
import sys


CONTROL_NAME = "codex-safe-rm"
CONTROL_VERSION = "1"
_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9._-]+\Z")


class SafeRmError(Exception):
    """A fail-closed validation or runtime-safety error."""


@dataclass
class PreparedTarget:
    operand: str
    parent_fd: int
    leaf: str
    exists: bool

    def close(self) -> None:
        os.close(self.parent_fd)


def require_runtime_safety() -> None:
    """Require the fd-relative and symlink-resistant primitives we rely on."""
    missing: list[str] = []
    if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
        missing.append("shutil.rmtree.avoids_symlink_attacks")
    if "dir_fd" not in inspect.signature(shutil.rmtree).parameters:
        missing.append("shutil.rmtree(dir_fd=...)")
    if os.open not in os.supports_dir_fd:
        missing.append("os.open(dir_fd=...)")
    if os.stat not in os.supports_dir_fd:
        missing.append("os.stat(dir_fd=...)")
    if not hasattr(os, "O_DIRECTORY"):
        missing.append("os.O_DIRECTORY")
    if not hasattr(os, "O_NOFOLLOW"):
        missing.append("os.O_NOFOLLOW")
    if missing:
        raise SafeRmError(
            "runtime cannot provide required fd-relative, symlink-resistant removal: "
            + ", ".join(missing)
        )


def validate_operand(operand: str, cwd: Path) -> tuple[str, ...]:
    """Validate one literal relative directory operand."""
    if not operand:
        raise SafeRmError("empty deletion target is not allowed")
    if os.path.isabs(operand):
        raise SafeRmError(f"absolute deletion target is not allowed: {operand!r}")

    components = tuple(operand.split("/"))
    for component in components:
        if not component:
            raise SafeRmError(f"empty path component is not allowed: {operand!r}")
        if component in {".", ".."}:
            raise SafeRmError(f"dot path component is not allowed: {operand!r}")
        if component.lower() == ".git":
            raise SafeRmError(f"repository metadata directory is never removable: {operand!r}")
        if _SAFE_COMPONENT.fullmatch(component) is None:
            raise SafeRmError(f"non-literal or unsupported path component: {operand!r}")

    resolved = (cwd / operand).resolve(strict=False)
    try:
        relative = resolved.relative_to(cwd)
    except ValueError as exc:
        raise SafeRmError(f"deletion target resolves outside the invocation cwd: {operand!r}") from exc
    if not relative.parts:
        raise SafeRmError(f"deletion target resolves to the invocation cwd: {operand!r}")
    return components


def remove_directories(operands: list[str], *, cwd: Path | None = None) -> None:
    """Validate all operands, then remove them without pathname or shell fallback."""
    require_runtime_safety()
    if not operands:
        raise SafeRmError("at least one directory operand is required")

    invocation_cwd = (cwd or Path.cwd()).resolve(strict=True)
    validated = [(operand, validate_operand(operand, invocation_cwd)) for operand in operands]
    _reject_overlapping_targets(validated)

    cwd_flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        cwd_flags |= os.O_CLOEXEC
    cwd_fd = os.open(invocation_cwd, cwd_flags)
    prepared: list[PreparedTarget] = []
    try:
        for operand, components in validated:
            prepared.append(_prepare_target(cwd_fd, operand, components))
        for target in prepared:
            if not target.exists:
                continue
            try:
                shutil.rmtree(target.leaf, dir_fd=target.parent_fd)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise SafeRmError(f"failed to remove {target.operand!r}: {exc}") from exc
    finally:
        for target in prepared:
            target.close()
        os.close(cwd_fd)


def _prepare_target(cwd_fd: int, operand: str, components: tuple[str, ...]) -> PreparedTarget:
    parent_fd = os.dup(cwd_fd)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        for component in components[:-1]:
            try:
                next_fd = os.open(component, flags, dir_fd=parent_fd)
            except FileNotFoundError:
                return PreparedTarget(operand, parent_fd, components[-1], False)
            except OSError as exc:
                raise SafeRmError(
                    f"parent component is not a real directory or cannot be opened safely: {operand!r}: {exc}"
                ) from exc
            os.close(parent_fd)
            parent_fd = next_fd

        leaf = components[-1]
        try:
            info = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return PreparedTarget(operand, parent_fd, leaf, False)
        if not stat.S_ISDIR(info.st_mode):
            raise SafeRmError(f"deletion target is not a real directory: {operand!r}")
        return PreparedTarget(operand, parent_fd, leaf, True)
    except Exception:
        os.close(parent_fd)
        raise


def _reject_overlapping_targets(validated: list[tuple[str, tuple[str, ...]]]) -> None:
    for index, (left_operand, left) in enumerate(validated):
        for right_operand, right in validated[index + 1 :]:
            common = min(len(left), len(right))
            if left[:common] == right[:common]:
                raise SafeRmError(
                    f"overlapping deletion targets are not allowed: {left_operand!r}, {right_operand!r}"
                )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--version"]:
        print(f"{CONTROL_NAME} {CONTROL_VERSION}")
        return 0
    if len(args) < 3 or args[:2] != ["-rf", "--"]:
        print(f"usage: {CONTROL_NAME} -rf -- TARGET [TARGET ...]", file=sys.stderr)
        return 2
    try:
        remove_directories(args[2:])
    except (OSError, SafeRmError) as exc:
        print(f"{CONTROL_NAME}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
