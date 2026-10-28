# Safe Refresh Repositories

`enforcement.safe_refresh_repos` safely refreshes resolved local Git checkouts
before another deterministic tool depends on local repository state.
It is intentionally narrow: it verifies that each configured path is the Git
worktree top level and that the checkout is clean and on the expected branch,
then fetches canonical `main` and fast-forwards only when local `HEAD` is an
ancestor of fetched `main`. Ordinary branch-cleanup inventory mode also
requires the expected upstream tracking ref.

Use an existing branch-cleanup JSON config as the scope contract:

```sh
python3 -m enforcement.safe_refresh_repos --config examples/branch-cleanup.json
```

The helper reuses branch cleanup's canonical provider discovery, exclusions,
workspace resolution, local overrides, and checkout/provider identity verifier.
It fails before fetch or merge when provider-backed scope is not mutation-ready
because credential completeness or stable-ID exclusion reconciliation is
unproven. For provider targets, exactly matching fetch/push locators and the
enumerated provider repository ID are also required before refresh. Legacy
top-level `repositories` configs remain supported through branch cleanup's
explicit compatibility mode.
Branch policy fields such as `protected_branches` and `stale_approvals` remain
owned by `enforcement.branch_cleanup` and do not change refresh mechanics.

## Task-start catalog mode

After isolated checkouts are mounted, and before work begins, a task-start
caller can run:

```sh
python3 -m enforcement.safe_refresh_repos \
  --startup-catalog "$CATALOG_PATH" \
  --workspace-root "$WORKSPACE_ROOT" \
  --checkout-branch work \
  --output-format json
```

This mode uses only selected catalog entries and makes no provider-discovery or
1Password reads. It requires a clean task-local branch without upstream, the
catalog commit in its history, and matching GitHub fetch/push locators. It
fast-forwards to the commit fetched from `main`, preserving the branch name.
Call it only at a new task boundary; stop on any blocked result. It must not
run against an active task's checkout.

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

- `refreshed`: the checkout fast-forwarded and now matches fetched `main`.
- `already-current`: the checkout was safe to inspect and already matched the
  fetched `main` after fetch.
- `skipped`: the resolved repository was not selected by `--repo`.
- `blocked`: the helper refused to refresh because the state was ambiguous or
  unsafe, such as a missing checkout, dirty worktree, unexpected branch,
  unexpected upstream, wrong or unverified repository identity, fetch failure,
  local commits ahead of or diverged from `main`, non-fast-forward merge
  failure, or a final `HEAD` mismatch.

The command exits `1` when any selected repository is blocked, `2` for config,
provider-scope, or CLI errors, and `0` otherwise.

## Boundaries

This helper does not classify branches, read or apply branch-cleanup policy,
delete refs, prune remotes, write automation memory, schedule follow-up work,
or define workflow policy. It can be used before `enforcement.branch_cleanup`,
repo settings audits, or other local-source-sensitive checks, but those callers
remain responsible for their own policy and reporting semantics.
