# Local agent configuration review

`python3 -m enforcement.agent_review_cli` is an inert, local, observe-and-report
reviewer for explicitly enrolled Codex, Claude Code, and file-backed agents. It
reads enrolled files and creates one new local JSON record. It never starts an
agent, tests a permission by running a command, writes an inspected file,
accepts a baseline, changes enrollment, or configures a scheduler. The operator
owns cadence, the record location and retention, notification route, and human
disposition. Do not run it on live agent homes before a separate qualification
and baseline decision.

## Local enrollment

The operator stores enrollment outside the implementation repository and names
an absolute record path in the predeclared local operational-record directory.
The path must be unused; the writer uses exclusive creation. The operator
declares a dated/versioned filename, record retention, and notification route
in the local run contract before invoking the command. An unavailable directory
or existing record blocks the run. No fallback destination is selected.

Synthetic example (the paths are fixtures, not an active workstation scope):

```json
{
  "record_root": "/tmp/fixture/records",
  "record_retention": "fixture retention policy",
  "agents": [
    {"id": "codex", "kind": "codex", "version": "fixture-version", "support": "official-doc-review-id", "launch_context": "fixture-context", "config_root": "/tmp/fixture/codex", "projects": ["/tmp/fixture/project"], "context_evidence": {"managed_and_system": "fixture-evidence", "profile_trust_and_invocation": "fixture-evidence", "nested_and_fallback_instructions": "fixture-evidence"}},
    {"id": "claude", "kind": "claude-code", "version": "fixture-version", "support": "official-doc-review-id", "launch_context": "fixture-context", "config_root": "/tmp/fixture/claude", "projects": ["/tmp/fixture/project"], "context_evidence": {"managed": "fixture-evidence", "ancestor_and_nested_instructions": "fixture-evidence", "environment_and_invocation": "fixture-evidence"}},
    {"id": "other", "kind": "file-backed", "version": "fixture-version", "support": "unverified", "launch_context": "fixture-context", "root": "/tmp/fixture/other", "files": [{"path": "/tmp/fixture/other/instructions.md", "surface": "instruction"}]}
  ]
}
```

```text
python3 -m enforcement.agent_review_cli --enrollment /absolute/local/enrollment.json --record /absolute/local/records/20260924T120000Z-review.json --previous /absolute/local/records/previous.json --baseline /absolute/local/records/accepted.json
```

`--previous` is a prior successful observation and `--baseline` is a separately
human-accepted record. A `PARTIAL` record is rejected as either reference.
Neither is changed. If omitted, comparison is
`unavailable`; the first scan cannot be a clean comparison. A changed
fingerprint never updates the accepted baseline. The command prints only a
compact status and counts. The full record contains safe aliases, content
hashes, unit dispositions, source coverage, and comparison identities. It
does not contain raw config values, rule commands, instruction bodies, exact
private paths, or raw launch context. The hashes are unsalted and can confirm
guessed values, so keep operational records private and out of GitHub, Linear,
telemetry, and external model requests. A `PARTIAL` run exits 1; setup or record
failure exits 2. Preserve partial records for diagnosis and do not use them as
the next successful observation.

Before reading inspected files, the CLI fetches the applicable official Codex
and Claude Code documentation and records their content hashes. It sends no
local configuration or instructions in those requests. A failed documentation
fetch blocks the run. A changed documentation identity appears in comparison;
its semantic effect still requires human review.

## Provider interpretation

Codex and Claude Code have separate discovery and precedence contracts. This
implementation inventories the explicitly enrolled user root and project roots;
file presence alone never proves loading or effective behavior. Enrolled roots
must exist and must not be symlinks. User-level
Codex `config.toml`, `AGENTS.override.md`/`AGENTS.md`, and `.rules` files are
covered. Project `.codex/config.toml`, project instructions, and project
`.codex/rules` are covered as conditional context. Codex rule parsing is an
inert literal `prefix_rule` subset; unsupported Starlark produces `UNKNOWN`.
For Claude Code, user and project settings, `CLAUDE.md`, `CLAUDE.local.md`,
project `.claude/CLAUDE.md`, and Markdown rules are covered. Setting keys and entries are separate units;
restrictive permission entries are retained as guardrail candidates. Prose
overlap receives human `REVIEW_RATIONALE`, never an automated deletion verdict.
The existing instruction drift scanner supplies the AGENTS advisory checks;
its output is kept advisory and does not establish runtime loading.

The generic file-backed mode accepts only explicit files under its enrolled
root. Its loading/effectiveness field stays `UNKNOWN` even when content can be
inventoried. It needs no `rules` file or invented universal permission syntax.

Before a live agent is qualified, independently establish its exact installed
product/version, effective root and overrides (`CODEX_HOME` or
`CLAUDE_CONFIG_DIR` where applicable), working directory, trust and managed
layers, invocation/environment settings, source loading, and current provider
support. A `version` or `support` label in enrollment is an identity supplied
by the operator, not proof of those facts. Unknown coverage, conditional
imports, nested instructions, unsupported rule syntax, and managed settings
must be recorded and resolved or accepted by the human at the per-agent
baseline boundary. The fixture tests prove scanner mechanics only.

`context_evidence` names operator-held evidence for relevant layers that this
bounded file scan cannot establish. Missing domains create explicit `UNKNOWN`
units and a `PARTIAL` result. Labels alone are not a substitute for actual
local verification: the record hashes each label and calls it
`operator_attested`. Codex uses `managed_and_system`,
`profile_trust_and_invocation`, and `nested_and_fallback_instructions`;
Claude Code uses `managed`, `ancestor_and_nested_instructions`, and
`environment_and_invocation`.

When a managed, system, ancestor, nested, or other needed source is actually
present, enroll its exact path under that agent's optional `context_files` with
`surface` (`config`, `permission`, or `instruction`) and `ownership` (`user`,
`shared`, or `managed`). Those files are inventoried as context. Their loading
remains unverified until the local context evidence establishes it; shared and
managed content is never a personal prune target.

Once a human has accepted a per-agent baseline, the local enrollment can pin
required behavior with `invariants` entries containing `agent`, `source`,
`expected_content_sha256`, and a local `evidence` identity. A missing expected
unit produces invariant `drift` and a `PARTIAL` result. The evidence identity
is hashed in the record. This is where the machine-specific CAK-169 Codex
writable-root decision must be represented for the relevant Codex enrollment;
the reviewer does not apply it to Claude or infer it from a historical issue.
No invariant is created or accepted automatically.

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
