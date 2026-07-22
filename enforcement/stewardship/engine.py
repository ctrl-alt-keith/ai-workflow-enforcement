"""Shared pre-delivery pipeline for dry-run and propose stewardship runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import shlex
import subprocess
from typing import Callable, Protocol

from .agents_startup_routing import (
    AgentsStartupRoutingContext,
    AgentsStartupRoutingStrategy,
)
from .config import RepositoryPolicy, StewardshipConfig
from .docs_drift import DocsDriftContext, DocsDriftStrategy
from .models import (
    DEFAULT_STRATEGY_IDENTIFIER,
    ENGINE_SCHEMA_VERSION,
    CollisionResult,
    DeliveryProposal,
    DeliveryResult,
    EligibilityResult,
    Mode,
    RepositoryInfo,
    StewardshipReceipt,
    StrategyMetadata,
    StrategyResult,
    ValidationResult,
    artifact_reference,
    strategy_metadata,
)


VALIDATION_PATTERN = re.compile(
    r"Use\s+`([^`]+)`\s+as\s+the\s+canonical\s+local\s+validation\s+entrypoint",
    re.IGNORECASE,
)
FORBIDDEN_COMMAND_TOKENS = {";", "&&", "||", "|", ">", ">>", "<"}


class Gateway(Protocol):
    @property
    def read_available(self) -> bool: ...

    @property
    def write_available(self) -> bool: ...

    def repository_info(self, repository: str) -> RepositoryInfo: ...

    def branch_sha(self, repository: str, branch: str) -> str | None: ...

    def resolve_ref(self, repository: str, target_ref: str) -> str | None: ...

    def hydrate(self, repository: str, base_sha: str, destination: Path) -> None: ...

    def existing_stewardship_pr(
        self, repository: str, collision_marker: str
    ) -> str | None: ...

    def deliver(self, repository_root: Path, proposal: DeliveryProposal) -> DeliveryResult: ...


class Strategy(Protocol):
    def run(self, context: object) -> StrategyResult: ...


@dataclass(frozen=True)
class SelectedStrategy:
    metadata: StrategyMetadata
    execute: Callable[
        [Path, RepositoryPolicy, tuple[str, ...]],
        StrategyResult,
    ]


class StewardshipEngine:
    def __init__(
        self,
        *,
        config: StewardshipConfig,
        gateway: Gateway,
        strategy_identifier: str = DEFAULT_STRATEGY_IDENTIFIER,
        strategy: Strategy | None = None,
        clock: Callable[[], datetime] | None = None,
        redactions: tuple[str, ...] = (),
    ) -> None:
        self._config = config
        self._gateway = gateway
        self._strategy = _select_strategy(strategy_identifier, strategy)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._redactions = tuple(value for value in redactions if value)

    def run(
        self,
        *,
        repository: str,
        mode: Mode,
        target_ref: str = "",
        run_identifier: str,
        engine_revision: str,
        workspace_root: Path,
        evidence_dir: Path,
    ) -> StewardshipReceipt:
        started = self._timestamp()
        receipt = self._new_receipt(
            repository=repository,
            mode=mode,
            run_identifier=run_identifier,
            engine_revision=engine_revision,
            started_at=started,
            target_ref=target_ref,
        )
        evidence_dir.mkdir(parents=True, exist_ok=True)
        stage = "target_resolution"

        try:
            if not repository.strip():
                return self._stop(
                    receipt,
                    terminal="blocked_before_strategy",
                    failure_stage=stage,
                    error="a repository must be explicitly targeted",
                )
            if mode == "propose" and target_ref:
                return self._stop(
                    receipt,
                    terminal="blocked_before_strategy",
                    failure_stage="target_ref_validation",
                    error="target_ref is supported only in dry-run mode",
                )
            policy = self._config.policy_for(repository)
            if policy is None:
                receipt.eligibility = asdict(
                    EligibilityResult(
                        decision="ineligible",
                        reason="The repository is not in the reviewed MVP allowlist.",
                        controlling_source="config/hosted-stewardship.json",
                        evidence=(f"explicit_target={repository}",),
                    )
                )
                return self._stop(
                    receipt,
                    terminal="blocked_ineligible",
                    failure_stage="eligibility",
                )

            stage = "github_read_access"
            if not self._gateway.read_available:
                receipt.eligibility = asdict(
                    EligibilityResult(
                        decision="blocked",
                        reason="The required read-only GitHub App identity was unavailable.",
                        controlling_source="runtime GitHub App token generation",
                        evidence=(f"explicit_target={repository}",),
                    )
                )
                return self._stop(
                    receipt,
                    terminal="blocked_before_strategy",
                    failure_stage=stage,
                )

            repository_info = self._gateway.repository_info(repository)
            receipt.base_branch = repository_info.default_branch
            receipt.effective_target_ref = target_ref or repository_info.default_branch
            if repository_info.archived:
                receipt.eligibility = asdict(
                    EligibilityResult(
                        decision="ineligible",
                        reason="Archived repositories are not eligible for stewardship proposals.",
                        controlling_source="current GitHub repository metadata",
                        evidence=("archived=true",),
                    )
                )
                return self._stop(
                    receipt,
                    terminal="blocked_ineligible",
                    failure_stage="eligibility",
                )

            if target_ref:
                stage = "target_resolution"
                base_sha = self._gateway.resolve_ref(repository, target_ref)
            else:
                stage = "base_resolution"
                base_sha = self._gateway.branch_sha(
                    repository, repository_info.default_branch
                )
            if not base_sha:
                raise RuntimeError("the effective target ref did not resolve to a commit")
            receipt.base_sha = base_sha

            stage = "hydration"
            checkout = workspace_root / repository.replace("/", "--")
            workspace_root.mkdir(parents=True, exist_ok=True)
            self._gateway.hydrate(repository, base_sha, checkout)

            stage = "eligibility"
            instructions_path = checkout / "AGENTS.md"
            if not instructions_path.is_file():
                receipt.eligibility = asdict(
                    EligibilityResult(
                        decision="blocked",
                        reason="Current repo-local instructions could not be retrieved.",
                        controlling_source="AGENTS.md",
                    )
                )
                return self._stop(
                    receipt,
                    terminal="blocked_before_strategy",
                    failure_stage=stage,
                )
            instructions = instructions_path.read_text(encoding="utf-8")
            policy_path = checkout / policy.policy_path
            if not policy_path.is_file():
                return self._eligibility_block(
                    receipt,
                    reason="The configured repo-local stewardship policy source was absent.",
                    source=policy.policy_path,
                )
            policy_text = policy_path.read_text(encoding="utf-8")
            if policy.required_policy_marker not in policy_text:
                receipt.eligibility = asdict(
                    EligibilityResult(
                        decision="ineligible",
                        reason="Repo-local policy does not explicitly permit hosted stewardship proposals.",
                        controlling_source=policy.policy_path,
                        evidence=("required_policy_marker=absent",),
                    )
                )
                return self._stop(
                    receipt,
                    terminal="blocked_ineligible",
                    failure_stage=stage,
                )
            validation_command = _resolve_validation_command(instructions)
            if validation_command is None:
                return self._eligibility_block(
                    receipt,
                    reason="A repository-native validation path could not be resolved from current guidance.",
                    source="AGENTS.md",
                )
            receipt.eligibility = asdict(
                EligibilityResult(
                    decision="eligible",
                    reason="Explicit target, App access, active status, instructions, policy, and validation were verified.",
                    controlling_source=policy.policy_path,
                    evidence=(
                        f"explicit_target={repository}",
                        "github_app_access=available",
                        "archived=false",
                        "instructions=AGENTS.md",
                        f"policy={policy.policy_path}",
                        f"validation={' '.join(validation_command)}",
                    ),
                )
            )

            stage = "strategy"
            try:
                strategy_result = self._strategy.execute(
                    checkout,
                    policy,
                    validation_command,
                )
            except Exception as exc:  # strategy failures must retain a receipt
                receipt.strategy_result = asdict(
                    StrategyResult(
                        outcome="failed",
                        summary="Selected stewardship strategy execution failed.",
                    )
                )
                return self._stop(
                    receipt,
                    terminal="strategy_failed",
                    failure_stage=stage,
                    error=str(exc),
                )
            receipt.strategy_result = asdict(strategy_result)
            if strategy_result.outcome == "blocked":
                return self._stop(
                    receipt,
                    terminal="strategy_blocked",
                    failure_stage=stage,
                )
            if strategy_result.outcome == "failed":
                return self._stop(
                    receipt,
                    terminal="strategy_failed",
                    failure_stage=stage,
                )
            if strategy_result.outcome == "no_change":
                receipt.validation = asdict(
                    ValidationResult(
                        command=validation_command,
                        reason="No changed proposal required repository-native validation.",
                        status="unavailable",
                    )
                )
                receipt.would_create_pr_reason = "The strategy produced no justified documentation change."
                return self._stop(receipt, terminal="eligible_no_change")

            stage = "proposal_construction"
            changed_paths = tuple(_git_lines(checkout, "diff", "--name-only"))
            if changed_paths != strategy_result.changed_paths:
                raise RuntimeError(
                    "strategy-reported changed paths did not match the working tree"
                )
            patch = _git(checkout, "diff", "--binary", "--full-index")
            if not patch:
                raise RuntimeError("strategy reported changed but produced no patch")
            patch_path = evidence_dir / "proposal.patch"
            patch_path.write_text(patch, encoding="utf-8")
            diff_digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
            receipt.changed_paths = list(changed_paths)
            receipt.diff_digest = diff_digest
            receipt.patch_artifact = artifact_reference(patch_path, evidence_dir)

            stage = "validation"
            validation = self._run_validation(
                checkout=checkout,
                command=validation_command,
                evidence_dir=evidence_dir,
            )
            receipt.validation = asdict(validation)
            if validation.status != "passed":
                receipt.would_create_pr_reason = "Repository-native validation did not pass."
                return self._stop(
                    receipt,
                    terminal="validation_failed",
                    failure_stage=stage,
                )

            if target_ref:
                receipt.would_create_pr_reason = (
                    "A non-default target ref is inspection-only and cannot produce a delivery proposal."
                )
                return self._stop(receipt, terminal="dry_run_complete")

            metadata = self._strategy.metadata
            branch = (
                f"stewardship/{metadata.identifier}/{base_sha[:12]}-{diff_digest[:12]}"
            )
            commit_message = metadata.commit_message
            pr_title = metadata.pr_title
            pr_body = _build_pr_body(
                repository=repository,
                base_sha=base_sha,
                engine_revision=engine_revision,
                strategy_result=strategy_result,
                validation=validation,
                changed_paths=changed_paths,
                metadata=metadata,
            )
            receipt.proposed_branch = branch
            receipt.proposed_commit_message = commit_message
            receipt.proposed_pr_title = pr_title
            receipt.proposed_pr_body = pr_body

            stage = "collision_detection"
            existing_pr = self._gateway.existing_stewardship_pr(
                repository, metadata.collision_marker
            )
            if existing_pr:
                collision = CollisionResult(
                    decision="existing_stewardship_pr",
                    reason=(
                        "An open stewardship PR already exists for strategy "
                        f"{metadata.identifier}."
                    ),
                    existing_pr_url=existing_pr,
                )
                receipt.collision = asdict(collision)
                receipt.would_create_pr_reason = "An existing stewardship PR prevents a duplicate."
                return self._stop(receipt, terminal="skipped_existing_pr")

            branch_sha = self._gateway.branch_sha(repository, branch)
            if branch_sha is not None:
                collision = CollisionResult(
                    decision="proposed_branch_exists",
                    reason="The deterministic proposed branch already exists and will not be overwritten.",
                )
                receipt.collision = asdict(collision)
                receipt.would_create_pr_reason = "An existing branch prevents safe delivery."
                return self._stop(
                    receipt,
                    terminal="blocked_branch_exists",
                    failure_stage=stage,
                )

            observed_base_sha = self._gateway.branch_sha(
                repository, repository_info.default_branch
            )
            if observed_base_sha != base_sha:
                collision = CollisionResult(
                    decision="base_sha_changed",
                    reason="The target base branch advanced after hydration.",
                    observed_base_sha=observed_base_sha,
                )
                receipt.collision = asdict(collision)
                receipt.would_create_pr_reason = "The validated base identity is stale."
                return self._stop(
                    receipt,
                    terminal="blocked_base_sha_changed",
                    failure_stage=stage,
                )

            collision = CollisionResult(
                decision="clear",
                reason=(
                    f"No open {metadata.identifier} stewardship PR or proposed branch "
                    "was found, and the base SHA is unchanged."
                ),
                observed_base_sha=observed_base_sha,
            )
            receipt.collision = asdict(collision)
            receipt.would_create_pr = True
            receipt.would_create_pr_reason = (
                "The observed remote state permits a delivery attempt; later remote state may differ."
            )
            proposal = DeliveryProposal(
                repository=repository,
                base_branch=repository_info.default_branch,
                base_sha=base_sha,
                branch=branch,
                commit_message=commit_message,
                pr_title=pr_title,
                pr_body=pr_body,
                changed_paths=changed_paths,
                patch=patch,
                diff_digest=diff_digest,
                validation=validation,
                collision=collision,
            )

            if mode == "dry-run":
                return self._stop(receipt, terminal="dry_run_complete")

            stage = "remote_delivery"
            delivery = self._gateway.deliver(checkout, proposal)
            receipt.remote_mutations_attempted = [
                str(item["operation"]) for item in delivery.mutations
            ]
            receipt.remote_mutation_results = [dict(item) for item in delivery.mutations]
            if not delivery.success:
                return self._stop(
                    receipt,
                    terminal="delivery_failed",
                    failure_stage=stage,
                    error=delivery.error,
                )
            receipt.remote_mutation_results.append(
                {
                    "operation": "delivery_receipt",
                    "success": True,
                    "branch": delivery.branch,
                    "commit_sha": delivery.commit_sha,
                    "pr_url": delivery.pr_url,
                }
            )
            return self._stop(receipt, terminal="delivery_succeeded")
        except Exception as exc:
            return self._stop(
                receipt,
                terminal="blocked_before_strategy" if stage != "remote_delivery" else "delivery_failed",
                failure_stage=stage,
                error=str(exc),
            )

    def _eligibility_block(
        self, receipt: StewardshipReceipt, *, reason: str, source: str
    ) -> StewardshipReceipt:
        receipt.eligibility = asdict(
            EligibilityResult(
                decision="blocked",
                reason=reason,
                controlling_source=source,
            )
        )
        if "validation" in reason.casefold():
            receipt.validation = asdict(
                ValidationResult(reason=reason, status="unavailable")
            )
        return self._stop(
            receipt,
            terminal="blocked_before_strategy",
            failure_stage="eligibility",
        )

    def _run_validation(
        self,
        *,
        checkout: Path,
        command: tuple[str, ...],
        evidence_dir: Path,
    ) -> ValidationResult:
        import os

        environment = os.environ.copy()
        for name in ("GH_TOKEN", "STEWARDSHIP_READ_TOKEN", "STEWARDSHIP_WRITE_TOKEN"):
            environment.pop(name, None)
        result = subprocess.run(
            command,
            cwd=checkout,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        log_path = evidence_dir / "validation.log"
        log = (
            f"command={' '.join(command)}\n"
            f"exit_code={result.returncode}\n"
            "--- stdout ---\n"
            f"{result.stdout}"
            "\n--- stderr ---\n"
            f"{result.stderr}"
        )
        log_path.write_text(self._sanitize(log), encoding="utf-8")
        return ValidationResult(
            command=command,
            reason="Resolved from the current repo-local AGENTS.md canonical validation guidance.",
            status="passed" if result.returncode == 0 else "failed",
            exit_code=result.returncode,
            log_artifact=artifact_reference(log_path, evidence_dir),
        )

    def _new_receipt(
        self,
        *,
        repository: str,
        mode: Mode,
        run_identifier: str,
        engine_revision: str,
        started_at: str,
        target_ref: str,
    ) -> StewardshipReceipt:
        return StewardshipReceipt(
            schema_version=ENGINE_SCHEMA_VERSION,
            run_identifier=run_identifier,
            started_at=started_at,
            completed_at=None,
            mode=mode,
            repository=repository,
            requested_target_ref=target_ref or None,
            effective_target_ref=None,
            base_branch=None,
            base_sha=None,
            engine_revision=engine_revision,
            strategy_identifier=self._strategy.metadata.identifier,
            strategy_revision=self._strategy.metadata.revision,
            eligibility=asdict(
                EligibilityResult(
                    decision="blocked",
                    reason="Eligibility was not completed.",
                    controlling_source="not_resolved",
                )
            ),
            strategy_result=asdict(
                StrategyResult(
                    outcome="blocked",
                    summary="Strategy execution did not begin.",
                )
            ),
            changed_paths=[],
            diff_digest=None,
            patch_artifact=None,
            validation=asdict(ValidationResult()),
            proposed_branch=None,
            proposed_commit_message=None,
            proposed_pr_title=None,
            proposed_pr_body=None,
            collision=asdict(
                CollisionResult(
                    decision="not_checked",
                    reason="Collision detection did not run.",
                )
            ),
            would_create_pr=False,
            would_create_pr_reason="The pre-delivery pipeline did not establish a deliverable proposal.",
            remote_mutations_attempted=[],
            remote_mutation_results=[],
            final_terminal_state="running",
            failure_stage=None,
            bounded_error=None,
        )

    def _stop(
        self,
        receipt: StewardshipReceipt,
        *,
        terminal: str,
        failure_stage: str | None = None,
        error: str | None = None,
    ) -> StewardshipReceipt:
        receipt.completed_at = self._timestamp()
        receipt.final_terminal_state = terminal
        receipt.failure_stage = failure_stage
        receipt.bounded_error = self._sanitize(error)[:2000] if error else None
        return receipt

    def _sanitize(self, value: str | None) -> str:
        sanitized = value or ""
        for secret in self._redactions:
            sanitized = sanitized.replace(secret, "[REDACTED]")
        return sanitized

    def _timestamp(self) -> str:
        return self._clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_validation_command(instructions: str) -> tuple[str, ...] | None:
    match = VALIDATION_PATTERN.search(instructions)
    if match is None:
        return None
    try:
        command = tuple(shlex.split(match.group(1)))
    except ValueError:
        return None
    if not command or any(token in FORBIDDEN_COMMAND_TOKENS for token in command):
        return None
    return command


def _git(repository_root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments),
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[:2000])
    return result.stdout


def _git_lines(repository_root: Path, *arguments: str) -> list[str]:
    return [line for line in _git(repository_root, *arguments).splitlines() if line]


def _build_pr_body(
    *,
    repository: str,
    base_sha: str,
    engine_revision: str,
    strategy_result: StrategyResult,
    validation: ValidationResult,
    changed_paths: tuple[str, ...],
    metadata: StrategyMetadata,
) -> str:
    paths = "\n".join(f"- `{path}`" for path in changed_paths)
    evidence = "\n".join(f"- {item}" for item in strategy_result.evidence)
    return (
        f"{metadata.collision_marker}\n"
        "## Hosted Stewardship Engine\n\n"
        f"{strategy_result.summary}\n\n"
        "### Evidence\n\n"
        f"{evidence}\n\n"
        "### Exact change\n\n"
        f"{paths}\n\n"
        "### Validation\n\n"
        f"- Command: `{' '.join(validation.command)}`\n"
        f"- Result: `{validation.status}` (exit `{validation.exit_code}`)\n\n"
        "### Proposal identity\n\n"
        f"- Repository: `{repository}`\n"
        f"- Base SHA: `{base_sha}`\n"
        f"- Strategy: `{metadata.identifier}` revision `{metadata.revision}`\n"
        f"- Engine revision: `{engine_revision}`\n\n"
        "This review-ready proposal was produced by the Hosted Stewardship Engine. "
        "Human merge remains the acceptance boundary.\n"
    )


def _select_strategy(
    identifier: str,
    implementation: Strategy | None = None,
) -> SelectedStrategy:
    metadata = strategy_metadata(identifier)
    if identifier == "docs-drift":
        strategy = implementation or DocsDriftStrategy()

        def execute(
            repository_root: Path,
            policy: RepositoryPolicy,
            validation_command: tuple[str, ...],
        ) -> StrategyResult:
            return strategy.run(
                DocsDriftContext(
                    repository_root=repository_root,
                    documentation_path=policy.documentation_path,
                    validation_command=validation_command,
                )
            )

        return SelectedStrategy(metadata=metadata, execute=execute)

    if identifier == "agents-startup-routing":
        strategy = implementation or AgentsStartupRoutingStrategy()

        def execute(
            repository_root: Path,
            policy: RepositoryPolicy,
            validation_command: tuple[str, ...],
        ) -> StrategyResult:
            del policy, validation_command
            return strategy.run(
                AgentsStartupRoutingContext(repository_root=repository_root)
            )

        return SelectedStrategy(metadata=metadata, execute=execute)

    raise ValueError(f"unsupported stewardship strategy: {identifier}")
