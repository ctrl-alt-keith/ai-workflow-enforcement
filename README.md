# ai-workflow-enforcement

Lightweight reinforcement tools for the `ctrl-alt-keith` AI workflow model.

This repository is not a workflow orchestrator. It holds small operational
checks that help humans notice when staging notes, canonical playbook guidance,
and repo-local execution guidance may be drifting out of alignment.
It is not an independent workflow-policy source: `ai-workflow-playbook` remains
the canonical source of reusable workflow doctrine, and this repository
implements selected, checkable portions of that doctrine mechanically.

## Current Tools

Compare repository-local validation claims with directly observable Makefile
targets without executing validation or mutating repositories:

```sh
python3 -m enforcement.validation_contract_inventory /path/to/repository
python3 -m enforcement.validation_contract_inventory /path/to/repository --output-format json
```

Multiple repository roots may be supplied. Markdown and JSON reports retain
claim and implementation evidence, use `Unclear` when evidence is incomplete,
and do not score or rank repositories. See
`docs/validation-contract-inventory.md` for source and classification bounds.

Generate an on-demand, source-backed preflight for one local repository:

```sh
python3 -m enforcement.repo_preflight /path/to/repository
python3 -m enforcement.repo_preflight /path/to/repository --output-format json
```

The report is advisory, stale after capture, and not a source of truth. It
does not persist repository descriptors or infer capabilities. Hosted GitHub
metadata is read only when `--include-hosted` is explicitly supplied. See
`docs/repo-preflight.md` for source boundaries and unavailable semantics.

The first experiment is a filesystem-scoped notes vs playbook drift scanner.
It compares configured staging-note roots with configured playbook roots and
reports possible overlap candidates using deterministic heuristics:

- repeated non-generic headings
- repeated normalized phrases
- configurable token similarity
- whether the note appears to reference canonical playbook guidance

The scanner also emits a small advisory workflow-policy finding stream for the
first workflow transmission checks:

- `AGENTS.md` files missing canonical playbook routing or containing large
  copied-doctrine overlap
- weak command-form wording that names only `git` and `gh`
- noncanonical runtime, generated, copied-instruction, or staging surfaces using
  authority language
- staged/runtime rules that look stronger than matching playbook guidance
- staged/runtime artifact-output rules, such as drop-in prompt, copy/paste-safe,
  or delta-only prohibitions, that lack matching playbook guidance
- ordinary repo commands shown through `zsh -lc`, `bash -lc`, or `sh -c`
- implementation guidance that presents branch-only or optional-worktree flows
  without required repo-local worktree language
- cross-repo scope gaps when authoritative organization inventory, an explicit
  repository list, or a caller-owned manifest is provided
- Codex sandbox docs/examples that imply `writable_roots` is the exhaustive
  effective writable root set

The scanner reports possible drift for human review. Its default exit behavior
is advisory and non-blocking: overlap candidates and workflow-policy findings
still exit 0. It does not modify files, resolve drift, create policy, or claim
that one source is authoritative for a specific local decision. Use
`--fail-on-candidates` only when a caller wants the optional non-zero exit for
overlap candidates.

Use `docs/drift-review-calibration.md` to interpret candidates as operational
review prompts. It describes lightweight human review categories such as
confirmed drift, acceptable duplication, intentional staging overlap,
historical residue, and false positives without changing scanner behavior.

## Quick Start

See `AGENTS.md` for the repository validation command.

Run the scanner against explicit roots:

```sh
python3 -m enforcement.cli \
  --notes-root ../ai-workflow-incubator \
  --playbook-root ../ai-workflow-playbook/docs \
  --ignore 'archive/**'
```

Or use a JSON config:

```sh
python3 -m enforcement.cli --config examples/drift-scan.json
```

The default output is human-readable text:

```sh
python3 -m enforcement.cli --config examples/drift-scan.json --output-format text
```

Machine-readable JSON output is available for callers that want to transport
the same advisory signals into another human-reviewed workflow:

```sh
python3 -m enforcement.cli --config examples/drift-scan.json --output-format json
```

The JSON report is intentionally modest and deterministic. It contains a schema
version, report type, advisory marker, scan summary, candidate count, and the
same candidate evidence shown in text output: note path, possible playbook
target, repeated headings and phrases, token similarity, canonical-reference
presence, scanner reasons, and suggested direction. It also includes advisory
workflow-policy findings with kind, path, line, snippet, reasons, and suggested
direction. It does not record workflow state, persist classifications, escalate
findings, or describe remediation steps.

For cross-repo advisory scans, provide an explicit workspace root and
authoritative inventory. The preferred mode is `organization`, which uses
GitHub CLI organization enumeration to build the visible repository inventory
before checking local checkout availability. Explicit
`organization_repositories` entries or repeated `--organization-repository`
CLI values can narrow that visible inventory for scoped scans. A caller-owned
manifest can also narrow or override scope for local workflows. The scanner
does not require or infer a playbook-owned workspace manifest, and raw local
checkout layout is never authoritative workspace scope.

## Hosted Workflow Drift Audit

The repository-owned workflow drift audit runs every Monday at `17:40 UTC`
and supports manual `workflow_dispatch` runs. The hosted job checks out the
requested enforcement commit, hydrates every visible active repository in the
`ctrl-alt-keith` organization into a clean scan workspace, runs the canonical
advisory scan, and then runs repository-native validation.

The repository contract is:

```sh
make workflow-drift-setup
make workflow-drift-audit
```

The scanner has no third-party Python package dependencies. The setup target
therefore verifies the hosted Python and GitHub CLI dependencies instead of
performing an empty package installation.

Cross-repository access uses a dedicated GitHub App installation token. The
workflow's built-in `GITHUB_TOKEN` is scoped to this repository, so it cannot
provide the complete private organization inventory. A personal access token
would bind the automation to a user and persist a longer-lived credential.
Instead, `actions/create-github-app-token@v3` creates a masked, short-lived
installation token for each run and revokes it in the action's post step. The
App and generated token are narrowed to exactly these repository permissions:

- Metadata: read
- Contents: read

No organization or account permissions are required. Install the App on the
`ctrl-alt-keith` organization with access to all repositories, including
private repositories, and configure:

- repository variable `WORKFLOW_DRIFT_APP_CLIENT_ID`: the GitHub App client ID;
- repository secret `WORKFLOW_DRIFT_APP_PRIVATE_KEY`: one complete PEM private
  key generated for the App.

The token is exposed only as step-local `GH_TOKEN` for organization inventory,
checkout hydration, and the canonical scanner. It is not placed in a global
workflow environment, written to evidence, or persisted by checkout.

Inventory completeness is measured before scanning. The workflow paginates
`GET /installation/repositories`, verifies that the number of unique entries
matches the API's `total_count`, compares that set with `gh repo list`, records
archived exclusions, verifies the required enforcement, incubator, and
playbook repositories, and hydrates every active repository in that
installation-visible set. The evidence records the expected and hydrated
counts plus every exact revision. A token can prove complete coverage of its
installation scope; it cannot independently prove that an organization owner
configured the installation for all repositories rather than a selected
subset. Selecting **All repositories** during installation is therefore part
of the operator contract. Missing credentials, App authentication failure,
inventory disagreement, incomplete pagination, or incomplete hydration is
`Unable to verify`; partial visibility is never reported as clean.

Initial setup and representative-run verification:

1. Create a dedicated GitHub App owned by the appropriate account for the
   `ctrl-alt-keith` organization.
2. Set repository permissions to only Metadata: read and Contents: read; leave
   all organization and account permissions unset.
3. Install the App on the `ctrl-alt-keith` organization.
4. Select **All repositories** so the audit covers current private repositories
   and automatically includes repositories created later.
5. Set repository variable `WORKFLOW_DRIFT_APP_CLIENT_ID` to the App's client
   ID.
6. Generate a private key and set repository secret
   `WORKFLOW_DRIFT_APP_PRIVATE_KEY` to the entire PEM, including its begin and
   end lines.
7. Dispatch `Workflow drift audit` manually after the workflow is available on
   the default branch.
8. Verify the summary reports successful token generation, matching inventory
   totals, and complete hydration; inspect the retained artifact for inventory,
   exclusions, revisions, and scanner evidence.
9. Rotate or revoke the private key using the procedure below when the key is
   replaced or no longer needed.

The previous local scheduler registration remains unchanged by this repository
work. Cut it over only after the representative hosted run is accepted.

To rotate the private key, generate a new App private key, replace
`WORKFLOW_DRIFT_APP_PRIVATE_KEY`, dispatch and verify a representative run, and
then delete the old private key from the App. Do not commit, paste into a pull
request, or retain private-key material in evidence.

The workflow uses these result semantics:

- `Clean`: the scanner, canonical validation, and repository-state check pass,
  and the scanner reports no candidates.
- `Drift detected`: the scanner reports overlap or workflow-policy candidates.
  Findings remain advisory, so this classification is visible in the summary
  and evidence without failing the job solely because findings exist.
- `Failed`: checkout, setup, scanner execution, canonical validation,
  repository-state verification, or evidence upload fails.
- `Unable to verify`: the App token cannot be generated or authenticated, the
  complete installation-visible organization inventory cannot be established,
  or one or more required clean repository inputs cannot be retrieved.

Every run writes a concise Actions job summary and retains the complete raw
evidence artifact for 14 days. The artifact includes the tested enforcement
SHA, non-secret App authentication metadata, installation and organization
inventories, completeness counts, archived exclusions, exact hydrated
revisions, raw scanner JSON and stderr, setup and validation logs,
classification, and repository-state evidence. The job has only
`contents: read` workflow permission, persists no checkout credentials,
performs no remediation, and creates no commits, pull requests, issues, or
provider changes. This repository currently has no generated workflow artifact
family; the checked-in workflow definition and its repository contract tests
are the applicable consistency surfaces.

## Hosted Stewardship Engine

The manually dispatched Hosted Stewardship Engine is a separate, bounded
proposal path. It targets one explicitly allowlisted repository, hydrates an
exact base SHA, runs the single Docs Drift strategy, executes repository-native
validation, and records a versioned receipt plus exact patch. `dry-run` stops
without remote mutation; `propose` may use a separate repository-scoped
delivery App to open one ready-for-review PR. It never merges or enables
auto-merge. See `docs/hosted-stewardship.md` for operation, permissions,
eligibility, collision handling, receipts, and deferred scope.

Render that JSON into a concise local review packet:

```sh
python3 -m enforcement.review_packet --input scan.json
```

The packet is a markdown handoff artifact. It summarizes the scan, candidate
count, candidate evidence, and suggested reviewer questions while explicitly
preserving human-reviewed classification. See `docs/review-packet.md` for the
intended local handoff workflow.

## Safe Refresh Repositories

The safe refresh helper updates configured local Git checkouts before another
tool depends on local source state. It reuses the branch-cleanup JSON
`repositories` inventory, verifies clean working trees, expected branches, and
expected upstreams, then runs `git fetch` and `git pull --ff-only`.

```sh
python3 -m enforcement.safe_refresh_repos --config examples/branch-cleanup.json
```

It reports `refreshed`, `already-current`, `skipped`, and `blocked`
repositories, exits non-zero when any selected repository is blocked, and does
not delete refs, prune remotes, write automation memory, or schedule follow-up
work. See `docs/safe-refresh-repos.md`.

## Codex Safe Recursive Removal

`codex-safe-rm` is a versioned enforcement control for deleting literal
relative directory trees beneath the invocation working directory. It gives a
Codex fixed-prefix approval rule a reviewed executable that validates every
dynamic operand, rejects `.git` and containment escapes, and requires
fd-relative, symlink-resistant removal support.

The reviewed source is installed explicitly rather than maintained under
`~/.local/bin`:

```sh
make install
make verify-install
```

Direct `rm` remains approval-gated. See `docs/codex-safe-rm.md` for the threat
model, guarantees, non-guarantees, rule fixture, update flow, and uninstall
behavior.

## Branch Cleanup Reinforcement

The branch cleanup tool is a dry-run-first operational reinforcement helper for
configured Git repositories. It reports normal Git-proven merged branch cleanup
separately from human-approved stale non-ancestor cleanup, and it only mutates
refs with explicit `--apply`.

```sh
python3 -m enforcement.branch_cleanup --config examples/branch-cleanup.json
```

Stale cleanup requires config-supplied approval and evidence, such as live
GitHub merged-PR exact-head evidence with `--audit-github-prs` or recorded
merged PR metadata with a matching branch-tip OID. The tool does not write
automation memory, schedule follow-up work, or broaden this repository into a
remediation platform. Dry-run mode does not fetch or prune; apply mode
fetches/prunes first, so action lists may differ if remote refs changed. Clean
linked worktrees for normally merged local branches are removed before branch
deletion; linked worktrees with uncommitted or untracked changes are preserved.
See `docs/branch-cleanup.md`.

## Org PR And Issue Scan

The org PR and issue scan is a report-only maintenance automation helper for
listing current open pull requests and open issues across dynamically
enumerated repositories in the `ctrl-alt-keith` GitHub organization.

```sh
python3 -m enforcement.org_pr_issue_scan
```

It uses the local `gh` CLI authentication/runtime, handles paginated repository
and work-item responses, optionally narrows the report with repeated `--repo`
values after live organization enumeration, excludes pull requests from issue
results, groups findings by repository, and reports skipped or inaccessible
repository scopes with reasons. Machine-readable JSON is available with
`--output-format json`. The default exit behavior remains advisory; use
`--fail-on-error` when an automation should exit non-zero for repository
enumeration failures or partial per-repository collection. See
`docs/org-pr-issue-scan.md`.

## Work-State Advisory Index

The work-state index composes the existing organization PR/issue scan,
branch-cleanup dry-run report, and locally available `git worktree list
--porcelain` facts into one timestamped advisory report:

```sh
python3 -m enforcement.work_state_index \
  --branch-cleanup-config examples/branch-cleanup.json
```

Markdown is the default and JSON is available with `--output-format json`.
Repeated `--repo` values provide a bounded per-repository view. Each source
retains its command, capture time, freshness, errors, status, and native
payload; unavailable local evidence is reported as unavailable rather than
empty. The index is stale after capture, is not a source of truth, and does not
authorize cleanup or mutation. See `docs/work-state-index.md`.

## Repo Settings Audit

The repo settings audit is a read-only hosted governance checker for one GitHub
repository at a time.

```sh
python3 -m enforcement.repo_settings_audit --repo ctrl-alt-keith/example
```

It fetches governance docs and config from a single GitHub source-of-truth ref,
defaulting to `main`, then compares hosted settings such as default branch,
branch protection or rulesets, required status checks, pull-request settings,
up-to-date requirements, force-push/deletion restrictions, Actions workflows,
Dependabot config presence, and hosted merge method values. Prose-only merge
method policy mentions remain advisory `unknown` until concrete expected
settings are documented and parsed. Local checkout state is reported separately
so stale working-tree or local-branch docs do not silently define hosted
expectations.

Organization-wide hosted reporting is available with `--org`. It suppresses
local-source comparison by default so stale local checkouts do not look like
hosted governance drift:

```sh
python3 -m enforcement.repo_settings_audit --org ctrl-alt-keith
```

When local checkouts are available, `--workspace-root` adds a separate
read-only local-source lane by mapping each repo to
`<workspace-root>/<repo-name>`. The audit does not switch branches or clean
worktrees. Machine-readable JSON is available with `--output-format json`.
See `docs/repo-settings-audit.md`.

## Workflow Contracts

Phase 2 introduces the first minimal structured workflow contract: a
`drift_review` task envelope. The envelope describes workflow intent, inputs,
constraints, expected outputs, and validation expectations for the existing
local drift-review loop. It is descriptive, not executable automation.

Phase 2 also includes a small `drift_review_result` attestation for completed
review outcomes. The attestation records human-reviewed classification,
cleanup-required status, reviewer type, and evidence summary as portable
operational evidence. It does not persist workflow state, trigger remediation,
or enforce classification policy.

Validate the example envelope:

```sh
python3 -m enforcement.task_envelope examples/drift-review-envelope.json
```

Validate the example review-result attestation:

```sh
python3 -m enforcement.review_result_attestation examples/drift-review-result-attestation.json
```

See `docs/workflow-contracts.md`,
`schemas/drift-review-task-envelope.schema.json`, and
`schemas/drift-review-result-attestation.schema.json`,
`examples/drift-review-envelope.json`, and
`examples/drift-review-result-attestation.json`.

## Skill Packaging

Phase 3 introduces the first minimal reusable skill package:
`skills/drift_review/`. The package is a non-executable capability description
for the existing `drift_review` workflow. Its manifest references the current
task envelope, result attestation, examples, validators, scanner, and
review-packet renderer without adding orchestration, automatic remediation,
workflow state, GitHub/CI integration, scheduling, marketplace discovery, or
agent coordination behavior.

See `docs/skill-packaging.md` and `skills/drift_review/manifest.json`.
The Phase 3 closure note is in `docs/phase-3-skill-closure.md`.

Paths in a config file are resolved relative to that config file.
Config `ignore` entries are additive: built-in safety ignores such as `.git/**`,
`.worktrees/**`, `__pycache__/**`, and `.venv/**` always remain active, and
configured ignores add to that set. CLI `--ignore` entries add to the combined
config/default set.

Ignore values are glob patterns matched against paths relative to each
configured root. Quote CLI glob values so shells such as zsh pass them to the
scanner unchanged. To ignore a directory's contents, use a glob such as
`archive/**`; a plain directory path such as `archive/` does not skip files
under that directory. The example config uses `archive/**` for this reason.

## Repository Model

- `ai-workflow-playbook` remains the canonical source of reusable workflow
  doctrine, including philosophy, operating guidance, authority boundaries, and
  human and agent operating models.
- `ai-workflow-incubator` remains the private staging/incubation repo for ideas,
  pressure, and experiments.
- This repository owns mechanical verification, advisory and validation
  tooling, drift reporting, and reusable automation that enforces or assists
  selected playbook guidance.
- When enforcement output and playbook doctrine diverge, the playbook is
  authoritative and enforcement should be updated to match.
- Not every playbook rule requires enforcement, and not every enforcement
  capability should become playbook doctrine.
- Repo-local execution guidance belongs in this repository's `AGENTS.md`.

See `docs/product-boundary.md` for the concise repository boundary guide.

## Non-Goals

- automatic remediation
- mutating GitHub API integration
- CI enforcement
- independent workflow doctrine or policy ownership
- embeddings or vector databases
- LLM-backed semantic search
- generalized orchestration frameworks
- plugin systems
- multi-agent runtime infrastructure
- package publishing or release automation

## Limitations

The scanner uses simple text heuristics. It can miss semantically similar text
that uses different wording, and it can surface benign overlap when repeated
phrasing is intentional. Treat its output as a review prompt, not a verdict.

See `docs/philosophy.md`,
`docs/phase-1-advisory-drift-reinforcement.md`,
`docs/workflow-contracts.md`, `docs/phase-2-contract-closure.md`,
`docs/skill-packaging.md`, `docs/phase-3-skill-closure.md`, and
`docs/future-directions.md` for the operating posture, Phase 1 closure note,
first workflow contract, Phase 2 contract closure note, first skill package,
Phase 3 closure note, and deferred experiment areas.

> AI-generated. Human-verified. Occasionally argued about.

## Validation

Run the repository-native validation command before delivery:

```sh
make check
```
