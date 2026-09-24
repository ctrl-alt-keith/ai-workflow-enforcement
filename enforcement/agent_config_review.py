"""Manifest-scoped, read-only local review of agent configuration sources.

The manifest is an operator-owned declaration of scope, never an instruction
source. This module deliberately does not start either agent or read its home.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tomllib
from typing import Any
from urllib.parse import urlparse

from .config import ScannerConfig
from .drift_scanner import scan as scan_instruction_drift


DISPOSITIONS = frozenset({
    "KEEP_REQUIRED", "KEEP_GUARDRAIL", "PRUNE_CANDIDATE_REDUNDANT",
    "PRUNE_CANDIDATE_STALE", "PRUNE_CANDIDATE_UNSUPPORTED",
    "REVIEW_NARROWING", "REVIEW_RATIONALE", "UNKNOWN",
})
GUARDRAIL_KEYS = frozenset({
    "approval_policy", "sandbox_mode", "sandbox", "permissions", "disableAllHooks",
    "allowManagedPermissionRulesOnly", "disableBypassPermissionsMode",
})
CLAUDE_LISTS = frozenset({"allow", "ask", "deny", "additionalDirectories"})
SAFE_CONFIG_KEYS = {
    "codex_config": frozenset({"approval_policy", "sandbox_mode", "model", "project_doc_max_bytes", "project_doc_fallback_filenames", "features"}),
    "claude_settings": frozenset({"model", "effortLevel", "permissions", "claudeMdExcludes", "disableAllHooks", "disableBypassPermissionsMode"}),
}
CODEX_RULE = re.compile(r"^\s*prefix_rule\s*\(\s*pattern\s*=\s*(\[[^\n]*?\])(?:\s*,\s*decision\s*=\s*['\"](allow|prompt|forbidden)['\"])?\s*\)\s*$", re.M)
HEADING = re.compile(r"^#{1,6}\s+.+$", re.M)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def identity(value: Any) -> str:
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _read_source(path: Path, root: Path) -> tuple[bytes | None, str | None, dict[str, Any]]:
    """Read a declared regular file without following a leaf symlink."""
    if not _inside(path, root):
        return None, "outside_declared_root", {}
    try:
        # Resolve parents for containment, but never traverse a leaf symlink.
        if not _inside(path.parent.resolve(strict=True), root.resolve(strict=True)):
            return None, "parent_escapes_declared_root", {}
        if not stat.S_ISREG(path.lstat().st_mode):
            return None, "not_regular_file", {}
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                return None, "not_regular_file", {}
            if before.st_size > 2_000_000:
                return None, "source_too_large", {}
            with os.fdopen(os.dup(fd), "rb") as stream:
                raw = stream.read(2_000_001)
            after = os.fstat(fd)
            if len(raw) > 2_000_000 or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                return None, "source_changed_during_read", {}
            return raw, None, {"bytes": len(raw), "sha256": digest(raw), "mode": stat.S_IMODE(after.st_mode)}
        finally:
            os.close(fd)
    except FileNotFoundError:
        return None, "absent", {}
    except (OSError, ValueError):
        return None, "unreadable", {}


def _units(source: dict[str, Any], raw: bytes) -> tuple[list[dict[str, Any]], str | None]:
    kind = source["kind"]
    try:
        text = raw.decode("utf-8")
        if kind == "codex_config":
            data = tomllib.loads(text)
            return _flatten({key: data[key] for key in source["selected_keys"] if key in data}), None
        if kind == "claude_settings":
            data = json.loads(text)
            if not isinstance(data, dict):
                return [], "settings_not_object"
            return _flatten({key: data[key] for key in source["selected_keys"] if key in data}), None
        if kind == "codex_rules":
            found = list(CODEX_RULE.finditer(text))
            non_comments = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
            if len(found) != len(non_comments):
                return [], "unsupported_rule_syntax"
            result = []
            for i, match in enumerate(found):
                pattern = ast.literal_eval(match.group(1))
                if not isinstance(pattern, list) or not pattern or not all(
                    isinstance(part, str) or (isinstance(part, list) and part and all(isinstance(x, str) for x in part))
                    for part in pattern
                ):
                    return [], "unsupported_rule_syntax"
                result.append({"locator": f"rule:{i+1}", "value": {"pattern": pattern, "decision": match.group(2) or "allow"}})
            return result, None
        if kind == "instructions":
            matches = list(HEADING.finditer(text))
            if not matches:
                return [{"locator": "document", "value": text}], None
            result = []
            if text[:matches[0].start()].strip():
                result.append({"locator": "preamble", "value": text[:matches[0].start()]})
            for i, match in enumerate(matches):
                result.append({"locator": f"section:{i+1}", "value": text[match.start():matches[i+1].start() if i+1 < len(matches) else len(text)]})
            return result, None
    except (ValueError, SyntaxError, UnicodeError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        return [], "parse_failure"
    return [], "unsupported_source_type"


def _flatten(data: dict[str, Any], prefix: str = "") -> list[dict[str, Any]]:
    result = []
    for key, value in sorted(data.items()):
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.extend(_flatten(value, name))
        elif isinstance(value, list) and name.split(".")[-1] in CLAUDE_LISTS:
            result.extend({"locator": f"{name}[{i}]", "value": item} for i, item in enumerate(value))
        else:
            result.append({"locator": name, "value": value})
    return result


def _disposition(agent: str, kind: str, locator: str, value: Any, source: dict[str, Any]) -> tuple[str, str]:
    if kind == "instructions":
        return "REVIEW_RATIONALE", "Instruction meaning and loading require human review"
    if kind == "codex_rules":
        decision = value.get("decision")
        if decision in ("forbidden", "prompt"):
            return "KEEP_GUARDRAIL", "Restrictive execution rule"
        return "UNKNOWN", "Allow-rule need and command scope require local evidence"
    key = locator.split("[")[0]
    if key.split(".")[-1] in GUARDRAIL_KEYS or key.startswith("permissions.deny") or key.startswith("permissions.ask"):
        return "KEEP_GUARDRAIL", "Explicit restriction or approval boundary"
    if key.startswith("permissions.allow"):
        if isinstance(value, str) and ("*" in value or value in ("Bash", "Read", "Write", "Edit")):
            return "REVIEW_NARROWING", "Broad grant requires human scope assessment"
        return "UNKNOWN", "Grant need and effective scope require local evidence"
    defaults = source.get("documented_defaults", {})
    if key in defaults and value == defaults[key]:
        return "UNKNOWN", "Default-equivalent value; intentional rationale unverified"
    return "UNKNOWN", "Installed-version support and effective behavior unverified"


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    if not isinstance(manifest.get("agents"), list) or not manifest["agents"]:
        raise ValueError("declare at least one agent")
    if not isinstance(manifest.get("sources"), list) or not manifest["sources"]:
        raise ValueError("declare at least one source")
    names = set()
    for agent in manifest["agents"]:
        if agent.get("name") in names or agent.get("name") not in ("codex", "claude-code", "file-backed"):
            raise ValueError("duplicate or unsupported agent name")
        names.add(agent["name"])
        for field in ("product", "version", "version_evidence"):
            value = agent.get(field)
            pattern = r"[A-Za-z0-9 ._:+-]{1,100}" if field == "product" else r"[A-Za-z0-9._:+/-]{1,100}"
            if value is not None and (not isinstance(value, str) or not re.fullmatch(pattern, value)):
                raise ValueError("agent metadata must be short and non-secret")
    aliases = set()
    for source in manifest["sources"]:
        alias = source.get("alias")
        if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", alias) or alias in aliases:
            raise ValueError("source aliases must be unique and safe")
        aliases.add(alias)
        if source.get("agent") not in names or source.get("kind") not in ("codex_config", "codex_rules", "claude_settings", "instructions"):
            raise ValueError("unknown agent or source kind")
        if source["kind"] in SAFE_CONFIG_KEYS:
            selected = source.get("selected_keys")
            if not isinstance(selected, list) or not selected or any(key not in SAFE_CONFIG_KEYS[source["kind"]] for key in selected):
                raise ValueError("config and settings require selected non-secret keys")
        if source.get("owner") not in ("user", "personal-project", "shared", "managed"):
            raise ValueError("source owner required")
        for field in ("scope", "loading", "precedence"):
            value = source.get(field)
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", value)):
                raise ValueError("context metadata must be safe labels")
        support = source.get("support")
        if support is not None:
            parsed = urlparse(support) if isinstance(support, str) else None
            if not parsed or parsed.scheme != "https" or parsed.hostname not in ("learn.chatgpt.com", "developers.openai.com", "code.claude.com") or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("support must be a public official documentation URL")
        if not isinstance(source.get("root"), str) or not isinstance(source.get("relative"), str):
            raise ValueError("source root and relative path required")
        if not Path(source["root"]).is_absolute():
            raise ValueError("source root must be absolute")
        rel = Path(source["relative"])
        if rel.is_absolute() or ".." in rel.parts or rel == Path("."):
            raise ValueError("source relative path must stay beneath root")


def discover_sources(manifest: dict[str, Any]) -> dict[str, Any]:
    """Expand only declared roots and provider-specific source locations.

    Discovery is bounded to explicit agent roots and well-known filenames. It
    never follows directory symlinks, enters another repository, or runs an
    agent to learn what it might load. Loading remains UNKNOWN until qualified.
    """
    expanded = dict(manifest)
    sources = list(manifest.get("sources", []))
    for entry in manifest.get("discover", []):
        agent, role = entry.get("agent"), entry.get("role")
        root = Path(entry.get("root", ""))
        if not root.is_absolute() or role not in ("codex-user", "codex-project", "claude-user", "claude-project"):
            raise ValueError("discovery requires a supported role and absolute root")
        if (role.startswith("codex") and agent != "codex") or (role.startswith("claude") and agent != "claude-code"):
            raise ValueError("discovery role does not match agent")
        if role == "codex-project" and not entry.get("trusted"):
            raise ValueError("Codex project discovery requires declared trust")
        owner = "user" if role.endswith("user") else "personal-project"
        candidates: list[tuple[str, str]] = []
        if role == "codex-user":
            candidates.append(("config.toml", "codex_config"))
            candidates.extend((f"rules/{path.name}", "codex_rules") for path in _bounded_files(root / "rules", ".rules"))
            candidates.append(("AGENTS.override.md" if (root / "AGENTS.override.md").is_file() else "AGENTS.md", "instructions"))
        elif role == "codex-project":
            candidates.append((".codex/config.toml", "codex_config"))
            candidates.extend((f".codex/rules/{path.name}", "codex_rules") for path in _bounded_files(root / ".codex/rules", ".rules"))
            candidates.append(("AGENTS.override.md" if (root / "AGENTS.override.md").is_file() else "AGENTS.md", "instructions"))
        elif role == "claude-user":
            candidates.extend((("settings.json", "claude_settings"), ("CLAUDE.md", "instructions")))
        else:
            candidates.extend(((".claude/settings.json", "claude_settings"),
                               (".claude/settings.local.json", "claude_settings"),
                               ("CLAUDE.md", "instructions"), ("CLAUDE.local.md", "instructions")))
            candidates.extend((str(path.relative_to(root)), "instructions") for path in _bounded_files(root / ".claude/rules", ".md", recursive=True))
        for relative, kind in candidates:
            if not (root / relative).is_file():
                continue
            if any(s.get("agent") == agent and s.get("root") == str(root) and s.get("relative") == relative for s in sources):
                continue
            alias = f"{role}-{identity(relative)[:12]}"
            source_owner = owner
            if role == "codex-project" or (role == "claude-project" and relative != ".claude/settings.local.json"):
                source_owner = "shared"
            source = {"agent": agent, "alias": alias, "kind": kind, "owner": owner,
                      "root": str(root), "relative": relative, "loading": "UNKNOWN",
                      "precedence": role}
            source["owner"] = source_owner
            if kind in SAFE_CONFIG_KEYS:
                source["selected_keys"] = sorted(SAFE_CONFIG_KEYS[kind])
            sources.append(source)
    expanded["sources"] = sources
    return expanded


def _bounded_files(directory: Path, suffix: str, *, recursive: bool = False) -> list[Path]:
    if not directory.is_dir() or directory.is_symlink():
        return []
    result = []
    pending = [directory]
    while pending:
        current = pending.pop()
        for path in sorted(current.iterdir()):
            if path.is_symlink():
                continue
            if recursive and path.is_dir():
                pending.append(path)
            elif path.is_file() and path.suffix == suffix:
                result.append(path)
            if len(result) + len(pending) > 256:
                raise ValueError("bounded source discovery exceeded 256 entries")
    return result


def review(manifest: dict[str, Any], *, baseline: dict[str, Any] | None = None, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Produce a sanitized local report from explicitly declared sources only."""
    manifest = discover_sources(manifest)
    _validate_manifest(manifest)
    if baseline is not None and (not isinstance(baseline, dict) or baseline.get("report_type") != "local_agent_config_review" or not baseline.get("complete")):
        raise ValueError("baseline must be a local agent review report")
    if previous is not None and (not isinstance(previous, dict) or previous.get("report_type") != "local_agent_config_review" or not previous.get("complete")):
        raise ValueError("previous must be a successful local agent review report")
    agents = {a["name"]: a for a in manifest["agents"]}
    observations: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    cache: dict[Path, tuple[bytes | None, str | None, dict[str, Any]]] = {}
    drift: list[dict[str, Any]] = []
    for source in manifest["sources"]:
        name = source["agent"]
        root = Path(source["root"])
        path = root / source["relative"]
        physical = path.absolute()
        if physical not in cache:
            cache[physical] = _read_source(path, root)
        raw, error, evidence = cache[physical]
        obs = {"agent": name, "product": agents[name].get("product", name), "version": agents[name].get("version"),
               "version_evidence": agents[name].get("version_evidence"), "alias": source["alias"], "kind": source["kind"],
               "owner": source["owner"], "scope": source.get("scope", "unspecified"), "loading": source.get("loading", "UNKNOWN"),
               "precedence": source.get("precedence", "UNKNOWN"), "selected_keys": source.get("selected_keys"),
               "evidence": evidence, "status": error or "read"}
        observations.append(obs)
        if error:
            failures.append({"alias": source["alias"], "reason": error})
            continue
        parsed, parse_error = _units(source, raw or b"")
        if parse_error:
            obs["status"] = parse_error
            failures.append({"alias": source["alias"], "reason": parse_error})
            units.append({"agent": name, "alias": source["alias"], "locator": "file", "disposition": "UNKNOWN", "reason": "Parse or syntax failure"})
            continue
        if source["kind"] == "instructions" and manifest.get("playbook_root"):
            try:
                existing = scan_instruction_drift(ScannerConfig(
                    notes_roots=(path,), playbook_roots=(Path(manifest["playbook_root"]),),
                ))
                drift.append({"alias": source["alias"], "candidate_count": len(existing.candidates),
                              "advisory_finding_count": len(existing.advisory_findings),
                              "skip_count": len(existing.skipped_paths)})
                if existing.skipped_paths:
                    failures.append({"alias": source["alias"], "reason": "instruction_drift_scan_skip"})
            except (OSError, ValueError):
                failures.append({"alias": source["alias"], "reason": "instruction_drift_scan_unavailable"})
        for item in parsed:
            locator, value = item["locator"], item["value"]
            disposition, reason = _disposition(name, source["kind"], locator, value, source)
            if source["owner"] in ("shared", "managed") and disposition.startswith("PRUNE_"):
                disposition, reason = "REVIEW_RATIONALE", "Shared or managed source is context only"
            units.append({"agent": name, "alias": source["alias"], "locator": locator,
                          "content_sha256": identity(value), "disposition": disposition, "reason": reason,
                          "support": source.get("support", "UNKNOWN"), "loading": source.get("loading", "UNKNOWN")})
    # Repeated exact permission entries remain candidates for human review. A
    # restrictive decision cannot be classified as safely removable here.
    seen_permissions: set[tuple[str, str, str]] = set()
    for unit in units:
        alias = unit["alias"]
        source = next(s for s in manifest["sources"] if s["alias"] == alias)
        if source["kind"] not in ("codex_rules", "claude_settings") or "content_sha256" not in unit:
            continue
        locator = unit["locator"]
        if source["kind"] == "claude_settings" and not locator.startswith("permissions."):
            continue
        kind = "rule" if source["kind"] == "codex_rules" else locator.split("[")[0]
        key = (unit["agent"], kind, unit["content_sha256"])
        if key in seen_permissions and unit["disposition"] != "KEEP_GUARDRAIL":
            unit["disposition"] = "REVIEW_RATIONALE"
            unit["reason"] = "Exact entry repeats; verify intent and effective scope"
        seen_permissions.add(key)
    # No file contents, paths, commands, endpoints, or raw environment enter this report.
    fingerprints = sorted(identity(u) for u in units)
    source_fingerprints = sorted(identity(o) for o in observations)
    fingerprint = identity({"agents": manifest["agents"], "units": fingerprints, "sources": source_fingerprints, "drift": drift})
    agent_fingerprints = {
        name: identity({"agent": agents[name],
                        "units": [u for u in units if u["agent"] == name],
                        "sources": [o for o in observations if o["agent"] == name],
                        "drift": [d for d in drift if any(s["alias"] == d["alias"] and s["agent"] == name for s in manifest["sources"])]})
        for name in agents
    }
    old = set(previous.get("unit_fingerprints", [])) if previous else set()
    now = set(fingerprints)
    baseline_fp = baseline.get("fingerprint") if baseline else None
    complete = not failures and all(a.get("version") and a.get("version_evidence") for a in manifest["agents"])
    clean = complete and all(u["disposition"] in ("KEEP_REQUIRED", "KEEP_GUARDRAIL") for u in units)
    report = {"schema_version": 1, "report_type": "local_agent_config_review", "advisory": True,
              "complete": complete, "clean": clean, "fingerprint": fingerprint, "baseline_fingerprint": baseline_fp,
              "baseline_match": baseline_fp == fingerprint if baseline_fp else None,
              "agent_fingerprints": agent_fingerprints,
              "agent_baseline_match": {name: baseline.get("agent_fingerprints", {}).get(name) == value
                                       if baseline and baseline.get("agent_fingerprints", {}).get(name) else None
                                       for name, value in agent_fingerprints.items()},
              "previous_fingerprint": previous.get("fingerprint") if previous else None,
              "changed": previous is None or previous.get("fingerprint") != fingerprint,
              "new_unit_fingerprints": sorted(now - old) if previous else [],
              "resolved_unit_fingerprints": sorted(old - now) if previous else [],
              "unit_fingerprints": fingerprints, "sources": observations, "units": units,
              "failures": failures, "instruction_drift": drift,
              "notify": bool(failures or (previous and previous.get("fingerprint") != fingerprint))}
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Predeclared local record path; exclusive creation")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
        previous = json.loads(args.previous.read_text(encoding="utf-8")) if args.previous else None
        report = review(manifest, baseline=baseline, previous=previous)
        # The caller declares and provisions the local record directory. Never invent a fallback.
        parent_mode = args.output.parent.lstat().st_mode
        if not stat.S_ISDIR(parent_mode) or stat.S_IMODE(parent_mode) & 0o077 or args.output.is_symlink():
            raise ValueError("declared output directory must be private and non-symlink")
        with args.output.open("x", encoding="utf-8") as stream:
            os.chmod(args.output, 0o600)
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
        print(json.dumps({"record_written": True, "complete": report["complete"], "clean": report["clean"], "notify": report["notify"]}))
        return 0 if report["complete"] else 2
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"review blocked: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
