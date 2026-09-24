# Local agent configuration review

`python3 -m enforcement.agent_config_review --manifest SCOPE.json --output RECORD.json`
performs one manual, read-only review. It does not launch Codex or Claude Code,
change inspected files, enroll a scheduler, contact a model, or send a notification.
The operator supplies an exact scope and a pre-existing local operational-record
directory. Output uses exclusive creation. Keep the manifest and records in the
approved local owner and retention policy, outside this repository. Do not use
this tool until that destination and retention are declared.
The output directory must already exist, be private (`0700` or equivalent),
and not be a symlink.

The manifest is JSON with `schema_version: 1`, an `agents` array, and `sources`
array. Each agent has `name` (`codex`, `claude-code`, or `file-backed`),
`product`, exact installed `version`, and `version_evidence`. A source has a
unique safe `alias`, `agent`, `kind` (`codex_config`, `codex_rules`,
`claude_settings`, or `instructions`), `owner` (`user`, `personal-project`,
`shared`, or `managed`), absolute `root`, and path `relative` to that root.
Optional `scope`, `loading`, `precedence`, and `support` record locally verified
context. Omitted loading/precedence is `UNKNOWN`, never inferred from a name.
For `codex_config` and `claude_settings`, `selected_keys` is required. Only
built-in non-secret top-level keys can be selected; other fields are excluded
after inert parsing. Selected permission values may still contain private
commands or paths, so records stay local.
The optional `playbook_root` invokes the existing advisory drift scanner for
declared instruction files. Its raw snippets stay local; this report retains
only counts. `discover` can name an explicit root and role (`codex-user`,
`codex-project`, `claude-user`, or `claude-project`) to expand current provider
filenames and bounded rule directories beneath that root. Codex project
discovery requires `trusted: true`. This never discovers an undeclared agent,
project, or root; returned loading remains `UNKNOWN` until qualified. A
file-backed agent gets instruction inventory
without an unsupported claim that its file loads.

`--previous` accepts a prior successful local report for new/resolved and quiet
unchanged comparison. `--baseline` accepts a separately human-accepted report;
the tool compares its fingerprint but never accepts or rewrites that baseline.
The report stores source metadata, SHA-256 and dispositions by safe alias and
entry locator. It omits raw content, command patterns, URLs from source files,
environment, and full private paths. Treat records as local sensitive
operational evidence despite that sanitization. A `notify` flag asks the
separate, human-approved notifier to consider a compact notice; it does not
send one. Missing or invalid sources, parsing failure, and absent version
evidence mark the result incomplete. A failed destination is a blocked run.
`complete` means the declared sources were read and parsed with version evidence;
`clean` additionally requires every unit to be a verified keep disposition.
An `UNKNOWN` is therefore never a clean pass.

The initial parser is intentionally conservative. It parses TOML Codex
configuration, one-line literal Codex `prefix_rule` entries, Claude JSON
settings, and Markdown sections. Unknown syntax and support get `UNKNOWN`;
restrictive rules remain `KEEP_GUARDRAIL`. Apparent instruction overlap is
`REVIEW_RATIONALE`, never a deletion claim. Effective behavior still depends
on the exact installed build and launch context. Operator qualification must
resolve profiles, project trust, CLI/environment overrides, managed settings,
imports, conditional instructions, hooks, and mixed authentication stores
before a per-agent baseline or schedule is accepted. Do not point the reviewer
at mixed secret stores; enroll narrowly selected non-secret sources only.

Provider interpretation references, refreshed for this implementation:

- [Codex configuration and precedence](https://learn.chatgpt.com/docs/config-file/config-basic)
- [Codex execution-policy rules](https://learn.chatgpt.com/docs/agent-configuration/rules)
- [Codex AGENTS.md discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Claude Code settings and precedence](https://code.claude.com/docs/en/settings)
- [Claude Code persistent instructions and conditional rules](https://code.claude.com/docs/en/memory)

Fixture validation is `make check`. It does not qualify a live workstation,
accept Codex or Claude baselines, choose cadence, or activate a schedule.
