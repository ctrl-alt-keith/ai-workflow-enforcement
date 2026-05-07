# Drift Review Skill Package

This directory is the first reusable skill package for the existing
`drift_review` workflow. It is a portable capability description, not an
execution mechanism.

## Contents

- `manifest.json` describes the skill name, supported task envelope,
  supported attestation, expected inputs and outputs, referenced tooling,
  operational boundaries, and validation expectations.

## Contract Spine

The package reuses existing repository contracts instead of redefining the
workflow:

- `schemas/drift-review-task-envelope.schema.json`
- `examples/drift-review-envelope.json`
- `enforcement/task_envelope.py`
- `schemas/drift-review-result-attestation.schema.json`
- `examples/drift-review-result-attestation.json`
- `enforcement/review_result_attestation.py`

The scanner and review-packet renderer remain the referenced local tools:

- `enforcement/cli.py`
- `enforcement/review_packet.py`

## Boundaries

The manifest preserves the current workflow boundaries:

- classification remains human-reviewed
- scan findings remain advisory
- no orchestration runtime is introduced
- no automatic execution is introduced
- no automatic remediation is introduced
- no workflow state machine or persistent workflow state is introduced
- no GitHub, CI, marketplace, discovery, scheduling, or agent coordination
  behavior is introduced

Validate repository readiness through the canonical entrypoint:

```sh
make check
```
