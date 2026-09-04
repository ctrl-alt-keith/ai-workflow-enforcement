# Branch Cleanup

`enforcement.branch_cleanup` is a dry-run-first operational reinforcement tool
for reviewing stale Git branches across active GitHub organization members. It
reports provider scope, local resolution, and branch/worktree evidence, and it
only mutates refs when run with `--apply`.

Run a dry-run:

```sh
python3 -m enforcement.branch_cleanup --config examples/branch-cleanup.json
```

Apply the reported deletions:

```sh
python3 -m enforcement.branch_cleanup --config examples/branch-cleanup.json --apply
```

Run bounded normal cleanup with re-scans after each successful apply:

```sh
python3 -m enforcement.branch_cleanup --config examples/branch-cleanup.json --apply --retry-normal-cleanup
```

Audit stale/non-ancestor refs without deleting them:

```sh
python3 -m enforcement.branch_cleanup --config examples/branch-cleanup.json --audit-stale --audit-github-prs
```

## Safety Model

The single-pass tool uses seven phases:

1. enumerate and reconcile provider-owned active repository scope, stable-ID
   exclusions, credential breadth, and local targets
2. verify each existing local Git target's fetch URL, effective push URL,
   current provider locator, and stable provider repository ID
3. audit local and remote branch refs
4. plan or apply normal Git-proven cleanup for refs already ancestors of the
   remote default branch
5. plan or apply stale cleanup for non-ancestor refs only when live GitHub
   exact-head evidence or `git cherry` patch-equivalence proves eligibility
6. audit every registered worktree, prune only Git-classified stale metadata in
   apply mode, and verify final worktree state
7. emit a text or JSON report

Dry-run is the default. Mutation is limited to `--apply`.

`--apply` is also the complete non-interactive authority boundary for
policy-proven cleanup. It automatically removes a secondary worktree when the
related branch action is authorized and every worktree safety check passes; it
does not request a second confirmation for that worktree. The CLI never reads
stdin or depends on a TTY, and its Git and GitHub child processes run with
stdin closed and interactive credential prompts disabled. Audit/report-only
mode remains read-only. Unsafe, uncertain, and human-review classifications are
preserved without prompting.

This unattended behavior is deliberately narrower than a generic confirmation
bypass. There is no `--yes`, `--force`, or `--assume-yes` option, and apply mode
does not weaken any classification or removal invariant. A safe worktree is
removed only with Git's non-force `git worktree remove` mechanism and only as
part of its authorized branch-cleanup action.

The tool skips missing or uninspectable local checkouts, dirty repos, missing
default remote refs, protected branches, symbolic remote refs, and ambiguous
branch names. A missing checkout remains a provider candidate and is reported
as skipped; filesystem absence never changes organization membership.

## Provider Scope And Completeness

Provider-backed scope uses GitHub's current organization repository state as
membership authority. Every current member with `archived = false` is an
inspection candidate. Archived, deleted, and transferred-out repositories are
not kept in scope by local configuration, while newly created or transferred-in
active members enter scope automatically.

The shared organization enumerator marks a result `complete` only for the
implementation's supported scope-bearing credential profile. The same acting
credential must provide all of this evidence:

- `/user` identifies the actor and returns an `X-OAuth-Scopes` header containing
  `repo` plus either `read:org` or its documented `admin:org` parent
- `GET /orgs/{org}` returns full organization details with the same scope set,
  no `X-GitHub-SSO` restriction, and valid non-negative integer
  `public_repos` and `total_private_repos` fields
- the actor's organization membership response identifies the same login with
  `state=active` and `role=admin`
- every paginated repository response succeeds and every entry parses,
  including positive numeric repository ID, current `full_name`, visibility,
  private and archived flags, and default branch
- counts from the complete paginated result, before archived repositories are
  removed from branch-cleanup candidates, exactly match `public_repos`,
  `total_private_repos`, and their sum

Scope labels are prerequisites, not completeness proof. The same credential
must return the actor, unrestricted active-owner membership, complete parsed
pagination, full organization counts, and exact public/private/total agreement.
Any missing, malformed, restricted, mismatched, or unsupported evidence leaves
scope `unknown`. Read-only runs preserve partial evidence; apply exits before
cleanup. Internal-visibility repositories also remain partial evidence because
GitHub does not define which organization total includes them.

The provider contract relies on GitHub's official
[OAuth scope reference](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps),
[organization repository endpoint](https://docs.github.com/en/rest/repos/repos#list-organization-repositories),
[full organization details endpoint](https://docs.github.com/en/rest/orgs/orgs#get-an-organization),
[membership endpoint](https://docs.github.com/en/rest/orgs/members#get-an-organization-membership-for-the-authenticated-user),
and [pagination guidance](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api).
This provider contract was verified against those sources on 2026-08-14.

Explicit exclusions reconcile after provider discovery by positive numeric
GitHub repository ID. Each retains its recorded `organization/repository`
locator, durable `reason`, and `authority`. A rename with the same ID remains
excluded and the report shows the current locator plus `locator_drift=true`.
An archived member with the same ID is reported as reconciled and excluded. If
the ID is absent (including deletion or transfer out), or the recorded locator
now belongs to a different ID, the exclusion is unresolved/conflicting. It is
preserved in read-only output and blocks provider-backed mutation pending human
disposition. Duplicate exclusion IDs or locators are rejected at load time.
The tool never rewrites or deletes an exclusion.

The `workspace_root` maps each included repository to
`<workspace_root>/<repository-name>`. `repository_overrides` may change only a
specific member's local `path`, `remote`, or `default_branch`; an override
never adds a repository to provider scope. Relative override paths resolve
beneath `workspace_root`.

Path resolution is not repository identity. For every existing provider-backed
Git checkout, the selected remote must have exactly one fetch URL and one
effective push URL; both must parse as the exact current `github.com` locator
from provider enumeration. The tool then reads `/repos/{owner}/{name}` and
requires both its current `full_name` and stable numeric `id` to match the
enumerated target. Wrong owners, wrong names, stale rename/transfer locators,
ambiguous or missing remotes, and provider-ID disagreement are reported as
`mismatch` or `unverified`. Read-only inspection skips that target after
reporting the evidence. Apply mode performs this identity preflight for every
existing Git target before entering repository cleanup, so no target is
mutation-capable when any checked target fails. GitHub may redirect old remote
locators after a rename or transfer, but that redirect is not accepted as
current local configuration; operators must update the remote explicitly.
See GitHub's official
[repository response](https://docs.github.com/en/rest/repos/repos#get-a-repository)
[remote URL guidance](https://docs.github.com/en/get-started/git-basics/managing-remote-repositories),
and [repository rename guidance](https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository).
This identity seam was verified against those sources on 2026-08-14.

For local branches checked out in linked worktrees, normal cleanup is allowed
only when Git proves the branch is an ancestor of the remote default branch and
the linked worktree passes every worktree safety check below. Apply mode removes
the clean linked worktree with `git worktree remove` before deleting the branch,
then verifies that both its filesystem path and registered metadata are gone.
It never passes `--force`.

Stale cleanup mirrors that linked-worktree guard for local branches. If a
non-ancestor local branch has live exact-head or patch-equivalence evidence and
is checked out only in a clean linked worktree, dry-run reports it as
`would_delete` and records that apply mode will remove the clean linked
worktree before deleting the branch. Apply mode re-checks the worktree state
and branch tip before removing the worktree. Dirty, uninspectable, target, and
changed-tip worktrees remain preserve/fail closed.

## Worktree Safety And Final Audit

Worktree removal authority is derived from the exact local branch-cleanup
action. A branch being merged, deleted elsewhere, or named like an old topic is
not sufficient. A live linked worktree is removable only when its related local
branch action is currently authorized as `would_delete`, the action is being
applied, and all of these checks pass immediately before removal:

- the repository is a resolved provider candidate or legacy compatibility
  target and is not blocked
- the worktree is neither the primary worktree nor the configured target path
- `git worktree list --porcelain -z --expire now` identifies the registration
  and its branch or detached state
- the live path resolves to the same Git common directory as the configured
  repository, with the expected linked-worktree administrative directory
- the worktree is unlocked
- `git status --porcelain=v1 -z --untracked-files=all` is empty
- no merge, rebase, cherry-pick, revert, bisect, or sequencer state is present
- the checked-out branch is not protected or ambiguous
- normal cleanup still proves ancestry, or stale cleanup still proves the exact
  approved branch tip, immediately before removal

Dirty, staged, unstaged, untracked, conflicted, uninspectable, locked, primary,
configured-target, protected, detached, ambiguous, active-operation, skipped,
or failed-deletion worktrees are preserve-only. A detached clean worktree is
not inferred to belong to a branch from its commit identity. A removal or final
verification failure stops deletion of the related branch and is reported. If
Git unexpectedly rejects branch deletion after removing its clean linked
worktree, the tool recreates that worktree with `git worktree add` and verifies
the restored clean registration. Restoration fails closed if the exact original
path has reappeared, including as a symlink; the tool does not clear, overwrite,
relocate, or force-reuse that path. Restoration failure remains a reported
manual recovery condition. The tool does not force removal or delete a
directory directly.

The audit also distinguishes live worktrees from stale administrative metadata.
Git's stable porcelain `prunable` annotation is the authority for missing-path
metadata. Dry-run reports that metadata without mutation. Apply mode invokes
`git worktree prune --expire now` only when an unlocked, non-primary prunable
entry was discovered, then re-runs porcelain discovery to prove which entries
were pruned. Pruning stale metadata never authorizes deletion of a live
filesystem checkout or its branch. Locked missing-path entries remain
preserved.

Reports include every discovered worktree, its branch and repository identity,
safety observations, related cleanup disposition, action result, authority,
final verification, and any residual manual action. JSON schema version `5`
provides the same evidence plus a top-level `worktree_summary`; the schema-2
`removed` and `preserved_by_reason` summary aliases remain available.

`cleanup_authority` comes from the policy decision that authorized a safe
worktree removal or metadata prune, not from the later attempted-action field.
An `apply_policy_authorized` record identifies that CLI policy boundary; it
does not claim that a human approved that individual worktree interactively.

Repeated apply is idempotent: a removed worktree or pruned metadata entry is not
rediscovered on the next pass. Branches preserved solely because missing-path
metadata first required safe pruning may become eligible on a later bounded
pass, after fresh branch and worktree inspection.

Built-in protected branches are always kept as a safety floor: `main`,
`master`, `trunk`, and `develop`. Configured `protected_branches` add to that
set; they do not replace it.

Dry-run mode does not fetch or prune. Apply mode runs `git fetch <remote>
--prune` before planning and applying cleanup. Because remote refs can change
between those two modes, dry-run and apply action lists may differ.

`--retry-normal-cleanup` makes that behavior explicit and bounded. It starts
with a dry-run, applies normal cleanup only when `--apply` is also present,
then re-scans after each apply. It repeats while the latest re-scan reports
`normal_cleanup` actions with `would_delete`, stopping when no such refs remain
or when `--max-apply-passes` is reached. The default pass cap is 3. Retry mode
applies only `normal_cleanup` actions; `stale_cleanup` actions are reported but
preserved during the sequence. Use a single-pass `--apply` run without
`--retry-normal-cleanup` for stale cleanup.

Normal cleanup stays separate from stale cleanup. Normal cleanup uses Git
ancestor proof. Stale cleanup deletes a ref when live GitHub evidence proves a
merged PR's `head_oid` exactly matches the branch tip, or when `git cherry`
proves patch-equivalence to the default branch.

For squash-merged pull requests, branch tips commonly remain non-ancestors of
the default branch even though GitHub can prove that the pull request was
merged. `--apply --audit-github-prs` authorizes their cleanup only when live
evidence shows the associated PR is `MERGED` and its `headRefOid` exactly
matches the stale ref tip. Merge status alone is insufficient.

`--audit-stale` adds report-only classifications for non-ancestor refs that
cannot be deleted by normal cleanup. It reports why each candidate was not
auto-deleted and may classify refs as:

- `stale_candidate_patch_equivalent`
- `stale_candidate_merged_pr_exact_head`
- `closed_unmerged_preserve`
- `needs_human_review`
- `blocked_dirty_worktree`

Patch-equivalence uses `git cherry` against the remote default branch. When
`--audit-github-prs` is supplied, the audit also uses `gh pr list` for current
PR state, merged timestamps, and head SHA evidence. If GitHub evidence cannot
be retrieved or parsed, the candidate remains `needs_human_review`.

The `stale_candidate_merged_pr_exact_head` classification becomes a deletion
action only in `--apply --audit-github-prs` mode, after the live query is
re-run. `stale_candidate_patch_equivalent` likewise becomes a deletion action
when `git cherry` re-proves it. Closed-unmerged PR refs, dirty worktree refs,
protected branches, ambiguous refs, and refs without matching evidence remain
preserve-only.

Remote stale refs stay conservative when a same-named local branch is checked
out in any worktree, even if that linked worktree is clean. In that case the
remote stale cleanup action is preserved so the local worktree can be handled
first or left intact intentionally.

The library does not write automation memory or schedule work. GitHub PR audit
evidence is retrieved only when `--audit-github-prs` is explicitly requested.

## Config Shape

```json
{
  "scope": {
    "provider": "github_organization",
    "organization": "ctrl-alt-keith",
    "workspace_root": "[workspace-root]",
    "exclusions": [
      {
        "repository_id": 123456789,
        "repository": "ctrl-alt-keith/example-excluded-repository",
        "reason": "Durable policy exception",
        "authority": "CAK-000"
      }
    ],
    "repository_overrides": {
      "ctrl-alt-keith/example-nonstandard-checkout": {
        "path": "nested/example-nonstandard-checkout"
      }
    }
  },
  "protected_branches": ["main", "master", "trunk", "develop"]
}
```

Replace `[workspace-root]` with the local checkout root. Relative
`workspace_root` values resolve from the config directory. The expected
initial exclusion set is empty unless a current authority establishes an
exception. Look up and record the provider's positive numeric repository ID;
do not guess it. Provider exclusions without `repository_id` are rejected, and
the tool does not migrate or rewrite old entries automatically.

Existing configs with a top-level `repositories` array remain accepted in
`legacy_explicit_compatibility` mode so operators can stage migration without
an outage. That mode preserves the previous target and mutation behavior but
explicitly reports that it does not establish complete organization
membership. New fleet configuration should use provider-backed scope; do not
add new repositories to the legacy positive allowlist.
