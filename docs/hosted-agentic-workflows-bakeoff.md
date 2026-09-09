# Hosted agentic-workflow qualification

CAK-283 is qualifying which workstation-scheduled Codex automations can move to
GitHub-hosted execution without inheriting unobserved local state or expanding
write authority. The first implementation is a controlled bake-off between two
manual GitHub Agentic Workflows:

- `.github/workflows/hosted-dead-surface-copilot.md` runs Codex with
  `copilot/gpt-5.3-codex`, billed through GitHub Copilot.
- `.github/workflows/hosted-dead-surface-openai.md` runs Codex with
  `gpt-5.3-codex`, billed directly through a dedicated OpenAI automation key.

No winner has been selected. Both workflows remain manually dispatched and
stage their safe outputs, so qualification can compare proposal quality without
creating branches or pull requests. A later change from staged preview to a
real draft pull request is a separate human-reviewed decision.

The existing fixed-strategy Hosted Stewardship Engine is unchanged. It remains
the deterministic owner of its three fixed strategies.

For clone-to-preflight instructions, see
[Hosted Codex bake-off setup](hosted-agentic-workflows-setup.md).

## Active local fleet classification

This classification reflects the ten active Codex scheduler definitions
inspected for CAK-283 on 2026-09-09. Hosted-eligible means the task's core work
can be performed from GitHub and repository sources; it does not mean its local
schedule may be retired before hosted equivalence is accepted.

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

The four paused definitions are not part of the active fleet and remain
unchanged: Organization PR & Issue Scan, Workflow Drift Audit, Region Policy
Drift Review, and Knowledge Adapters Weekly Chaos + All Validation.

## Controlled comparison

The two workflows hold these properties constant:

- target repository and operator-supplied exact base SHA;
- Dead Surface Sweep prompt body and checked-in scenario evidence;
- exact Playbook source `16fc40db184882cbdfc117b0aba53b8719ddbe99`;
- manual admin-only trigger and exact `CAK-283` acknowledgement;
- 24-turn, 30-minute, and 500-AI-credit main-agent budgets;
- read-only agent GitHub permissions and the same network/tool surface;
- one draft proposal maximum, eight files, 128 KiB, protected files blocked;
- staged safe outputs, no issue fallback, no merge, and no auto-merge;
- duplicate suppression marker and changed-base rejection;
- `make check` and `git diff --check`; and
- 200-AI-credit threat-detection budget before safe-output processing.

The necessary differences are limited to inference and authentication:

| Component | Copilot variant | Direct OpenAI variant |
| --- | --- | --- |
| Main model | `copilot/gpt-5.3-codex` | `gpt-5.3-codex` |
| Main billing | GitHub Copilot organization billing | OpenAI API project billing |
| Main auth | `copilot-requests: write` on the job token | `OPENAI_API_KEY` GitHub secret |
| Threat detection | Copilot-backed `copilot/gpt-5.3-codex` | OpenAI-backed `gpt-5.3-codex` |
| Provider credential | No static inference secret | Dedicated project/service-account key required |

The selected model names align as closely as the two engine routes permit, but
requested names do not prove identical serving implementations. Record each
run's effective model when gh-aw or the provider exposes it and retain any
mismatch as an experimental limitation.

Threat detection remains enabled in both workflows. It uses the same billing
path as that workflow's main agent rather than silently spending Copilot
credits in the direct-provider variant. The generated detection job is distinct
from the main job, has its own budget, and runs before staged safe outputs.

## Manual qualification matrix

Every pair uses the same `bakeoff_pair_id`, `bakeoff_scenario`, and
`expected_base_sha`. The workflow rejects a dispatch whose checkout does not
equal the supplied SHA and rejects delivery if `origin/main` moves during the
run. Run each pair sequentially to avoid interpreting concurrent platform load
as an engine difference.

| Scenario | Evidence pack | Expected result |
| --- | --- | --- |
| `clean-no-change` | `examples/qualification/dead-surface-bakeoff/clean/` | Justified `noop`; no proposal preview. |
| `controlled-candidate` | `examples/qualification/dead-surface-bakeoff/controlled/` | One staged preview removing only the redundant wrapper. |
| `ambiguous-rejection` | `examples/qualification/dead-surface-bakeoff/ambiguous/` | Justified rejection because consumer compatibility is unresolved. |

The fixture results are qualification evidence, not production findings. Start
with one Copilot/OpenAI pair for each scenario. Repeat a scenario only when a
failure or material variance needs explanation; do not declare a winner from a
single happy-path run.

After this PR is merged and the applicable human gates are cleared, record the
current `origin/main` SHA, then dispatch both lock workflows with identical
inputs. The first live billable dispatch remains separately human-gated.

```text
gh workflow run hosted-dead-surface-copilot.lock.yml --ref main \
  -f qualification_acknowledgement=CAK-283 \
  -f bakeoff_pair_id=[pair-id] \
  -f bakeoff_scenario=[scenario] \
  -f expected_base_sha=[exact-main-sha]

gh workflow run hosted-dead-surface-openai.lock.yml --ref main \
  -f qualification_acknowledgement=CAK-283 \
  -f bakeoff_pair_id=[same-pair-id] \
  -f bakeoff_scenario=[same-scenario] \
  -f expected_base_sha=[same-exact-main-sha]
```

Do not disable staged mode merely to make the controlled candidate visible.
Staged mode preserves the complete draft-PR preview while preventing duplicate
repository mutations.

## Evidence and cost accounting

gh-aw v0.88.7 generates a compact `usage` artifact for both workflows. It
contains run/model metadata, agent and detection usage summaries, and raw
normalized token-usage records where the engine exposes them. Retrieve the two
run artifacts with
`gh aw logs hosted-dead-surface-copilot hosted-dead-surface-openai --last [count] --artifacts usage --json --output [attempt-local-directory]`
or the normal GitHub Actions artifact surface. Keep the raw artifact attached
to its run; record only the compact comparison in CAK-283.

For each component and run, record the value or `unavailable`; never replace a
missing value with zero:

| Class | Fields |
| --- | --- |
| Identity | pair ID, scenario, variant, run URL/ID, exact base SHA, requested model, effective model |
| Outcome | success/noop/failure, candidate preview, validation result, duplicate/changed-base result, accepted-useful-result disposition |
| Usage | input, cached-input, output, reasoning tokens when separate, total tokens, gh-aw AI credits, provider-reported cost |
| GitHub | agent/detection/conclusion/safe-output durations, billable Actions minutes, Copilot request or premium-request consumption |
| Operations | wall-clock duration, retry count, human interventions, diagnostic quality |
| Security | effective permissions by job, secret names exposed by job, safe-output and threat-detection result |

GitHub request counts, Copilot entitlement consumption, gh-aw AI Credits, and
dollar charges are distinct measurements. Do not translate one into another
unless the current billing statement provides the applicable rate. Actions
minutes likewise remain raw metering until the account's current included
allowance and runner rate are applied.

For direct OpenAI runs, prefer provider-reported cost. When only token counts
are available, use the official GPT-5.3-Codex prices checked on 2026-09-09:
$1.75 per million uncached input tokens, $0.175 per million cached input tokens,
and $14.00 per million output tokens. Reasoning tokens are a reported output
subclass and must not be charged a second time. Label the result estimated and
name every unavailable token class.

Use cost per accepted useful result as the primary financial synthesis:

```text
(provider/API charges + attributable GitHub/Copilot charges + Actions charges)
/ accepted useful results
```

An accepted useful result is either a review-worthy bounded candidate that
passes the quality and validation bar, or a justified no-change result where a
proposal was inappropriate. More proposals do not improve the score.

After real paired runs exist, project observed usage for one repository/run,
the four-item hosted-eligible cohort, the observed no-change mix, and a
reasonable observed higher-cost case. Show API charges, GitHub/Copilot
consumption, shared platform costs, and Actions minutes separately. Do not use
guessed token volumes once actual measurements exist.

## Credential lifecycle boundary

The Copilot path introduces no static inference secret. Its organization-billed
route requires human confirmation of Copilot entitlement, billing, and the
`copilot-requests: write` permission before the first dispatch.

The authored direct path adds only the `OPENAI_API_KEY` GitHub secret. Both the
main and threat-detection Codex environments map `CODEX_API_KEY` to that same
secret so the gh-aw runtime cannot silently select a different repository
credential. A pre-agent step also fails closed when `OPENAI_API_KEY` is absent.
The value must be a dedicated OpenAI project/service-account automation key,
not a personal or general-purpose key. This PR neither creates nor installs it.

Production readiness still requires human-assisted minting, reviewed
organization-secret exposure to the eligible repository cohort, rollback-safe
replacement, an exact-ref smoke test, verified cutover, predecessor revocation,
and sanitized evidence. No repository file, log, PR, artifact, Linear comment,
or model-visible content may contain the secret value, a derivative of it, an
environment dump, or a raw provider response. OpenAI's project API documents
listing and deleting project keys but not issuing user keys, so provider-side
minting remains a human gate.

Anthropic is not an inference path in this bake-off, but the CAK-283 dual-
provider production criterion remains open. Anthropic workspace keys are bound
to their workspace, and its Admin usage report can group by API key, workspace,
and model. That supports later attribution but does not remove the human gate
for key creation, disabling, deletion, or GitHub exposure.

## Generated security inspection

Both generated manifests were inspected after compilation with gh-aw v0.88.7.
The agent and threat-detection jobs are repository-read-only. Because staged
mode is enabled, the generated safe-output job has no repository write
permission. The generated conclusion job still has `actions: write` and
`issues: write`; the agent cannot access that token, but the broader framework
permission remains an explicit human review item.

All generated action references are immutable SHAs. Firewall, MCP gateway, and
runtime containers are digest-pinned. The OpenAI lock manifest names
`OPENAI_API_KEY`; optional framework secret names remain in the generated
manifest but are not wired into OpenAI main or detection inference. The Copilot
main and detection jobs use the GitHub token with `copilot-requests: write` and
do not reference `OPENAI_API_KEY` or `CODEX_API_KEY` secrets.

## Current official sources

The external contracts above were checked on 2026-09-09 against:

- [gh-aw Codex engine](https://github.github.com/gh-aw/engines/codex/)
- [gh-aw authentication](https://github.github.com/gh-aw/reference/auth/)
- [gh-aw billing](https://github.github.com/gh-aw/reference/billing/)
- [gh-aw cost management](https://github.github.com/gh-aw/reference/cost-management/)
- [gh-aw permissions](https://github.github.com/gh-aw/reference/permissions/)
- [gh-aw safe outputs and staged mode](https://github.github.com/gh-aw/reference/staged-mode/)
- [gh-aw threat detection](https://github.github.com/gh-aw/reference/threat-detection/)
- [gh-aw artifacts](https://github.github.com/gh-aw/reference/artifacts/)
- [GitHub Copilot billing](https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-copilot/about-billing-for-github-copilot)
- [OpenAI GPT-5.3-Codex model and pricing](https://developers.openai.com/api/docs/models/gpt-5.3-codex)
- [OpenAI API authentication](https://platform.openai.com/docs/api-reference/authentication)
- [OpenAI project API keys](https://platform.openai.com/docs/api-reference/project-api-keys/list)
- [OpenAI usage and costs](https://platform.openai.com/docs/api-reference/usage)
- [Anthropic usage reporting](https://docs.anthropic.com/en/api/admin-api/usage-cost/get-messages-usage-report)
- [Anthropic workspace key management](https://support.anthropic.com/en/articles/9796807-creating-and-managing-workspaces)

## Human gates and unresolved migrations

Do not merge or dispatch these workflows until a human has accepted the exact
PR head, generated permission posture, Copilot entitlement/billing, and direct
OpenAI secret exposure. Do not mint, install, widen, rotate, revoke, or delete a
credential as part of repository implementation. Do not disable staged mode,
add a schedule, select a winner, or pause a local job before representative
paired evidence is reviewed.

CAK-177 remains local. Its production contract requires independent Codex and
Claude discovery at the same exact revision before synthesis, while one
agentic workflow supplies one agent job. A single Codex workflow, same-engine
sub-agents, or sequential disclosure would not meet that independence
contract. The Docs Drift, Staff Engineer, and Test Gap automations also remain
local until the hosted substrate and their repository cohorts, budgets, and
duplicate-suppression contracts are accepted.
