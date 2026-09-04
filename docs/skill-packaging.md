# Skill Packaging

Skill packaging provides an inspectable capability boundary around the
drift-review contracts. It is metadata for reviewers and local adapters, not
executable automation.

The package is `skills/drift_review/`. Its manifest points to the
existing task envelope, result attestation, examples, validators, scanner, and
review-packet renderer.

## Boundary

The skill remains non-executable to preserve the workflow posture already
established by reinforcement and contracts:

- classification remains human-reviewed
- scanner output remains advisory
- remediation remains a separate human decision
- validation remains explicit through existing validators and `make check`
- no orchestration runtime, scheduler, state machine, persistence layer, GitHub
  integration, CI integration, marketplace, discovery system, or agent
  coordination behavior is introduced
