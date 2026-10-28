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
It fails before fetch or pull when provider-backed scope is not mutation-ready
because credential completeness or stable-ID exclusion reconciliation is
unproven. For provider targets, exactly matching fetch/push locators and the
enumerated provider repository ID are also required before refresh. Legacy
top-level `repositories` configs remain supported through branch cleanup's
explicit compatibility mode.
Branch policy fields such as `protected_branches` and `stale_approvals` remain
owned by `enforcement.branch_cleanup` and do not change refresh mechanics.

## Managed task startup

The cloud environment's selected-repository catalog is a separate, narrower
inventory. At a **new task startup boundary**, after the platform has mounted
its isolated checkouts and before agents start working, the saved setup script
can invoke:

```sh
python3 -m enforcement.safe_refresh_repos \
  --startup-catalog "$CATALOG_PATH" \
  --workspace-root "$WORKSPACE_ROOT" \
  --checkout-branch work \
  --output-format json
```

The catalog mode uses the existing selected repository names, mount paths, and
commit pins; it does not enumerate an organization or read 1Password. Each
checkout must be on the specified task-local branch without an upstream, have
a clean tree, and have one GitHub fetch URL and one push URL matching the catalog
repository name. Its `HEAD` must contain the catalog's pinned commit. A fetched `origin/main` can then fast-forward the `work`
branch without renaming it. The original catalog pin remains a reproducible
starting snapshot; this command qualifies the resulting checkout at startup.

This command must not run against checkouts in active tasks. A blocked result
requires startup to stop and report the repository and reason. The saved cloud
setup/configuration is the invocation owner; this repository only supplies the
refresh implementation. Updating that saved configuration and publishing a new
version require their own review and fresh-runtime retest.

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
