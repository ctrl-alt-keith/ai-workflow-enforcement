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

## Safety Model

The tool uses five phases:

1. discover configured repo targets and current Git state
2. audit local and remote branch refs
3. plan or apply normal Git-proven cleanup for refs already ancestors of the
   remote default branch
4. plan or apply approved stale cleanup for non-ancestor refs only when config
   contains explicit approval and matching evidence
5. emit a text or JSON report

Dry-run is the default. Mutation is limited to `--apply`.

The tool skips dirty repos, conservative repos such as `.github`, missing
default remote refs, protected branches, symbolic remote refs, ambiguous branch
names, and branches checked out in worktrees.

Normal cleanup stays separate from stale cleanup. Normal cleanup uses Git
ancestor proof. Stale cleanup requires config approval plus evidence such as a
merged GitHub PR record whose `head_oid` matches the branch tip, or an explicit
patch-equivalence approval validated with `git cherry`.

The library does not write automation memory, schedule work, or call the GitHub
API. GitHub PR evidence is supplied by config so humans can decide what proof
is acceptable before any non-ancestor cleanup.

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
        "kind": "github_merged_pr",
        "pr_number": 123,
        "state": "MERGED",
        "merged_at": "2026-05-08T00:00:00Z",
        "head_oid": "replace-with-branch-tip-oid"
      }
    }
  ]
}
```

Paths in config are resolved relative to the config file.
