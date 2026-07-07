# Enforcement Philosophy

This repository reinforces a workflow model whose reusable doctrine lives in
`ai-workflow-playbook`. It operates in a layered system:

- staging material where ideas and pressure can be rough
- canonical reusable playbook guidance
- repo-local execution guidance

The reinforcement layer owns mechanical verification, advisory and validation
tooling, drift reporting, and reusable automation for selected playbook
guidance. It should help humans notice convergence problems without turning
the workflow into autonomous governance or independent workflow policy. When
reinforcement signals and playbook doctrine diverge, the playbook is
authoritative and reinforcement should be updated to match.

Not every playbook rule needs enforcement, and not every reinforcement
capability should become playbook doctrine. The first scanner therefore uses
deterministic, explainable heuristics and produces human-readable review
signals.

The intended behavior is modest:

- surface likely overlap between staging notes and playbook guidance
- point at possible canonical targets
- suggest cleanup direction
- leave judgment and remediation to maintainers
- optionally serialize the same advisory evidence as machine-readable signal
  transport

The scanner should stay scoped to configured filesystem roots. It should not
read broadly from the workspace, mutate files, call hosted services, or infer
authority beyond the configured comparison.

Machine-readable output does not change that posture. It exists so reinforcement
signals can be composed, archived with review notes, or inspected by local tools
without scraping terminal text. It should not become a governance interface,
classification store, escalation path, remediation plan, or workflow state
machine.
