"""Configuration loading for drift scans."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Iterable


DEFAULT_IGNORE_PATTERNS = (
    ".git/**",
    "**/.git/**",
    ".worktrees/**",
    "**/.worktrees/**",
    "__pycache__/**",
    "**/__pycache__/**",
    ".venv/**",
    "**/.venv/**",
)


@dataclass(frozen=True)
class ScannerConfig:
    notes_roots: tuple[Path, ...]
    playbook_roots: tuple[Path, ...]
    workspace_root: Path | None = None
    organization: str | None = None
    workspace_manifest: Path | None = None
    organization_repositories: tuple[str, ...] = ()
    ignore_patterns: tuple[str, ...] = DEFAULT_IGNORE_PATTERNS
    similarity_threshold: float = 0.55
    min_heading_matches: int = 2
    min_phrase_words: int = 8
    min_phrase_matches: int = 2
    max_candidates: int = 50

    def resolved(self, base_dir: Path | None = None) -> "ScannerConfig":
        base = base_dir or Path.cwd()
        return replace(
            self,
            notes_roots=_resolve_roots(self.notes_roots, base),
            playbook_roots=_resolve_roots(self.playbook_roots, base),
            workspace_root=_resolve_optional_path(self.workspace_root, base),
            workspace_manifest=_resolve_optional_path(self.workspace_manifest, base),
        )


def load_config(path: Path) -> ScannerConfig:
    """Load JSON config, resolving roots near the file and adding ignores to defaults."""
    data = json.loads(path.read_text(encoding="utf-8"))
    base_dir = path.parent
    config = ScannerConfig(
        notes_roots=_as_paths(data.get("notes_roots", ())),
        playbook_roots=_as_paths(data.get("playbook_roots", ())),
        workspace_root=_as_optional_path(data.get("workspace_root")),
        organization=data.get("organization"),
        workspace_manifest=_as_optional_path(data.get("workspace_manifest")),
        organization_repositories=tuple(data.get("organization_repositories", ())),
        ignore_patterns=_combined_ignore_patterns(data.get("ignore", ())),
        similarity_threshold=float(data.get("similarity_threshold", 0.55)),
        min_heading_matches=int(data.get("min_heading_matches", 2)),
        min_phrase_words=int(data.get("min_phrase_words", 8)),
        min_phrase_matches=int(data.get("min_phrase_matches", 2)),
        max_candidates=int(data.get("max_candidates", 50)),
    )
    return config.resolved(base_dir)


def merge_cli_config(
    base: ScannerConfig | None,
    *,
    notes_roots: Iterable[str],
    playbook_roots: Iterable[str],
    ignore_patterns: Iterable[str],
    similarity_threshold: float | None,
    min_heading_matches: int | None,
    min_phrase_words: int | None,
    min_phrase_matches: int | None,
    max_candidates: int | None,
    workspace_root: str | None = None,
    organization: str | None = None,
    workspace_manifest: str | None = None,
    organization_repositories: Iterable[str] = (),
) -> ScannerConfig:
    config = base or ScannerConfig(notes_roots=(), playbook_roots=())
    if notes_roots:
        config = replace(config, notes_roots=_as_paths(notes_roots))
    if playbook_roots:
        config = replace(config, playbook_roots=_as_paths(playbook_roots))
    if ignore_patterns:
        config = replace(
            config,
            ignore_patterns=_combined_ignore_patterns(ignore_patterns, config.ignore_patterns),
        )
    if workspace_root is not None:
        config = replace(config, workspace_root=Path(workspace_root))
    if organization is not None:
        config = replace(config, organization=organization)
    if workspace_manifest is not None:
        config = replace(config, workspace_manifest=Path(workspace_manifest))
    if organization_repositories:
        config = replace(config, organization_repositories=tuple(organization_repositories))
    if similarity_threshold is not None:
        config = replace(config, similarity_threshold=similarity_threshold)
    if min_heading_matches is not None:
        config = replace(config, min_heading_matches=min_heading_matches)
    if min_phrase_words is not None:
        config = replace(config, min_phrase_words=min_phrase_words)
    if min_phrase_matches is not None:
        config = replace(config, min_phrase_matches=min_phrase_matches)
    if max_candidates is not None:
        config = replace(config, max_candidates=max_candidates)
    return config.resolved()


def _as_paths(values: Iterable[str | Path]) -> tuple[Path, ...]:
    return tuple(Path(value) for value in values)


def _as_optional_path(value: str | None) -> Path | None:
    if value is None:
        return None
    return Path(value)


def _resolve_roots(paths: Iterable[Path], base_dir: Path) -> tuple[Path, ...]:
    return tuple(
        (base_dir / path).resolve() if not path.is_absolute() else path.resolve()
        for path in paths
    )


def _resolve_optional_path(path: Path | None, base_dir: Path) -> Path | None:
    if path is None:
        return None
    return (base_dir / path).resolve() if not path.is_absolute() else path.resolve()


def _combined_ignore_patterns(
    extra_patterns: Iterable[str],
    base_patterns: Iterable[str] = DEFAULT_IGNORE_PATTERNS,
) -> tuple[str, ...]:
    patterns: list[str] = []
    for pattern in tuple(base_patterns) + tuple(extra_patterns):
        if pattern not in patterns:
            patterns.append(pattern)
    return tuple(patterns)
