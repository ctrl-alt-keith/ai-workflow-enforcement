# Safe Refresh Repositories

`enforcement.safe_refresh_repos` safely refreshes resolved local Git checkouts
before another deterministic tool depends on local repository state.
It is intentionally narrow: it verifies that each checkout is clean, on the
expected branch, tracking the expected upstream, then runs `git fetch` and
`git pull --ff-only`.

Use an existing branch-cleanup JSON config as the scope contract:

```sh
python3 -m enforcement.safe_refresh_repos --config examples/branch-cleanup.json
```

The helper reuses branch cleanup's canonical provider discovery, exclusions,
workspace resolution, and local overrides. It fails before fetch or pull when
provider-backed candidate scope is `unknown`. Legacy top-level `repositories`
configs remain supported through branch cleanup's explicit compatibility mode.
Branch policy fields such as `protected_branches` and `stale_approvals` remain
owned by `enforcement.branch_cleanup` and do not change refresh mechanics.

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
- `skipped`: the resolved repository was not selected by `--repo`.
- `blocked`: the helper refused to refresh because the state was ambiguous or
  unsafe, such as a missing checkout, dirty worktree, unexpected branch,
  unexpected upstream, fetch failure, non-fast-forward pull failure, or a final
  `HEAD` mismatch.

The command exits `1` when any selected repository is blocked, `2` for config,
provider-scope, or CLI errors, and `0` otherwise.

## Boundaries

This helper does not classify branches, read or apply branch-cleanup policy,
delete refs, prune remotes, write automation memory, schedule follow-up work,
or define workflow policy. It can be used before `enforcement.branch_cleanup`,
repo settings audits, or other local-source-sensitive checks, but those callers
remain responsible for their own policy and reporting semantics.
