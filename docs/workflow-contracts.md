# Workflow Contracts

Workflow contracts make local workflow intent explicit without turning this
repository into a workflow engine. They describe the task boundary, expected
inputs, operational constraints, and review outputs that a human or local tool
can inspect before acting.

The first contract is intentionally small: a `drift_review` task envelope with
schema version `1`. It represents the existing local drift-review loop:

- run the advisory drift scanner
- transport scanner output as JSON
- render a markdown review packet
- leave classification, cleanup decisions, and follow-up work to human review

## Reinforcement vs Contracts

Phase 1 reinforcement produced advisory signals. The scanner and review packet
helped reviewers notice possible notes/playbook drift, but the signal itself
did not define a reusable workflow boundary.

The `drift_review` contract is the next smallest step. It declares workflow
intent and constraints in a stable envelope:

- `task_type`
- `schema_version`
- `inputs`
- `constraints`
- `expected_outputs`
- `validation_expectations`

The envelope remains descriptive. The validator checks only required top-level
fields, known task type, schema version, and object-shaped sections. It does
not run scans, render packets, remediate drift, persist decisions, call GitHub,
enforce policy, or coordinate agents.

## Review Result Attestations

An attestation complements the task envelope after the review loop is complete.
The envelope says what work is being requested and what boundaries apply. The
attestation records the reviewed outcome that a human chose to preserve as
portable operational evidence.

The first attestation is a `drift_review_result` record with schema version `1`:

- `attestation_type`
- `schema_version`
- `source_task_type`
- `classification`
- `cleanup_required`
- `reviewer_type`
- `evidence_summary`

The attestation remains descriptive. It does not become workflow state, does
not track lifecycle transitions, does not trigger cleanup, and does not encode
policy. Classification remains human-reviewed: the validator checks that the
field is present and non-empty, but it does not infer, normalize, or enforce a
classification taxonomy.

## Files

- `schemas/drift-review-task-envelope.schema.json` defines the inspectable
  envelope shape.
- `schemas/drift-review-result-attestation.schema.json` defines the completed
  review-result attestation shape.
- `examples/drift-review-envelope.json` shows the current local drift-review
  workflow as a concrete envelope.
- `examples/drift-review-result-attestation.json` records the completed Phase 1
  outcome where the remaining candidate was acceptable duplication / false
  positive and no cleanup was required.
- `enforcement/task_envelope.py` provides deterministic lightweight loading and
  validation.
- `enforcement/review_result_attestation.py` provides deterministic lightweight
  loading and validation for completed review-result attestations.

Validate the example envelope:

```sh
python3 -m enforcement.task_envelope examples/drift-review-envelope.json
```

Validate the example attestation:

```sh
python3 -m enforcement.review_result_attestation examples/drift-review-result-attestation.json
```

## Skill Packaging

The contract gives prompts, local tooling, AI adapters, or skill packages a
stable object to inspect. Phase 3 adds the first minimal package at
`skills/drift_review/`. That package references this contract spine without
turning it into executable automation.

See `docs/phase-2-contract-closure.md` for the concise Phase 2 closure note.
See `docs/skill-packaging.md` for the Phase 3 package boundary.

Deferred directions remain explicit:

- no generalized workflow engine
- no orchestration runtime or DAG execution
- no automatic remediation
- no persistent workflow state
- no policy engine
- no GitHub or CI integration
- no generalized skill or plugin architecture
