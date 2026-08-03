# Repository Preflight

The repository preflight is an on-demand, report-only orientation aid for one
local Git repository. It emits Markdown by default and JSON for callers that
need a machine-readable transport format:

```sh
python3 -m enforcement.repo_preflight /path/to/repository
python3 -m enforcement.repo_preflight /path/to/repository --output-format json
```

The repository path defaults to the current directory. The command reads
current evidence and writes only its report to standard output. It does not
fetch, checkout, modify files or refs, persist a descriptor, update hosted
work items, dispatch tasks, or route workers.

## Source boundaries

The local report keeps evidence source-specific:

- `repo_local_agents` reports whether root `AGENTS.md` exists and, when
  readable, its headings. It does not interpret the guidance as capabilities.
- `validation_tooling` reports syntactically observable Make targets from the
  root `Makefile`. If the file is absent, equivalent tooling is explicitly
  unavailable rather than guessed.
- `git_metadata` runs read-only Git commands for the current branch, configured
  remotes, working-tree state, and `origin/HEAD`. A default branch is unknown
  when that direct symbolic-ref evidence is unavailable. Structured remote URLs
  omit user information, query parameters, and fragments so report output does
  not reproduce embedded credentials or transport-only parameters.

Hosted GitHub metadata is excluded by default. Opt in explicitly when an
existing authenticated `gh` session is appropriate:

```sh
python3 -m enforcement.repo_preflight . --include-hosted
```

That source reads only repository visibility, default branch, and archived
state with `gh api`. It does not start authentication, fetch Git data, or make
hosted changes. Failure remains attached to the hosted source while local
evidence is preserved.

## Semantics

Every source section includes its path or command, capture timestamp, status,
facts, and errors or unavailable reasons. The envelope includes a schema
version, report type, repository identity, capture timestamp, advisory notice,
and overall source status.

The report is advisory, stale after capture, and not a source of truth. Its
JSON schema is a report transport envelope, not a canonical capability model.
It makes no claims about orchestration readiness, maturity, mutation safety,
privacy posture, provider risk, deployment criticality, worker role, or task
priority.
