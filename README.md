# ai-workflow-enforcement

Lightweight reinforcement tools for the `ctrl-alt-keith` AI workflow model.

This repository is not a workflow orchestrator. It holds small operational
checks that help humans notice when staging notes, canonical playbook guidance,
and repo-local execution guidance may be drifting out of alignment.

## Current Tool

The first experiment is a filesystem-scoped notes vs playbook drift scanner.
It compares configured staging-note roots with configured playbook roots and
reports possible overlap candidates using deterministic heuristics:

- repeated non-generic headings
- repeated normalized phrases
- configurable token similarity
- whether the note appears to reference canonical playbook guidance

The scanner reports possible drift. It does not modify files, resolve drift, or
claim that one source is authoritative for a specific local decision.

## Quick Start

Run validation:

```sh
make check
```

Run the scanner against explicit roots:

```sh
python3 -m enforcement.cli \
  --notes-root ../cross-repo-threads \
  --playbook-root ../ai-workflow-playbook/docs \
  --ignore archive/**
```

Or use a JSON config:

```sh
python3 -m enforcement.cli --config examples/drift-scan.json
```

Paths in a config file are resolved relative to that config file.

## Repository Model

- `ai-workflow-playbook` remains the canonical source of reusable workflow
  guidance.
- `cross-repo-threads` and similar notes repositories remain staging layers for
  ideas, pressure, and experiments.
- This repository provides reinforcement signals that help keep those layers
  aligned.
- Repo-local execution guidance belongs in this repository's `AGENTS.md`.

## Non-Goals

- automatic remediation
- GitHub API integration
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

See `docs/philosophy.md` and `docs/future-directions.md` for the operating
posture and deferred experiment areas.
