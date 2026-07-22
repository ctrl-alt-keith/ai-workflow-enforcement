# Hosted Stewardship Engine

The Hosted Stewardship Engine is a manually dispatched, single-repository MVP
that constructs a validated local documentation proposal and either stops with
durable evidence or creates one review-ready pull request. It is not the former
`docs-drift-sweep` automation and does not implement discovery, scheduling, or
multiple strategies.

The workflow is `.github/workflows/hosted-stewardship.yml`. Its only eligible
MVP target is `ctrl-alt-keith/ai-workflow-enforcement`, declared in
`config/hosted-stewardship.json`.

## Modes And Shared Pipeline

Manual dispatch requires `repository` and `mode`. The modes are exactly:

- `dry-run`: constructs and validates the real local proposal, checks current
  remote collision state, records whether delivery would be attempted, and
  performs no remote mutation.
- `propose`: traverses the same pipeline, then may create one branch, commit,
  push, and ready-for-review pull request.

Both modes use the same Python entrypoint and differ only after proposal
construction, repository-native validation, collision detection, and the
base-SHA recheck. The Docs Drift strategy has no mode input.

The shared pipeline is:

1. require an explicitly selected allowlisted repository;
2. verify the read-only GitHub App token and current repository metadata;
3. resolve the default branch and exact base SHA;
4. hydrate a new clean checkout at that SHA;
5. retrieve `AGENTS.md` and the configured repo-local policy source;
6. resolve repository-native validation from current `AGENTS.md` guidance;
7. execute Docs Drift against the clean checkout;
8. capture the exact binary/full-index patch and SHA-256 digest;
9. run repository-native validation with credential variables removed;
10. check for an open strategy-marked PR, an existing proposed branch, and a
    changed base SHA;
11. construct an explicit delivery proposal;
12. stop with evidence or invoke the isolated delivery method.

There is no automatic rebase or regeneration. A changed base SHA invalidates
the proposal.

## Eligibility

Eligibility is fail-closed and records a decision, reason, and controlling
source. A repository is eligible only when:

- it was explicitly selected by dispatch;
- it is present in the reviewed repository-owned allowlist;
- the read-only App can retrieve current metadata and the repository is not
  archived;
- a clean exact-SHA checkout contains current `AGENTS.md`;
- the configured repo-local policy file contains its reviewed permission
  marker; and
- `AGENTS.md` names a direct repository-native canonical validation command.

An unallowlisted, archived, or non-permitting repository is `ineligible`.
Unavailable access, instructions, policy, or validation is `blocked`. Strategy
execution starts only after an `eligible` decision.

This mechanism deliberately avoids organization discovery, opt-in inference,
a policy service, and a policy DSL. Adding another target requires a reviewed
configuration and target-policy change.

## Docs Drift Strategy

The strategy has one current rule: the configured primary documentation file
must name the canonical validation command resolved from `AGENTS.md`. If the
command is absent, the strategy appends a small `Validation` section. If the
command is already present, it returns `no_change`. A missing configured
documentation file is `blocked`.

The strategy reports outcome, rationale, changed paths, evidence, and
validation requirements. It does not clone, authenticate, select branches,
commit, push, create PRs, inspect mode, handle collisions, or store evidence.

## Authentication And Authority

Read and delivery identities are distinct:

- Read and hydration reuse the existing dedicated read-only workflow-drift App
  with `Metadata: read` and `Contents: read`.
- Propose delivery uses a separate stewardship App. For this MVP, install it
  only on `ctrl-alt-keith/ai-workflow-enforcement` and grant exactly
  `Metadata: read`, `Contents: write`, and `Pull requests: write`.

Configure these existing read-App values:

- repository variable `WORKFLOW_DRIFT_APP_CLIENT_ID`;
- repository secret `WORKFLOW_DRIFT_APP_PRIVATE_KEY`.

Configure these separate delivery-App values:

- repository variable `STEWARDSHIP_WRITE_APP_CLIENT_ID`;
- repository secret `STEWARDSHIP_WRITE_APP_PRIVATE_KEY`.

The workflow requests a token scoped to the explicitly selected repository.
Dry-run does not generate the delivery token. Tokens are exposed only to the
engine process, removed from the environment used for target validation, and
redacted from bounded errors and logs. Git credentials are supplied through a
short-lived askpass process file without writing the token to disk or argv.

GitHub documents that creating Git refs requires
[`Contents: write`](https://docs.github.com/en/rest/git/refs#create-a-reference)
and creating a pull request requires
[`Pull requests: write`](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request).
The token action supports repository narrowing and explicit permission inputs;
see the
[`actions/create-github-app-token`](https://github.com/actions/create-github-app-token)
contract.

GitHub does not expose a finer App permission that allows branch creation while
cryptographically denying every possible merge operation. Therefore the App
must not be granted administration, settings, organization, workflow, issue,
or ruleset-bypass authority, and the default branch must retain its normal PR
protection with the App absent from bypass lists. The engine has no merge or
auto-merge code path and tests prohibit those workflow surfaces. This provider
permission granularity remains a residual control dependency; the MVP must not
be described as having a provider-enforced branch-write-without-merge scope.

## Collision And Delivery Boundary

Every generated PR body contains:

```text
<!-- hosted-stewardship:docs-drift -->
```

An open PR with that marker blocks another Docs Drift proposal. Branch names
are deterministic:

```text
stewardship/docs-drift/<base-sha-prefix>-<diff-digest-prefix>
```

Any existing branch at that name blocks delivery. The engine never updates a
ref, force-pushes, or decides that an unmarked branch is safe to replace. It
also rereads the base branch immediately before the delivery boundary and
blocks if the SHA differs from the hydrated base.

The delivery input includes repository, base branch and SHA, branch, commit
message, PR title and body, changed paths, exact patch and digest, validation,
and collision evidence. Delivery verifies that the working-tree and staged
patch still match that input before pushing. A partial failure is recorded; the
MVP does not retry, update, or clean up a remote branch.

## Receipts And Evidence

Every engine terminal state writes `receipt.json` using
`schemas/hosted-stewardship-receipt.schema.json` schema version `1`. The receipt
contains run identity and timestamps, mode, repository and exact proposal
identity, engine and strategy revisions, eligibility, strategy result, changed
paths, diff digest, validation, proposed delivery metadata, collision result,
`would_create_pr`, remote mutations, final terminal state, failure stage, and a
bounded redacted error.

Large data remains separate:

- `proposal.patch` is the exact validated patch;
- `validation.log` contains the selected command, exit status, stdout, and
  stderr;
- `receipt.json` references those files by evidence-relative path.

Actions retains the complete evidence directory for 14 days. A dry-run receipt
says that delivery would be attempted based on observed state; it does not
promise that a later propose run will still succeed.

Terminal states distinguish no change, dry-run completion, validation failure,
eligibility and strategy blocks, strategy failure, existing PR, branch
collision, changed base, delivery failure, and delivery success.

## Deferred Scope

The MVP intentionally defers fleet discovery and scheduling, fan-out, queues,
concurrency control beyond one target, budgets, additional strategies, plugin
loading, provider abstraction, retries, rebasing, PR updates, force-pushes,
branch cleanup, generalized policy and validation layers, dashboards,
notifications, comments, labels, auto-merge, merge, settings changes, and
marketplace packaging.
