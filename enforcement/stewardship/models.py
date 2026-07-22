"""Typed boundaries for the Hosted Stewardship Engine MVP."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


ENGINE_SCHEMA_VERSION = 1
DEFAULT_STRATEGY_IDENTIFIER = "docs-drift"
SUPPORTED_STRATEGY_IDENTIFIERS = (
    "docs-drift",
    "agents-startup-routing",
    "worktree-ignore-baseline",
)

Mode = Literal["dry-run", "propose"]
EligibilityDecision = Literal["eligible", "ineligible", "blocked"]
StrategyOutcome = Literal["changed", "no_change", "blocked", "failed"]
ValidationStatus = Literal["passed", "failed", "blocked", "unavailable"]
CollisionDecision = Literal[
    "clear",
    "not_checked",
    "existing_stewardship_pr",
    "proposed_branch_exists",
    "base_sha_changed",
]


@dataclass(frozen=True)
class RepositoryInfo:
    full_name: str
    default_branch: str
    archived: bool


@dataclass(frozen=True)
class StrategyMetadata:
    identifier: str
    revision: str
    commit_message: str
    pr_title: str
    collision_marker: str


DOCS_DRIFT_METADATA = StrategyMetadata(
    identifier="docs-drift",
    revision="1",
    commit_message="docs: document repository validation",
    pr_title="docs: document repository validation",
    collision_marker="<!-- hosted-stewardship:docs-drift -->",
)

AGENTS_STARTUP_ROUTING_METADATA = StrategyMetadata(
    identifier="agents-startup-routing",
    revision="1",
    commit_message="docs: restore the repository startup route",
    pr_title="docs: restore the repository startup route",
    collision_marker="<!-- hosted-stewardship:agents-startup-routing -->",
)

WORKTREE_IGNORE_BASELINE_METADATA = StrategyMetadata(
    identifier="worktree-ignore-baseline",
    revision="1",
    commit_message="chore: restore the worktree ignore baseline",
    pr_title="chore: restore the worktree ignore baseline",
    collision_marker="<!-- hosted-stewardship:worktree-ignore-baseline -->",
)


def strategy_metadata(identifier: str) -> StrategyMetadata:
    """Return metadata for one of the three deliberately fixed strategies."""

    if identifier == DOCS_DRIFT_METADATA.identifier:
        return DOCS_DRIFT_METADATA
    if identifier == AGENTS_STARTUP_ROUTING_METADATA.identifier:
        return AGENTS_STARTUP_ROUTING_METADATA
    if identifier == WORKTREE_IGNORE_BASELINE_METADATA.identifier:
        return WORKTREE_IGNORE_BASELINE_METADATA
    raise ValueError(f"unsupported stewardship strategy: {identifier}")


@dataclass(frozen=True)
class EligibilityResult:
    decision: EligibilityDecision
    reason: str
    controlling_source: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyResult:
    outcome: StrategyOutcome
    summary: str
    changed_paths: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    validation_requirements: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationResult:
    command: tuple[str, ...] = ()
    reason: str = "Validation did not run because the pipeline did not reach it."
    status: ValidationStatus = "blocked"
    exit_code: int | None = None
    log_artifact: str | None = None


@dataclass(frozen=True)
class CollisionResult:
    decision: CollisionDecision
    reason: str
    existing_pr_url: str | None = None
    observed_base_sha: str | None = None


@dataclass(frozen=True)
class DeliveryProposal:
    repository: str
    base_branch: str
    base_sha: str
    branch: str
    commit_message: str
    pr_title: str
    pr_body: str
    changed_paths: tuple[str, ...]
    patch: str
    diff_digest: str
    validation: ValidationResult
    collision: CollisionResult


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    branch: str | None = None
    commit_sha: str | None = None
    pr_url: str | None = None
    error: str | None = None
    mutations: tuple[dict[str, Any], ...] = ()


@dataclass
class StewardshipReceipt:
    schema_version: int
    run_identifier: str
    started_at: str
    completed_at: str | None
    mode: Mode
    repository: str
    requested_target_ref: str | None
    effective_target_ref: str | None
    base_branch: str | None
    base_sha: str | None
    engine_revision: str
    strategy_identifier: str
    strategy_revision: str
    eligibility: dict[str, Any]
    strategy_result: dict[str, Any]
    changed_paths: list[str]
    diff_digest: str | None
    patch_artifact: str | None
    validation: dict[str, Any]
    proposed_branch: str | None
    proposed_commit_message: str | None
    proposed_pr_title: str | None
    proposed_pr_body: str | None
    collision: dict[str, Any]
    would_create_pr: bool
    would_create_pr_reason: str
    remote_mutations_attempted: list[str]
    remote_mutation_results: list[dict[str, Any]]
    final_terminal_state: str
    failure_stage: str | None
    bounded_error: str | None
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def artifact_reference(path: Path, evidence_dir: Path) -> str:
    """Return a stable evidence-relative artifact reference."""

    return path.relative_to(evidence_dir).as_posix()
