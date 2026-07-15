# Work-State Advisory Index

`enforcement.work_state_index` composes existing work-state evidence into one
report-only index. It does not replace or duplicate the organization PR/issue
scan or branch-cleanup dry run. Each remains independently usable, and its
source-specific JSON payload is retained inside a small composition envelope.

Run an organization/workspace view with an existing branch-cleanup config:

```sh
python3 -m enforcement.work_state_index \
  --branch-cleanup-config examples/branch-cleanup.json
```

Narrow both sources safely to a configured repository:

```sh
python3 -m enforcement.work_state_index \
  --repo ai-workflow-enforcement \
  --branch-cleanup-config examples/branch-cleanup.json
```

Markdown is the default. Use `--output-format json` for the machine-readable
form. Existing branch stale-audit evidence can be included with `--audit-stale`
and `--audit-github-prs`; these flags are passed to the existing dry-run API and
do not add a second classifier.

## Advisory And Freshness Semantics

The index is advisory, stale after capture, not a source of truth, and not
authorization for cleanup or mutation. Every available or failed source has
its own capture timestamp. `fresh_at_capture` means only that the source was
observed during that capture; the result is stale immediately afterward and
must be refreshed before operational decisions. An unavailable source has no
capture timestamp, an `unavailable` freshness value, and no invented payload.

Source status values are:

- `available`: the source returned complete observable evidence
- `partial`: the source returned evidence while preserving one or more errors
- `failed`: the source invocation failed and supplied no advisory payload
- `unavailable`: the source lacked required context, such as a branch-cleanup
  config or local Git worktree access

Source failures do not discard other results. Errors remain attached to their
source section. Local worktree evidence is the literal output of `git worktree
list --porcelain` for each configured repository. The index does not infer
ownership, safety, staleness, deletion candidacy, or missing worktrees from
that output.

No report is persisted. The command performs no branch deletion, pruning,
fetching, rebasing, pushing, checkout, PR/issue mutation, or cleanup action.
