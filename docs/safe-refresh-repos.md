# Safe Refresh Repositories

`enforcement.safe_refresh_repos` safely refreshes configured local Git
checkouts before another deterministic tool depends on local repository state.
It is intentionally narrow: it verifies that each checkout is clean, on the
expected branch, tracking the expected upstream, then runs `git fetch` and
`git pull --ff-only`.

Use an existing branch-cleanup JSON config as the repository inventory:

```sh
python3 -m enforcement.safe_refresh_repos --config examples/branch-cleanup.json
```

The helper intentionally reads only the top-level `repositories` list from
that branch-cleanup-compatible config. Branch cleanup policy fields such as
`protected_branches` and `stale_approvals` are ignored by safe refresh and
remain owned by `enforcement.branch_cleanup`.

Refresh one or more named repositories from that inventory:

```sh
python3 -m enforcement.safe_refresh_repos \
  --config examples/branch-cleanup.json \
  --repo ai-workflow-enforcement
```

Machine-readable JSON is available for callers that need stable status counts:

```sh
python3 -m enforcement.safe_refresh_repos \
  --config examples/branch-cleanup.json \
  --output-format json
```

## Statuses

- `refreshed`: the checkout fast-forwarded and now matches the expected
  upstream tracking ref.
- `already-current`: the checkout was safe to inspect and already matched the
  expected upstream tracking ref after fetch/pull.
- `skipped`: the repository was in the config but not selected by `--repo`.
- `blocked`: the helper refused to refresh because the state was ambiguous or
  unsafe, such as a missing checkout, dirty worktree, unexpected branch,
  unexpected upstream, fetch failure, non-fast-forward pull failure, or a final
  `HEAD` mismatch.

The command exits `1` when any selected repository is blocked, `2` for config
or CLI errors, and `0` otherwise.

## Boundaries

This helper does not classify branches, read or apply branch-cleanup policy,
delete refs, prune remotes, write automation memory, schedule follow-up work,
or define workflow policy. It can be used before `enforcement.branch_cleanup`,
repo settings audits, or other local-source-sensitive checks, but those callers
remain responsible for their own policy and reporting semantics.
