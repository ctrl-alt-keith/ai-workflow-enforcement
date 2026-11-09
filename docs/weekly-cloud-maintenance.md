# Weekly cloud maintenance definitions

[`config/weekly-cloud-maintenance/catalog.json`](../config/weekly-cloud-maintenance/catalog.json)
versions the four cloud maintenance specifications and their `weekly` cadence.
Each adjacent Markdown file contains its complete coordinator prompt. These
are the reconstructed prompts supplied from the four restored K2 schedules,
not recovered copies of the deleted schedules. The only text normalization is
a final LF newline.

Validate the catalog and prompt files without contacting a scheduler:

```sh
python3 -m enforcement.maintenance_jobs config/weekly-cloud-maintenance/catalog.json
```

The validator follows the small read-only [workflow-contract](workflow-contracts.md)
pattern. Schema version 1 permits only `schema_version` and `jobs`; each job
requires a unique stable `id`, nonempty `title`, `cadence: weekly`, and an
adjacent regular Markdown `prompt_file`. Prompt files must be nonempty UTF-8
without BOM, use LF, and end in LF. Unknown fields, other cadences, missing
prompts, symlinks, and paths outside the catalog directory fail with exit 2.
Success exits 0. Canonical `make check` validates the checked-in catalog and
exercises rejected inputs.

## Ownership

This catalog is inspectable operational source, not a scheduler or an immutable
execution receipt. Its IDs are stable definition names, not live scheduler IDs.
Exact weekday/hour, cron, timezone, enabled state, live scheduler identifiers,
and execution history stay in the active scheduler. Timing changes need no PR.
The saved environment selector in each prompt is a launch constraint; it does
not prove the child's published environment version or runtime model. Verify
those at launch as the prompt requires.

The validator checks shape and prompt readability, not semantic safety,
authorization, selector availability, live inventory, or scheduler drift. It
does not render, install, reconcile, enable, launch, or alter jobs. A catalog
edit does not update saved schedules; an authorized operator must compare and
apply the selected full prompt through the scheduler and read it back. Current
Playbook guidance and live authority continue to govern execution. See the
[product boundary](product-boundary.md) and the Playbook's
[maintenance contract](https://github.com/ctrl-alt-keith/ai-workflow-playbook/blob/main/docs/maintenance-automations.md).

## Run records: recommendation and unresolved configuration

Repository proposals belong in each owning repository's normal PR surface.
Cross-repository and report-only outcomes need a workspace-owned durable
operational-record destination, declared before execution. Recommend keeping
that destination outside this source tree, alongside the hosted workspace's
operational configuration, with one immutable dated/versioned record per
occurrence, exclusive creation, a producing receipt, and an operator-selected
retention rule. The concrete hosted store and retention window still require
an owner decision; none is configured or created by this change.

The existing [local agent review](local-agent-review.md#local-enrollment)
similarly leaves location and retention to external enrollment. Its local
records are not a declared hosted destination for these sweeps. The workflow
drift audit's GitHub Actions artifacts belong to that separate workflow, and
[issue artifact capture](issue-artifact-capture.md) is issue-scoped; neither
establishes ownership for recurring sweep records. Scheduler/thread history
alone has not been qualified against the operational-record contract. Until a
destination is declared and verified, report that missing prerequisite and
stop the affected run; do not improvise a store or retention policy.
