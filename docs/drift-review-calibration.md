# Drift Review Calibration

The drift scanner reports candidates for human review. Inspect the reported
note and possible playbook target before deciding whether any cleanup is
warranted.

## Evidence To Review

- repeated headings and phrases
- token similarity
- presence of a canonical playbook reference
- scanner reasons and suggested direction
- whether the note is active guidance, staging material, or historical evidence

## Review Categories

| Category | Disposition |
| --- | --- |
| Confirmed drift | Replace duplicated doctrine with a link to its canonical playbook owner while preserving repo-specific evidence. |
| Acceptable duplication | Keep local context that improves use or review. |
| Intentional staging overlap | Keep active exploratory material until its owning workflow reaches a disposition. |
| Historical residue | Remove obsolete material in a scoped cleanup. |
| False positive | Take no content action; tune the scanner only when repeated noise justifies it. |

## Interpretation Notes

Heading-only matches are weaker than repeated phrases. Token similarity is a
closeness signal, not a severity score. A canonical reference does not prove
duplicated text is still useful.

Frozen proposals and review records can remain overlap candidates because a
historical label does not establish that duplication is intentional. Policy
findings, however, distinguish executable guidance from command histories,
negative examples, analysis, and stopped attempts.

Ignored-path counts represent unique excluded roots or files. Traversal stops
at ignored roots such as `.git/`, `.worktrees/`, `.venv/`,
`__pycache__/`, and configured archives.
