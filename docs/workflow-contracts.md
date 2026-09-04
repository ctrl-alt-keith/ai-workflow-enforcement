# Workflow Contracts

Workflow contracts make local workflow intent explicit without turning this
repository into a workflow engine. They describe the task boundary, expected
inputs, operational constraints, and review outputs that a human or local tool
can inspect before acting.

The `drift_review` task envelope uses schema version `1` and represents the
local drift-review loop:

- run the advisory drift scanner
- transport scanner output as JSON
- render a markdown review packet
- leave classification, cleanup decisions, and follow-up work to human review

## Task Envelope

The `drift_review` contract declares workflow intent and constraints in a
stable envelope:

- `task_type`
- `schema_version`
- `inputs`
- `constraints`
- `expected_outputs`
- `validation_expectations`

The envelope remains descriptive. The validator checks the required and
allowed top-level fields, known task type, schema version, and object-shaped
sections. Unknown top-level fields are rejected so the validator stays aligned
with the strict schema contract. It does not run scans, render packets,
remediate drift, persist decisions, call GitHub, enforce policy, or coordinate
agents.

## Review Result Attestations

An attestation complements the task envelope after the review loop is complete.
The envelope says what work is being requested and what boundaries apply. The
attestation records the reviewed outcome that a human chose to preserve as
portable operational evidence.

The `drift_review_result` attestation uses schema version `1`:

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
- `examples/drift-review-result-attestation.json` provides a valid completed
  review example.
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

The contract gives prompts, local tooling, adapters, and skill packages a
stable object to inspect. The package at `skills/drift_review/` references
this contract without turning it into executable automation. See
`docs/skill-packaging.md`.
