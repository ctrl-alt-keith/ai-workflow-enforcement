---
name: Hosted Dead Surface Bake-off - Copilot
description: Manually qualify the Copilot-backed half of the CAK-283 dead-surface bake-off.
intent: Compare hosted inference paths without widening merge authority, credential scope, or operator attention.
on:
  workflow_dispatch:
    inputs:
      qualification_acknowledgement:
        description: Type CAK-283 to acknowledge that this is a human-gated bake-off.
        required: true
        type: string
      bakeoff_pair_id:
        description: Shared identifier for the paired Copilot and OpenAI runs.
        required: true
        type: string
      expected_base_sha:
        description: Exact main commit shared by both runs in the pair.
        required: true
        type: string
      bakeoff_scenario:
        description: Qualification scenario to run against the checked-in evidence pack.
        required: true
        type: choice
        options:
          - clean-no-change
          - controlled-candidate
          - ambiguous-rejection
  roles: [admin]
  stale-check: full
  skip-if-match: 'is:pr is:open "cak-283-hosted-dead-surface-bakeoff" in:body'
permissions:
  contents: read
  pull-requests: read
  copilot-requests: write
if: github.event.inputs.qualification_acknowledgement == 'CAK-283' && github.event.inputs.bakeoff_pair_id != '' && github.event.inputs.expected_base_sha != ''
strict: true
engine: codex
model: copilot/gpt-5.3-codex
max-turns: 24
max-ai-credits: 500
timeout-minutes: 30
concurrency:
  group: cak-283-hosted-dead-surface-bakeoff
  job-discriminator: ${{ github.event.inputs.bakeoff_pair_id }}
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
    env:
      BAKEOFF_PAIR_ID: ${{ github.event.inputs.bakeoff_pair_id }}
      BAKEOFF_SCENARIO: ${{ github.event.inputs.bakeoff_scenario }}
      EXPECTED_BASE_SHA: ${{ github.event.inputs.expected_base_sha }}
    run: |
      test "$(git rev-parse HEAD)" = "$EXPECTED_BASE_SHA"
      git rev-parse HEAD > /tmp/gh-aw/agent/tested-base.txt
      echo "sha=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"
      printf 'variant=copilot\npair_id=%s\nscenario=%s\nexpected_base_sha=%s\nstarted_at=%s\n' \
        "$BAKEOFF_PAIR_ID" "$BAKEOFF_SCENARIO" "$EXPECTED_BASE_SHA" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        > /tmp/gh-aw/agent/bakeoff-context.txt
  - name: Hydrate exact playbook source
    env:
      PLAYBOOK_REPOSITORY: https://github.com/ctrl-alt-keith/ai-workflow-playbook.git
      PLAYBOOK_SHA: 16fc40db184882cbdfc117b0aba53b8719ddbe99
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
  staged: true
  threat-detection:
    max-ai-credits: 200
    engine:
      id: copilot
      model: copilot/gpt-5.3-codex
  create-pull-request:
    title-prefix: "[CAK-283 bake-off] "
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
      - examples/qualification/dead-surface-bakeoff/**/*.py
---

# Hosted Dead Surface Bake-off

## Startup

Your first repository action must be to read and apply
`/tmp/gh-aw/agent/ai-workflow-playbook/docs/start-here.md`. Follow its routing
for implementation work, then read the checked-out repository's `AGENTS.md` and
the task-relevant authoritative sources they select. Stop with
`report_incomplete` if either startup source is missing, inconsistent with the
tested checkout, or cannot be applied.

## Scope

Operate only on `ctrl-alt-keith/ai-workflow-enforcement` at the exact checked-out
commit recorded in `/tmp/gh-aw/agent/tested-base.txt`. Read the variant, paired
run identifier, and scenario from `/tmp/gh-aw/agent/bakeoff-context.txt`. This
is a manual, staged platform-qualification slice of CAK-283, not a recurring
schedule, a production finding, or the full CAK-177 contract.

Use only the selected scenario under
`examples/qualification/dead-surface-bakeoff/` as the candidate evidence pack:

- `clean-no-change`: inspect `clean/`; return `noop` unless its current source
  evidence actually contains a qualifying dead surface.
- `controlled-candidate`: inspect `controlled/`; propose only a source-grounded
  removal of its redundant wrapper when the current evidence and validation
  support that result.
- `ambiguous-rejection`: inspect `ambiguous/`; apply the normal rejection rule
  when the evidence leaves ownership or compatibility unresolved.

Do not inspect the other scenario directories for candidate selection. Treat
these paths explicitly as qualification fixtures, never as production findings.

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
finding merely to prove that the bake-off ran.

## Implementation and delivery

When one candidate clears the bar:

1. Reinspect every authoritative source that supports it. Search open and
   closed pull requests, remote branches, and recent history for the stable
   bake-off marker or an equivalent change.
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

The staged pull request preview must name the simplification. Its body must include:

- `<!-- cak-283-hosted-dead-surface-bakeoff -->`;
- the evidenced root cause and the simpler resulting contract;
- the tested commit from `/tmp/gh-aw/agent/tested-base.txt` and changed paths;
- the exact validation commands and outcomes;
- explicit exclusions, residual risk, and rollback posture; and
- the variant, paired run identifier, scenario, and a statement that this is a
  qualification-fixture result rather than a production finding; and
- a statement that human review and merge authority are unchanged.

Do not merge, enable auto-merge, push directly to an existing branch, create an
issue, modify a workflow or instruction file, or make a second proposal. The
configured post-agent step independently reruns validation and rejects a moved
default branch before the safe-output job can create the draft pull request.

If no candidate clears the bar, call `noop` with a short evidence-based reason
and create no proposal. If required evidence, tooling, or validation is
unavailable, call `report_incomplete` once with the exact blocker. Staged mode
must remain enabled for the bake-off: it records a safe-output preview and must
not create a branch or pull request.
