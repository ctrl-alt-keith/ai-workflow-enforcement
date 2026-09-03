"""Restore the fixed shared-workflow route in a repository's root AGENTS.md."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .models import StrategyResult


REQUIRED_PATH = "ai-workflow-playbook/docs/start-here.md"
GOVERNING_RULE_REFERENCE = (
    "ctrl-alt-keith/ai-workflow-playbook/docs/start-here.md"
)
ALLOWED_CHANGED_PATH = "AGENTS.md"
APPROVED_BLOCK = (
    "\n\n## Shared Workflow Entry Point\n\n"
    "Start with `ai-workflow-playbook/docs/start-here.md` before repository or "
    "software work. Use this `AGENTS.md` only for repository-specific execution "
    "guidance.\n"
)
_APPROVED_BLOCK_BYTES = APPROVED_BLOCK.encode("utf-8")

_FENCE_OPEN = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")
_RESERVED_HEADING = re.compile(
    r"^[ \t]{0,3}##[ \t]+Shared[ \t]+Workflow[ \t]+Entry[ \t]+Point"
    r"[ \t]*#*[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_ACTIVE_ROUTE = re.compile(
    rf"\b(?:start\s+(?:with|from)|read)\s+(?:the\s+)?"
    rf"(?:`|<|\*\*|\[)?{re.escape(REQUIRED_PATH)}(?:`|>|\*\*|\])?",
    re.IGNORECASE,
)
_AMBIGUOUS_PREFIX = re.compile(
    r"\b(?:do\s+not|don't|never|not|no\s+longer|historical(?:ly)?|previously|"
    r"formerly|used\s+to|for\s+example|examples?(?:-only)?|e\.g\.|instead\s+of)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AgentsStartupRoutingContext:
    repository_root: Path


class AgentsStartupRoutingStrategy:
    """Append the one approved route only when the existing state is unambiguous."""

    def run(self, context: AgentsStartupRoutingContext) -> StrategyResult:
        agents = context.repository_root / ALLOWED_CHANGED_PATH
        if agents.is_symlink():
            return _blocked("Root AGENTS.md is a symlink and cannot be modified safely.")
        if not agents.is_file():
            return _blocked("Root AGENTS.md is missing or is not a regular file.")

        try:
            original_bytes = agents.read_bytes()
        except OSError:
            return _blocked("Root AGENTS.md could not be read.")
        try:
            original = original_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return _blocked("Root AGENTS.md is not valid UTF-8.")

        active_prose = _without_fenced_code(original)
        if _has_active_route(active_prose):
            return StrategyResult(
                outcome="no_change",
                summary="Root AGENTS.md already contains an active shared-workflow route.",
                evidence=_evidence("active_route=present"),
            )
        if REQUIRED_PATH in active_prose:
            return _blocked(
                "The required path appears in prose without an unambiguous active route."
            )
        if _RESERVED_HEADING.search(active_prose):
            return _blocked(
                "The reserved Shared Workflow Entry Point heading already exists without an active route."
            )

        expected = original_bytes + _APPROVED_BLOCK_BYTES
        try:
            with agents.open("ab") as stream:
                written = stream.write(_APPROVED_BLOCK_BYTES)
            if written != len(_APPROVED_BLOCK_BYTES):
                raise OSError("the fixed append was incomplete")
            observed = agents.read_bytes()
        except OSError as exc:
            return StrategyResult(
                outcome="failed",
                summary="The approved AGENTS.md append could not be written or verified.",
                evidence=_evidence(f"error={type(exc).__name__}"),
            )
        if observed != expected or not observed.startswith(original_bytes):
            return StrategyResult(
                outcome="failed",
                summary="The AGENTS.md append did not preserve the original bytes exactly.",
                evidence=_evidence("prefix_verification=failed"),
            )

        return StrategyResult(
            outcome="changed",
            summary="Appended the approved shared-workflow entry point to root AGENTS.md.",
            changed_paths=(ALLOWED_CHANGED_PATH,),
            evidence=_evidence(
                "active_route=absent",
                "reserved_heading=absent",
                "mutation=exact_fixed_append",
                "original_bytes=preserved_as_exact_prefix",
            ),
        )


def _without_fenced_code(text: str) -> str:
    visible: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
        candidate = line.rstrip("\r\n")
        if fence_character is None:
            match = _FENCE_OPEN.match(candidate)
            if match is None:
                visible.append(line)
                continue
            marker = match.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            visible.append("\n" if line.endswith(("\n", "\r")) else "")
            continue

        closing = re.match(
            rf"^[ ]{{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*$",
            candidate,
        )
        if closing is not None:
            fence_character = None
            fence_length = 0
        visible.append("\n" if line.endswith(("\n", "\r")) else "")
    return "".join(visible)


def _has_active_route(prose: str) -> bool:
    section_heading = ""
    for paragraph in re.split(r"\n[ \t]*\n", prose):
        normalized = " ".join(paragraph.split())
        first_line = next(
            (line.strip() for line in paragraph.splitlines() if line.strip()), ""
        )
        if re.match(r"^#{1,6}[ \t]+", first_line):
            section_heading = first_line
        for match in _ACTIVE_ROUTE.finditer(normalized):
            prefix = f"{section_heading} {normalized[: match.start()]}"
            if _AMBIGUOUS_PREFIX.search(prefix) is None:
                return True
    return False


def _blocked(summary: str) -> StrategyResult:
    return StrategyResult(
        outcome="blocked",
        summary=summary,
        evidence=_evidence("classification=fail_closed"),
    )


def _evidence(*items: str) -> tuple[str, ...]:
    return (
        f"path={ALLOWED_CHANGED_PATH}",
        f"required_path={REQUIRED_PATH}",
        f"governing_rule_reference={GOVERNING_RULE_REFERENCE}",
        "governing_rule_reference_usage=implementation_traceability_only",
        *items,
    )
