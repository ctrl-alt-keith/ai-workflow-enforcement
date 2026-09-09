# Hosted agentic-workflow qualification

CAK-283 is qualifying which workstation-scheduled Codex automations can move to
GitHub-hosted execution without inheriting unobserved local state or expanding
write authority. The initial implementation is
`.github/workflows/hosted-dead-surface-pilot.md`: a manually dispatched,
single-repository slice of the active Dead Surface Sweep.

The pilot does not replace or modify the existing Hosted Stewardship Engine.
That engine remains the deterministic owner of its three fixed strategies. The
agentic pilot is a separate public-preview substrate with a narrower initial
cohort and an explicit adoption gate.

## Active local fleet classification

This classification reflects the ten active Codex scheduler definitions
inspected for CAK-283 on 2026-09-09. “Hosted-eligible” means the task's core work
can be performed from GitHub and repository sources; it does not mean its local
schedule may be retired before a hosted equivalence run is accepted.

| Automation | Locality | Reason |
| --- | --- | --- |
| Dead Surface Sweep | Hosted-eligible | Repository and GitHub evidence can support a bounded per-repository proposal. |
| Docs Drift Sweep | Hosted-eligible | Repository implementation and documentation are the required sources. |
| Staff Engineer Rounds | Hosted-eligible | A reviewed repository cohort can be processed without workstation-only state. |
| Test Gap Sweep | Hosted-eligible | Repository tests, history, issues, and pull requests are sufficient inputs. |
| AGENTS Drift Detector | Hybrid | GitHub repositories are hosted-readable, but the active contract also inspects the workspace-level local `AGENTS.md` and Dropbox state. |
| Repository Governance Audit | Hybrid | GitHub reads are hostable, while the active definition pins a local Enforcement checkout and local report destination. |
| Staging vs Canon Audit | Hybrid | The comparison depends on local workspace and staging surfaces. |
| Automation Consistency Review | Local | The task inspects the live local scheduler and configuration surfaces. |
| Compact Automation Memory | Local | The task reads and compacts local automation `memory.md` files. |
| Delete Merged Repository Branches | Local | The task mutates local branches and worktrees. |

The four paused definitions are not part of the active fleet. They remain
paused and unchanged: Organization PR & Issue Scan, Workflow Drift Audit,
Region Policy Drift Review, and Knowledge Adapters Weekly Chaos + All
Validation.

## Pilot contract

The first slice intentionally narrows Dead Surface Sweep from an organization
fan-out to this repository only. It has these controls:

- manual `workflow_dispatch` only, with an exact `CAK-283` acknowledgement and
  repository-admin role check;
- a pre-activation search that skips the agent while another pull request from
  this pilot remains open;
- Codex running through GitHub Copilot inference, so the pilot introduces no
  new static OpenAI or Anthropic secret;
- read-only repository and pull-request access in the agent job;
- one draft pull request maximum, no issue fallback, no merge or auto-merge,
  and an eight-file/128 KiB patch ceiling;
- an allowlist limited to Enforcement source, tests, docs, configuration, and
  schemas, with protected files blocked; and
- a deterministic post-agent gate that reruns `make check`, checks the diff,
  and verifies that `origin/main` still equals the tested workflow-dispatch
  commit before the separate safe-output job may write.

The generated safe-output job receives only `contents: write` and
`pull-requests: write`. GitHub Agentic Workflows v0.88.7 also emits a separate
conclusion job with `contents: write`, `issues: write`, and
`pull-requests: write` even though activation comments, failure issues, and
incomplete-report issues are disabled here. The agent cannot access that job's
token, and all referenced actions are pinned, but the conservative generated
permission set is still an explicit public-preview adoption risk for human
review before merge and first dispatch.

The workflow hydrates the Playbook from exact commit
`2055935f6206a07c7085cb679a757369b9072488`. Updating that dependency requires a
source review, recompilation, and the same repository validation as any other
workflow change.

The workflow source is compiled with GitHub Agentic Workflows `v0.88.7`. Both
the Markdown source and generated `.lock.yml` are committed; the generated file
is marked in `.gitattributes`.

## Credential lifecycle boundary

The pilot requests `copilot-requests: write` only for model inference and uses
the run's normal GitHub token for the safe-output job. Before the first dispatch,
the operator must confirm that organization billing and repository Actions
settings allow the selected Codex-through-Copilot path and allow a workflow to
create a pull request. No credential or repository setting is changed by this
implementation.

If a future workflow requires a static provider key, use two organization-level
secret slots for the selected repository cohort, keep the active slot in a
non-secret selector, smoke-test the standby slot at an exact ref, switch only
after review, then revoke and remove the predecessor with human approval.
Receipts may record slot identity, provider, scope, and transition outcome, but
must exclude secret values, hashes of secret values, raw provider responses,
environment dumps, and full logs. Minting, widening, switching, revoking, and
deleting credentials remain human-gated.

## Adoption gates and unresolved migrations

Do not dispatch the pilot until the implementation pull request is reviewed and
merged and the credential and repository-setting checks above are accepted. Do
not add a schedule or pause the local Dead Surface Sweep until a manual run has
demonstrated the intended draft-PR path, a second run against unchanged state
produces no duplicate, validation failure blocks delivery, and operator noise is
acceptable.

CAK-177 remains local. Its production contract requires independent Codex and
Claude discovery at the same exact revision before synthesis, while one agentic
workflow supplies one agent job. A single Codex workflow, same-engine
sub-agents, or sequential disclosure would not meet that independence contract.
The Docs Drift, Staff Engineer, and Test Gap automations also remain local until
the first pilot establishes the hosted substrate and their repository cohorts,
budgets, and duplicate-suppression contracts are reviewed.
