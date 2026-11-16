# Weekly cloud maintenance definitions

[`config/weekly-cloud-maintenance/catalog.json`](../config/weekly-cloud-maintenance/catalog.json)
versions four job definitions with `weekly` cadence and adjacent coordinator
prompts. These are edited candidates derived from the restored K2 prompts,
not exact saved scheduler bytes. This PR does not apply them to the scheduler;
an authorized operator must compare, apply and read back any selected prompt.

```sh
python3 -m enforcement.maintenance_jobs config/weekly-cloud-maintenance/catalog.json
```

The read-only validator requires schema version 1 and nonempty `jobs`, each with
a unique `id`, nonempty `title`, `cadence: weekly`, and adjacent regular Markdown
`prompt_file`. Prompt files must be nonempty UTF-8 without BOM, use LF and end
in LF. Unknown fields, other cadences and invalid prompts exit 2; success exits
0. `make check` covers the catalog and rejected inputs. Validation checks shape
and readability, not prompt semantics, authorization or live scheduler state.

Exact timing, cron, enabled state and live scheduler IDs remain scheduler-owned;
timing changes need no PR. The saved environment selector is a launch constraint,
not proof of a published version. Execution follows the current
[Playbook maintenance contract](https://github.com/ctrl-alt-keith/ai-workflow-playbook/blob/main/docs/maintenance-automations.md).

Keep repository proposals and their validation evidence in their owning PRs.
Report each run's inspected and unattempted scope, skips, blockers, findings,
exact known run and child identities, and actionable follow-up to the operator.
Do not infer a clean result from missing output or rely on scheduler history
without verifying its exact run identity and contents. Routine summaries need
no separate permanent archive. Retain an artifact only when its exact identity
materially supports review or recovery or an explicit owning contract requires
it; apply the owning storage and verification rules. Stop the dependent action
if required evidence cannot be retained or verified.
