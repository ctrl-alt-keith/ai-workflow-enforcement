"""Filesystem-scoped scanner for likely notes vs playbook drift."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
import json
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
AUTHORITY_TERMS_RE = re.compile(
    r"\b(source of truth|canonical|authoritative|definitive|primary operational reference|official workflow definition|governs?|defines?)\b",
    re.IGNORECASE,
)
AUTHORITY_SEGMENT_SPLIT_RE = re.compile(r"[.;:!?]|\b(?:but|however|though|although|while)\b", re.IGNORECASE)
AUTHORITY_DISCLAIMER_PHRASES = (
    "noncanonical",
    "non canonical",
    "not a canonical",
    "not canonical",
    "not authoritative",
    "not the authoritative source",
    "not an authoritative source",
    "not become an implicit authority",
    "not as canonical repository readiness guidance",
    "not be treated",
    "not treat as canonical",
    "not treated as canonical",
    "do not make content canonical",
    "do not treat",
    "does not make",
    "rather than an independent canonical source",
)
AUTHORITY_EXTERNAL_SOURCE_PHRASES = (
    "authoritative docs",
    "authoritative official",
    "ai workflow playbook as canonical",
    "ai workflow playbook is the canonical",
    "absorbed into canonical guidance",
    "authoritative retrievable state",
    "authoritative sources are available",
    "can be authoritative for behavior claims",
    "canonical local validation",
    "canonical local blocking validation",
    "canonical guidance already owns",
    "canonical guidance can",
    "canonical guidance lives in ai workflow playbook",
    "canonical guidance remains",
    "canonical lifecycle",
    "canonical playbook",
    "canonical reference links",
    "canonical reusable workflow policy source",
    "canonical validation",
    "github issues and prs remain authoritative",
    "implemented behavior",
    "live retrievable authoritative state",
    "make check is the canonical",
    "official documentation",
    "playbook update task updates canonical guidance",
    "release notes and changelogs",
    "remain authoritative for implementation work",
    "repository source of truth",
    "repository state",
    "risks acting like a second source of truth",
    "when authoritative sources are available",
)
AUTHORITY_DISCUSSION_PHRASES = (
    "appear authoritative",
    "authoritative source scanner",
    "authoritative source rollout",
    "authoritative source work",
    "can look",
    "canonical false",
    "canonical elsewhere",
    "canonical source checked",
    "canonical source confusion",
    "canonical source evidence",
    "canonical source use",
    "canonical sources",
    "audit findings distinguish canonical guidance",
    "checking whether audit findings distinguish canonical guidance",
    "feel authoritative",
    "hardened authoritative source",
    "identify authority boundary risks",
    "manifest fields authoritative",
    "non authoritative source exception",
    "not yet fully promoted into canonical guidance",
    "promotion task updates canonical guidance",
    "phrase source of truth",
    "playbook repository and canonical source",
    "repo owns it as the canonical",
    "scratch artifacts are disposable",
    "shadow canonical risk",
    "shadow authoritative surfaces",
    "source of truth language",
    "source of truth wording",
    "treating ai workflow incubator as canonical",
    "sufficiently authoritative",
    "using canonical or source of truth language",
    "what would become canonical if promoted",
)
AUTHORITY_CLAIM_PHRASES = (
    "authoritative guidance",
    "canonical true",
    "definitive guidance",
    "definitive instruction",
    "definitive instructions",
    "official workflow definition",
    "primary operational reference",
    "source of truth",
    "source is canonical",
    "surface is canonical",
    "artifact is canonical",
    "note is canonical",
    "file is canonical",
)
AUTHORITY_CLAIM_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\b(?:is|are|as|becomes?|serves as|acts as)\s+(?:the\s+)?(?:canonical|authoritative|definitive)\b",
        r"\b(?:canonical|authoritative|definitive)\s+(?:source|guidance|instruction|instructions|reference|definition)\b",
        r"\b(?:primary|official)\s+(?:operational\s+)?(?:reference|workflow definition)\b",
        r"\b(?:this|the|current|runtime)\s+(?:document|prompt|file|note|artifact|surface)\s+governs\b",
        r"\b(?:governs|defines)\s+(?:the\s+)?(?:workflow|operational workflow|instructions)\b",
        r"\btreat\s+this\s+as\s+(?:the\s+)?source\s+of\s+truth\b",
        r"\buse\s+this\s+as\s+(?:the\s+)?canonical\s+workflow\b",
    )
)
STRONG_RULE_RE = re.compile(r"\b(must|never|do not|required|prohibit(?:ed|s)?|only)\b", re.IGNORECASE)
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
WORKTREE_CREATION_RE = re.compile(
    r"\bgit\s+worktree\s+add\b"
    r"|"
    r"\b(?:create|creating|add|adding|set\s+up|setting\s+up|spin\s+up|spinning\s+up|make|making)\s+"
    r"(?:a\s+|an\s+|the\s+)?(?:new\s+|fresh\s+|repo-local\s+|isolated\s+|clean\s+){0,3}worktrees?\b"
    r"|"
    r"\bworktree\s+creation\b"
    r"|"
    r"\b(?:use|using)\s+worktrees?\s+for\s+(?:parallel\s+)?(?:same[-\s]+repo|same\s+repository|parallel)\b"
    r"|"
    r"\b(?:one\s+)?worktrees?\s+per\s+(?:issue|task|lane|arc)\b",
    re.IGNORECASE,
)
BRANCH_ONLY_IMPLEMENTATION_RE = re.compile(
    r"\bnormal\s+branches?\b[^.\n]{0,160}\b(?:fine|ok|okay|acceptable|sufficient|enough|safe)\b"
    r"[^.\n]{0,160}\b(?:single|sequential|single[-\s]+task|implementation|repo[-\s]+changing)\b"
    r"|"
    r"\b(?:single|sequential|single[-\s]+task|implementation|repo[-\s]+changing)\b"
    r"[^.\n]{0,160}\bnormal\s+branches?\b[^.\n]{0,160}\b(?:fine|ok|okay|acceptable|sufficient|enough|safe)\b"
    r"|"
    r"\bworktrees?\b[^.\n]{0,120}\b(?:only|just)\b[^.\n]{0,80}\b(?:parallel|isolation)\b"
    r"|"
    r"\b(?:only|just)\b[^.\n]{0,80}\bworktrees?\b[^.\n]{0,120}\b(?:parallel|isolation)\b",
    re.IGNORECASE,
)
ISOLATED_SURFACE_CREATION_RE = re.compile(
    r"\b(?:create|creating|add|adding|set\s+up|setting\s+up|spin\s+up|spinning\s+up|make|making)\b"
    r"[^.\n]{0,100}\b(?:isolated|isolation)\b"
    r"[^.\n]{0,80}\b(?:execution\s+)?(?:surfaces?|workspaces?|checkouts?|containers?)\b",
    re.IGNORECASE,
)
WORKTREE_SELECTION_SIGNAL_RE = re.compile(
    r"\bgit\s+worktree\s+list\b"
    r"|"
    r"\b(?:inspect|check|list|review)\w*\b[^.\n]{0,100}\b\.worktrees/?\b"
    r"|"
    r"\b\.worktrees/?\b[^.\n]{0,100}\b(?:inspect|check|list|review|existing)\w*\b"
    r"|"
    r"\breuse\w*\b[^.\n]{0,100}\b(?:existing\s+)?(?:clean\s+)?(?:repo-local\s+)?worktree\b"
    r"|"
    r"\bexisting\s+(?:clean\s+)?(?:repo-local\s+)?worktree\b"
    r"|"
    r"\bone\s+dedicated\s+repo[-\s]+local\s+worktree\b"
    r"|"
    r"\b(?:select|choose|reuse|create|creating|use|using|set\s+up|setting\s+up)\w*\b"
    r"[^.\n]{0,160}\brepo-local\s+(?:git\s+)?worktree\b"
    r"|"
    r"\bchoose\w*\b[^.\n]{0,160}\b(?:existing\s+worktree|new\s+worktree)\b"
    r"|"
    r"\bexisting\s+worktree\b[^.\n]{0,160}\bnew\s+worktree\b"
    r"|"
    r"\bnew\s+worktree\b[^.\n]{0,160}\bexisting\s+worktree\b",
    re.IGNORECASE,
)
NEGATIVE_WORKTREE_GUIDANCE_RE = re.compile(
    r"\b(?:do\s+not|don't|avoid|warning|warns?|unnecessary|churn|overweight|underweight|"
    r"fail(?:s|ed|ing)?\s+to|missing|without|ignore(?:s|d)?\s+existing|no\s+new\s+worktree)\b",
    re.IGNORECASE,
)
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

RULE_TOPICS = (
    (
        "complete output",
        re.compile(
            r"\b("
            r"complete|self-contained|directly usable|drop-in|full updated artifact|ready to paste|"
            r"copy/paste-safe|copy-paste-safe|copyable|partial edits?"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "shell wrapper restrictions",
        re.compile(r"\b(zsh -lc|bash -lc|sh -c|wrapper shells?|shell wrappers?)\b", re.IGNORECASE),
    ),
    (
        "partial prompt prohibitions",
        re.compile(
            r"\b("
            r"partial prompts?|continuation fragments?|change x to y|diff-style|delta-only|"
            r"targeted edits?"
            r")\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role boundary rules",
        re.compile(r"\b(interaction mode|implementation mode|review/audit|orchestration|role boundaries?)\b", re.IGNORECASE),
    ),
)


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
    advisory_findings = _scan_advisory_findings(config, notes.documents, playbook.documents, workspace)
    return ScanResult(
        candidates=tuple(candidates[: config.max_candidates]),
        notes_files_scanned=len(notes.documents),
        playbook_files_scanned=len(playbook.documents),
        ignored_paths=tuple(notes.ignored_paths + playbook.ignored_paths + workspace.ignored_paths),
        advisory_findings=tuple(advisory_findings[: config.max_candidates]),
    )


@dataclass(frozen=True)
class _DocumentLoad:
    documents: tuple[Document, ...]
    ignored_paths: tuple[Path, ...]


@dataclass(frozen=True)
class _WorkspaceLoad:
    documents: tuple[Document, ...]
    agents_documents: tuple[Document, ...]
    ignored_paths: tuple[Path, ...]
    findings: tuple[AdvisoryFinding, ...]


def _load_documents(roots: tuple[Path, ...], ignore_patterns: tuple[str, ...]) -> _DocumentLoad:
    documents: list[Document] = []
    ignored_paths: list[Path] = []
    for configured_root in roots:
        root = configured_root.resolve()
        for path in _iter_files(root):
            if _is_ignored(path, root, ignore_patterns):
                ignored_paths.append(path)
                continue
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            documents.append(Document(root=root, path=path, text=path.read_text(encoding="utf-8")))
    return _DocumentLoad(tuple(documents), tuple(ignored_paths))


def _load_workspace_documents(config: ScannerConfig) -> _WorkspaceLoad:
    if config.workspace_root is None:
        return _WorkspaceLoad((), (), (), ())
    inventory = _workspace_inventory(config)
    if inventory.findings and not inventory.repositories:
        return _WorkspaceLoad((), (), (), inventory.findings)

    documents: list[Document] = []
    agents_documents: list[Document] = []
    ignored_paths: list[Path] = []
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
        agents_path = repo_root / "AGENTS.md"
        if agents_path.exists():
            agents_documents.append(Document(root=repo_root, path=agents_path, text=agents_path.read_text(encoding="utf-8")))

    return _WorkspaceLoad(tuple(documents), tuple(agents_documents), tuple(ignored_paths), tuple(findings))


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
            ("gh", "repo", "list", organization, "--json", "nameWithOwner", "--limit", "1000"),
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
        if isinstance(name, str) and name.strip():
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


def _iter_files(root: Path) -> tuple[Path, ...]:
    if root.is_file():
        return (root.resolve(),)
    files: list[Path] = []
    for path in root.rglob("*"):
        resolved = path.resolve()
        if not resolved.is_file():
            continue
        if not _is_within(resolved, root):
            continue
        files.append(resolved)
    return tuple(files)


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
    config: ScannerConfig,
    notes: tuple[Document, ...],
    playbook: tuple[Document, ...],
    workspace: _WorkspaceLoad,
) -> list[AdvisoryFinding]:
    playbook_text = "\n".join(document.text for document in playbook)
    findings: list[AdvisoryFinding] = list(workspace.findings)
    scanned_documents = _unique_documents(notes + workspace.documents)

    for document in workspace.agents_documents:
        findings.extend(_scan_agents_alignment(document, playbook))

    for document in scanned_documents:
        findings.extend(_scan_weak_command_form_wording(document))
        findings.extend(_scan_sandbox_writable_roots_claims(document))
        if _is_noncanonical_surface(document, config):
            findings.extend(_scan_authority_language(document))
            findings.extend(_scan_staged_rule_mismatches(document, playbook_text))
        findings.extend(_scan_shell_wrapper_examples(document))
        findings.extend(_scan_worktree_creation_guidance(document))

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


def _has_full_command_form_guidance(normalized: str, *, require_execution_layer: bool = True) -> bool:
    return not _command_form_gaps(normalized, require_execution_layer=require_execution_layer)


def _command_form_gaps(normalized: str, *, require_execution_layer: bool = True) -> list[str]:
    required = [
        ("direct command execution", ("direct command", "direct git", "direct gh")),
        ("make command mention", ("make",)),
        ("python command mention", ("python",)),
        ("repo-local script or tool mention", ("repo local", "repo-local")),
        ("wrapper shell restriction", ("wrapper shell", "shell wrapper", "zsh lc", "bash lc", "sh c")),
        ("wrapper-shell preflight", ("preflight", "before using", "before choosing", "check whether")),
    ]
    if require_execution_layer:
        required.append(
            (
                "git/gh execution-layer setting",
                ("native argv", "shell false", "login false", "use shell false", "implicit shell", "login shell"),
            )
        )
    gaps: list[str] = []
    for label, options in required:
        if not any(option in normalized for option in options):
            gaps.append(label)
    return gaps


def _scan_weak_command_form_wording(document: Document) -> list[AdvisoryFinding]:
    normalized = normalize_text(document.text)
    if not _mentions_weak_git_gh_only_wording(normalized):
        return []
    gaps = _command_form_gaps(normalized)
    if not gaps:
        return []
    line_number, line = _first_matching_line(document.text, re.compile(r"direct.*git.*gh|git.*gh.*commands?", re.IGNORECASE))
    return [
        AdvisoryFinding(
            kind="weak_command_form_wording",
            path=document.path,
            line=line_number,
            snippet=line,
            reasons=tuple(gaps),
            suggested_direction=(
                "Strengthen local wording to include make, python, repo-local scripts, "
                "wrapper-shell preflight, explicit shell-wrapper restrictions, and execution-layer "
                "settings that avoid implicit shell or login-shell wrapping for git/gh."
            ),
        )
    ]


def _mentions_weak_git_gh_only_wording(normalized: str) -> bool:
    return (
        "prefer direct git and gh commands" in normalized
        or "direct git and gh commands" in normalized
    )


def _scan_authority_language(document: Document) -> list[AdvisoryFinding]:
    findings: list[AdvisoryFinding] = []
    lines = _iter_lines(document.text)
    for index, (line_number, line) in enumerate(lines):
        if not AUTHORITY_TERMS_RE.search(line):
            continue
        normalized_line = normalize_text(line)
        context = _line_context(lines, index)
        if not _has_noncanonical_authority_claim(
            line,
            normalized_line,
            context,
            is_heading=line.lstrip().startswith("#"),
        ):
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


def _has_noncanonical_authority_claim(
    line: str,
    normalized_line: str,
    normalized_context: str,
    *,
    is_heading: bool,
) -> bool:
    if _is_direct_authority_claim(line, normalized_line, is_heading=is_heading):
        return True
    if _is_authority_context_exception(normalized_context):
        return False
    return _has_ambiguous_authority_reference(normalized_line, normalized_context)


def _is_direct_authority_claim(line: str, normalized_line: str, *, is_heading: bool) -> bool:
    if is_heading and "source of truth" in normalized_line:
        return False
    if normalized_line == "canonical guidance":
        return False
    if _is_benign_playbook_canonical_reference(normalized_line):
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
    if _has_authority_exception(normalized_segment):
        return False
    if any(phrase in normalized_segment for phrase in AUTHORITY_CLAIM_PHRASES):
        return True
    return any(pattern.search(normalized_segment) for pattern in AUTHORITY_CLAIM_PATTERNS)


def _has_authority_exception(normalized_text: str) -> bool:
    if _is_playbook_override_authority_claim(normalized_text):
        return False
    if _is_benign_playbook_canonical_reference(normalized_text):
        return True
    if (
        "canonical guidance" in normalized_text
        and any(
            term in normalized_text
            for term in (
                "audit drift",
                "canonical guidance ownership",
                "distinguish canonical guidance",
                "findings are useful",
                "repo local execution",
                "staging notes",
            )
        )
    ):
        return True
    if (
        "canonical source" in normalized_text
        and any(
            term in normalized_text
            for term in (
                "could better record",
                "role mode confusion",
                "under weighting",
                "validation role",
            )
        )
    ):
        return True
    exception_phrases = (
        AUTHORITY_DISCLAIMER_PHRASES
        + AUTHORITY_EXTERNAL_SOURCE_PHRASES
        + AUTHORITY_DISCUSSION_PHRASES
        + ("implementation repositories", "non authoritative", "not enforcement tooling")
    )
    return any(term in normalized_text for term in exception_phrases)


def _is_benign_playbook_canonical_reference(normalized_text: str) -> bool:
    if "ai workflow playbook" not in normalized_text or "canonical" not in normalized_text:
        return False
    if _is_playbook_override_authority_claim(normalized_text):
        return False
    benign_patterns = (
        r"\bai workflow playbook\b.{0,80}\bis\b.{0,80}\bcanonical\b.{0,80}\b(?:source|reference|guidance|policy)\b",
        r"\bai workflow playbook\b.{0,80}\b(?:repository|docs?)\b.{0,80}\bcanonical\b.{0,80}\b(?:source|reference|guidance|policy)\b",
        r"\buse\b.{0,80}\bai workflow playbook\b.{0,80}\bas\b.{0,80}\bcanonical\b.{0,80}\b(?:source|reference|guidance|policy)\b",
        r"\bcanonical\b.{0,80}\b(?:guidance|source|reference|policy)\b.{0,80}\b(?:lives|remains)\b.{0,80}\bai workflow playbook\b",
    )
    return any(re.search(pattern, normalized_text) for pattern in benign_patterns)


def _is_playbook_override_authority_claim(normalized_text: str) -> bool:
    override_patterns = (
        r"\b(?:replaces?|supersedes?|overrides?)\b.{0,80}\bai workflow playbook\b.{0,80}\bcanonical\b",
        r"\btreat\s+this\b.{0,80}\bnot\b.{0,20}\bai workflow playbook\b.{0,80}\bcanonical\b",
        r"\bnot\b.{0,20}\bai workflow playbook\b.{0,80}\bas\b.{0,20}\bcanonical\b",
    )
    return any(re.search(pattern, normalized_text) for pattern in override_patterns)


def _has_authority_disclaimer(normalized_text: str) -> bool:
    return any(term in normalized_text for term in AUTHORITY_DISCLAIMER_PHRASES)


def _has_authority_discussion_context(normalized_text: str) -> bool:
    context_phrases = AUTHORITY_EXTERNAL_SOURCE_PHRASES + AUTHORITY_DISCUSSION_PHRASES
    return any(term in normalized_text for term in context_phrases)


def _has_authority_claim_language(normalized_text: str) -> bool:
    if any(phrase in normalized_text for phrase in AUTHORITY_CLAIM_PHRASES):
        return True
    return any(pattern.search(normalized_text) for pattern in AUTHORITY_CLAIM_PATTERNS)


def _has_context_suppressed_authority_language(normalized_context: str) -> bool:
    if _has_authority_disclaimer(normalized_context):
        return True
    return _has_authority_discussion_context(normalized_context)


def _has_ambiguous_authority_reference(normalized_line: str, normalized_context: str) -> bool:
    if _has_context_suppressed_authority_language(normalized_context):
        return False
    if "source of truth" in normalized_line:
        return True
    if "authoritative" in normalized_line:
        return True
    if "canonical" in normalized_line:
        return False
    if _has_authority_claim_language(normalized_line):
        return True
    return False


def _is_authority_context_exception(normalized_context: str) -> bool:
    exception_terms = (
        "not a canonical source",
        "not canonical playbook guidance",
        "not authoritative live operational state",
        "risks acting like a second source of truth",
    )
    return any(term in normalized_context for term in exception_terms)


def _scan_staged_rule_mismatches(document: Document, playbook_text: str) -> list[AdvisoryFinding]:
    findings: list[AdvisoryFinding] = []
    for line_number, line in _iter_lines(document.text):
        if not STRONG_RULE_RE.search(line):
            continue
        for topic, pattern in RULE_TOPICS:
            if not pattern.search(line):
                continue
            if _playbook_has_strong_topic(playbook_text, pattern):
                continue
            findings.append(
                AdvisoryFinding(
                    kind="staged_rule_stronger_than_playbook",
                    path=document.path,
                    line=line_number,
                    snippet=line.strip(),
                    reasons=(f"strong noncanonical {topic} wording lacks matching playbook representation",),
                    suggested_direction="Review whether the rule should be promoted to the playbook or softened as noncanonical evidence.",
                )
            )
    return findings


def _playbook_has_strong_topic(playbook_text: str, pattern: re.Pattern[str]) -> bool:
    return any(
        pattern.search(line) and STRONG_RULE_RE.search(line)
        for _, line in _iter_lines(playbook_text)
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


def _scan_worktree_creation_guidance(document: Document) -> list[AdvisoryFinding]:
    findings: list[AdvisoryFinding] = []
    lines = _iter_lines(document.text)
    for index, (line_number, line) in enumerate(lines):
        context = _nearby_context(lines, index, radius=5)
        if BRANCH_ONLY_IMPLEMENTATION_RE.search(line) and not WORKTREE_SELECTION_SIGNAL_RE.search(context):
            findings.append(
                AdvisoryFinding(
                    kind="implementation_work_without_required_worktree",
                    path=document.path,
                    line=line_number,
                    snippet=line.strip(),
                    reasons=("implementation guidance presents branch-only or optional-worktree flow without required repo-local worktree language",),
                    suggested_direction=(
                        "State that implementation changes require selecting, reusing, or creating a dedicated repo-local worktree before making changes."
                    ),
                )
            )
        if not _encourages_worktree_creation(line):
            continue
        if _is_descriptive_worktree_record(line, context):
            continue
        if WORKTREE_SELECTION_SIGNAL_RE.search(context):
            continue
        if NEGATIVE_WORKTREE_GUIDANCE_RE.search(context):
            continue
        findings.append(
            AdvisoryFinding(
                kind="worktree_creation_without_inspection_signal",
                path=document.path,
                line=line_number,
                snippet=line.strip(),
                reasons=("worktree or isolated-surface creation appears without nearby required repo-local worktree selection, reuse, or creation guidance",),
                suggested_direction=(
                    "Add nearby guidance to inspect `git worktree list` and repo-local `.worktrees/`, then select, reuse, or create a dedicated repo-local worktree before implementation changes."
                ),
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


def _encourages_worktree_creation(line: str) -> bool:
    return bool(WORKTREE_CREATION_RE.search(line) or ISOLATED_SURFACE_CREATION_RE.search(line))


def _is_descriptive_worktree_record(line: str, context: str) -> bool:
    normalized_line = normalize_text(line)
    normalized_context = normalize_text(context)
    if (
        "run shape" in normalized_context
        and "source repository" in normalized_context
        and "base at setup" in normalized_context
    ):
        return True
    if "repository ran git worktree add in parallel" in normalized_line:
        return True
    if "commands run" in normalized_context and "git worktree add" in normalized_line:
        return True
    if re.match(r"^\s*\d+\.\s+[\w/-]*worktree creation\s*$", line):
        return True
    return False


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


def _first_matching_line(text: str, pattern: re.Pattern[str]) -> tuple[int, str]:
    for line_number, line in _iter_lines(text):
        if pattern.search(line):
            return line_number, line.strip()
    return 1, ""


def _iter_lines(text: str) -> tuple[tuple[int, str], ...]:
    return tuple(enumerate(text.splitlines(), start=1))


def _line_context(lines: tuple[tuple[int, str], ...], index: int) -> str:
    context = [lines[index][1]]
    previous_line = _nearest_nonblank_line(lines, range(index - 1, -1, -1))
    next_line = _nearest_nonblank_line(lines, range(index + 1, len(lines)))
    if previous_line:
        context.insert(0, previous_line)
    if next_line:
        context.append(next_line)
    return normalize_text(" ".join(context))


def _nearby_context(lines: tuple[tuple[int, str], ...], index: int, *, radius: int) -> str:
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return "\n".join(line for _, line in lines[start:end])


def _nearest_nonblank_line(lines: tuple[tuple[int, str], ...], indexes: range) -> str:
    for line_index in indexes:
        line = lines[line_index][1].strip()
        if line:
            return line
    return ""


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
