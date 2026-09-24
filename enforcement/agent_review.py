"""Local, inert inventory of enrolled agent files. No inspected source is written.

The enrollment and record destination are operator-owned local inputs. This
module deliberately does not launch either agent to ask for its effective state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import ast
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tomllib
from typing import Any
from urllib.request import Request, urlopen

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
_CLAUDE_IMPORT = re.compile(r"(?<!\w)@([A-Za-z0-9_./~-]+)")
_CODEX_SAFE_FIELDS = frozenset({"approval_policy", "sandbox_mode", "model"})
_CLAUDE_SAFE_FIELDS = frozenset({"model"})
_CLAUDE_PERMISSION_LISTS = frozenset({"allow", "ask", "deny"})
_CLAUDE_PERMISSION_SCALARS = frozenset({"defaultMode", "disableBypassPermissionsMode"})
_REFERENCES = {
    "codex": {"config": "https://learn.chatgpt.com/docs/config-file/config-reference",
              "permission": "https://learn.chatgpt.com/docs/agent-configuration/rules",
              "instruction": "https://learn.chatgpt.com/docs/agent-configuration/agents-md"},
    "claude-code": {"config": "https://code.claude.com/docs/en/settings",
                    "permission": "https://code.claude.com/docs/en/permissions",
                    "instruction": "https://code.claude.com/docs/en/memory"},
}
_CONTEXT_DOMAINS = {
    "codex": ("managed_and_system", "profile_trust_and_invocation", "nested_and_fallback_instructions"),
    "claude-code": ("managed", "ancestor_and_nested_instructions", "environment_and_invocation"),
}


def refresh_provider_docs(enrollment: dict[str, Any]) -> dict[str, str]:
    """Fetch only official public docs; send no inspected source data."""
    kinds = {agent.get("kind") for agent in enrollment.get("agents", []) if isinstance(agent, dict)}
    urls = sorted({url for kind in kinds for url in _REFERENCES.get(kind, {}).values()})
    checked: dict[str, str] = {}
    for url in urls:
        request = Request(url, headers={"User-Agent": "local-agent-review/1"})
        with urlopen(request, timeout=15) as response:
            final_url = response.geturl()
            if not (final_url.startswith("https://learn.chatgpt.com/") or
                    final_url.startswith("https://code.claude.com/")):
                raise ValueError("provider documentation redirected outside official host")
            body = response.read(4_000_001)
            if not body or len(body) > 4_000_000:
                raise ValueError("provider documentation unavailable or over size bound")
            checked[url] = _digest(body)
    return checked


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
    boundary: Path | None = None


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _identity(value: Any) -> str:
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _scope_identity(enrollment: dict[str, Any]) -> str:
    """Discovery and execution context change scope; retention and invariants do not."""
    selected = []
    for agent in enrollment["agents"]:
        selected.append({key: agent.get(key) for key in
                         ("id", "kind", "config_root", "root", "projects", "files", "context_files",
                          "launch_context", "context_evidence")})
    return _identity(selected)


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return Path(value)


def _directory(value: Any, label: str) -> Path:
    path = _path(value, label)
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{label} must be an existing nonsymlink directory")
    return path


def _source(agent: dict[str, Any], alias: str, path: Path, surface: str,
            ownership: str, context: str, loading: str) -> Source:
    boundary = None
    if alias.startswith("user/"):
        boundary = Path(agent["config_root"])
    elif alias.startswith("project-"):
        match = re.match(r"project-(\d+)", alias)
        if match:
            boundary = Path(agent["projects"][int(match.group(1))])
    elif alias.startswith("file-"):
        boundary = Path(agent["root"])
    elif alias.startswith("context-file-"):
        roots = [agent.get("config_root"), agent.get("root"), *agent.get("projects", [])]
        for candidate in roots:
            if isinstance(candidate, str) and Path(candidate).is_absolute() and _inside(path, Path(candidate)):
                boundary = Path(candidate)
                break
        if boundary is None:
            boundary = Path(path.anchor)
    return Source(agent["id"], agent["kind"], agent["version"], alias, path,
                  surface, ownership, _digest(context.encode()), loading,
                  _digest(agent["support"].encode()), boundary)


def _markdown_sources(agent: dict[str, Any], root: Path, alias: str, name: str,
                      ownership: str, context: str, loading: str) -> list[Source]:
    return [_source(agent, f"{alias}/{name}", root / name, "instruction",
                    ownership, context, loading)]


def _rules(agent: dict[str, Any], directory: Path, alias: str, ownership: str,
           context: str, loading: str) -> list[Source]:
    """Bounded discovery in a known rules directory, without following links."""
    if not directory.exists():
        return []
    if directory.is_symlink() or directory.parent.is_symlink() or not directory.is_dir():
        return [_source(agent, f"{alias}/rules", directory, "instruction",
                        ownership, context, "UNKNOWN")]
    found = []
    walk_errors: list[OSError] = []
    for base, dirs, files in os.walk(directory, followlinks=False, onerror=walk_errors.append):
        for name in dirs:
            if (Path(base) / name).is_symlink():
                rel = (Path(base) / name).relative_to(directory).as_posix()
                found.append(_source(agent, f"{alias}/rules/{_digest(rel.encode())[:12]}",
                                     Path(base) / name, "instruction", ownership, context, "UNKNOWN"))
        dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink())
        for name in sorted(files):
            path = Path(base) / name
            if path.suffix.lower() in _RULE_SUFFIXES:
                rel = path.relative_to(directory).as_posix()
                found.append(_source(agent, f"{alias}/rules/{_digest(rel.encode())[:12]}", path,
                                     "instruction", ownership, context, loading))
    for index, error in enumerate(walk_errors):
        path = Path(error.filename) if error.filename else directory
        found.append(_source(agent, f"{alias}/rules/unreadable-{index}", path,
                             "instruction", ownership, context, "UNKNOWN"))
    return found


def _codex_rules(agent: dict[str, Any], directory: Path, alias: str,
                 ownership: str, context: str, loading: str) -> list[Source]:
    if not directory.exists():
        return []
    if directory.is_symlink() or directory.parent.is_symlink() or not directory.is_dir():
        return [_source(agent, f"{alias}/rules", directory, "permission", ownership,
                        context, "UNKNOWN")]
    found = []
    for path in sorted(directory.iterdir()):
        if path.suffix == ".rules" or path.is_dir():
            found.append(_source(agent, f"{alias}/rules/{_digest(path.name.encode())[:12]}", path,
                                 "permission", ownership, context,
                                 loading if path.suffix == ".rules" else "UNKNOWN"))
    return found


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
        extra = agent.get("context_files", [])
        if not isinstance(extra, list):
            raise ValueError("context_files must be a list")
        for index, item in enumerate(extra):
            if (not isinstance(item, dict) or item.get("surface") not in
                    {"config", "permission", "instruction"} or item.get("ownership") not in
                    {"user", "shared", "managed"}):
                raise ValueError("context file requires surface and ownership")
            path = _path(item.get("path"), "context file path")
            sources.append(_source(agent, f"context-file-{index}", path, item["surface"],
                                   item["ownership"], agent["launch_context"],
                                   "operator-enrolled context; effectiveness unverified"))
        if kind == "file-backed":
            root = _directory(agent.get("root"), "file-backed root")
            files = agent.get("files")
            if not isinstance(files, list) or not files:
                raise ValueError("file-backed agent requires explicit files")
            for index, item in enumerate(files):
                if not isinstance(item, dict) or item.get("surface") not in ("config", "permission", "instruction"):
                    raise ValueError("file-backed file requires a surface")
                path = _path(item.get("path"), "file path")
                if not _inside(path.resolve(), root.resolve()):
                    raise ValueError("file-backed path leaves enrolled root")
                ownership = item.get("ownership")
                if ownership not in {"user", "shared", "managed"}:
                    raise ValueError("file-backed ownership must be user, shared, or managed")
                sources.append(_source(agent, f"file-{index}", path, item["surface"],
                                       ownership, agent["launch_context"], "UNKNOWN"))
            continue
        root = _directory(agent.get("config_root"), "config_root")
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
                project = _directory(value, "project")
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
                project = _directory(value, "project")
                alias = f"project-{index}"
                for name in _CLAUDE_SETTINGS:
                    ownership = "user" if name.endswith(".local.json") else "shared"
                    sources.append(_source(agent, f"{alias}/.claude/{name}", project / ".claude" / name,
                                           "config", ownership, context, "documented project settings layer"))
                for name in ("CLAUDE.md", "CLAUDE.local.md"):
                    ownership = "user" if name.endswith(".local.md") else "shared"
                    sources.extend(_markdown_sources(agent, project, alias, name, ownership, context,
                                                     "ancestor instruction layer; conflicts need judgment"))
                sources.extend(_markdown_sources(agent, project / ".claude", f"{alias}/.claude",
                                                 "CLAUDE.md", "shared", context,
                                                 "project memory layer; conflicts need judgment"))
                sources.extend(_rules(agent, project / ".claude/rules", alias, "shared", context,
                                      "conditional Markdown rule"))
    aliases = [(s.agent, s.alias) for s in sources]
    if len(aliases) != len(set(aliases)):
        raise ValueError("duplicate source alias")
    return sources


def _unit(source: Source, locator: str, digest: str, disposition: str,
          reason: str) -> dict[str, str]:
    if disposition not in DISPOSITIONS:
        raise ValueError("invalid disposition")
    return {"agent": source.agent, "product": source.product, "version": source.version,
            "source": source.alias, "surface": source.surface, "ownership": source.ownership,
            "locator": locator, "content_sha256": digest, "disposition": disposition,
            "reason": reason, "loading": source.loading, "context": source.context,
            "precedence": _precedence(source), "support": source.support,
            "provider_reference": "UNKNOWN" if source.ownership == "context" else
            _REFERENCES.get(source.product, {}).get(
                "config" if source.path.suffix in {".toml", ".json"} else source.surface, "UNKNOWN"),
            "evidence": ""}


def _precedence(source: Source) -> str:
    if source.ownership == "context":
        return "UNKNOWN"
    if source.alias.startswith("context-file-"):
        return "operator-enrolled context; precedence unverified"
    if source.product == "codex":
        if source.path.suffix == ".rules":
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


def _structured_units(source: Source, value: dict[str, Any]) -> list[dict[str, str]]:
    """Select documented safe provider fields; keep all other structures opaque."""
    units: list[dict[str, str]] = []
    seen_allow: set[str] = set()

    def add(locator: str, item: Any, surface: str, opaque: bool = False) -> None:
        selected = replace(source, surface=surface)
        if opaque:
            units.append(_unit(selected, f"opaque-{_digest(locator.encode())[:12]}", "",
                               "UNKNOWN", "unsupported structured field; contents not inventoried"))
            return
        digest = _identity(item)
        locator_digest = _identity((re.sub(r"\[\d+\]", "[]", locator), item))
        occurrence = sum(unit["locator"].startswith(f"entry-{locator_digest[:12]}-") for unit in units) + 1
        safe_locator = f"entry-{locator_digest[:12]}-{occurrence}"
        restrictive_claude = source.product == "claude-code" and (
            locator.startswith("permissions.deny[") or locator.startswith("permissions.ask["))
        restrictive_codex = source.product == "codex" and isinstance(item, str) and (
            (locator == "approval_policy" and item in {"on-request", "untrusted"}) or
            (locator == "sandbox_mode" and item in {"read-only", "workspace-write"}))
        disposition = "KEEP_GUARDRAIL" if restrictive_claude or restrictive_codex else "UNKNOWN"
        reason = ("restrictive or approval boundary; preserve pending human review"
                  if disposition == "KEEP_GUARDRAIL" else
                  "effective behavior and necessity require installed-version evidence")
        if source.product == "claude-code" and locator.startswith("permissions.allow["):
            if digest in seen_allow and source.ownership == "user":
                disposition, reason = ("PRUNE_CANDIDATE_REDUNDANT",
                                       "exact duplicate allow entry in one settings file; verify intent")
            elif digest in seen_allow:
                disposition, reason = ("REVIEW_RATIONALE",
                                       "duplicate shared allow entry; route to source owner")
            seen_allow.add(digest)
        units.append(_unit(selected, safe_locator, digest, disposition, reason))

    safe_fields = _CODEX_SAFE_FIELDS if source.product == "codex" else _CLAUDE_SAFE_FIELDS
    for key, item in sorted(value.items()):
        if key in safe_fields and isinstance(item, (str, bool, int, float)):
            add(key, item, "permission" if key in {"approval_policy", "sandbox_mode"} else "config")
        elif source.product == "codex" and key == "sandbox_workspace_write" and isinstance(item, dict):
            for setting, setting_value in sorted(item.items()):
                locator = f"sandbox_workspace_write.{setting}"
                if setting == "writable_roots" and isinstance(setting_value, list) and all(
                        isinstance(root, str) for root in setting_value):
                    for index, root in enumerate(setting_value):
                        add(f"{locator}[{index}]", root, "permission")
                elif setting == "network_access" and isinstance(setting_value, bool):
                    add(locator, setting_value, "permission")
                else:
                    add(locator, None, "permission", opaque=True)
        elif source.product == "claude-code" and key == "permissions" and isinstance(item, dict):
            for permission_key, permission_value in sorted(item.items()):
                locator = f"permissions.{permission_key}"
                if permission_key in _CLAUDE_PERMISSION_LISTS and isinstance(permission_value, list):
                    if all(isinstance(entry, str) for entry in permission_value):
                        for index, entry in enumerate(permission_value):
                            add(f"{locator}[{index}]", entry, "permission")
                    else:
                        add(locator, None, "permission", opaque=True)
                elif permission_key in _CLAUDE_PERMISSION_SCALARS and isinstance(
                        permission_value, (str, bool)):
                    add(locator, permission_value, "permission")
                else:
                    add(locator, None, "permission", opaque=True)
        else:
            add(key, None, source.surface, opaque=True)
    return units


def _codex_rule_units(source: Source, text: str) -> list[dict[str, str]]:
    """Parse the documented literal prefix_rule subset without evaluating Starlark."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, MemoryError):
        raise ValueError("unsupported rules syntax") from None
    units: list[dict[str, str]] = []
    seen: dict[str, int] = {}
    for index, statement in enumerate(tree.body, 1):
        call = statement.value if isinstance(statement, ast.Expr) else None
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "prefix_rule":
            identity = _digest(ast.dump(statement).encode())
            seen[identity] = seen.get(identity, 0) + 1
            units.append(_unit(source, f"statement-{identity[:12]}-{seen[identity]}", identity,
                               "UNKNOWN", "unsupported rule statement"))
            continue
        try:
            fields = {item.arg: ast.literal_eval(item.value) for item in call.keywords if item.arg}
        except (ValueError, TypeError, SyntaxError, MemoryError):
            identity = _digest(ast.dump(call).encode())
            seen[identity] = seen.get(identity, 0) + 1
            units.append(_unit(source, f"rule-{identity[:12]}-{seen[identity]}", identity,
                               "UNKNOWN", "nonliteral rule requires installed-version parser evidence"))
            continue
        pattern = fields.get("pattern")
        decision = fields.get("decision", "allow")
        if not isinstance(pattern, list) or not pattern or not isinstance(decision, str) or decision not in {"allow", "prompt", "forbidden"}:
            identity = _digest(ast.dump(call).encode())
            seen[identity] = seen.get(identity, 0) + 1
            units.append(_unit(source, f"rule-{identity[:12]}-{seen[identity]}", identity,
                               "UNKNOWN", "unsupported or incomplete prefix_rule"))
            continue
        identity = _identity((pattern, decision))
        occurrence = seen.get(identity, 0) + 1
        if decision in {"prompt", "forbidden"}:
            disposition, reason = "KEEP_GUARDRAIL", "restrictive execution boundary"
        elif identity in seen and source.ownership == "user":
            disposition, reason = "PRUNE_CANDIDATE_REDUNDANT", "duplicate allow rule in one source; verify rationale"
        elif identity in seen:
            disposition, reason = "REVIEW_RATIONALE", "duplicate shared rule; route to source owner"
        else:
            disposition, reason = "UNKNOWN", "allow rule need and effective reach require review"
        seen[identity] = occurrence
        units.append(_unit(source, f"rule-{identity[:12]}-{occurrence}", identity, disposition, reason))
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
            heading_counts: dict[str, int] = {}
            for i, match in enumerate(headings, 1):
                heading_id = _digest(match.group(1).strip().casefold().encode())[:12]
                heading_counts[heading_id] = heading_counts.get(heading_id, 0) + 1
                parts.append((f"section-{heading_id}-{heading_counts[heading_id]}",
                              text[match.start(): headings[i].start() if i < len(headings) else len(text)]))
        shadowed = source.loading.startswith("shadowed by")
        units = [_unit(source, locator, _digest(body.encode()),
                       "UNKNOWN" if shadowed else "REVIEW_RATIONALE",
                       "not loaded in this context" if shadowed else
                       "written guidance requires human semantic judgment") for locator, body in parts]
        if source.product == "claude-code":
            imports_seen: dict[str, int] = {}
            for match in _CLAUDE_IMPORT.finditer(text):
                identity = _digest(match.group(1).encode())
                imports_seen[identity] = imports_seen.get(identity, 0) + 1
                units.append(_unit(source, f"import-{identity[:12]}-{imports_seen[identity]}", identity,
                                   "UNKNOWN", "import target and loading remain unverified"))
        advisory = () if shadowed else scan_enrolled_instruction(source.path, text)
        if source.ownership == "user":
            advisory = tuple(finding for finding in advisory
                             if finding.kind != "agents_missing_canonical_playbook_reference")
        for kind in sorted({finding.kind for finding in advisory}):
            units.append(_unit(source, f"drift:{kind}", _digest(kind.encode()),
                               "REVIEW_RATIONALE", "existing instruction-drift scanner advisory"))
        return units
    if source.product == "codex" and source.path.suffix == ".rules":
        return _codex_rule_units(source, raw.decode("utf-8"))
    if source.product == "file-backed":
        return [_unit(source, "opaque-file", "", "UNKNOWN",
                      "unverified file-backed semantics; contents not inventoried")]
    try:
        value = tomllib.loads(raw.decode("utf-8")) if source.path.suffix == ".toml" else json.loads(raw)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(type(exc).__name__) from None
    if not isinstance(value, dict):
        raise ValueError("root is not an object")
    return _structured_units(source, value)


def _safe_read(source: Source) -> tuple[bytes | None, str | None, str]:
    path = source.path
    if source.boundary is not None:
        try:
            relative = path.relative_to(source.boundary)
        except ValueError:
            return None, "outside enrolled root", ""
        current = source.boundary
        for part in relative.parts[:-1]:
            current = current / part
            if current.is_symlink():
                return None, "symlink not followed", ""
    link_path = None
    link_before = None
    link_name = ""
    if path.is_symlink():
        if not (source.product == "codex" and source.surface == "permission"
                and "/rules/" in source.alias and path.suffix == ".rules"):
            return None, "symlink not followed", ""
        try:
            link_before = path.lstat()
            link_name = os.readlink(path)
            if (not stat.S_ISLNK(link_before.st_mode) or not link_name.endswith(".rules")
                    or "/" in link_name or link_name in {".", ".."}):
                return None, "symlink not followed", ""
            link_path = path
            path = path.parent / link_name
            if path.is_symlink() or not path.is_file():
                return None, "symlink not followed", ""
        except OSError:
            return None, "unreadable", ""
    try:
        if not path.exists():
            return None, "absent", ""
        if not path.is_file():
            return None, "not a regular file", ""
        if link_path is not None and not hasattr(os, "O_NOFOLLOW"):
            return None, "symlink not followed", ""
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                return None, "not a regular file", ""
            raw = handle.read(2_000_001)
            after = os.fstat(handle.fileno())
        if len(raw) > 2_000_000:
            return None, "file exceeds 2 MB bound", ""
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            return None, "source changed during read", ""
        if link_path is not None:
            link_after = link_path.lstat()
            if (link_before.st_dev, link_before.st_ino, link_before.st_size, link_before.st_mtime_ns) != (
                    link_after.st_dev, link_after.st_ino, link_after.st_size, link_after.st_mtime_ns) or os.readlink(link_path) != link_name:
                return None, "source changed during read", ""
        return raw, None, _digest(link_name.encode()) if link_path is not None else ""
    except OSError:
        return None, "unreadable", ""


def review(enrollment: dict[str, Any], previous: dict[str, Any] | None = None,
           baseline: dict[str, Any] | None = None,
           provider_docs: dict[str, str] | None = None) -> dict[str, Any]:
    """Produce a sanitized deterministic comparison; no output destination chosen here."""
    sources = discover(enrollment)
    units: list[dict[str, str]] = []
    coverage: list[dict[str, str]] = []
    physical: dict[tuple[Path, Path | None], tuple[bytes | None, str | None, str]] = {}
    for agent in enrollment["agents"]:
        if agent["kind"] not in _CONTEXT_DOMAINS:
            continue
        evidence = agent.get("context_evidence", {})
        if not isinstance(evidence, dict):
            raise ValueError("context_evidence must be an object")
        for domain in _CONTEXT_DOMAINS[agent["kind"]]:
            identifier = evidence.get(domain)
            if isinstance(identifier, str) and re.fullmatch(r"verified:[A-Za-z0-9._/-]{6,}", identifier):
                coverage.append({"agent": agent["id"], "source": f"context/{domain}",
                                 "status": "operator_attested", "evidence_sha256": _digest(identifier.encode())})
            else:
                coverage.append({"agent": agent["id"], "source": f"context/{domain}",
                                 "status": "unverified_context"})
                context_source = _source(agent, f"context/{domain}", Path("/"), "config",
                                         "context", agent["launch_context"], "UNKNOWN")
                units.append(_unit(context_source, "context", "", "UNKNOWN",
                                   "higher or conditional layer not qualified"))
    for source in sources:
        active = source
        if source.product == "codex" and source.path.name == "AGENTS.md":
            override = source.path.with_name("AGENTS.override.md")
            override_key = (override, source.boundary)
            if override_key not in physical:
                physical[override_key] = _safe_read(replace(source, path=override))
            override_raw, override_failure, _ = physical[override_key]
            if override_failure is None and override_raw is not None and override_raw.strip():
                active = replace(source, loading="shadowed by nonempty override; not loaded")
        source_key = (source.path, source.boundary)
        if source_key not in physical:
            physical[source_key] = _safe_read(active)
        raw, failure, link_sha256 = physical[source_key]
        if failure:
            required = active.alias.startswith(("context-file-", "file-"))
            status = "missing enrolled file" if failure == "absent" and required else failure
            coverage.append({"agent": active.agent, "source": active.alias, "status": status})
            if status != "absent":
                units.append(_unit(active, "file", "", "UNKNOWN", status))
            continue
        assert raw is not None
        try:
            parsed = _parse(active, raw)
            units.extend(parsed or [_unit(active, "file", _digest(raw), "UNKNOWN",
                                          "empty or unsupported inventory")])
            observation = {"agent": active.agent, "source": active.alias,
                           "status": "inspected", "sha256": _digest(raw)}
            if link_sha256:
                observation["link_sha256"] = link_sha256
            coverage.append(observation)
        except (ValueError, UnicodeDecodeError, RecursionError):
            units.append(_unit(active, "file", _digest(raw), "UNKNOWN", "parse failure"))
            coverage.append({"agent": active.agent, "source": active.alias, "status": "parse failure"})
    for agent in enrollment["agents"]:
        if agent["kind"] == "file-backed":
            continue
        user_sources = [item for item in coverage if item["agent"] == agent["id"]
                        and item["source"].startswith("user/")]
        if user_sources and not any(item["status"] == "inspected" for item in user_sources):
            coverage.append({"agent": agent["id"], "source": "user-source-set",
                             "status": "no user source inspected"})
            context_source = _source(agent, "user-source-set", Path("/"), "config",
                                     "context", agent["launch_context"], "UNKNOWN")
            units.append(_unit(context_source, "source-set", "", "UNKNOWN",
                               "all user sources absent or unavailable"))
    units.sort(key=lambda u: (u["agent"], u["source"], u["locator"], u["content_sha256"]))
    coverage.sort(key=lambda c: (c["agent"], c["source"]))
    invariant_results: list[dict[str, str]] = []
    for invariant in enrollment.get("invariants", []):
        if not isinstance(invariant, dict) or not all(isinstance(invariant.get(key), str) for key in
                                                       ("agent", "source", "locator", "expected_content_sha256", "evidence")):
            raise ValueError("invariant requires agent, source, locator, expected_content_sha256, evidence")
        expected = invariant["expected_content_sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("invariant expected_content_sha256 must be SHA-256")
        matching = any(unit["agent"] == invariant["agent"] and unit["source"] == invariant["source"]
                       and unit["locator"] == invariant["locator"] and unit["content_sha256"] == expected
                       and not unit["loading"].startswith("shadowed") for unit in units)
        invariant_results.append({"agent": invariant["agent"], "source": invariant["source"],
                                  "locator": invariant["locator"], "expected_sha256": expected,
                                  "evidence_sha256": _digest(invariant["evidence"].encode()),
                                  "status": "present" if matching else "drift"})
    current_ids = {_identity((u["agent"], u["source"], u["locator"], u["content_sha256"],
                              u["disposition"], u["loading"], u["precedence"], u["version"], u["support"])) for u in units}
    observed_ids = {_identity((item["agent"], item["source"], item["status"],
                               item.get("sha256", ""), item.get("evidence_sha256", "")) +
                              ((item["link_sha256"],) if "link_sha256" in item else ()))
                    for item in coverage}
    current_docs = provider_docs or {}
    scope_identity = _scope_identity(enrollment)
    def compare(other: dict[str, Any] | None) -> dict[str, Any]:
        if other is None:
            return {"status": "unavailable", "reference_sha256": "", "new": [], "resolved": [],
                    "source_new": [], "source_resolved": [], "provider_docs_changed": []}
        if (other.get("schema_version") != 2 or other.get("result") != "OBSERVED"
                or not isinstance(other.get("fingerprints"), list)
                or not isinstance(other.get("observation_fingerprints"), list)
                or not isinstance(other.get("provider_docs"), dict)):
            raise ValueError("comparison record has unsupported schema")
        old_values = other["fingerprints"]
        old_observed = other["observation_fingerprints"]
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
               for value in [*old_values, *old_observed]):
            raise ValueError("comparison record has invalid fingerprints")
        old_docs = other["provider_docs"]
        if any(not isinstance(url, str) or not isinstance(digest, str) or
               not re.fullmatch(r"[0-9a-f]{64}", digest) for url, digest in old_docs.items()):
            raise ValueError("comparison record has invalid provider documentation")
        old = set(old_values)
        old_sources = set(old_observed)
        status = "compared" if other.get("scope_sha256") == scope_identity else "scope_changed"
        return {"status": status, "reference_sha256": _identity(other),
                "new": sorted(current_ids - old),
                "resolved": sorted(old - current_ids),
                "source_new": sorted(observed_ids - old_sources),
                "source_resolved": sorted(old_sources - observed_ids),
                "provider_docs_changed": sorted(url for url in current_docs.keys() | old_docs.keys()
                                                if current_docs.get(url) != old_docs.get(url))}
    previous_comparison = compare(previous)
    baseline_comparison = compare(baseline)
    baseline_status = ("unavailable" if baseline is None else "drift" if
                       any(baseline_comparison[key] for key in
                           ("new", "resolved", "source_new", "source_resolved", "provider_docs_changed")) or
                       baseline_comparison["status"] == "scope_changed" else "aligned")
    critical = (any(c["status"] not in ("inspected", "absent", "operator_attested") for c in coverage)
                or any(item["status"] == "drift" for item in invariant_results))
    return {"schema_version": 2, "authority": "observe-and-report", "result": "PARTIAL" if critical else "OBSERVED",
            "scope_sha256": scope_identity, "coverage": coverage, "units": units,
            "invariants": invariant_results,
            "provider_docs": current_docs,
            "fingerprints": sorted(current_ids), "observation_fingerprints": sorted(observed_ids),
            "previous": previous_comparison,
            "accepted_baseline": baseline_comparison, "baseline_status": baseline_status,
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
