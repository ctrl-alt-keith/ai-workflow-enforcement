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
    normalize_text,
    token_similarity,
)


SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".rst"}
AUTHORITY_SEGMENT_SPLIT_RE = re.compile(r"[.;:!?]|\b(?:but|however|though|although|while)\b", re.IGNORECASE)
AUTHORITY_CLAIM_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\b(?:this|the|current|runtime)\s+(?:runtime\s+)?(?:document|prompt|file|note|artifact|surface|playbook)\s+"
        r"(?:is|are|becomes?|remains?|serves as|acts as)\s+(?:the\s+)?(?:canonical|authoritative|definitive)\b",
        r"\b(?:this|the|current|runtime)\s+(?:document|prompt|file|note|artifact|surface)\s+governs\b",
        r"\b(?:this|the|current|runtime)\s+(?:runtime\s+)?(?:document|prompt|file|note|artifact|surface|playbook)\s+"
        r"(?:is|becomes?|serves as|acts as)\s+(?:the\s+)?source\s+of\s+truth\b",
        r"\b(?:treat|use)\s+this(?:\s+(?:document|prompt|file|note|artifact|surface))?\s+as\s+"
        r"(?:the\s+)?source\s+of\s+truth\b",
        r"\b(?:treat|use|retain|present)\s+this\b.{0,80}\bas\s+(?:the\s+)?canonical\s+"
        r"(?:workflow(?:\s+reference)?|source|reference|guidance)\b",
    )
)
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
WRITABLE_ROOTS_EXHAUSTIVE_RE = re.compile(
    r"\bwritable_roots\b[^.\n]{0,120}\b(?:all|complete|exhaustive|only|sole|solely|entire)\b"
    r"|"
    r"\b(?:all|complete|exhaustive|only|sole|solely|entire)\b[^.\n]{0,120}\bwritable_roots\b",
    re.IGNORECASE,
)
WRITABLE_ROOTS_CORRECTIVE_RE = re.compile(
    r"\bwritable\s+roots\b.{0,100}\b(?:not\s+exhaustive|not\s+solely|not\s+just|not\s+the\s+complete|not\s+the\s+full)\b"
    r"|"
    r"\bdo\s+not\s+assume\b.{0,80}\bwritable\s+roots\b"
    r"|"
    r"\bwritable\s+roots\b.{0,100}\b(?:may|can)\s+also\s+include\b"
    r"|"
    r"\beffective\s+writable\s+roots?\b.{0,80}\b(?:may|can)\s+also\s+include\b",
    re.IGNORECASE,
)

RUNTIME_SURFACE_PARTS = {
    "generated",
    "runtime",
    "runtime-artifacts",
    "snapshots",
    "snapshot",
    "staging",
    "staged",
    "custom-instructions",
    "copied-custom-instructions",
}

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
        if "frozen historical evidence context" in self.reasons:
            return (
                "Keep the overlap visible; verify the canonical owner reference and frozen record context, "
                "then preserve the historical application when it is intentional."
            )
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
        note_is_frozen_history = _is_frozen_historical_evidence(note.text)

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
                note_is_frozen_history,
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
    advisory_findings = _scan_advisory_findings(config, notes.documents, playbook.documents, workspace)
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
        files, ignored = _iter_files(root, ignore_patterns)
        ignored_paths.extend(ignored)
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
        organization_repositories, finding = _enumerate_organization_repositories(config.organization, config.workspace_root)
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
) -> tuple[tuple[str, ...], AdvisoryFinding | None]:
    try:
        completed = subprocess.run(
            ("gh", "repo", "list", organization, "--json", "nameWithOwner,isArchived", "--limit", "1000"),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return (), _inventory_unavailable_finding(organization, workspace_root, str(exc))
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"gh exited {completed.returncode}"
        return (), _inventory_unavailable_finding(organization, workspace_root, detail)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return (), _inventory_unavailable_finding(organization, workspace_root, f"invalid gh JSON: {exc}")
    if not isinstance(payload, list):
        return (), _inventory_unavailable_finding(organization, workspace_root, "gh JSON was not a repository list")

    repositories: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get("nameWithOwner")
        if isinstance(name, str) and name.strip() and item.get("isArchived") is not True:
            repositories.append(name.strip())
    return tuple(repositories), None


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
) -> tuple[AdvisoryFinding, ...]:
    return tuple(
        AdvisoryFinding(
            kind="workspace_scope_inventory_mismatch",
            path=path,
            line=1,
            snippet=repo,
            reasons=(reason,),
            suggested_direction=direction,
        )
        for repo in missing
    )


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


def _iter_files(root: Path, ignore_patterns: tuple[str, ...]) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    if root.is_file():
        resolved = root.resolve()
        if _is_ignored(resolved, resolved, ignore_patterns):
            return (), (resolved,)
        return (resolved,), ()
    files: list[Path] = []
    ignored_paths: list[Path] = []
    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
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
    return tuple(files), tuple(ignored_paths)


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
    is_frozen_history: bool,
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
    if reasons and is_frozen_history:
        reasons.append("frozen historical evidence context")
    return reasons


def _is_frozen_historical_evidence(text: str) -> bool:
    normalized = normalize_text(text)
    return (
        "frozen" in normalized
        and any(term in normalized for term in ("proposal", "review", "retrospective", "evidence", "artifact"))
        and any(term in normalized for term in ("historical", "identity", "digest", "record", "boundary", "reviewed"))
    )


def _candidate_sort_key(candidate: OverlapCandidate) -> tuple[float, int, int, str]:
    return (
        -candidate.similarity,
        -len(candidate.repeated_phrases),
        -len(candidate.repeated_headings),
        candidate.note_path.as_posix(),
    )


def _scan_advisory_findings(
    config: ScannerConfig,
    notes: tuple[Document, ...],
    playbook: tuple[Document, ...],
    workspace: _WorkspaceLoad,
) -> list[AdvisoryFinding]:
    findings: list[AdvisoryFinding] = list(workspace.findings)
    scanned_documents = _unique_documents(notes + workspace.documents)

    for document in workspace.agents_documents:
        findings.extend(_scan_agents_alignment(document, playbook))

    for document in scanned_documents:
        findings.extend(_scan_sandbox_writable_roots_claims(document))
        if _is_noncanonical_surface(document, config):
            findings.extend(_scan_authority_language(document))
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


def _playbook_phrase_set(playbook: tuple[Document, ...]) -> set[str]:
    phrases: set[str] = set()
    for document in playbook:
        phrases.update(normalized_phrases(document.text, 8))
    return phrases


def _scan_authority_language(document: Document) -> list[AdvisoryFinding]:
    if _is_frozen_historical_evidence(document.text):
        return []

    findings: list[AdvisoryFinding] = []
    for line_number, line in _iter_lines(document.text):
        normalized_line = normalize_text(line)
        if not _is_direct_authority_claim(line, normalized_line, is_heading=line.lstrip().startswith("#")):
            continue
        findings.append(
            AdvisoryFinding(
                kind="noncanonical_authority_language",
                path=document.path,
                line=line_number,
                snippet=line.strip(),
                reasons=("noncanonical surface uses authority-language wording",),
                suggested_direction="Replace authority language with a playbook reference or label the surface as noncanonical evidence.",
            )
        )
    return findings


def _is_direct_authority_claim(line: str, normalized_line: str, *, is_heading: bool) -> bool:
    if "?" in line:
        return False
    if is_heading and "source of truth" in normalized_line:
        return False
    segments = _authority_segments(line)
    return any(_segment_has_authority_claim(segment) for segment in segments)


def _authority_segments(line: str) -> tuple[str, ...]:
    segments = tuple(
        normalize_text(segment)
        for segment in AUTHORITY_SEGMENT_SPLIT_RE.split(line)
        if normalize_text(segment)
    )
    return segments or (normalize_text(line),)


def _segment_has_authority_claim(normalized_segment: str) -> bool:
    if _negates_authority_claim(normalized_segment):
        return False
    return any(pattern.search(normalized_segment) for pattern in AUTHORITY_CLAIM_PATTERNS)


def _negates_authority_claim(normalized_segment: str) -> bool:
    return bool(
        re.search(
            r"\b(?:not|noncanonical|non canonical|never|do not|does not|must not|should not)\b"
            r".{0,80}\b(?:canonical|authoritative|definitive|source of truth)\b",
            normalized_segment,
        )
    )


def _scan_shell_wrapper_examples(document: Document) -> list[AdvisoryFinding]:
    findings: list[AdvisoryFinding] = []
    lines = _iter_lines(document.text)
    for index, (line_number, line) in enumerate(lines):
        for match in WRAPPER_EXAMPLE_RE.finditer(line):
            command = match.group("command").strip()
            if not ORDINARY_REPO_COMMAND_RE.search(command):
                continue
            if _requires_shell_syntax(command):
                continue
            context = _nearby_context(lines, index, radius=2)
            previous_line = lines[index - 1][1] if index else ""
            if _is_negative_shell_wrapper_example(f"{previous_line} {line}"):
                continue
            if _is_explanatory_shell_wrapper_discussion(document, context):
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


def _scan_sandbox_writable_roots_claims(document: Document) -> list[AdvisoryFinding]:
    findings: list[AdvisoryFinding] = []
    lines = _iter_lines(document.text)
    for index, (line_number, line) in enumerate(lines):
        if not WRITABLE_ROOTS_EXHAUSTIVE_RE.search(line):
            continue
        context = _nearby_context(lines, index, radius=2)
        if _has_writable_roots_exhaustive_exception(context):
            continue
        findings.append(
            AdvisoryFinding(
                kind="sandbox_writable_roots_exhaustive_claim",
                path=document.path,
                line=line_number,
                snippet=line.strip(),
                reasons=("Codex effective writable roots can include implicit project and temp roots",),
                suggested_direction=(
                    "Describe `writable_roots` as explicit config roots and mention effective-policy inspection plus implicit root exclusions."
                ),
            )
        )
    return findings


def _has_writable_roots_exhaustive_exception(context: str) -> bool:
    normalized = normalize_text(context)
    if "imply writable roots is the exhaustive" in normalized:
        return True
    return bool(WRITABLE_ROOTS_CORRECTIVE_RE.search(normalized))


def _is_negative_shell_wrapper_example(line: str) -> bool:
    normalized = normalize_text(line)
    if re.search(r"\bnot\s+`?(?:/(?:usr/)?bin/)?(?:(?:zsh|bash)\s+-lc|sh\s+-c)\b", line, re.IGNORECASE):
        return True
    negative_markers = (
        "bad example",
        "do not",
        "don't",
        "incorrect",
        "must not",
        "never",
        "not normal",
        "not recommended",
        "rather than",
        "should not",
        "wrong",
        "avoid",
    )
    return any(marker in normalized for marker in negative_markers)


def _requires_shell_syntax(command: str) -> bool:
    stripped = command.strip()
    return bool(ENV_ASSIGNMENT_RE.search(stripped) or SHELL_SYNTAX_RE.search(stripped))


def _is_explanatory_shell_wrapper_discussion(document: Document, context: str) -> bool:
    normalized = normalize_text(context)
    explanatory_context_markers = (
        "evidence",
        "observed failure",
        "observed failures",
        "observed example",
        "discussion",
        "explanatory",
        "descriptive",
        "source material",
        "not guidance",
        "not policy",
        "not an instruction",
    )
    if any(marker in normalized for marker in explanatory_context_markers):
        return True
    parts = {part.lower() for part in document.path.parts}
    local_policy_context_markers = (
        "no rule layer allow",
        "static rule matching",
        "hook allow intent",
        "hook payload command",
        "static policy",
        "runtime behavior",
        "fact status",
    )
    if (
        {"runtime-artifacts", "codex-local-policy"} <= parts
        and any(marker in normalized for marker in local_policy_context_markers)
    ):
        return True
    if (
        {"runtime-artifacts", "codex-local-policy"} <= parts
        and (
            "codex execpolicy check" in normalized
            or "hook" in normalized
            or "matrix" in normalized
            or "payload" in normalized
            or "runtime" in normalized
            or (
                "git worktree remove" in normalized
                and ("zsh lc" in normalized or "bash lc" in normalized)
            )
        )
    ):
        return True
    return bool(
        {"operational-evidence", "workflow-patterns"} & parts
        and "wrapper" in normalized
        and ("drift" in normalized or "failure pattern" in normalized)
    )


def _is_noncanonical_surface(document: Document, config: ScannerConfig) -> bool:
    if any(_is_within(document.path, root.resolve()) for root in config.notes_roots):
        return True
    parts = {part.lower() for part in document.path.parts}
    return bool(parts & RUNTIME_SURFACE_PARTS)


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


def _nearby_context(lines: tuple[tuple[int, str], ...], index: int, *, radius: int) -> str:
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return "\n".join(line for _, line in lines[start:end])


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
