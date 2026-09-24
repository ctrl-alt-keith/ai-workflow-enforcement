"""Filesystem-scoped scanner for likely notes vs playbook drift."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
import json
import os
from pathlib import Path
import re
import subprocess

from .config import ScannerConfig
from .heuristics import (
    has_canonical_reference,
    normalized_headings,
    normalized_phrases,
    normalized_words,
    token_similarity,
)


SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".rst"}
WRAPPER_EXAMPLE_RE = re.compile(
    r"(?<![\w.-])(?:/(?:usr/)?bin/)?(?:(?:zsh|bash)\s+-lc|sh\s+-c)\s+"
    r"(?:--\s+)?(?P<quote>[`'\"])(?P<command>.*?)(?P=quote)",
    re.IGNORECASE,
)
ORDINARY_REPO_COMMAND_RE = re.compile(r"^(?:git|gh|make|python|python3|\./[\w./-]+|(?:scripts|bin|tools)/[\w./-]+)\b")
SHELL_SYNTAX_RE = re.compile(
    r"&&|\|\||[|<>;]|\$\(|\$\{|\$[A-Za-z_][A-Za-z0-9_]*|[`*?]"
    r"|\b(?:for|while|until|if|case|then|else|elif|fi|do|done|esac)\b",
    re.IGNORECASE,
)
ENV_ASSIGNMENT_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=[^\s]+(?:\s+|$))+")
@dataclass(frozen=True)
class Document:
    root: Path
    path: Path
    text: str

    @property
    def display_path(self) -> str:
        try:
            return self.path.relative_to(self.root).as_posix()
        except ValueError:
            return self.path.as_posix()


@dataclass(frozen=True)
class OverlapCandidate:
    note_path: Path
    playbook_path: Path
    repeated_headings: tuple[str, ...]
    repeated_phrases: tuple[str, ...]
    similarity: float
    has_canonical_reference: bool
    reasons: tuple[str, ...]

    @property
    def suggested_direction(self) -> str:
        if self.has_canonical_reference:
            return "Review staged note for stale duplicate wording; keep local evidence or context only."
        return "Consider replacing repeated guidance with a short canonical playbook reference."


@dataclass(frozen=True)
class AdvisoryFinding:
    kind: str
    path: Path
    line: int
    snippet: str
    reasons: tuple[str, ...]
    suggested_direction: str


@dataclass(frozen=True)
class ScanResult:
    candidates: tuple[OverlapCandidate, ...]
    notes_files_scanned: int
    playbook_files_scanned: int
    ignored_paths: tuple[Path, ...]
    advisory_findings: tuple[AdvisoryFinding, ...] = ()
    skipped_paths: tuple["SkippedPath", ...] = ()


@dataclass(frozen=True)
class SkippedPath:
    path: Path
    reason: str


def scan(config: ScannerConfig) -> ScanResult:
    _validate_config(config)
    notes = _load_documents(config.notes_roots, config.ignore_patterns)
    playbook = _load_documents(config.playbook_roots, config.ignore_patterns)
    workspace = _load_workspace_documents(config)

    candidates: list[OverlapCandidate] = []
    for note in notes.documents:
        note_headings = normalized_headings(note.text)
        note_phrases = normalized_phrases(note.text, config.min_phrase_words)
        note_has_reference = has_canonical_reference(note.text)
        for target in playbook.documents:
            target_headings = normalized_headings(target.text)
            target_phrases = normalized_phrases(target.text, config.min_phrase_words)

            repeated_headings = tuple(sorted(note_headings & target_headings))
            repeated_phrases = tuple(sorted((note_phrases & target_phrases).keys()))
            similarity = token_similarity(note.text, target.text)
            reasons = _candidate_reasons(
                repeated_headings,
                repeated_phrases,
                similarity,
                note_has_reference,
                config,
            )
            if not reasons:
                continue

            candidates.append(
                OverlapCandidate(
                    note_path=note.path,
                    playbook_path=target.path,
                    repeated_headings=repeated_headings,
                    repeated_phrases=repeated_phrases[:5],
                    similarity=similarity,
                    has_canonical_reference=note_has_reference,
                    reasons=tuple(reasons),
                )
            )

    candidates.sort(key=_candidate_sort_key)
    advisory_findings = _scan_advisory_findings(notes.documents, playbook.documents, workspace)
    return ScanResult(
        candidates=tuple(candidates[: config.max_candidates]),
        notes_files_scanned=len(notes.documents),
        playbook_files_scanned=len(playbook.documents),
        ignored_paths=_unique_paths(notes.ignored_paths + playbook.ignored_paths + workspace.ignored_paths),
        advisory_findings=tuple(advisory_findings[: config.max_candidates]),
        skipped_paths=_unique_skips(notes.skipped_paths + playbook.skipped_paths + workspace.skipped_paths),
    )


@dataclass(frozen=True)
class _DocumentLoad:
    documents: tuple[Document, ...]
    ignored_paths: tuple[Path, ...]
    skipped_paths: tuple[SkippedPath, ...]


@dataclass(frozen=True)
class _WorkspaceLoad:
    documents: tuple[Document, ...]
    agents_documents: tuple[Document, ...]
    ignored_paths: tuple[Path, ...]
    findings: tuple[AdvisoryFinding, ...]
    skipped_paths: tuple[SkippedPath, ...]


def _load_documents(roots: tuple[Path, ...], ignore_patterns: tuple[str, ...]) -> _DocumentLoad:
    documents: list[Document] = []
    ignored_paths: list[Path] = []
    skipped_paths: list[SkippedPath] = []
    for configured_root in roots:
        root = configured_root.resolve()
        files, ignored, traversal_skips = _iter_files(root, ignore_patterns)
        ignored_paths.extend(ignored)
        skipped_paths.extend(traversal_skips)
        for path in files:
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            document, skipped = _read_document(root, path)
            if document is not None:
                documents.append(document)
            if skipped is not None:
                skipped_paths.append(skipped)
    return _DocumentLoad(
        tuple(documents),
        _unique_paths(tuple(ignored_paths)),
        _unique_skips(tuple(skipped_paths)),
    )


def _read_document(root: Path, path: Path) -> tuple[Document | None, SkippedPath | None]:
    try:
        return Document(root=root, path=path, text=path.read_text(encoding="utf-8")), None
    except UnicodeDecodeError:
        return None, SkippedPath(path, "not valid UTF-8")
    except OSError as exc:
        return None, SkippedPath(path, f"unreadable: {exc.strerror or type(exc).__name__}")


def _load_workspace_documents(config: ScannerConfig) -> _WorkspaceLoad:
    if config.workspace_root is None:
        return _WorkspaceLoad((), (), (), (), ())
    inventory = _workspace_inventory(config)
    if inventory.findings and not inventory.repositories:
        return _WorkspaceLoad((), (), (), inventory.findings, ())

    documents: list[Document] = []
    agents_documents: list[Document] = []
    ignored_paths: list[Path] = []
    skipped_paths: list[SkippedPath] = []
    findings: list[AdvisoryFinding] = list(inventory.findings)

    for repo in inventory.repositories:
        repo_root = config.workspace_root / _repo_name(repo)
        if not repo_root.exists():
            findings.append(
                AdvisoryFinding(
                    kind="workspace_scope_missing_checkout",
                    path=repo_root,
                    line=1,
                    snippet=repo,
                    reasons=("repository is in active inventory but no local checkout was found",),
                    suggested_direction="Review whether the local workspace is complete before relying on cross-repo scan coverage.",
                )
            )
            continue
        loaded = _load_documents((repo_root,), config.ignore_patterns)
        documents.extend(loaded.documents)
        ignored_paths.extend(loaded.ignored_paths)
        skipped_paths.extend(loaded.skipped_paths)
        agents_documents.extend(document for document in loaded.documents if document.path.name == "AGENTS.md")

    return _WorkspaceLoad(
        tuple(documents),
        tuple(agents_documents),
        tuple(ignored_paths),
        tuple(findings),
        _unique_skips(tuple(skipped_paths)),
    )


@dataclass(frozen=True)
class _WorkspaceInventory:
    repositories: tuple[str, ...]
    findings: tuple[AdvisoryFinding, ...]


def _workspace_inventory(config: ScannerConfig) -> _WorkspaceInventory:
    explicit_repositories = tuple(config.organization_repositories)
    manifest_repositories = _read_workspace_manifest(config.workspace_manifest) if config.workspace_manifest else ()
    findings: list[AdvisoryFinding] = []

    if config.organization:
        organization_repositories, archived_repositories, finding = _enumerate_organization_repositories(
            config.organization, config.workspace_root
        )
        if finding is not None:
            return _WorkspaceInventory((), (finding,))
        active_repositories = organization_repositories
        if explicit_repositories:
            findings.extend(
                _inventory_mismatch_findings(
                    missing=_repositories_not_in(explicit_repositories, organization_repositories),
                    path=config.workspace_root,
                    reason="explicit repository inventory is not visible in organization enumeration",
                    direction="Reconcile the scoped repository list with visible GitHub organization inventory.",
                    archived_repositories=archived_repositories,
                )
            )
            active_repositories = _intersect_repositories(active_repositories, explicit_repositories)
        if manifest_repositories:
            findings.extend(
                _inventory_mismatch_findings(
                    missing=_repositories_not_in(manifest_repositories, organization_repositories),
                    path=config.workspace_manifest or config.workspace_root,
                    reason="caller-owned manifest repository is not visible in organization enumeration",
                    direction="Reconcile the caller-owned manifest with visible GitHub organization inventory.",
                    archived_repositories=archived_repositories,
                )
            )
            active_repositories = _intersect_repositories(active_repositories, manifest_repositories)
        return _WorkspaceInventory(active_repositories, tuple(findings))

    if explicit_repositories and manifest_repositories:
        findings.extend(
            _inventory_mismatch_findings(
                missing=_repositories_not_in(explicit_repositories, manifest_repositories),
                path=config.workspace_manifest or config.workspace_root,
                reason="explicit repository inventory is not present in caller-owned workspace manifest",
                direction="Reconcile explicit repository inventory with the configured caller-owned manifest.",
            )
        )
        return _WorkspaceInventory(_intersect_repositories(manifest_repositories, explicit_repositories), tuple(findings))

    if explicit_repositories:
        return _WorkspaceInventory(explicit_repositories, ())
    if manifest_repositories:
        return _WorkspaceInventory(manifest_repositories, ())

    return _WorkspaceInventory(
        (),
        (
            AdvisoryFinding(
                kind="workspace_scope_missing_inventory",
                path=config.workspace_root,
                line=1,
                snippet="workspace root configured without authoritative repository inventory",
                reasons=("raw local filesystem traversal is not authoritative workspace scope",),
                suggested_direction=(
                    "Configure a GitHub organization for enumeration, an explicit repository list, "
                    "or a caller-owned workspace manifest before scanning workspace scope."
                ),
            ),
        ),
    )


def _enumerate_organization_repositories(
    organization: str,
    workspace_root: Path,
) -> tuple[tuple[str, ...], tuple[str, ...], AdvisoryFinding | None]:
    try:
        completed = subprocess.run(
            ("gh", "repo", "list", organization, "--json", "nameWithOwner,isArchived", "--limit", "1000"),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return (), (), _inventory_unavailable_finding(organization, workspace_root, str(exc))
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"gh exited {completed.returncode}"
        return (), (), _inventory_unavailable_finding(organization, workspace_root, detail)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return (), (), _inventory_unavailable_finding(organization, workspace_root, f"invalid gh JSON: {exc}")
    if not isinstance(payload, list):
        return (), (), _inventory_unavailable_finding(organization, workspace_root, "gh JSON was not a repository list")

    repositories: list[str] = []
    archived_repositories: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get("nameWithOwner")
        if not isinstance(name, str) or not name.strip():
            continue
        if item.get("isArchived") is True:
            archived_repositories.append(name.strip())
        else:
            repositories.append(name.strip())
    return tuple(repositories), tuple(archived_repositories), None


def _inventory_unavailable_finding(organization: str, workspace_root: Path, detail: str) -> AdvisoryFinding:
    return AdvisoryFinding(
        kind="workspace_scope_inventory_unavailable",
        path=workspace_root,
        line=1,
        snippet=organization,
        reasons=(f"GitHub organization repository enumeration failed: {detail}",),
        suggested_direction=(
            "Retry with GitHub CLI access, or provide an explicit repository list "
            "or caller-owned manifest for a scoped advisory scan."
        ),
    )


def _inventory_mismatch_findings(
    *,
    missing: tuple[str, ...],
    path: Path,
    reason: str,
    direction: str,
    archived_repositories: tuple[str, ...] = (),
) -> tuple[AdvisoryFinding, ...]:
    archived_names = _normalized_repository_names(archived_repositories)
    findings: list[AdvisoryFinding] = []
    for repo in missing:
        if _repo_name(repo) in archived_names:
            findings.append(
                AdvisoryFinding(
                    kind="workspace_scope_archived_repository",
                    path=path,
                    line=1,
                    snippet=repo,
                    reasons=("repository is archived and outside active workspace scope",),
                    suggested_direction="Remove it from active scope or keep it in a separately archived-record inventory.",
                )
            )
            continue
        findings.append(
            AdvisoryFinding(
                kind="workspace_scope_inventory_mismatch",
                path=path,
                line=1,
                snippet=repo,
                reasons=(reason,),
                suggested_direction=direction,
            )
        )
    return tuple(findings)


def _repositories_not_in(repositories: tuple[str, ...], inventory: tuple[str, ...]) -> tuple[str, ...]:
    inventory_names = _normalized_repository_names(inventory)
    return tuple(sorted(repo for repo in repositories if _repo_name(repo) not in inventory_names))


def _intersect_repositories(repositories: tuple[str, ...], allowed: tuple[str, ...]) -> tuple[str, ...]:
    allowed_names = _normalized_repository_names(allowed)
    return tuple(repo for repo in repositories if _repo_name(repo) in allowed_names)


def _read_workspace_manifest(path: Path) -> tuple[str, ...]:
    repositories: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        repositories.append(cleaned)
    return tuple(repositories)


def _normalized_repository_names(repositories: tuple[str, ...]) -> set[str]:
    return {_repo_name(repo) for repo in repositories}


def _repo_name(repository: str) -> str:
    return repository.rstrip("/").split("/")[-1]


def _iter_files(
    root: Path, ignore_patterns: tuple[str, ...]
) -> tuple[tuple[Path, ...], tuple[Path, ...], tuple[SkippedPath, ...]]:
    if root.is_file():
        resolved = root.resolve()
        if _is_ignored(resolved, resolved, ignore_patterns):
            return (), (resolved,), ()
        return (resolved,), (), ()
    files: list[Path] = []
    ignored_paths: list[Path] = []
    skipped_paths: list[SkippedPath] = []

    def record_walk_error(error: OSError) -> None:
        path = Path(error.filename) if error.filename else root
        skipped_paths.append(SkippedPath(path, f"unreadable: {error.strerror or type(error).__name__}"))

    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False, onerror=record_walk_error):
        current_path = Path(current)
        retained_directories: list[str] = []
        for name in sorted(directory_names):
            path = (current_path / name).resolve()
            if not _is_within(path, root):
                continue
            if _is_ignored_directory(path, root, ignore_patterns):
                ignored_paths.append(path)
                continue
            retained_directories.append(name)
        directory_names[:] = retained_directories

        for name in sorted(file_names):
            path = (current_path / name).resolve()
            if not path.is_file() or not _is_within(path, root):
                continue
            if _is_ignored(path, root, ignore_patterns):
                ignored_paths.append(path)
                continue
            files.append(path)
    return tuple(files), tuple(ignored_paths), _unique_skips(tuple(skipped_paths))


def _is_ignored_directory(path: Path, root: Path, ignore_patterns: tuple[str, ...]) -> bool:
    return _is_ignored(path, root, ignore_patterns) or _is_ignored(
        path / "__scanner_ignored_subtree__",
        root,
        ignore_patterns,
    )


def _is_ignored(path: Path, root: Path, ignore_patterns: tuple[str, ...]) -> bool:
    rel = path.relative_to(root).as_posix()
    return any(fnmatch(rel, pattern) or fnmatch(path.name, pattern) for pattern in ignore_patterns)


def _candidate_reasons(
    repeated_headings: tuple[str, ...],
    repeated_phrases: tuple[str, ...],
    similarity: float,
    has_reference: bool,
    config: ScannerConfig,
) -> list[str]:
    reasons: list[str] = []
    if len(repeated_headings) >= config.min_heading_matches:
        reasons.append("repeated heading")
    if len(repeated_phrases) >= config.min_phrase_matches:
        reasons.append("repeated normalized phrase")
    if similarity >= config.similarity_threshold:
        reasons.append("token similarity threshold")
    if reasons and not has_reference:
        reasons.append("missing canonical reference")
    return reasons


def _candidate_sort_key(candidate: OverlapCandidate) -> tuple[float, int, int, str]:
    return (
        -candidate.similarity,
        -len(candidate.repeated_phrases),
        -len(candidate.repeated_headings),
        candidate.note_path.as_posix(),
    )


def _scan_advisory_findings(
    notes: tuple[Document, ...],
    playbook: tuple[Document, ...],
    workspace: _WorkspaceLoad,
) -> list[AdvisoryFinding]:
    findings: list[AdvisoryFinding] = list(workspace.findings)
    scanned_documents = _unique_documents(notes + workspace.documents)

    for document in workspace.agents_documents:
        findings.extend(_scan_agents_alignment(document, playbook))

    for document in scanned_documents:
        findings.extend(_scan_shell_wrapper_examples(document))

    findings.sort(key=lambda finding: (finding.path.as_posix(), finding.line, finding.kind))
    return findings


def _unique_documents(documents: tuple[Document, ...]) -> tuple[Document, ...]:
    seen: set[Path] = set()
    unique: list[Document] = []
    for document in documents:
        if document.path in seen:
            continue
        seen.add(document.path)
        unique.append(document)
    return tuple(unique)


def _unique_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    return tuple(dict.fromkeys(paths))


def _unique_skips(skips: tuple[SkippedPath, ...]) -> tuple[SkippedPath, ...]:
    return tuple(dict.fromkeys(skips))


def _scan_agents_alignment(document: Document, playbook: tuple[Document, ...]) -> list[AdvisoryFinding]:
    findings: list[AdvisoryFinding] = []
    if not has_canonical_reference(document.text):
        findings.append(
            _finding(
                "agents_missing_canonical_playbook_reference",
                document,
                "Canonical playbook reference not found",
                ("AGENTS.md should identify the playbook as the reusable workflow source",),
                "Add a short reference to ai-workflow-playbook or docs/start-here.md.",
            )
        )

    playbook_phrases = _playbook_phrase_set(playbook)
    agents_phrases = set(normalized_phrases(document.text, 8))
    repeated_phrases = agents_phrases & playbook_phrases
    if len(repeated_phrases) >= 12 and len(normalized_words(document.text)) >= 800:
        findings.append(
            _finding(
                "agents_large_canonical_duplication",
                document,
                "Large playbook overlap in AGENTS.md",
                ("AGENTS.md repeats many canonical playbook phrases",),
                "Keep thin operational reinforcement, but move broad reusable workflow text back to the playbook.",
            )
        )
    return findings


def scan_enrolled_instruction(path: Path, text: str) -> tuple[AdvisoryFinding, ...]:
    """Reuse existing AGENTS and command-example advisories for one enrolled file.

    The local agent reviewer consumes only finding kinds; snippets and raw
    private paths remain on this process's local side of its reporting boundary.
    """
    document = Document(path.parent, path, text)
    findings = _scan_shell_wrapper_examples(document)
    if path.name == "AGENTS.md":
        findings.extend(_scan_agents_alignment(document, ()))
    return tuple(findings)


def _playbook_phrase_set(playbook: tuple[Document, ...]) -> set[str]:
    phrases: set[str] = set()
    for document in playbook:
        phrases.update(normalized_phrases(document.text, 8))
    return phrases


def _scan_shell_wrapper_examples(document: Document) -> list[AdvisoryFinding]:
    findings: list[AdvisoryFinding] = []
    for line_number, line in _iter_lines(document.text):
        for match in WRAPPER_EXAMPLE_RE.finditer(line):
            command = match.group("command").strip()
            if not ORDINARY_REPO_COMMAND_RE.search(command):
                continue
            if _requires_shell_syntax(command):
                continue
            findings.append(
                AdvisoryFinding(
                    kind="ordinary_repo_command_shell_wrapper_example",
                    path=document.path,
                    line=line_number,
                    snippet=line.strip(),
                    reasons=(f"wrapper shell example contains ordinary repo command: {command}",),
                    suggested_direction="Use direct argv form in examples unless shell syntax is actually required.",
                )
            )
    return findings


def _requires_shell_syntax(command: str) -> bool:
    stripped = command.strip()
    return bool(ENV_ASSIGNMENT_RE.search(stripped) or SHELL_SYNTAX_RE.search(stripped))


def _finding(
    kind: str,
    document: Document,
    snippet: str,
    reasons: tuple[str, ...],
    suggested_direction: str,
) -> AdvisoryFinding:
    return AdvisoryFinding(
        kind=kind,
        path=document.path,
        line=1,
        snippet=snippet,
        reasons=reasons,
        suggested_direction=suggested_direction,
    )


def _iter_lines(text: str) -> tuple[tuple[int, str], ...]:
    return tuple(enumerate(text.splitlines(), start=1))


def _validate_config(config: ScannerConfig) -> None:
    if not config.notes_roots:
        raise ValueError("at least one notes root is required")
    if not config.playbook_roots:
        raise ValueError("at least one playbook root is required")
    for root in config.notes_roots + config.playbook_roots:
        if not root.exists():
            raise ValueError(f"configured root does not exist: {root}")
    if config.workspace_root is not None and not config.workspace_root.exists():
        raise ValueError(f"configured workspace root does not exist: {config.workspace_root}")
    if config.workspace_manifest is not None and not config.workspace_manifest.exists():
        raise ValueError(f"configured workspace manifest does not exist: {config.workspace_manifest}")
    if config.min_phrase_words < 3:
        raise ValueError("min_phrase_words must be at least 3")
    if config.min_phrase_matches < 1:
        raise ValueError("min_phrase_matches must be at least 1")
    if config.min_heading_matches < 1:
        raise ValueError("min_heading_matches must be at least 1")
    if not 0 <= config.similarity_threshold <= 1:
        raise ValueError("similarity_threshold must be between 0 and 1")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
