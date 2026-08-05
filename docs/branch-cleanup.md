# Branch Cleanup

`enforcement.branch_cleanup` is a dry-run-first operational reinforcement tool
for reviewing stale Git branches across explicitly configured repositories.
It reports evidence and only mutates refs when run with `--apply`.

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

The single-pass tool uses six phases:

1. discover configured repo targets and current Git state
2. audit local and remote branch refs
3. plan or apply normal Git-proven cleanup for refs already ancestors of the
   remote default branch
4. plan or apply approved stale cleanup for non-ancestor refs only when config
   contains explicit approval and matching evidence
5. audit every registered worktree, prune only Git-classified stale metadata in
   apply mode, and verify final worktree state
6. emit a text or JSON report

Dry-run is the default. Mutation is limited to `--apply`.

The tool skips dirty repos, missing default remote refs, protected branches,
symbolic remote refs, and ambiguous branch names.

For local branches checked out in linked worktrees, normal cleanup is allowed
only when Git proves the branch is an ancestor of the remote default branch and
the linked worktree passes every worktree safety check below. Apply mode removes
the clean linked worktree with `git worktree remove` before deleting the branch,
then verifies that both its filesystem path and registered metadata are gone.
It never passes `--force`.

Approved stale cleanup mirrors that linked-worktree guard for local branches.
If a non-ancestor local branch has explicit stale approval, matching evidence,
and is checked out only in a clean linked worktree, dry-run reports it as
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

- the repository is an explicit config target and is not blocked
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

Every repository report includes every discovered worktree, including the
primary worktree and entries removed or pruned during that pass. Each worktree
record contains:

- repository, path, primary flag, branch or detached commit, and HEAD
- path, Git administrative consistency, porcelain cleanliness, and exact
  porcelain entries
- operation, lock, and prunable state with available reasons
- related branch-cleanup classification, outcome, and reason
- worktree cleanup classification, attempted action, result, and blocker
- stale-metadata prune status, final verification state, and residual manual
  action

Human-readable reports include aggregate discovered, removed, stale-metadata,
dirty/locked, failure, and remaining-related-cleanup counts plus per-worktree
details. JSON reports expose the same stable fields under each repository's
`worktrees` list and provide a top-level `worktree_summary`. The JSON schema
version is `2`.

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
applies only `normal_cleanup` actions; approved `stale_cleanup` actions are
reported but preserved during the sequence. Use a single-pass `--apply` run
without `--retry-normal-cleanup` for explicitly approved stale cleanup.

Codex command enforcement for this workflow should allow the direct Git argv
forms needed for safe cleanup: `git worktree list --porcelain -z --expire now`,
`git status --porcelain=v1 -z --untracked-files=all`, `git worktree remove
<path>`, `git worktree prune --expire now`, the branch deletion forms such as
`git branch -d -- <branch>`, and `git worktree add <path> <branch>` only for
verified restoration after an unexpected branch-deletion failure.

Normal cleanup stays separate from stale cleanup. Normal cleanup uses Git
ancestor proof. Stale cleanup requires config approval plus evidence such as a
merged GitHub PR record whose `head_oid` matches the branch tip, or an explicit
patch-equivalence approval validated with `git cherry`.

For squash-merged pull requests, branch tips commonly remain non-ancestors of
the default branch even though GitHub can prove that the pull request was
merged. Use a branch-specific stale approval with
`"kind": "github_merged_pr_exact_head"` together with `--audit-github-prs` to
require live GitHub evidence that the associated PR is `MERGED` and that the
PR `headRefOid` still matches the stale ref tip. This approval does not infer
safety from merge status alone; the head SHA must match.

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

Stale audit classifications are not deletion approvals. Stale cleanup still
requires explicit `stale_approvals` entries in config, and apply mode deletes
only stale refs whose configured approval evidence validates exactly.
Closed-unmerged PR refs, dirty worktree refs, protected branches, ambiguous
refs, and refs without matching evidence remain preserve-only.

Remote stale refs stay conservative when a same-named local branch is checked
out in any worktree, even if that linked worktree is clean. In that case the
remote stale cleanup action is preserved so the local worktree can be handled
first with a local approval or left intact intentionally.

The library does not write automation memory or schedule work. GitHub PR audit
evidence is retrieved only when `--audit-github-prs` is explicitly requested.

## Config Shape

```json
{
  "repositories": [
    {
      "name": "ai-workflow-enforcement",
      "path": ".",
      "remote": "origin",
      "default_branch": "main"
    }
  ],
  "protected_branches": ["main", "master", "trunk", "develop"],
  "stale_approvals": [
    {
      "repo": "ai-workflow-enforcement",
      "scope": "remote",
      "branch": "example/stale-merged-pr",
      "approved_by": "human reviewer",
      "reason": "Merged PR evidence reviewed; remote ref is stale.",
      "evidence": {
        "kind": "github_merged_pr_exact_head"
      }
    },
    {
      "repo": "ai-workflow-enforcement",
      "scope": "remote",
      "branch": "example/stale-merged-pr-with-recorded-evidence",
      "approved_by": "human reviewer",
      "reason": "Recorded merged PR evidence reviewed; remote ref is stale.",
      "evidence": {
        "kind": "github_merged_pr",
        "pr_number": 123,
        "state": "MERGED",
        "merged_at": "2026-05-08T00:00:00Z",
        "head_oid": "replace-with-branch-tip-oid"
      }
    },
    {
      "repo": "ai-workflow-enforcement",
      "scope": "local",
      "branch": "example/patch-equivalent-stale-branch",
      "approved_by": "human reviewer",
      "reason": "Patch-equivalence evidence reviewed separately.",
      "evidence": {
        "kind": "patch_equivalent"
      }
    }
  ]
}
```

Paths in config are resolved relative to the config file.
