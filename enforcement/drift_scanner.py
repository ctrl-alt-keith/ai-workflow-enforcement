"""Filesystem-scoped scanner for likely notes vs playbook drift."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from .config import ScannerConfig
from .heuristics import (
    has_canonical_reference,
    normalized_headings,
    normalized_phrases,
    token_similarity,
)


SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".rst"}


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
class ScanResult:
    candidates: tuple[OverlapCandidate, ...]
    notes_files_scanned: int
    playbook_files_scanned: int
    ignored_paths: tuple[Path, ...]


def scan(config: ScannerConfig) -> ScanResult:
    _validate_config(config)
    notes = _load_documents(config.notes_roots, config.ignore_patterns)
    playbook = _load_documents(config.playbook_roots, config.ignore_patterns)

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
    return ScanResult(
        candidates=tuple(candidates[: config.max_candidates]),
        notes_files_scanned=len(notes.documents),
        playbook_files_scanned=len(playbook.documents),
        ignored_paths=tuple(notes.ignored_paths + playbook.ignored_paths),
    )


@dataclass(frozen=True)
class _DocumentLoad:
    documents: tuple[Document, ...]
    ignored_paths: tuple[Path, ...]


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


def _validate_config(config: ScannerConfig) -> None:
    if not config.notes_roots:
        raise ValueError("at least one notes root is required")
    if not config.playbook_roots:
        raise ValueError("at least one playbook root is required")
    for root in config.notes_roots + config.playbook_roots:
        if not root.exists():
            raise ValueError(f"configured root does not exist: {root}")
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
