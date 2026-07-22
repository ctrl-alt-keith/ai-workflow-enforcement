"""Restore the fixed root worktree ignore baseline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import StrategyResult


REQUIRED_PATH = ".gitignore"
REQUIRED_RULE = ".worktrees/"
ALLOWED_CHANGED_PATH = REQUIRED_PATH
GOVERNING_RULE_REFERENCE = (
    "ctrl-alt-keith/ai-workflow-playbook/docs/new-repo-bootstrap.md"
)
_TOKEN = ".worktrees"


@dataclass(frozen=True)
class WorktreeIgnoreBaselineContext:
    repository_root: Path


class WorktreeIgnoreBaselineStrategy:
    """Append the exact worktree rule only when the file is unambiguous."""

    def run(self, context: WorktreeIgnoreBaselineContext) -> StrategyResult:
        gitignore = context.repository_root / REQUIRED_PATH
        if gitignore.is_symlink():
            return _blocked(
                "Root .gitignore is a symlink and cannot be modified safely."
            )
        if not gitignore.is_file():
            return _blocked("Root .gitignore is missing or is not a regular file.")

        try:
            original_bytes = gitignore.read_bytes()
        except OSError:
            return _blocked("Root .gitignore could not be read.")
        try:
            original = original_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return _blocked("Root .gitignore is not valid UTF-8.")

        matching_lines = sum(
            line.removesuffix("\r") == REQUIRED_RULE
            for line in original.split("\n")
        )
        token_occurrences = original.count(_TOKEN)
        if token_occurrences:
            if matching_lines and token_occurrences == matching_lines:
                return StrategyResult(
                    outcome="no_change",
                    summary=(
                        "Root .gitignore already contains the exact active "
                        "worktree rule."
                    ),
                    evidence=_evidence("exact_rule=present"),
                )
            return _blocked(
                "Root .gitignore contains an alternate or ambiguous "
                ".worktrees occurrence."
            )

        newline = _newline_convention(original_bytes)
        if newline is None:
            return _blocked(
                "Root .gitignore has no unambiguous supported newline convention."
            )
        encoded_rule = REQUIRED_RULE.encode("utf-8")
        if not original_bytes or original_bytes.endswith(newline):
            suffix = encoded_rule + newline
        else:
            suffix = newline + encoded_rule + newline
        expected = original_bytes + suffix

        try:
            with gitignore.open("ab") as stream:
                written = stream.write(suffix)
            if written != len(suffix):
                raise OSError("the fixed append was incomplete")
            observed = gitignore.read_bytes()
        except OSError as exc:
            return _blocked(
                "The exact .gitignore rule could not be appended and verified.",
                f"error={type(exc).__name__}",
            )
        if not observed.startswith(original_bytes) or observed != expected:
            return _blocked(
                "The .gitignore append failed deterministic prefix verification.",
                "prefix_verification=failed",
            )

        return StrategyResult(
            outcome="changed",
            summary="Appended the exact worktree ignore rule to root .gitignore.",
            changed_paths=(ALLOWED_CHANGED_PATH,),
            evidence=_evidence(
                "worktrees_token=absent",
                "mutation=exact_deterministic_append",
                "original_bytes=preserved_as_exact_prefix",
            ),
        )


def _newline_convention(content: bytes) -> bytes | None:
    without_crlf = content.replace(b"\r\n", b"")
    has_crlf = b"\r\n" in content
    has_lf = b"\n" in without_crlf
    has_cr = b"\r" in without_crlf
    if has_cr or (has_crlf and has_lf):
        return None
    return b"\r\n" if has_crlf else b"\n"


def _blocked(summary: str, *extra_evidence: str) -> StrategyResult:
    return StrategyResult(
        outcome="blocked",
        summary=summary,
        evidence=_evidence("classification=fail_closed", *extra_evidence),
    )


def _evidence(*items: str) -> tuple[str, ...]:
    return (
        f"path={ALLOWED_CHANGED_PATH}",
        f"required_rule={REQUIRED_RULE}",
        f"governing_rule_reference={GOVERNING_RULE_REFERENCE}",
        "governing_rule_reference_usage=implementation_traceability_only",
        *items,
    )
