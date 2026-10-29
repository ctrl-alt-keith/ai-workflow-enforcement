# Cloud environment startup

Only at the beginning of a new isolated task, after checkouts are available and before repository work, run:

```sh
PYTHONPATH=/workspace/ai-workflow-enforcement python3 -m enforcement.safe_refresh_repos --startup-catalog /workspace/.onboarding-tools/repositories.json --workspace-root /workspace --checkout-branch work --output-format json
```

Stop on any nonzero exit and report blocked repositories and reasons. Never run this refresh against active or resumed task checkouts. If the task boundary is uncertain, stop and report that uncertainty rather than refreshing.

After successful refresh, read /workspace/ai-workflow-playbook/docs/start-here.md and follow its routing. Active or resumed tasks follow Playbook routing without rerunning task-start refresh. Cloud tasks are already isolated; do not create a Git worktree unless explicitly requested.
