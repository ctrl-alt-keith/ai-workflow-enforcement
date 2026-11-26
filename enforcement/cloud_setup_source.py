"""Read-only alignment checks for Cloud Setup build and restoration sources."""

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
BRANCH_STARTUP_FILES = (
    "cloud-environments/openai/workspace-AGENTS.md",
    "enforcement/cloud_task_start.py",
)


class SetupSourceError(ValueError):
    """A nonsecret, fail-closed source precondition failure."""


def _git_bytes(repo: Path, *args: str) -> bytes:
    try:
        process = subprocess.run(
            ["git", "--no-replace-objects", "-c", "core.hooksPath=/dev/null", *args],
            cwd=repo, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SetupSourceError("Git source check unavailable or timed out") from None
    if process.returncode:
        raise SetupSourceError("Git source check failed; provider output suppressed")
    return process.stdout


def _git(repo: Path, *args: str) -> str:
    return _git_bytes(repo, *args).decode(errors="replace").strip()


def _branch_tip(repo: Path, branch: str) -> str:
    ref = f"refs/heads/{branch}"
    try:
        fields = _git(repo, "ls-remote", "--exit-code", "origin", ref).split()
    except SetupSourceError:
        raise SetupSourceError("hosted build branch unavailable from origin") from None
    if len(fields) != 2 or not SHA.fullmatch(fields[0]) or fields[1] != ref:
        raise SetupSourceError("hosted branch did not resolve to one exact commit")
    return fields[0]


def _main(repo: Path) -> str:
    return _branch_tip(repo, "main")


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


def verify_main_build(*, catalog: Path, workspace_root: Path, build_branch: str | None = None,
                      reviewed_branch_tip: str | None = None) -> dict[str, object]:
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

    branch_commit = None
    if build_branch is not None:
        if (build_branch == "main" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", build_branch)
                or not reviewed_branch_tip or not SHA.fullmatch(reviewed_branch_tip)):
            raise SetupSourceError("branch builds require a non-main branch name and its reviewed full tip")
        repo = _checked_mount(targets[REPOSITORY])
        try:
            _git(repo, "check-ref-format", f"refs/heads/{build_branch}")
        except SetupSourceError:
            raise SetupSourceError("invalid hosted build branch name") from None
        branch_commit = _branch_tip(repo, build_branch)
        if branch_commit != reviewed_branch_tip:
            raise SetupSourceError("hosted build branch differs from the reviewed tip")
        try:
            _git(repo, "cat-file", "-e", f"{branch_commit}^{{commit}}")
        except SetupSourceError:
            raise SetupSourceError("reviewed branch tip must be fetched before building") from None
        main = sources[REPOSITORY]["source_commit"]
        for path in BRANCH_STARTUP_FILES:
            main_entry = _git(repo, "ls-tree", main, "--", path)
            branch_entry = _git(repo, "ls-tree", branch_commit, "--", path)
            if not main_entry or not main_entry.startswith(("100644 blob ", "100755 blob ")) or branch_entry != main_entry:
                raise SetupSourceError(f"branch changes the main-tracking startup contract: {path}")
        for path in ("cloud-environments/openai/install.sh", "enforcement/op_runtime.py"):
            if not _git(repo, "ls-tree", branch_commit, "--", path).startswith(("100644 blob ", "100755 blob ")):
                raise SetupSourceError(f"branch build input is missing or not a regular file: {path}")
    elif reviewed_branch_tip is not None:
        raise SetupSourceError("reviewed branch tip requires a named branch build")

    for name, evidence in sources.items():
        repo = _checked_mount(targets[name])
        if _main(repo) != evidence["source_commit"] or _clean_head(repo) != evidence["source_commit"]:
            raise SetupSourceError(f"build source changed during verification: {name}")
    if branch_commit is not None:
        repo = _checked_mount(targets[REPOSITORY])
        if _branch_tip(repo, build_branch) != branch_commit:
            raise SetupSourceError("hosted build branch moved during source verification")
        sources[REPOSITORY]["main_commit"] = sources[REPOSITORY]["source_commit"]
        sources[REPOSITORY]["source_commit"] = branch_commit
    result = {"status": "aligned", "selection_mode": "main", "build_mode": "main", "sources": sources,
              "catalog_sha256": hashlib.sha256(catalog_before).hexdigest()}
    if branch_commit is not None:
        result.update(build_mode="branch", build_ref=f"refs/heads/{build_branch}")
        result["artifact_sha256"] = {
            installed: hashlib.sha256(_git_bytes(repo, "cat-file", "blob", f"{branch_commit}:{path}")).hexdigest()
            for installed, path in (
                ("op-runtime.py", "enforcement/op_runtime.py"),
                ("cloud-task-start.py", "enforcement/cloud_task_start.py"),
            )
        }
    if catalog.read_bytes() != catalog_before:
        raise SetupSourceError("generated catalog changed during source verification")
    return result


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
    parser.add_argument("--build-branch", help="per-build Enforcement branch with main-tracking checkouts")
    parser.add_argument("--reviewed-branch-tip", help="reviewed exact tip for this branch build")
    args = parser.parse_args(argv)
    try:
        if args.selected_commit is None:
            if args.selection_mode != "main" or args.reviewed_commit is not None or args.reviewed_main is not None:
                raise SetupSourceError("only main builds may omit the exact saved selection")
            result = verify_main_build(catalog=args.catalog, workspace_root=args.workspace_root,
                                       build_branch=args.build_branch, reviewed_branch_tip=args.reviewed_branch_tip)
        else:
            if args.build_branch is not None or args.reviewed_branch_tip is not None:
                raise SetupSourceError("branch builds use a main-tracking saved selection")
            result = verify_source(catalog=args.catalog, workspace_root=args.workspace_root,
                                   selection_mode=args.selection_mode, selected_commit=args.selected_commit,
                                   reviewed_commit=args.reviewed_commit, reviewed_main=args.reviewed_main)
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
