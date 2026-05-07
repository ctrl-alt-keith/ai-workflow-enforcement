# Enforcement Philosophy

This repository reinforces a workflow model that already separates three
layers:

- staging material where ideas and pressure can be rough
- canonical reusable playbook guidance
- repo-local execution guidance

The reinforcement layer should help humans notice convergence problems without
turning the workflow into autonomous governance. The first scanner therefore
uses deterministic, explainable heuristics and produces human-readable review
signals.

The intended behavior is modest:

- surface likely overlap between staging notes and playbook guidance
- point at possible canonical targets
- suggest cleanup direction
- leave judgment and remediation to maintainers

The scanner should stay scoped to configured filesystem roots. It should not
read broadly from the workspace, mutate files, call hosted services, or infer
authority beyond the configured comparison.

