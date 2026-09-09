---
name: Hosted Dead Surface Pilot
description: Manually qualify one bounded dead-surface proposal through Codex and a draft pull request.
intent: Qualify hosted repository simplification without widening merge authority, credential scope, or operator attention.
on:
  workflow_dispatch:
    inputs:
      qualification_acknowledgement:
        description: Type CAK-283 to acknowledge that this is a human-gated pilot.
        required: true
        type: string
  roles: [admin]
  stale-check: full
  skip-if-match: 'is:pr is:open "cak-283-hosted-dead-surface-pilot" in:body'
permissions:
  contents: read
  pull-requests: read
  copilot-requests: write
if: github.event.inputs.qualification_acknowledgement == 'CAK-283'
strict: true
engine: codex
model: copilot/gpt-5.3-codex
max-turns: 24
timeout-minutes: 30
concurrency:
  group: hosted-dead-surface-pilot
  job-discriminator: ${{ github.run_id }}
sandbox:
  agent:
    id: awf
network:
  allowed:
    - defaults
    - github
tools:
  cli-proxy: true
  edit:
  bash: ["*"]
  github:
    mode: gh-proxy
    toolsets: [repos, pull_requests]
steps:
  - name: Capture exact tested base
    id: tested-base
    run: |
      git rev-parse HEAD > /tmp/gh-aw/agent/tested-base.txt
      echo "sha=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"
  - name: Hydrate exact playbook source
    env:
      PLAYBOOK_REPOSITORY: https://github.com/ctrl-alt-keith/ai-workflow-playbook.git
      PLAYBOOK_SHA: 2055935f6206a07c7085cb679a757369b9072488
    run: |
      git clone --filter=blob:none --no-checkout "$PLAYBOOK_REPOSITORY" /tmp/gh-aw/agent/ai-workflow-playbook
      git -C /tmp/gh-aw/agent/ai-workflow-playbook checkout --detach "$PLAYBOOK_SHA"
      test "$(git -C /tmp/gh-aw/agent/ai-workflow-playbook rev-parse HEAD)" = "$PLAYBOOK_SHA"
post-steps:
  - name: Enforce validation and unchanged base before delivery
    env:
      TESTED_BASE_SHA: ${{ steps.tested-base.outputs.sha }}
    run: |
      git diff --check
      make check
      git fetch --no-tags origin main
      test "$(git rev-parse HEAD)" = "$TESTED_BASE_SHA"
      test "$(git rev-parse origin/main)" = "$TESTED_BASE_SHA"
safe-outputs:
  activation-comments: false
  report-failure-as-issue: false
  report-incomplete:
    create-issue: false
  missing-tool: false
  missing-data: false
  allowed-github-references: [repo]
  max-bot-mentions: 1
  threat-detection:
    engine:
      id: copilot
      model: copilot/gpt-5-mini
  create-pull-request:
    title-prefix: "[CAK-283 pilot] "
    branch-prefix: "agentic/dead-surface-"
    base-branch: main
    allowed-base-branches: [main]
    draft: true
    auto-merge: false
    fallback-as-issue: false
    auto-close-issue: false
    if-no-changes: ignore
    stacked: false
    protected-files: blocked
    max: 1
    max-patch-size: 128
    max-patch-files: 8
    allowed-files:
      - enforcement/**/*.py
      - tests/**/*.py
      - docs/**/*.md
      - config/**/*.json
      - schemas/**/*.json
---

# Hosted Dead Surface Pilot

## Startup

Your first repository action must be to read and apply
`/tmp/gh-aw/agent/ai-workflow-playbook/docs/start-here.md`. Follow its routing
for implementation work, then read the checked-out repository's `AGENTS.md` and
the task-relevant authoritative sources they select. Stop with
`report_incomplete` if either startup source is missing, inconsistent with the
tested checkout, or cannot be applied.

## Scope

Operate only on `ctrl-alt-keith/ai-workflow-enforcement` at the exact checked-out
commit recorded in `/tmp/gh-aw/agent/tested-base.txt`. This is a manual
platform-qualification slice of CAK-283, not a recurring schedule and not the
full CAK-177 multi-model simplification contract.

Inspect current source, tests, documentation, configuration, recent commits,
open pull requests, and the canonical validation contract. Select at most one
dead or redundant surface whose removal fixes an evidenced root cause while
preserving intended behavior. Prefer deletion or collapse into an existing
canonical owner.

An eligible candidate must:

- be supported by current source evidence at the tested commit;
- remove an unused surface, obsolete compatibility path, duplicated source of
  truth, needless wrapper, or misleading guardrail;
- fit one reason and no more than eight changed files;
- have a complete repository-native validation path; and
- have no equivalent open pull request or unresolved product, architecture,
  compatibility, security, permission, migration, or provider decision.

Reject formatting, naming, wording-only cleanup, speculative refactors, broad
rewrites, new features, workflow changes, instruction changes, dependency
changes, and changes whose safety depends on workstation state. Do not create a
finding merely to prove that the pilot ran.

## Implementation and delivery

When one candidate clears the bar:

1. Reinspect every authoritative source that supports it. Search open and
   closed pull requests, remote branches, and recent history for the stable
   pilot marker or an equivalent change.
2. Make the smallest complete change inside the configured file allowlist.
3. Update focused tests or documentation only when required by the behavior.
4. Run `make check` and `git diff --check`. If either fails, do not declare a
   pull-request output.
5. Review the complete diff for scope, secrets, generated files, and accidental
   changes.
6. Use the `safeoutputs` CLI exactly once to declare one `create_pull_request`
   output. Use an actual multiline JSON payload, not display-escaped text.

Do not commit the working-tree change. The safe-output path packages the patch,
and the unchanged-HEAD check is part of the delivery gate.

The pull request title must name the simplification. Its body must include:

- `<!-- cak-283-hosted-dead-surface-pilot -->`;
- the evidenced root cause and the simpler resulting contract;
- the tested commit from `/tmp/gh-aw/agent/tested-base.txt` and changed paths;
- the exact validation commands and outcomes;
- explicit exclusions, residual risk, and rollback posture; and
- a statement that human review and merge authority are unchanged.

Do not merge, enable auto-merge, push directly to an existing branch, create an
issue, modify a workflow or instruction file, or make a second proposal. The
configured post-agent step independently reruns validation and rejects a moved
default branch before the safe-output job can create the draft pull request.

If no candidate clears the bar, call `noop` with a short evidence-based reason
and create no visible output. If required evidence, tooling, or validation is
unavailable, call `report_incomplete` once with the exact blocker.
