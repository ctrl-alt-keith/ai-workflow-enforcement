"""Read-only alignment checks for main-tracking and deliberately pinned Setup builds."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from .safe_refresh_repos import RepoTarget, load_startup_catalog, _verify_catalog_locator


REPOSITORY = "ctrl-alt-keith/ai-workflow-enforcement"
MAIN_BUILD_MOUNTS = {
    REPOSITORY: "ai-workflow-enforcement",
    "ctrl-alt-keith/secrets-lifecycle": "secrets-lifecycle",
}
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


def _catalog(catalog: Path, workspace_root: Path) -> tuple[bytes, dict[str, RepoTarget]]:
    try:
        contents = catalog.read_bytes()
        targets = load_startup_catalog(catalog, workspace_root).repositories
    except (OSError, ValueError, UnicodeError):
        raise SetupSourceError("startup catalog unavailable or invalid") from None
    return contents, {entry.name: entry for entry in targets}


def _checked_mount(target: RepoTarget) -> Path:
    repo = target.path
    if repo.is_symlink() or not repo.is_dir():
        raise SetupSourceError("catalog checkout must be an ordinary directory")
    if Path(_git(repo, "rev-parse", "--show-toplevel")).resolve() != repo.resolve():
        raise SetupSourceError("catalog mount is not the checkout root")
    if _verify_catalog_locator(target):
        raise SetupSourceError("catalog checkout remote does not match the selected repository")
    return repo


def _clean_head(repo: Path) -> str:
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise SetupSourceError("checkout contains changes; preserve them and stop")
    return _git(repo, "rev-parse", "HEAD")


def verify_main_build(*, catalog: Path, workspace_root: Path) -> dict[str, object]:
    """Qualify the two build sources separately from the restored catalog commits."""
    catalog_before, targets = _catalog(catalog, workspace_root)
    sources: dict[str, dict[str, str]] = {}
    for name, mount in MAIN_BUILD_MOUNTS.items():
        target = targets.get(name)
        if target is None or target.path != workspace_root / mount:
            raise SetupSourceError(f"required build mount missing or unexpected: {name}")
        repo = _checked_mount(target)
        source_commit = _clean_head(repo)
        if source_commit != _main(repo):
            raise SetupSourceError(f"clean build checkout does not match hosted main: {name}")
        baseline = target.catalog_commit.lower()
        try:
            if _git(repo, "cat-file", "-t", baseline) != "commit":
                raise SetupSourceError("invalid baseline commit")
            _git(repo, "merge-base", "--is-ancestor", baseline, source_commit)
        except SetupSourceError:
            raise SetupSourceError(f"restored catalog commit is not an ancestor of build main: {name}") from None
        sources[name] = {"catalog_commit": baseline, "source_commit": source_commit}

    for name, evidence in sources.items():
        repo = _checked_mount(targets[name])
        if _main(repo) != evidence["source_commit"] or _clean_head(repo) != evidence["source_commit"]:
            raise SetupSourceError(f"build source changed during verification: {name}")
    if catalog.read_bytes() != catalog_before:
        raise SetupSourceError("generated catalog changed during source verification")
    return {"status": "aligned", "selection_mode": "main", "sources": sources,
            "catalog_sha256": hashlib.sha256(catalog_before).hexdigest()}


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

    catalog_before, targets = _catalog(catalog, workspace_root)
    target = targets.get(REPOSITORY)
    if target is None:
        raise SetupSourceError("enforcement repository absent from the generated catalog")
    if target.catalog_commit.lower() != selected_commit:
        raise SetupSourceError("saved selection and generated catalog differ")
    repo = _checked_mount(target)

    def aligned_checkout() -> None:
        if _clean_head(repo) != selected_commit:
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
    parser.add_argument("--selected-commit",
                        help="use the single-source check with an exact saved selection")
    parser.add_argument("--reviewed-commit", help="explicitly reviewed pinned build commit")
    parser.add_argument("--reviewed-main", help="separately reviewed current-main comparison target")
    args = parser.parse_args(argv)
    try:
        if args.selected_commit is None:
            if args.selection_mode != "main" or args.reviewed_commit is not None or args.reviewed_main is not None:
                raise SetupSourceError("only main builds may omit the exact saved selection")
            result = verify_main_build(catalog=args.catalog, workspace_root=args.workspace_root)
        else:
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
