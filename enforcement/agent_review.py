"""Local, inert inventory of enrolled agent files. No inspected source is written.

The enrollment and record destination are operator-owned local inputs. This
module deliberately does not launch either agent to ask for its effective state.
"""

from __future__ import annotations

from dataclasses import dataclass
import ast
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tomllib
from typing import Any

from .drift_scanner import scan_enrolled_instruction


DISPOSITIONS = frozenset({
    "KEEP_REQUIRED", "KEEP_GUARDRAIL", "PRUNE_CANDIDATE_REDUNDANT",
    "PRUNE_CANDIDATE_STALE", "PRUNE_CANDIDATE_UNSUPPORTED",
    "REVIEW_NARROWING", "REVIEW_RATIONALE", "UNKNOWN",
})
_ALLOWED_KINDS = frozenset({"codex", "claude-code", "file-backed"})
_CLAUDE_SETTINGS = ("settings.json", "settings.local.json")
_RULE_SUFFIXES = frozenset({".md", ".markdown"})
_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_CLAUDE_IMPORT = re.compile(r"(?<!\w)@([^\s`]+\.md)\b")
_SENSITIVE_FIELDS = frozenset({"token", "apikey", "api_key", "password", "secret",
                                "oauth", "credentials", "env", "mcpservers"})
_REFERENCES = {
    "codex": {"config": "https://learn.chatgpt.com/docs/config-file/config-reference",
              "permission": "https://learn.chatgpt.com/docs/agent-configuration/rules",
              "instruction": "https://learn.chatgpt.com/docs/agent-configuration/agents-md"},
    "claude-code": {"config": "https://code.claude.com/docs/en/settings",
                    "permission": "https://code.claude.com/docs/en/permissions",
                    "instruction": "https://code.claude.com/docs/en/memory"},
}


@dataclass(frozen=True)
class Source:
    agent: str
    product: str
    version: str
    alias: str
    path: Path
    surface: str
    ownership: str
    context: str
    loading: str
    support: str


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _identity(value: Any) -> str:
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return Path(value)


def _source(agent: dict[str, Any], alias: str, path: Path, surface: str,
            ownership: str, context: str, loading: str) -> Source:
    return Source(agent["id"], agent["kind"], agent["version"], alias, path,
                  surface, ownership, _digest(context.encode()), loading,
                  _digest(agent["support"].encode()))


def _markdown_sources(agent: dict[str, Any], root: Path, alias: str, name: str,
                      ownership: str, context: str, loading: str) -> list[Source]:
    return [_source(agent, f"{alias}/{name}", root / name, "instruction",
                    ownership, context, loading)]


def _rules(agent: dict[str, Any], directory: Path, alias: str, ownership: str,
           context: str, loading: str) -> list[Source]:
    """Bounded discovery in a known rules directory, without following links."""
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        return [_source(agent, f"{alias}/rules", directory, "instruction",
                        ownership, context, "UNKNOWN")]
    found = []
    for base, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink())
        for name in sorted(files):
            path = Path(base) / name
            if path.suffix.lower() in _RULE_SUFFIXES:
                rel = path.relative_to(directory).as_posix()
                found.append(_source(agent, f"{alias}/rules/{_digest(rel.encode())[:12]}", path,
                                     "instruction", ownership, context, loading))
    return found


def _codex_rules(agent: dict[str, Any], directory: Path, alias: str,
                 ownership: str, context: str, loading: str) -> list[Source]:
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        return [_source(agent, f"{alias}/rules", directory, "permission", ownership,
                        context, "UNKNOWN")]
    return [_source(agent, f"{alias}/rules/{_digest(path.name.encode())[:12]}", path,
                    "permission", ownership, context, loading)
            for path in sorted(directory.iterdir()) if path.suffix == ".rules"]


def discover(enrollment: dict[str, Any]) -> list[Source]:
    """Discover only standard paths under explicitly enrolled roots."""
    agents = enrollment.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError("enrollment requires a nonempty agents list")
    sources: list[Source] = []
    ids: set[str] = set()
    for agent in agents:
        if not isinstance(agent, dict) or agent.get("kind") not in _ALLOWED_KINDS:
            raise ValueError("agent kind must be codex, claude-code, or file-backed")
        if not isinstance(agent.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]+", agent["id"]):
            raise ValueError("agent id must be a safe local alias")
        if agent["id"] in ids:
            raise ValueError("duplicate agent id")
        ids.add(agent["id"])
        for required in ("version", "support", "launch_context"):
            if not isinstance(agent.get(required), str) or not agent[required]:
                raise ValueError(f"{agent['id']} requires {required}")
        if not re.fullmatch(r"[A-Za-z0-9._+-]{1,80}", agent["version"]):
            raise ValueError("version must be a bounded identifier")
        kind = agent["kind"]
        if kind == "file-backed":
            root = _path(agent.get("root"), "file-backed root")
            files = agent.get("files")
            if not isinstance(files, list) or not files:
                raise ValueError("file-backed agent requires explicit files")
            for index, item in enumerate(files):
                if not isinstance(item, dict) or item.get("surface") not in ("config", "permission", "instruction"):
                    raise ValueError("file-backed file requires a surface")
                path = _path(item.get("path"), "file path")
                if not _inside(path.resolve(), root.resolve()):
                    raise ValueError("file-backed path leaves enrolled root")
                sources.append(_source(agent, f"file-{index}", path, item["surface"],
                                       "user", agent["launch_context"], "UNKNOWN"))
            continue
        root = _path(agent.get("config_root"), "config_root")
        projects = agent.get("projects", [])
        if not isinstance(projects, list):
            raise ValueError("projects must be a list")
        context = agent["launch_context"]
        if kind == "codex":
            sources.append(_source(agent, "user/config.toml", root / "config.toml", "config",
                                   "user", context, "documented user layer; effective value unverified"))
            for name in ("AGENTS.override.md", "AGENTS.md"):
                sources.extend(_markdown_sources(agent, root, "user", name, "user", context,
                                                 "first nonempty override or AGENTS.md"))
            sources.extend(_codex_rules(agent, root / "rules", "user", "user", context,
                                        "documented execution-policy layer"))
            for index, value in enumerate(projects):
                project = _path(value, "project")
                alias = f"project-{index}"
                sources.append(_source(agent, f"{alias}/.codex/config.toml", project / ".codex/config.toml",
                                       "config", "shared", context, "conditional on project trust"))
                for name in ("AGENTS.override.md", "AGENTS.md"):
                    sources.extend(_markdown_sources(agent, project, alias, name, "shared", context,
                                                     "first nonempty; path and working-directory conditional"))
                sources.extend(_codex_rules(agent, project / ".codex/rules", alias, "shared", context,
                                            "conditional on project trust"))
        else:
            for name in _CLAUDE_SETTINGS:
                sources.append(_source(agent, f"user/{name}", root / name, "config",
                                       "user", context, "documented settings layer; effective value unverified"))
            for name in ("CLAUDE.md", "CLAUDE.local.md"):
                sources.extend(_markdown_sources(agent, root, "user", name, "user", context,
                                                 "documented user instruction layer"))
            sources.extend(_rules(agent, root / "rules", "user", "user", context,
                                  "conditional Markdown rule"))
            for index, value in enumerate(projects):
                project = _path(value, "project")
                alias = f"project-{index}"
                for name in _CLAUDE_SETTINGS:
                    ownership = "user" if name.endswith(".local.json") else "shared"
                    sources.append(_source(agent, f"{alias}/.claude/{name}", project / ".claude" / name,
                                           "config", ownership, context, "documented project settings layer"))
                for name in ("CLAUDE.md", "CLAUDE.local.md"):
                    ownership = "user" if name.endswith(".local.md") else "shared"
                    sources.extend(_markdown_sources(agent, project, alias, name, ownership, context,
                                                     "ancestor instruction layer; conflicts need judgment"))
                sources.extend(_rules(agent, project / ".claude/rules", alias, "shared", context,
                                      "conditional Markdown rule"))
    aliases = [(s.agent, s.alias) for s in sources]
    if len(aliases) != len(set(aliases)):
        raise ValueError("duplicate source alias")
    return sources


def _unit(source: Source, locator: str, digest: str, disposition: str,
          reason: str, *, evidence: str = "") -> dict[str, str]:
    if disposition not in DISPOSITIONS:
        raise ValueError("invalid disposition")
    return {"agent": source.agent, "product": source.product, "version": source.version,
            "source": source.alias, "surface": source.surface, "ownership": source.ownership,
            "locator": locator, "content_sha256": digest, "disposition": disposition,
            "reason": reason, "loading": source.loading, "context": source.context,
            "precedence": _precedence(source), "support": source.support,
            "provider_reference": _REFERENCES.get(source.product, {}).get(source.surface, "UNKNOWN"),
            "evidence": evidence}


def _precedence(source: Source) -> str:
    if source.product == "codex":
        if source.surface == "permission" or source.path.suffix == ".rules":
            return "matching rules combine by most restrictive decision; active layers conditional"
        if source.surface == "instruction":
            return "root-to-working-directory; first nonempty file per directory, closer guidance later"
        return ("project layer above user when trusted; CLI and profile may override"
                if source.alias.startswith("project-") else
                "user layer below trusted project, profile, and CLI overrides")
    if source.product == "claude-code":
        if source.surface == "permission":
            return "deny then ask then allow; settings lists merge across layers"
        if source.surface == "instruction":
            return "ancestor instructions concatenate; conflict resolution is model judgment"
        return "managed then CLI then project-local then shared project then user; lists may merge"
    return "UNKNOWN"


def _flatten(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        if not value:
            return [(prefix, value)]
        result = []
        for key, child in sorted(value.items()):
            # Do not inventory secret-bearing mixed-store fields or leak keys.
            if key.lower() in _SENSITIVE_FIELDS or any(
                    marker in key.lower() for marker in ("token", "secret", "password", "apikey", "api_key")):
                result.append((f"{prefix}.{key}" if prefix else key, None))
                continue
            result.extend(_flatten(child, f"{prefix}.{key}" if prefix else key))
        return result
    if isinstance(value, list):
        if not value:
            return [(prefix, value)]
        return [(f"{prefix}[{i}]", child) for i, child in enumerate(value)]
    return [(prefix, value)]


def _codex_rule_units(source: Source, text: str) -> list[dict[str, str]]:
    """Parse the documented literal prefix_rule subset without evaluating Starlark."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        raise ValueError("unsupported rules syntax") from None
    units: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, statement in enumerate(tree.body, 1):
        call = statement.value if isinstance(statement, ast.Expr) else None
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "prefix_rule":
            units.append(_unit(source, f"statement-{index}", _digest(ast.dump(statement).encode()),
                               "UNKNOWN", "unsupported rule statement"))
            continue
        try:
            fields = {item.arg: ast.literal_eval(item.value) for item in call.keywords if item.arg}
        except (ValueError, TypeError, SyntaxError, MemoryError):
            units.append(_unit(source, f"rule-{index}", _digest(ast.dump(call).encode()),
                               "UNKNOWN", "nonliteral rule requires installed-version parser evidence"))
            continue
        pattern = fields.get("pattern")
        decision = fields.get("decision", "allow")
        if not isinstance(pattern, list) or not pattern or not isinstance(decision, str) or decision not in {"allow", "prompt", "forbidden"}:
            units.append(_unit(source, f"rule-{index}", _digest(ast.dump(call).encode()),
                               "UNKNOWN", "unsupported or incomplete prefix_rule"))
            continue
        identity = _identity((pattern, decision))
        if decision in {"prompt", "forbidden"}:
            disposition, reason = "KEEP_GUARDRAIL", "restrictive execution boundary"
        elif identity in seen:
            disposition, reason = "PRUNE_CANDIDATE_REDUNDANT", "duplicate allow rule in one source; verify rationale"
        else:
            disposition, reason = "UNKNOWN", "allow rule need and effective reach require review"
        seen.add(identity)
        units.append(_unit(source, f"rule-{index}", identity, disposition, reason))
    return units


def _parse(source: Source, raw: bytes) -> list[dict[str, str]]:
    if source.surface == "instruction":
        text = raw.decode("utf-8")
        headings = list(_HEADING.finditer(text))
        parts = []
        if not headings:
            parts = [("document", text)]
        else:
            if text[:headings[0].start()].strip():
                parts.append(("preamble", text[:headings[0].start()]))
            parts.extend((f"section-{i}", text[match.start(): headings[i].start() if i < len(headings) else len(text)])
                         for i, match in enumerate(headings, 1))
        units = [_unit(source, locator, _digest(body.encode()), "REVIEW_RATIONALE",
                       "written guidance requires human semantic judgment") for locator, body in parts]
        if source.product == "claude-code":
            for i, match in enumerate(_CLAUDE_IMPORT.finditer(text), 1):
                units.append(_unit(source, f"import-{i}", _digest(match.group(1).encode()),
                                   "UNKNOWN", "import target and loading remain unverified"))
        for kind in sorted({finding.kind for finding in scan_enrolled_instruction(source.path, text)}):
            units.append(_unit(source, f"drift:{kind}", _digest(kind.encode()),
                               "REVIEW_RATIONALE", "existing instruction-drift scanner advisory"))
        return units
    if source.product == "codex" and source.path.suffix == ".rules":
        return _codex_rule_units(source, raw.decode("utf-8"))
    try:
        value = tomllib.loads(raw.decode("utf-8")) if source.path.suffix == ".toml" else json.loads(raw)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(type(exc).__name__) from None
    if not isinstance(value, dict):
        raise ValueError("root is not an object")
    units = []
    for locator, item in _flatten(value):
        if not locator:
            continue
        surface = "permission" if (locator.startswith("permissions.") or locator in
                                   {"approval_policy", "sandbox_mode"}) else "config"
        # Settings names and values may themselves be private. Locators are
        # stable salted-by-source hashes rather than raw keys/paths/commands.
        safe_locator = f"entry-{_digest(locator.encode())[:12]}"
        unit_source = Source(source.agent, source.product, source.version, source.alias,
                             source.path, surface, source.ownership, source.context,
                             source.loading, source.support)
        leaf = locator.split(".")[-1].lower()
        if item is None and (leaf in _SENSITIVE_FIELDS or any(
                marker in leaf for marker in ("token", "secret", "password", "apikey", "api_key"))):
            units.append(_unit(unit_source, safe_locator, "", "UNKNOWN",
                               "sensitive field excluded from inspection"))
            continue
        disposition = "KEEP_GUARDRAIL" if surface == "permission" and (
            locator.startswith("permissions.deny[") or locator.startswith("permissions.ask[")
            or locator in {"approval_policy", "sandbox_mode"}) else "UNKNOWN"
        reason = ("restrictive or approval boundary; preserve pending human review"
                  if disposition == "KEEP_GUARDRAIL" else
                  "effective behavior and necessity require installed-version evidence")
        if surface == "permission" and locator.startswith("permissions.allow["):
            same_list = [unit for unit in units if unit["surface"] == "permission"
                         and unit["content_sha256"] == _identity(item)]
            if same_list:
                disposition = "PRUNE_CANDIDATE_REDUNDANT"
                reason = "exact duplicate allow entry in one settings file; verify intent"
        units.append(_unit(unit_source, safe_locator, _identity(item), disposition, reason))
    return units


def _safe_read(source: Source) -> tuple[bytes | None, str | None]:
    path = source.path
    if path.is_symlink():
        return None, "symlink not followed"
    try:
        if not path.exists():
            return None, "absent"
        if not path.is_file():
            return None, "not a regular file"
        before = path.stat()
        with path.open("rb") as handle:
            raw = handle.read(2_000_001)
        after = path.stat()
        if len(raw) > 2_000_000:
            return None, "file exceeds 2 MB bound"
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            return None, "source changed during read"
        return raw, None
    except OSError:
        return None, "unreadable"


def review(enrollment: dict[str, Any], previous: dict[str, Any] | None = None,
           baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    """Produce a sanitized deterministic comparison; no output destination chosen here."""
    sources = discover(enrollment)
    units: list[dict[str, str]] = []
    coverage: list[dict[str, str]] = []
    physical: dict[Path, tuple[bytes | None, str | None]] = {}
    for source in sources:
        paths = [source.path]
        if not paths:
            coverage.append({"agent": source.agent, "source": source.alias, "status": "absent"})
        for path in paths:
            active = source if path == source.path else Source(
                source.agent, source.product, source.version,
                f"{source.alias}/{_digest(path.name.encode())[:12]}", path, "permission", source.ownership,
                source.context, source.loading, source.support)
            if path not in physical:
                physical[path] = _safe_read(active)
            raw, failure = physical[path]
            if failure:
                coverage.append({"agent": active.agent, "source": active.alias, "status": failure})
                if failure != "absent":
                    units.append(_unit(active, "file", "", "UNKNOWN", failure))
                continue
            assert raw is not None
            try:
                parsed = _parse(active, raw)
                units.extend(parsed or [_unit(active, "file", _digest(raw), "UNKNOWN",
                                              "empty or unsupported inventory")])
                coverage.append({"agent": active.agent, "source": active.alias,
                                 "status": "inspected", "sha256": _digest(raw)})
            except (ValueError, UnicodeDecodeError):
                units.append(_unit(active, "file", _digest(raw), "UNKNOWN", "parse failure"))
                coverage.append({"agent": active.agent, "source": active.alias, "status": "parse failure"})
    units.sort(key=lambda u: (u["agent"], u["source"], u["locator"], u["content_sha256"]))
    coverage.sort(key=lambda c: (c["agent"], c["source"]))
    current_ids = {_identity((u["agent"], u["source"], u["locator"], u["content_sha256"],
                              u["disposition"], u["loading"], u["precedence"], u["version"], u["support"])) for u in units}
    scope_identity = _identity(enrollment)
    def compare(other: dict[str, Any] | None) -> dict[str, Any]:
        if other is None:
            return {"status": "unavailable", "new": [], "resolved": []}
        if other.get("schema_version") != 1 or not isinstance(other.get("fingerprints"), list):
            raise ValueError("comparison record has unsupported schema")
        old_values = other["fingerprints"]
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
               for value in old_values):
            raise ValueError("comparison record has invalid fingerprints")
        old = set(old_values)
        status = "compared" if other.get("scope_sha256") == scope_identity else "scope_changed"
        return {"status": status, "new": sorted(current_ids - old),
                "resolved": sorted(old - current_ids)}
    critical = any(c["status"] not in ("inspected", "absent") for c in coverage)
    return {"schema_version": 1, "authority": "observe-and-report", "result": "PARTIAL" if critical else "OBSERVED",
            "scope_sha256": scope_identity, "coverage": coverage, "units": units,
            "fingerprints": sorted(current_ids), "previous": compare(previous),
            "accepted_baseline": compare(baseline),
            "limitations": ["Effective behavior requires runtime and provider evidence; fixture inspection is not live qualification.",
                            "No disposition accepts drift or authorizes an inspected-source edit."]}


def write_record(report: dict[str, Any], destination: Path) -> None:
    """Create one declared local record exclusively; never choose a fallback."""
    if not destination.is_absolute() or not destination.parent.is_dir():
        raise ValueError("declared record destination is unavailable")
    payload = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode()
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
