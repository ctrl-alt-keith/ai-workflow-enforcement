"""Read-only alignment checks for main-tracking and deliberately pinned Setup builds."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from .safe_refresh_repos import load_startup_catalog, _verify_catalog_locator


REPOSITORY = "ctrl-alt-keith/ai-workflow-enforcement"
SHA = re.compile(r"[0-9a-f]{40}")


class SetupSourceError(ValueError):
    """A nonsecret, fail-closed source precondition failure."""


def _git(repo: Path, *args: str) -> str:
    try:
        process = subprocess.run(
            ["git", "--no-replace-objects", "-c", "core.hooksPath=/dev/null", *args],
            cwd=repo, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SetupSourceError("Git source check unavailable or timed out") from None
    if process.returncode:
        raise SetupSourceError("Git source check failed; provider output suppressed")
    return process.stdout.strip()


def _main(repo: Path) -> str:
    fields = _git(repo, "ls-remote", "--exit-code", "origin", "refs/heads/main").split()
    if len(fields) != 2 or not SHA.fullmatch(fields[0]) or fields[1] != "refs/heads/main":
        raise SetupSourceError("hosted main did not resolve to one exact commit")
    return fields[0]


def verify_source(
    *, catalog: Path, workspace_root: Path, selection_mode: str,
    selected_commit: str, reviewed_commit: str | None = None,
    reviewed_main: str | None = None,
) -> dict[str, object]:
    """Check the enforcement entry; saved-editor identity/review remain caller evidence."""
    if not SHA.fullmatch(selected_commit):
        raise SetupSourceError("saved selection must be an exact commit")
    if selection_mode == "pinned":
        if not reviewed_commit or not reviewed_main or not all(
            SHA.fullmatch(value) for value in (reviewed_commit, reviewed_main)
        ):
            raise SetupSourceError("pinned builds require exact reviewed build and main commits")
        if selected_commit != reviewed_commit:
            raise SetupSourceError("saved selection differs from the reviewed build commit")
    elif selection_mode != "main" or reviewed_commit is not None or reviewed_main is not None:
        raise SetupSourceError("main builds do not accept pinned-build review overrides")

    try:
        catalog_before = catalog.read_bytes()
        targets = load_startup_catalog(catalog, workspace_root).repositories
    except (OSError, ValueError, UnicodeError):
        raise SetupSourceError("startup catalog unavailable or invalid") from None
    target = next((entry for entry in targets if entry.name == REPOSITORY), None)
    if target is None:
        raise SetupSourceError("enforcement repository absent from the generated catalog")
    if target.catalog_commit.lower() != selected_commit:
        raise SetupSourceError("saved selection and generated catalog differ")
    repo = target.path
    if repo.is_symlink() or not repo.is_dir():
        raise SetupSourceError("catalog checkout must be an ordinary directory")
    if Path(_git(repo, "rev-parse", "--show-toplevel")).resolve() != repo.resolve():
        raise SetupSourceError("catalog mount is not the checkout root")
    if _verify_catalog_locator(target):
        raise SetupSourceError("catalog checkout remote does not match the selected repository")

    def aligned_checkout() -> None:
        if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
            raise SetupSourceError("checkout contains changes; preserve them and stop")
        if _git(repo, "rev-parse", "HEAD") != selected_commit:
            raise SetupSourceError("generated catalog and checkout commit differ")

    aligned_checkout()
    current_main = _main(repo)
    if selection_mode == "main" and selected_commit != current_main:
        raise SetupSourceError("main-tracking build is behind freshly observed hosted main")
    if selection_mode == "pinned" and reviewed_main != current_main:
        raise SetupSourceError("hosted main changed from the separately reviewed comparison target")
    # The caller fetches this exact object without checking it out. No fetch or
    # automatic advancement belongs in this read-only guard.
    _git(repo, "cat-file", "-e", f"{current_main}^{{commit}}")
    try:
        _git(repo, "merge-base", "--is-ancestor", selected_commit, current_main)
    except SetupSourceError:
        raise SetupSourceError("build commit is not verified as merged into current main") from None
    changes = _git(repo, "diff", "--name-status", "--no-renames", selected_commit, current_main, "--")
    if _main(repo) != current_main:
        raise SetupSourceError("hosted main moved during source verification")
    aligned_checkout()
    if catalog.read_bytes() != catalog_before:
        raise SetupSourceError("generated catalog changed during source verification")
    return {
        "status": "aligned", "repository": REPOSITORY,
        "selection_mode": selection_mode, "source_commit": selected_commit,
        "current_main": current_main, "reviewed_main": reviewed_main,
        "main_changes": changes.splitlines() if changes else [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--selection-mode", choices=("main", "pinned"), required=True)
    parser.add_argument("--selected-commit", required=True,
                        help="exact enforcement commit read back from the saved editor")
    parser.add_argument("--reviewed-commit", help="explicitly reviewed pinned build commit")
    parser.add_argument("--reviewed-main", help="separately reviewed current-main comparison target")
    args = parser.parse_args(argv)
    try:
        result = verify_source(**vars(args))
    except (SetupSourceError, OSError) as error:
        # SetupSourceError contains controlled messages only; OS/provider data
        # is deliberately excluded from the JSON error surface.
        result = {"status": "blocked", "error": str(error) if isinstance(error, SetupSourceError)
                  else "source file unavailable during verification"}
        print(json.dumps(result, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
