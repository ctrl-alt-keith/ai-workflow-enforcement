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

For run records, keep repository proposals in their owning PRs. Recommend a
workspace-owned hosted operational destination outside this source tree for
aggregate/report-only records. Its locator and retention rule remain undecided;
no store is created here. Existing local-agent enrollment, workflow-drift Actions
artifacts and issue-scoped capture do not declare that destination. Affected
runs must stop until the required destination is declared and verified.
