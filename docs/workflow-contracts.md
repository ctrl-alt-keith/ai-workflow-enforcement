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

## Files

- `schemas/drift-review-task-envelope.schema.json` defines the inspectable
  envelope shape.
- `examples/drift-review-envelope.json` shows the current local drift-review
  workflow as a concrete envelope.
- `enforcement/task_envelope.py` provides deterministic lightweight loading and
  validation.

Validate the example envelope:

```sh
python3 -m enforcement.task_envelope examples/drift-review-envelope.json
```

## Future Skill Packaging

The contract gives future prompts, local tooling, AI adapters, or skill
packaging a stable object to inspect. That future packaging is deliberately not
implemented here. A later phase can decide whether the envelope should become
part of a reusable skill after repeated human-reviewed drift-review use proves
the need.

Deferred directions remain explicit:

- no generalized workflow engine
- no orchestration runtime or DAG execution
- no automatic remediation
- no persistent workflow state
- no policy engine
- no GitHub or CI integration
- no skill packaging in this phase
