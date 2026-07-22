# Product Boundary

`ai-workflow-enforcement` provides bounded mechanical reinforcement for the
`ctrl-alt-keith` workflow ecosystem. It turns established guidance and current
repository state into deterministic, reviewable evidence that helps humans make
workflow decisions.

It does not own the primary workflow decision. It does not create workflow
doctrine, decide final meaning, or remediate repositories on its own.

## Product Role

This repository owns selected reinforcement mechanisms when they can stay
small, deterministic, and review-oriented:

- deterministic verification
- bounded advisory tooling
- evidence-backed workflow signals
- review-oriented reports
- reusable reinforcement mechanisms

The Hosted Stewardship Engine is one narrow exception to the report-only tool
shape: it may construct a validated local documentation proposal and create one
review-ready pull request for an explicitly eligible repository through one of
two fixed strategies. It does not provide a plugin framework or dynamic
strategy discovery, classify final meaning, update existing proposals, merge,
enable auto-merge, or otherwise accept its own change. Hosted Stewardship
Engine proposals are review-ready evidence; human merge remains the remediation
boundary.

<!-- hosted-stewardship-policy:review-ready-human-merge -->

The tools in this repository should make existing workflow boundaries easier
to inspect. They should not replace the human decision that follows inspection.

## Core Invariant

Every enforcement output must remain evidence-backed, bounded, and
review-oriented; it must not become the authority that creates policy,
classifies final meaning, remediates state, or orchestrates generalized or
fleet-wide workflow. The Hosted Stewardship Engine may coordinate its fixed
single-repository proposal pipeline, but it cannot accept the proposal or
expand into strategy discovery, scheduling, fan-out, or merge authority.

Evidence-backed means an output points to inspectable inputs, observed state,
or deterministic checks. Bounded means the tool has a narrow declared scope and
does not silently expand into adjacent workflow ownership. Review-oriented
means the output is shaped for human or AI-assisted review, not for automatic
governance, cleanup, or classification.

When a reinforcement signal and reusable workflow doctrine diverge,
`ai-workflow-playbook` is authoritative and this repository should be updated
to match.

## Product Object

This repository does not need a single business object. Its durable outputs are
evidence artifacts that support human workflow decisions, including:

- advisory reports
- workflow contracts
- review packets
- attestations
- verification results

These artifacts may be human-readable or machine-readable. Either way, they
remain evidence transport. They do not become workflow state, final
classification, remediation approval, policy source, or orchestration runtime.

## Repository Boundaries

The surrounding repositories own different layers:

- `ai-workflow-playbook` owns reusable workflow doctrine: shared rules,
  operating guidance, authority boundaries, and human and agent operating
  models.
- Repository-local `AGENTS.md` files own repository-specific execution
  guidance: local validation, command form, file placement, branch and PR
  expectations, and local constraints.
- `ai-workflow-incubator` owns experimentation and promotion candidates:
  rough notes, observed pressure, candidate patterns, and material that is not
  yet durable workflow doctrine.
- `ai-workflow-enforcement` owns bounded mechanical reinforcement of
  established guidance: deterministic checks, advisory reports, contract
  validation, review packets, attestations, and other evidence-backed signals.

This boundary is intentionally not the same as the knowledge-ingestion
repositories. Enforcement is not a source-ingestion pipeline, content
destination, or primary knowledge object store. Its identity is the production
of trustworthy, bounded signals for review.

## Product Decision Filter

Use these questions when deciding whether new work belongs here:

- Does this produce better evidence for human workflow decisions?
- Does this strengthen deterministic verification?
- Does this improve advisory reporting?
- Does this make an existing workflow contract or attestation easier to inspect
  or validate?
- Does this remain bounded to established guidance and explicit inputs?
- Does this begin writing policy?
- Does this begin making human classifications?
- Does this begin remediating repositories?
- Does this begin generalized or fleet-wide workflow orchestration?
- Does this become an autonomous governance system?

Prefer changes that improve evidence, verification, or review handoff. Stop or
move the work elsewhere when it starts creating doctrine, final classification,
remediation, generalized orchestration, or governance authority.

## Non-Goals

This repository does not own:

- workflow doctrine
- policy authorship
- final human classification
- remediation
- generalized or fleet-wide orchestration
- governance administration
- autonomous workflow decisions
- replacing human review
- becoming the authority instead of supporting it

Future tooling can still grow when repeated use shows a concrete need, but
growth should preserve the invariant: evidence-backed, bounded, and
review-oriented.
