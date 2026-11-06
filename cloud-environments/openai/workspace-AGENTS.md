# Cloud environment startup

Only at the beginning of a new isolated task, after checkouts are available and before repository work, run:

Before invoking the refresh, select a supported network-enabled command execution
path and obtain any required permission grant. Where the exposed interface
supports them, use `sandbox_permissions="with_additional_permissions"` with
`additional_permissions.network.enabled=true`; an already authorized
network-enabled task context or another supported equivalent is also valid.
Do not invent arguments, bypass approval, or attempt the refresh through a known
network-restricted execution path. If no permitted path is available, stop and
report the blocker.

```sh
PYTHONPATH=/workspace/ai-workflow-enforcement python3 -m enforcement.safe_refresh_repos --startup-catalog /workspace/.onboarding-tools/repositories.json --workspace-root /workspace --checkout-branch work --output-format json
```

Stop on any nonzero exit and report blocked repositories and reasons. Never run this refresh against active or resumed task checkouts. If the task boundary is uncertain, stop and report that uncertainty rather than refreshing.

After successful refresh, read /workspace/ai-workflow-playbook/docs/start-here.md and follow its routing. Active or resumed tasks follow Playbook routing without rerunning task-start refresh. Cloud tasks are already isolated; do not create a Git worktree unless explicitly requested.
