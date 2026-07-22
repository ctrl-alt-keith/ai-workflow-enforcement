"""The single Docs Drift strategy implemented by the stewardship MVP."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import StrategyResult


@dataclass(frozen=True)
class DocsDriftContext:
    repository_root: Path
    documentation_path: str
    validation_command: tuple[str, ...]


class DocsDriftStrategy:
    """Keep the repository's primary doc explicit about canonical validation."""

    def run(self, context: DocsDriftContext) -> StrategyResult:
        documentation = context.repository_root / context.documentation_path
        if not documentation.is_file():
            return StrategyResult(
                outcome="blocked",
                summary="The configured primary documentation file was not present.",
                evidence=(context.documentation_path,),
            )

        command = " ".join(context.validation_command)
        original = documentation.read_text(encoding="utf-8")
        if command in original:
            return StrategyResult(
                outcome="no_change",
                summary="Primary documentation already names repository-native validation.",
                evidence=(
                    f"documentation={context.documentation_path}",
                    f"validation_command={command}",
                ),
                validation_requirements=(command,),
            )

        separator = "" if original.endswith("\n\n") else "\n" if original.endswith("\n") else "\n\n"
        addition = (
            f"{separator}## Validation\n\n"
            "Run the repository-native validation command before delivery:\n\n"
            "```sh\n"
            f"{command}\n"
            "```\n"
        )
        documentation.write_text(original + addition, encoding="utf-8")
        return StrategyResult(
            outcome="changed",
            summary="Added the missing repository-native validation command to primary documentation.",
            changed_paths=(context.documentation_path,),
            evidence=(
                f"documentation={context.documentation_path}",
                f"validation_command={command}",
                "reason=canonical validation was absent from primary documentation",
            ),
            validation_requirements=(command,),
        )
