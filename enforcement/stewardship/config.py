"""Reviewed configuration for the deliberately narrow stewardship MVP."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class RepositoryPolicy:
    repository: str
    policy_path: str
    required_policy_marker: str
    documentation_path: str


@dataclass(frozen=True)
class StewardshipConfig:
    repositories: dict[str, RepositoryPolicy]

    def policy_for(self, repository: str) -> RepositoryPolicy | None:
        return self.repositories.get(repository)


def load_config(path: Path) -> StewardshipConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("hosted stewardship config must use schema_version 1")
    raw_repositories = data.get("repositories")
    if not isinstance(raw_repositories, dict) or not raw_repositories:
        raise ValueError("hosted stewardship config must declare at least one repository")

    policies: dict[str, RepositoryPolicy] = {}
    for repository, raw in raw_repositories.items():
        if not isinstance(raw, dict):
            raise ValueError(f"repository policy must be an object: {repository}")
        policy = RepositoryPolicy(
            repository=repository,
            policy_path=_required_string(raw, "policy_path", repository),
            required_policy_marker=_required_string(
                raw, "required_policy_marker", repository
            ),
            documentation_path=_required_string(
                raw, "documentation_path", repository
            ),
        )
        policies[repository] = policy
    return StewardshipConfig(repositories=policies)


def _required_string(data: dict[str, object], key: str, repository: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{repository} must declare non-empty {key}")
    return value
