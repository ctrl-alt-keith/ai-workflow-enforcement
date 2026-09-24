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
    {"id": "codex", "kind": "codex", "version": "fixture-version", "support": "official-doc-review-id", "launch_context": "fixture-context", "config_root": "/tmp/fixture/codex", "projects": ["/tmp/fixture/project"]},
    {"id": "claude", "kind": "claude-code", "version": "fixture-version", "support": "official-doc-review-id", "launch_context": "fixture-context", "config_root": "/tmp/fixture/claude", "projects": ["/tmp/fixture/project"]},
    {"id": "other", "kind": "file-backed", "version": "fixture-version", "support": "unverified", "launch_context": "fixture-context", "root": "/tmp/fixture/other", "files": [{"path": "/tmp/fixture/other/instructions.md", "surface": "instruction"}]}
  ]
}
```

```text
python3 -m enforcement.agent_review_cli --enrollment /absolute/local/enrollment.json --record /absolute/local/records/20260924T120000Z-review.json --previous /absolute/local/records/previous.json --baseline /absolute/local/records/accepted.json
```

`--previous` is a prior successful observation and `--baseline` is a separately
human-accepted record. Neither is changed. If omitted, comparison is
`unavailable`; the first scan cannot be a clean comparison. A changed
fingerprint never updates the accepted baseline. The command prints only a
compact status and counts. The full record contains safe aliases, content
hashes, unit dispositions, source coverage, and comparison identities. It
does not contain raw config values, rule commands, instruction bodies, exact
private paths, or raw launch context. A `PARTIAL` run exits 1; setup or record
failure exits 2. Preserve partial records for diagnosis and do not use them as
the next successful observation.

## Provider interpretation

Codex and Claude Code have separate discovery and precedence contracts. This
implementation inventories the explicitly enrolled user root and project roots;
file presence alone never proves loading or effective behavior. User-level
Codex `config.toml`, `AGENTS.override.md`/`AGENTS.md`, and `.rules` files are
covered. Project `.codex/config.toml`, project instructions, and project
`.codex/rules` are covered as conditional context. Codex rule parsing is an
inert literal `prefix_rule` subset; unsupported Starlark produces `UNKNOWN`.
For Claude Code, user and project settings, `CLAUDE.md`, `CLAUDE.local.md`, and
Markdown rules are covered. Setting keys and entries are separate units;
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
