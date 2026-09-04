# ai-workflow-enforcement

Deterministic, bounded reinforcement tools for the `ctrl-alt-keith` AI
workflow ecosystem. Reusable workflow doctrine belongs to
[`ai-workflow-playbook`](https://github.com/ctrl-alt-keith/ai-workflow-playbook);
this repository implements selected checks and produces reviewable evidence.
See [the product boundary](docs/product-boundary.md) for the repository's
scope.

## Tools

| Capability | Entry point | Documentation |
| --- | --- | --- |
| Invoke the fixed issue-owned prompt delivery path | `python3 -m enforcement.prompt_delivery_invocation --help` | [Prompt delivery DAG](docs/prompt-delivery-dag.md) |
| Inspect the lower-level prompt delivery DAG | `python3 -m enforcement.prompt_delivery_dag --help` | [Prompt delivery DAG](docs/prompt-delivery-dag.md) |
| Check one manifest-bound Dropbox artifact scope | `python3 -m enforcement.artifact_store_integrity --help` | [Artifact-store integrity](docs/artifact-store-integrity.md) |
| Inventory repository validation claims and targets | `python3 -m enforcement.validation_contract_inventory --help` | [Validation contract inventory](docs/validation-contract-inventory.md) |
| Produce a source-backed repository preflight | `python3 -m enforcement.repo_preflight --help` | [Repository preflight](docs/repo-preflight.md) |
| Scan notes and playbook roots for advisory drift | `python3 -m enforcement.cli --help` | [Drift calibration](docs/drift-review-calibration.md) |
| Render scanner JSON for review | `python3 -m enforcement.review_packet --help` | [Review packet](docs/review-packet.md) |
| Refresh resolved local repositories safely | `python3 -m enforcement.safe_refresh_repos --help` | [Safe repository refresh](docs/safe-refresh-repos.md) |
| Review or apply branch cleanup | `python3 -m enforcement.branch_cleanup --help` | [Branch cleanup](docs/branch-cleanup.md) |
| List current organization pull requests and issues | `python3 -m enforcement.org_pr_issue_scan --help` | [Organization PR and issue scan](docs/org-pr-issue-scan.md) |
| Compose hosted and local work-state evidence | `python3 -m enforcement.work_state_index --help` | [Work-state index](docs/work-state-index.md) |
| Audit hosted repository settings | `python3 -m enforcement.repo_settings_audit --help` | [Repository settings audit](docs/repo-settings-audit.md) |
| Audit workflow-drift GitHub App evidence | `python3 -m enforcement.github_app_policy_audit --help` | [GitHub App policy audit](docs/github-apps/workflow-drift/README.md) |

All advisory reports remain evidence for review. They do not create policy,
classification, cleanup authority, or merge authority.

## Drift Scanner

Run the scanner against explicit roots:

```sh
python3 -m enforcement.cli \
  --notes-root ../ai-workflow-incubator \
  --playbook-root ../ai-workflow-playbook/docs \
  --ignore 'archive/**'
```

Or use the example configuration:

```sh
python3 -m enforcement.cli --config examples/drift-scan.json
```

The scanner reports repeated headings and phrases, token similarity, canonical
reference presence, and focused workflow-policy findings. Its default exit is
advisory; `--fail-on-candidates` enables the optional nonzero exit for overlap
candidates. Config paths resolve relative to the config file. Ignore patterns
are additive to built-in safety ignores and match paths relative to each
configured root.

Cross-repository scans require an explicit workspace root and an authoritative
organization inventory, narrowed by explicit repository values or a
caller-owned manifest when needed. Local checkout layout is not an inventory
source.

## Hosted Workflows

The weekly workflow-drift audit and its manual dispatch use:

```sh
make workflow-drift-setup
make workflow-drift-audit
```

The audit hydrates its visible active organization inventory, runs the advisory
scanner and canonical validation, verifies repository cleanliness, and retains
the run evidence for 14 days. Read access uses the dedicated workflow-drift
GitHub App with only Metadata and Contents read permissions. Missing or
incomplete inventory evidence is reported as unable to verify.

The manually dispatched Hosted Stewardship Engine targets one explicitly
allowlisted repository and one of three fixed strategies:
`docs-drift`, `agents-startup-routing`, or
`worktree-ignore-baseline`. Dry-run stops with evidence; propose mode may
open one ready-for-review pull request through a separate repository-scoped
delivery App. It never merges or enables auto-merge. See
[Hosted Stewardship Engine](docs/hosted-stewardship.md).

## Workflow Contracts

The `drift_review` envelope and result attestation are descriptive,
machine-validated contracts:

```sh
python3 -m enforcement.task_envelope examples/drift-review-envelope.json
python3 -m enforcement.review_result_attestation examples/drift-review-result-attestation.json
```

See [workflow contracts](docs/workflow-contracts.md) and
[skill packaging](docs/skill-packaging.md).

## Validation

```sh
make check
```
