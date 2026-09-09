# Hosted Codex bake-off setup

This runbook prepares the two CAK-283 hosted Codex variants for a paired,
staged comparison:

- `hosted-dead-surface-copilot` runs Codex with
  `copilot/gpt-5.3-codex` through GitHub Copilot organization billing.
- `hosted-dead-surface-openai` runs Codex with `gpt-5.3-codex`
  through direct OpenAI API billing.

Setup and validation do not authorize a live or billable dispatch. They also
do not authorize publishing the workflows to the default branch, changing a
schedule, disabling staged mode, widening permissions, selecting a winner,
retiring a local job, merging, or enabling auto-merge.

The experiment design, evidence fields, cost treatment, credential lifecycle,
and migration gates remain in [the bake-off contract](hosted-agentic-workflows-bakeoff.md).

## Prerequisites

Work from the repository root of a clean clone or the dedicated PR worktree.
The non-billable preflight requires:

- Git, Python 3, Make, and the GitHub CLI;
- GitHub CLI authentication with access to this repository;
- gh-aw `v0.88.7`, the compiler version recorded in the generated locks;
- GitHub Actions enabled with the generated SHA-pinned actions permitted; and
- repository admin access for the workflow's exact `roles: [admin]` gate.

GitHub itself requires write access to manually dispatch a workflow. This
bake-off is stricter: its generated activation guard admits only the exact admin
role. Run the local discovery checks first:

```sh
gh auth status
make help
gh aw version
```

`gh aw version` must report `v0.88.7`. If the extension is absent, install the
pinned version using the official gh-aw installation instructions:

```sh
gh extension install github/gh-aw@v0.88.7
```

Do not replace the pinned compiler merely because a newer release exists.
Upgrading the compiler and regenerating its locks is a separate reviewed
change.

### Copilot-backed path

The Copilot variant uses `copilot/gpt-5.3-codex` and
`copilot-requests: write`; it does not use an OpenAI provider secret. GitHub's
organization-billed route requires an organization with a Copilot plan and the
organization policies **Copilot CLI** and **Allow use of Copilot CLI billed to
the organization** enabled. Inspect those settings without changing them. A
missing entitlement or disabled policy must fail the run rather than trigger a
different billing path.

### Direct OpenAI path

The only GitHub provider-secret reference authored for the direct variant is
`secrets.OPENAI_API_KEY`. The workflow maps both runtime names,
`CODEX_API_KEY` and `OPENAI_API_KEY`, to that one reference and has a pre-agent
nonempty check. It does not select a `secrets.CODEX_API_KEY` fallback.

Check only whether the expected secret name exists; this command cannot read
its value:

```sh
gh secret list --repo ctrl-alt-keith/ai-workflow-enforcement --json name --jq 'any(.[]; .name == "OPENAI_API_KEY")'
```

`true` is required before the direct variant can run. A missing name is a
preflight blocker, not authority to create or inspect a credential. Use a
dedicated OpenAI project service-account automation key and follow the current
[OpenAI authentication](https://developers.openai.com/api/reference/overview#authentication),
[service-account key](https://developers.openai.com/api/reference/cli/resources/admin/subresources/organization/subresources/projects/subresources/service_accounts/subresources/api_keys/methods/create),
and [GitHub Actions secret](https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions)
documentation for the human-authorized external setup. Never place credential
material in repository files, command arguments, logs, PR text, or evidence.

The Copilot and direct OpenAI variants are separate authentication, billing,
usage-reporting, and recovery paths even though both execute Codex.

## Non-billable repository preflight

Run the commands below from the repository root. Compilation regenerates the
two lock files from their authored Markdown workflows. The next command proves
that regeneration produced no lock or action-pin drift.

```sh
gh aw compile hosted-dead-surface-copilot hosted-dead-surface-openai --strict --validate --poutine
git diff --exit-code -- .github/aw/actions-lock.json .github/workflows/hosted-dead-surface-copilot.lock.yml .github/workflows/hosted-dead-surface-openai.lock.yml
make check
git diff --check
```

Inspect the workflow names, scenarios, staged setting, models, and provider
wiring directly:

```sh
rg -n '^(name:|model:|  staged: true|          - (clean-no-change|controlled-candidate|ambiguous-rejection))' .github/workflows/hosted-dead-surface-copilot.md .github/workflows/hosted-dead-surface-openai.md
rg -n 'copilot-requests: write|secrets\.(OPENAI_API_KEY|CODEX_API_KEY)' .github/workflows/hosted-dead-surface-copilot.md .github/workflows/hosted-dead-surface-copilot.lock.yml .github/workflows/hosted-dead-surface-openai.md .github/workflows/hosted-dead-surface-openai.lock.yml
```

The second inspection must show `copilot-requests: write` only in the Copilot
variant and provider-secret references only to `secrets.OPENAI_API_KEY` in the
direct variant. Generated manifests can contain other framework-managed secret
names; do not mistake those for direct-provider selection.

Inspect the generated job permissions and immutable pins:

```sh
rg -n -B 6 -A 5 '^    permissions:$' .github/workflows/hosted-dead-surface-copilot.lock.yml .github/workflows/hosted-dead-surface-openai.lock.yml
rg -n 'uses: [^ ]+@[0-9a-f]{40}|@sha256:[0-9a-f]{64}' .github/workflows/hosted-dead-surface-copilot.lock.yml .github/workflows/hosted-dead-surface-openai.lock.yml
```

Review job meaning rather than treating line numbers or incidental generated
wording as policy. With gh-aw `v0.88.7`, the expected effective permissions are:

| Job | Copilot variant | Direct OpenAI variant |
| --- | --- | --- |
| Activation | `actions: read`, `contents: read` | `actions: read`, `contents: read` |
| Agent | `contents: read`, `pull-requests: read`, `copilot-requests: write` | `contents: read`, `pull-requests: read` |
| Threat detection | `contents: read`, `copilot-requests: write` | `contents: read` |
| Safe output | none (`permissions: {}`) | none (`permissions: {}`) |
| Conclusion | `actions: write`, `issues: write` | `actions: write`, `issues: write` |

The conclusion job's write permissions are broader than this staged bake-off's
desired output. The agent cannot access that token, but a human must accept the
generated posture before first dispatch. Do not bypass it or edit a generated
lock manually. Staged mode causes supported safe-output writes to be replaced
with an Actions summary preview; it does not make inference non-billable.

## GitHub publication boundary

PR-branch validation can complete while the workflows remain unmerged.
GitHub only accepts `workflow_dispatch` after the workflow file exists on the
repository's default branch. `--ref` can then select another branch, but it
does not remove the default-branch existence requirement.

This read-only command lists the workflows GitHub currently recognizes from
the default branch:

```sh
gh workflow list --repo ctrl-alt-keith/ai-workflow-enforcement --all
```

Before merge, absence of the two bake-off names is the expected platform
state. Publishing them requires a separately authorized merge. After
publication, confirm that both names appear and that GitHub Actions and the
required pinned actions are permitted. Do not dispatch as part of that check.

## Prepare one paired staged run

Each pair must use:

- the same `bakeoff_pair_id`;
- the same `bakeoff_scenario`;
- the same exact current `origin/main` SHA as `expected_base_sha`;
- staged mode enabled in both authored workflows; and
- sequential execution so concurrent platform load is not confused with an
  engine difference.

The three scenarios are `clean-no-change`, `controlled-candidate`, and
`ambiguous-rejection`. After default-branch publication and all human gates,
the following is a secret-free example for the controlled scenario. These are
live, billable dispatch commands: do not run them without separate approval.

```sh
git fetch --no-tags origin main
BAKEOFF_BASE_SHA=$(git rev-parse origin/main)
BAKEOFF_PAIR_ID=cak-283-controlled-001

gh workflow run hosted-dead-surface-copilot.lock.yml --ref main \
  -f qualification_acknowledgement=CAK-283 \
  -f bakeoff_pair_id="$BAKEOFF_PAIR_ID" \
  -f bakeoff_scenario=controlled-candidate \
  -f expected_base_sha="$BAKEOFF_BASE_SHA"

# Wait for the Copilot run to finish before dispatching the direct variant.
gh workflow run hosted-dead-surface-openai.lock.yml --ref main \
  -f qualification_acknowledgement=CAK-283 \
  -f bakeoff_pair_id="$BAKEOFF_PAIR_ID" \
  -f bakeoff_scenario=controlled-candidate \
  -f expected_base_sha="$BAKEOFF_BASE_SHA"
```

A maintainer has reached non-billable preflight when local compilation and
validation pass, locks are fresh, the generated permissions and provider
references are accepted for review, workflow publication requirements are
understood, and each external prerequisite is either verified or recorded as
a blocker. That state is readiness evidence, not dispatch authority.

## Evidence after an authorized run

For each run retain the pair ID, scenario, exact base SHA, requested and
observable effective model, result/no-op/failure, staged safe-output preview,
agent and threat-detection usage, gh-aw credits, provider usage and cost when
exposed, Copilot consumption when exposed, Actions duration, retries, human
interventions, and permission or configuration failures. Do not replace an
unavailable metric with zero.

Use `gh aw logs` for the run's duration, token, turn, artifact, and gh-aw credit
surface, then reconcile provider and GitHub billing in their owning dashboards.
Keep raw usage separate from cost estimates and projections; gh-aw credit and
cost estimates are best-effort and do not replace provider billing. See the
[bake-off evidence contract](hosted-agentic-workflows-bakeoff.md#evidence-and-cost-accounting)
for the comparison fields and retrieval command.

For credential replacement or recovery, preserve one invariant: install and
verify a dedicated replacement before retiring its predecessor, retain only
sanitized evidence, and keep all credential material out of repository state.
Follow current provider documentation; key creation, exposure, rotation, and
revocation remain human-gated external operations.

## Troubleshooting

| Symptom | Fail-closed inspection |
| --- | --- |
| Direct-provider secret check is `false` or activation reports a missing key | Confirm only the `OPENAI_API_KEY` secret name and the authored references; request human-authorized external setup. Never print or test the value locally. |
| Copilot inference is unavailable or forbidden | Inspect the organization Copilot plan, Copilot CLI policy, organization-billing policy, and generated `copilot-requests: write`; do not fall back to another credential. |
| `gh aw version` is not `v0.88.7` | Install or select the reviewed version, then recompile. Treat an upgrade as a separate change. |
| Compilation changes a lock or action-pin cache | Review the authored source, compiler identity, and generated diff; do not hand-edit the lock or claim freshness. |
| Checkout or delivery reports an expected-base mismatch | Fetch `origin/main`, abandon the stale pair identity, and prepare a new pair against one exact current SHA. |
| Activation reports a duplicate match | Inspect the open PR carrying the stable bake-off marker; do not weaken duplicate suppression. |
| No branch or PR appears after a successful staged run | Inspect the Actions summary for the staged preview. Do not disable staged mode to force a visible object. |
| Requested and effective models differ | Preserve both values as evidence and stop any comparison conclusion that depends on equivalence. |
| Generated conclusion permissions are not accepted | Stop before dispatch. Reconcile the framework posture through a reviewed source change; do not edit generated YAML or bypass the gate. |

## Official setup sources

These claims were checked on 2026-09-09 against:

- [GitHub manual workflow dispatch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
- [GitHub Actions repository settings](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository)
- [GitHub agentic workflow authentication](https://docs.github.com/en/copilot/how-tos/github-agentic-workflows/creating-github-agentic-workflows#authentication)
- [gh-aw Codex engine](https://github.github.com/gh-aw/engines/codex/)
- [gh-aw CLI and version installation](https://github.github.com/gh-aw/setup/cli/)
- [gh-aw compilation and lock pinning](https://github.github.com/gh-aw/reference/compilation-process/)
- [gh-aw permissions](https://github.github.com/gh-aw/reference/permissions/)
- [gh-aw staged mode](https://github.github.com/gh-aw/reference/staged-mode/)
- [gh-aw cost management](https://github.github.com/gh-aw/reference/cost-management/)
- [OpenAI API authentication](https://developers.openai.com/api/reference/overview#authentication)
- [OpenAI project service-account keys](https://developers.openai.com/api/reference/cli/resources/admin/subresources/organization/subresources/projects/subresources/service_accounts/subresources/api_keys/methods/create)
