# ai-workflow-enforcement

Lightweight reinforcement tools for the `ctrl-alt-keith` AI workflow model.

This repository is not a workflow orchestrator. It holds small operational
checks that help humans notice when staging notes, canonical playbook guidance,
and repo-local execution guidance may be drifting out of alignment.

## Current Tools

The first experiment is a filesystem-scoped notes vs playbook drift scanner.
It compares configured staging-note roots with configured playbook roots and
reports possible overlap candidates using deterministic heuristics:

- repeated non-generic headings
- repeated normalized phrases
- configurable token similarity
- whether the note appears to reference canonical playbook guidance

The scanner also emits a small advisory workflow-policy finding stream for the
first workflow transmission checks:

- `AGENTS.md` files missing interaction-mode, command-form, or playbook pointers
- weak command-form wording that names only `git` and `gh`
- noncanonical runtime, generated, copied-instruction, or staging surfaces using
  authority language
- staged/runtime rules that look stronger than matching playbook guidance
- ordinary repo commands shown through `zsh -lc`, `bash -lc`, or `sh -c`
- cross-repo scope gaps when an explicit workspace inventory is provided

The scanner reports possible drift for human review. Its default exit behavior
is advisory and non-blocking: overlap candidates and workflow-policy findings
still exit 0. It does not modify files, resolve drift, create policy, or claim
that one source is authoritative for a specific local decision. Use
`--fail-on-candidates` only when a caller wants the optional non-zero exit for
overlap candidates.

Use `docs/drift-review-calibration.md` to interpret candidates as operational
review prompts. It describes lightweight human review categories such as
confirmed drift, acceptable duplication, intentional staging overlap,
historical residue, and false positives without changing scanner behavior.

## Quick Start

Run validation:

```sh
make check
```

Run the scanner against explicit roots:

```sh
python3 -m enforcement.cli \
  --notes-root ../ai-workflow-incubator \
  --playbook-root ../ai-workflow-playbook/docs \
  --ignore 'archive/**'
```

Or use a JSON config:

```sh
python3 -m enforcement.cli --config examples/drift-scan.json
```

The default output is human-readable text:

```sh
python3 -m enforcement.cli --config examples/drift-scan.json --output-format text
```

Machine-readable JSON output is available for callers that want to transport
the same advisory signals into another human-reviewed workflow:

```sh
python3 -m enforcement.cli --config examples/drift-scan.json --output-format json
```

The JSON report is intentionally modest and deterministic. It contains a schema
version, report type, advisory marker, scan summary, candidate count, and the
same candidate evidence shown in text output: note path, possible playbook
target, repeated headings and phrases, token similarity, canonical-reference
presence, scanner reasons, and suggested direction. It also includes advisory
workflow-policy findings with kind, path, line, snippet, reasons, and suggested
direction. It does not record workflow state, persist classifications, escalate
findings, or describe remediation steps.

For cross-repo advisory scans, provide an explicit workspace root and manifest.
When organization repository inventory is available, pass it as
`organization_repositories` in config or repeated `--organization-repository`
CLI values. The scanner reconciles those inputs and scans only the active
intersection. It does not treat raw local checkout layout as authoritative
workspace scope.

Render that JSON into a concise local review packet:

```sh
python3 -m enforcement.review_packet --input scan.json
```

The packet is a markdown handoff artifact. It summarizes the scan, candidate
count, candidate evidence, and suggested reviewer questions while explicitly
preserving human-reviewed classification. See `docs/review-packet.md` for the
intended local handoff workflow.

## Branch Cleanup Reinforcement

The branch cleanup tool is a dry-run-first operational reinforcement helper for
configured Git repositories. It reports normal Git-proven merged branch cleanup
separately from human-approved stale non-ancestor cleanup, and it only mutates
refs with explicit `--apply`.

```sh
python3 -m enforcement.branch_cleanup --config examples/branch-cleanup.json
```

Stale cleanup requires config-supplied approval and evidence, such as merged PR
metadata with a matching branch-tip OID. The tool does not call the GitHub API,
write automation memory, schedule follow-up work, or broaden this repository
into a remediation platform. See `docs/branch-cleanup.md`.

## Workflow Contracts

Phase 2 introduces the first minimal structured workflow contract: a
`drift_review` task envelope. The envelope describes workflow intent, inputs,
constraints, expected outputs, and validation expectations for the existing
local drift-review loop. It is descriptive, not executable automation.

Phase 2 also includes a small `drift_review_result` attestation for completed
review outcomes. The attestation records human-reviewed classification,
cleanup-required status, reviewer type, and evidence summary as portable
operational evidence. It does not persist workflow state, trigger remediation,
or enforce classification policy.

Validate the example envelope:

```sh
python3 -m enforcement.task_envelope examples/drift-review-envelope.json
```

Validate the example review-result attestation:

```sh
python3 -m enforcement.review_result_attestation examples/drift-review-result-attestation.json
```

See `docs/workflow-contracts.md`,
`schemas/drift-review-task-envelope.schema.json`, and
`schemas/drift-review-result-attestation.schema.json`,
`examples/drift-review-envelope.json`, and
`examples/drift-review-result-attestation.json`.

## Skill Packaging

Phase 3 introduces the first minimal reusable skill package:
`skills/drift_review/`. The package is a non-executable capability description
for the existing `drift_review` workflow. Its manifest references the current
task envelope, result attestation, examples, validators, scanner, and
review-packet renderer without adding orchestration, automatic remediation,
workflow state, GitHub/CI integration, scheduling, marketplace discovery, or
agent coordination behavior.

See `docs/skill-packaging.md` and `skills/drift_review/manifest.json`.
The Phase 3 closure note is in `docs/phase-3-skill-closure.md`.

Paths in a config file are resolved relative to that config file.
Config `ignore` entries are additive: built-in safety ignores such as `.git/**`,
`.worktrees/**`, `__pycache__/**`, and `.venv/**` always remain active, and
configured ignores add to that set. CLI `--ignore` entries add to the combined
config/default set.

Ignore values are glob patterns matched against paths relative to each
configured root. Quote CLI glob values so shells such as zsh pass them to the
scanner unchanged. To ignore a directory's contents, use a glob such as
`archive/**`; a plain directory path such as `archive/` does not skip files
under that directory. The example config uses `archive/**` for this reason.

## Repository Model

- `ai-workflow-playbook` remains the canonical source of reusable workflow
  guidance.
- `ai-workflow-incubator` remains the private staging/incubation repo for ideas,
  pressure, and experiments.
- This repository provides reinforcement signals that help keep those layers
  aligned.
- Repo-local execution guidance belongs in this repository's `AGENTS.md`.

## Non-Goals

- automatic remediation
- GitHub API integration beyond consuming explicit evidence supplied in config
- CI enforcement
- embeddings or vector databases
- LLM-backed semantic search
- generalized orchestration frameworks
- plugin systems
- multi-agent runtime infrastructure
- package publishing or release automation

## Limitations

The scanner uses simple text heuristics. It can miss semantically similar text
that uses different wording, and it can surface benign overlap when repeated
phrasing is intentional. Treat its output as a review prompt, not a verdict.

See `docs/philosophy.md`,
`docs/phase-1-advisory-drift-reinforcement.md`,
`docs/workflow-contracts.md`, `docs/phase-2-contract-closure.md`,
`docs/skill-packaging.md`, `docs/phase-3-skill-closure.md`, and
`docs/future-directions.md` for the operating posture, Phase 1 closure note,
first workflow contract, Phase 2 contract closure note, first skill package,
Phase 3 closure note, and deferred experiment areas.

> AI-generated. Human-verified. Occasionally argued about.
