# Skill Packaging

Skill packaging is the next small layer above the existing drift-review
contracts. A skill package is an inspectable repository directory that describes
a reusable capability boundary around a proven workflow. It is metadata for
reviewers and local adapters to inspect; it is not executable automation.

The first package is `skills/drift_review/`. Its manifest points to the
existing task envelope, result attestation, examples, validators, scanner, and
review-packet renderer.

## Relationship to Earlier Phases

Phase 1 reinforcement produced advisory signals: the drift scanner, JSON signal
transport, calibration guidance, and markdown review packets. Those artifacts
help reviewers notice possible notes/playbook drift, but they do not classify
or clean up drift.

Phase 2 contracts made the workflow boundary explicit. The `drift_review`
task envelope describes intent, inputs, constraints, expected outputs, and
validation expectations. The `drift_review_result` attestation records a
completed human-reviewed outcome.

Phase 3 skill packaging ties those existing pieces together as a portable
capability description. The package describes what the workflow supports and
what remains outside its boundary. It does not replace the contracts and does
not duplicate their schema rules.

## Why Non-Executable

The skill remains non-executable to preserve the workflow posture already
established by reinforcement and contracts:

- classification remains human-reviewed
- scanner output remains advisory
- remediation remains a separate human decision
- validation remains explicit through existing validators and `make check`
- no orchestration runtime, scheduler, state machine, persistence layer, GitHub
  integration, CI integration, marketplace, discovery system, or agent
  coordination behavior is introduced

This keeps the package small enough to inspect and specific enough to reuse
without turning the repository into a workflow engine.
