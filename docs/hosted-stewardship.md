# Hosted Stewardship Engine

The Hosted Stewardship Engine is a manually dispatched, single-repository MVP
that constructs a validated local repository proposal and either stops with
durable evidence or creates one review-ready pull request. It is not the former
`docs-drift-sweep` automation and does not implement discovery, scheduling, or
dynamic strategy loading. The engine supports exactly three fixed strategies:
`docs-drift` revision `1`, `agents-startup-routing` revision `1`, and
`worktree-ignore-baseline` revision `1`. It has no plugin framework, runtime
registry, entry points, hooks, generalized strategy abstraction, shared append
framework, or dynamic discovery.

The workflow is `.github/workflows/hosted-stewardship.yml`. Its only eligible
MVP target is `ctrl-alt-keith/ai-workflow-enforcement`, declared in
`config/hosted-stewardship.json`.

## Modes And Shared Pipeline

Manual dispatch requires `repository`, `mode`, and `strategy` and accepts an
optional `target_ref`. `strategy` is an explicit choice among `docs-drift`,
`agents-startup-routing`, and `worktree-ignore-baseline`; `docs-drift` remains
the default. The GitHub
**Run workflow from** selector chooses the stewardship engine revision. It does
not choose the target repository state. `target_ref` chooses the branch, tag,
or commit inspected in a dry-run; blank means the repository's current default
branch.

The modes are exactly:

- `dry-run`: constructs and validates the real local change and performs no
  remote mutation. Blank-target runs also check current remote collision state
  and record whether delivery would be attempted; targeted runs remain
  inspection-only.
- `propose`: traverses the same pipeline, then may create one branch, commit,
  push, and ready-for-review pull request. A nonblank `target_ref` is rejected
  before target hydration or delivery, so propose mode remains bound to the
  current default branch.

Both modes use the same Python entrypoint. Blank-target runs continue to differ
only after proposal construction, repository-native validation, collision
detection, and the base-SHA recheck. A targeted dry-run shares the pipeline
through repository-native validation, then stops without constructing a
delivery proposal. Neither strategy receives the execution mode or performs
GitHub operations.

The shared pipeline is:

1. require an explicitly selected allowlisted repository;
2. verify the read-only GitHub App token and current repository metadata;
3. select the default branch for blank `target_ref`, or the supplied dry-run
   ref, and resolve it to an exact commit SHA;
4. hydrate a new clean checkout at that SHA;
5. retrieve `AGENTS.md` and the configured repo-local policy source;
6. resolve repository-native validation from current `AGENTS.md` guidance;
7. construct the selected strategy's narrow context and execute it against the
   clean checkout;
8. capture the exact binary/full-index patch and SHA-256 digest;
9. run repository-native validation with credential variables removed;
10. for blank-target runs, check for an open strategy-marked PR, an existing
    proposed branch, and a changed base SHA;
11. for blank-target runs, construct an explicit delivery proposal;
12. stop with evidence or invoke the isolated delivery method.

There is no automatic rebase or regeneration. A changed base SHA invalidates
the proposal.

GitHub's
[`Get a commit`](https://docs.github.com/en/rest/commits/commits#get-a-commit)
endpoint resolves commit SHAs, branch refs, and tag refs with `Contents: read`.
The engine uses that read-only endpoint for nonblank dry-run refs, fails closed
when the ref cannot be resolved, and hydrates only the returned exact SHA.

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

## AGENTS Startup Routing Strategy

`agents-startup-routing` revision `1` restores one missing active route from
root `AGENTS.md` to `ai-workflow-playbook/docs/start-here.md`. The Playbook path
is implementation traceability for the reviewed rule; strategy execution does
not hydrate or claim to verify live Playbook content.

The strategy ignores fenced code and recognizes positive active prose using
`start with`, `start from`, or `read`, including ordinary Markdown line
wrapping. An active route returns `no_change`. If the exact path and the
reserved `## Shared Workflow Entry Point` heading are both absent from active
or ambiguous prose, the strategy appends exactly this fixed block:

```markdown
## Shared Workflow Entry Point

Start with `ai-workflow-playbook/docs/start-here.md` before repository or software work. Use this `AGENTS.md` only for repository-specific execution guidance.
```

The strategy blocks rather than guessing when the path appears only in
negative, historical, example-only, or otherwise ambiguous prose; when the
reserved heading exists without a valid active route; or when root `AGENTS.md`
is missing, unreadable, non-UTF-8, or a symlink. It never creates a missing
file, repairs another AGENTS finding, or rewrites existing bytes. A successful
mutation verifies that the original bytes are the exact prefix of the result
and reports `AGENTS.md` as the only allowed changed path. The shared engine then
independently verifies reported paths against the working tree and runs the
repository-native validation command.

## Worktree Ignore Baseline Strategy

`worktree-ignore-baseline` revision `1` maintains exactly one repository
invariant in root `.gitignore`: the exact active logical line `.worktrees/`.
The governing
`ctrl-alt-keith/ai-workflow-playbook/docs/new-repo-bootstrap.md` path is
implementation traceability for the reviewed rule only; runtime execution does
not hydrate or verify live Playbook content.

When the `.worktrees` token is wholly absent, the strategy appends only
`.worktrees/` and a final newline. It preserves the original bytes as an exact
prefix, defaults to LF when no newline bytes exist, otherwise preserves an
unambiguous existing LF or CRLF convention, and reports `.gitignore` as its
only changed path. It does not create a missing `.gitignore`, rewrite or reorder
rules, normalize unrelated whitespace, infer equivalence, or repair another
ignore rule.

The exact active rule returns `no_change`. Any other `.worktrees` occurrence
blocks the strategy, including commented, negated, rooted, recursive, globbed,
escaped, whitespace-altered, or mixed exact-and-alternate forms. Missing,
unreadable, non-UTF-8, or symlinked `.gitignore` files block, as do ambiguous
newline conventions and append or verification failures. This is a fixed
strategy, not a generalized append strategy or reusable append subsystem. The
shared engine independently verifies the one-file scope and runs
repository-native validation. Human merge remains the acceptance boundary.

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
Dry-run does not generate the delivery token. A propose dispatch with a
nonblank `target_ref` also does not generate one and is rejected by the engine
before target hydration. Tokens are exposed only to the engine process, removed
from the environment used for target validation, and redacted from bounded
errors and logs. Git credentials are supplied through a short-lived askpass
process file without writing the token to disk or argv.

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

Every generated PR body contains the selected strategy's fixed marker. The three
markers are:

```text
<!-- hosted-stewardship:docs-drift -->
<!-- hosted-stewardship:agents-startup-routing -->
<!-- hosted-stewardship:worktree-ignore-baseline -->
```

An open PR blocks only another proposal with the same strategy marker; one
strategy's PR does not collide with the other. Branch names are deterministic
and include the selected strategy identifier:

```text
stewardship/<strategy-identifier>/<base-sha-prefix>-<diff-digest-prefix>
```

Any existing branch at that name blocks delivery. The engine never updates a
ref, force-pushes, or decides that an unmarked branch is safe to replace. It
also rereads the base branch immediately before the delivery boundary and
blocks if the SHA differs from the hydrated base.

Nonblank target refs never reach this boundary. After a successful targeted
dry-run inspection and validation, the receipt records `would_create_pr=false`
because arbitrary-ref inspection is evidence-only and cannot become a delivery
proposal.

The delivery input includes repository, base branch and SHA, branch, commit
message, PR title and body, changed paths, exact patch and digest, validation,
and collision evidence. Delivery verifies that the working-tree and staged
patch still match that input before pushing. A partial failure is recorded; the
MVP does not retry, update, or clean up a remote branch.

## Receipts And Evidence

Every engine terminal state writes `receipt.json` using
`schemas/hosted-stewardship-receipt.schema.json` schema version `1`. The receipt
contains run identity and timestamps, mode, repository and exact proposal
identity, requested and effective target refs, resolved base SHA, engine and
strategy revisions, eligibility, strategy result, changed paths, diff digest,
validation, proposed delivery metadata, collision result, `would_create_pr`,
remote mutations, final terminal state, failure stage, and a bounded redacted
error. Schema version `1` accepts exactly the `docs-drift`/`1`,
`agents-startup-routing`/`1`, and `worktree-ignore-baseline`/`1` identity pairs,
so historical receipts remain valid. The selected fixed metadata also supplies
the commit message, PR title, collision marker, and deterministic branch
namespace.

For a blank input, `requested_target_ref` is `null`, `effective_target_ref` is
the current default branch, and the existing `base_branch` and `base_sha`
meanings are unchanged. The two target-ref fields are additive optional schema
properties so receipts produced before this change remain valid schema-version
`1` documents; every new receipt emits both fields.

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
concurrency control beyond one target, budgets, any strategy beyond the three
fixed choices, plugins, dynamic discovery or loading, provider abstraction,
retries, rebasing, PR updates, force-pushes, branch cleanup, generalized policy
and validation layers, dashboards, notifications, comments, labels,
auto-merge, merge, settings changes, and marketplace packaging.
