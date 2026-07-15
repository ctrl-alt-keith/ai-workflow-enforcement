# Validation Contract Inventory

The validation contract inventory is an on-demand, report-only comparison of
repository-local validation claims and directly observable Makefile targets.
It preserves source evidence in Markdown and JSON without scoring repositories,
creating a persistent registry, or establishing centralized validation policy.

```sh
python3 -m enforcement.validation_contract_inventory /path/to/repository
python3 -m enforcement.validation_contract_inventory /path/to/repository --output-format json
```

Pass multiple repository roots to produce one inventory. Repository selection
remains caller-owned; the command does not infer an organization inventory from
the local filesystem.

The inventory reads `AGENTS.md`, `README.md`, and validation-, developer-,
contributor-, or workflow-named Markdown files under `docs/`. Explicit
`make <target>` claims are compared with targets captured by the existing
repository preflight inspection. Ambiguous validation prose, missing
documentation, and claims without a Makefile are `Unclear`. A missing target is
a `Mismatch` only when a Makefile is directly observable. Repositories with no
validation claim and no Makefile surface are `Not applicable`.

The report is evidence, not policy. It does not execute validation commands,
mutate repositories, inspect branch protection, rank repositories, or infer
equivalent tooling. Hosted CI check names remain intentionally deferred because
local evidence is sufficient for this initial bounded surface and hosted names
do not establish local command semantics by themselves.
