# Local agent configuration review

`python3 -m enforcement.agent_review_cli` reviews explicitly enrolled Codex,
Claude Code, and file-backed sources. It reads local files and creates one JSON
record; it does not start an agent or change inspected state. The operator owns
the review cadence, record location and retention, notification route, and
finding disposition. Live agent homes require separate qualification and a
human baseline decision before use.

## Local enrollment

Store enrollment outside this repository. Before a run, declare the local
record directory, dated/versioned filename, retention, and notification route.
Pass an unused absolute record path under that directory. The writer creates
the record exclusively; an unavailable directory or existing file blocks the
run.

Synthetic example (the paths are fixtures, not an active workstation scope):

```json
{
  "record_root": "/tmp/fixture/records",
  "record_retention": "fixture retention policy",
  "agents": [
    {"id": "codex", "kind": "codex", "version": "fixture-version", "support": "official-doc-review-id", "launch_context": "fixture-context", "config_root": "/tmp/fixture/codex", "projects": ["/tmp/fixture/project"], "context_evidence": {"managed_and_system": "verified:fixture-evidence", "profile_trust_and_invocation": "verified:fixture-evidence", "nested_and_fallback_instructions": "verified:fixture-evidence"}},
    {"id": "claude", "kind": "claude-code", "version": "fixture-version", "support": "official-doc-review-id", "launch_context": "fixture-context", "config_root": "/tmp/fixture/claude", "projects": ["/tmp/fixture/project"], "context_evidence": {"managed": "verified:fixture-evidence", "ancestor_and_nested_instructions": "verified:fixture-evidence", "environment_and_invocation": "verified:fixture-evidence"}},
    {"id": "other", "kind": "file-backed", "version": "fixture-version", "support": "unverified", "launch_context": "fixture-context", "root": "/tmp/fixture/other", "files": [{"path": "/tmp/fixture/other/instructions.md", "surface": "instruction", "ownership": "user"}]}
  ]
}
```

```text
python3 -m enforcement.agent_review_cli --enrollment /absolute/local/enrollment.json --record /absolute/local/records/20260924T120000Z-review.json --previous /absolute/local/records/previous.json --baseline /absolute/local/records/accepted.json
```

`--previous` names a prior `OBSERVED` record; `--baseline` names a separately
human-accepted `OBSERVED` record in the current record schema. Both are
read-only inputs. Without either, that comparison is `unavailable`. Comparison
separates finding fingerprints, source observations (including file order and
content), and provider-document identities. A change in any of them does not
update the baseline. `PARTIAL` or accepted-baseline drift exits 1; setup,
record, or reference-schema failure exits 2. Keep partial records for diagnosis,
but do not use them as comparison references.

The command prints compact status and counts. The local record contains safe
aliases, unsalted content hashes, unit dispositions, coverage, and comparison
identities. It excludes raw values, commands, instruction bodies, private
paths, and launch context. Unsalted hashes can confirm guessed values: keep
records private and out of GitHub, Linear, telemetry, and external model
requests.

Before reading enrolled files, the CLI fetches applicable official provider
documentation and records its content hashes. It sends no local content in
those requests. Fetch failure blocks the run; a changed document hash is
reported separately from new or resolved findings and needs human interpretation. Against an accepted baseline,
any document hash change reports drift and exits 1 until a human accepts a new
baseline.

## Provider interpretation

Codex and Claude Code use separate discovery and precedence rules. The reviewer
inventories enrolled user and project roots, which must exist and cannot be
symlinks. File presence does not establish loading or effective behavior. If no
user-root source can be inspected, coverage is `UNKNOWN` and the run is
`PARTIAL`, even when a project source is present.

- Codex: user `config.toml`, `AGENTS.override.md`/`AGENTS.md`, and `.rules`;
  project `.codex/config.toml`, instructions, and `.codex/rules` as conditional
  context. The inert parser accepts a literal `prefix_rule` subset and marks
  unsupported Starlark `UNKNOWN`. Structured inventory selects only
  `approval_policy`, `sandbox_mode`, `model`, and
  `sandbox_workspace_write.writable_roots`/`network_access`. Writable-root
  values are hashed only in the private local record, so accepted roots can be
  pinned by invariant without reporting private paths. A `.rules` file may be a
  relative symlink to a regular `.rules` file in the same rules directory. The
  reviewer inspects that target under the link's own source identity and hashes
  the target filename so retargeting changes comparison evidence. The link gets
  one `UNKNOWN` unit because its runtime loading is unverified; the regular
  target is inventoried separately. Links to other directories, chained links,
  and other symlinked sources remain `PARTIAL` and are not followed.
- Claude Code: user and project settings, `CLAUDE.md`, `CLAUDE.local.md`,
  project `.claude/CLAUDE.md`, and Markdown rules. Structured inventory selects
  `model`, permission `allow`/`ask`/`deny` lists, `defaultMode`, and
  `disableBypassPermissionsMode`; restrictive permissions remain guardrails.

Other structured fields remain opaque `UNKNOWN` units. Their nested values
are not inventoried or hashed individually; the private whole-file observation
hash still detects source changes.

Instruction overlap receives `REVIEW_RATIONALE` for human judgment. AGENTS
advisories come from the existing instruction drift scanner and do not prove
runtime loading.

File-backed enrollment names explicit files and their ownership (`user`,
`shared`, or `managed`) under one root. Loading and effectiveness stay
`UNKNOWN`; structured files stay opaque and no common rules format is assumed.

Live qualification requires independent evidence of installed product/version,
effective root and overrides (`CODEX_HOME` or `CLAUDE_CONFIG_DIR` where
applicable), working directory, trust and managed layers, invocation and
environment, source loading, and current provider support. Enrollment's
`version` and `support` are operator labels, not that evidence. At the
per-agent baseline decision, the human resolves or accepts unknown coverage,
conditional imports, nested instructions, unsupported syntax, and managed
settings. Fixture tests establish scanner behavior only.

`context_evidence` holds identities for operator-verified layers beyond the
file scan. A missing or invalid identity adds an `UNKNOWN` unit and yields
`PARTIAL`; a `verified:` identity is hashed and recorded as
`operator_attested`, which is a claim rather than proof of verification. Codex
uses `managed_and_system`,
`profile_trust_and_invocation`, and `nested_and_fallback_instructions`;
Claude Code uses `managed`, `ancestor_and_nested_instructions`, and
`environment_and_invocation`.
Changes to launch context or context evidence change comparison scope.

Enroll any present managed, system, ancestor, nested, or other needed source
under that agent's `context_files` with
`surface` (`config`, `permission`, or `instruction`) and `ownership` (`user`,
`shared`, or `managed`). Those files are inventoried as context. Their loading
remains unverified until local evidence establishes it; shared and managed
content is not a personal prune target.

After human baseline acceptance, local enrollment can pin required behavior
with `invariants` entries containing `agent`, `source`,
`locator`, `expected_content_sha256`, and a local `evidence` identity. Copy the
`locator` and hash from the accepted unit in the local record. A missing expected
unit produces invariant `drift` and `PARTIAL`. The evidence identity is hashed
in the record. A machine-specific CAK-169 Codex writable-root decision belongs
in the relevant Codex enrollment; it is not inferred or applied to Claude.
Invariants are neither created nor accepted automatically.

Current provider references, checked 2026-09-24:

- [Codex configuration and precedence](https://learn.chatgpt.com/docs/config-file/config-basic)
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Codex AGENTS discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Codex execution-policy rules](https://learn.chatgpt.com/docs/agent-configuration/rules)
- [Claude Code settings](https://code.claude.com/docs/en/settings)
- [Claude Code instructions](https://code.claude.com/docs/en/memory)
- [Claude Code permissions](https://code.claude.com/docs/en/permissions)

The provider references must be refreshed before each qualified live review;
their presence in this document does not make a later run's support current.
Each agent needs its own accepted baseline, a later real source/version
comparison, redaction and failure qualification, and Keith's explicit
activation decision. No scheduler is included here.
