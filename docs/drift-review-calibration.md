# Drift Review Calibration

The drift scanner reports possible overlap between staging notes and canonical
playbook guidance. A candidate is a review prompt, not a finding. Use the
reported evidence to decide whether the overlap needs cleanup, is expected, or
should simply be ignored.

## Review Evidence

Start with the scanner output:

- note path and possible canonical target
- repeated headings and phrases
- token similarity
- whether the note already references canonical playbook guidance
- scanner reasons, especially repeated phrases versus heading-only matches

Then inspect the files directly. Look for whether the note is still active,
whether the repeated text is durable guidance or local evidence, and whether the
playbook already covers the reusable behavior.

## Review Categories

| Category | Typical evidence | Human review behavior |
| --- | --- | --- |
| Confirmed drift | A staging note repeats durable guidance that now belongs in the playbook, especially without a canonical reference. | Replace repeated guidance with a short playbook reference, or open a focused cleanup if the current change should stay narrower. Preserve any local evidence that is not in the playbook. |
| Acceptable duplication | The note repeats a small amount of wording to preserve context, examples, or repo-specific evidence. | Keep the duplication when it helps the reader. Prefer a nearby playbook reference when the note depends on canonical guidance. |
| Intentional staging overlap | The note is actively developing wording, pressure, or examples before possible promotion. | Leave the staging material in place. Record why the overlap is intentional when that context would help a future reviewer. |
| Historical residue | The note appears old, already promoted, or no longer useful for active work. | Treat it as cleanup backlog. Archive, trim, or ignore it in a separate scoped change rather than mixing broad note cleanup into unrelated work. |
| False positive | The match is driven by generic structure, common operational vocabulary, or incidental token overlap. | Take no content action. If repeated noise becomes distracting, tune ignores or thresholds narrowly and with examples. |

## Calibration Notes

Early real scans surfaced a small number of candidates, which is the useful
operating range for this tool. The most actionable candidates tend to combine
repeated phrases with a missing canonical reference. Heading-only matches are
lower confidence and usually need more file-level context.

Canonical-reference presence changes the question. A note that already points
to the playbook may still contain stale duplicate wording, but the review should
focus on whether the duplicate text is useful context rather than treating the
candidate as missing attribution.

Token similarity is a closeness signal, not a severity score. High similarity
can indicate copied guidance, but it can also reflect intentional staging work
or examples that need to remain readable near the evidence.

## First Real Review Loop

The first real drift-review loop started after an archive-ignore correction and
reported 2 candidates. Human review classified `context-refresh.txt` as
historical residue with confirmed drift cleanup needed. That cleanup was
completed in `cross-repo-threads`, preserving the scanner's role as an advisory
signal rather than an authority.

A rerun reported 1 remaining candidate. The candidate was heading-only, had
token similarity of 0.20, and already included a canonical playbook reference.
It was accepted as acceptable duplication / false positive noise rather than a
cleanup target.

This loop is useful calibration evidence: the scanner surfaced a small,
reviewable set; one candidate led to scoped cleanup; and the remaining noise was
explainable from the reported evidence. The observed signal quality supports
continued advisory use with human classification, not threshold changes,
automated enforcement, or new process requirements.

## Deferred Areas

This calibration model remains operational and human-reviewed. It does not add
CI integration, GitHub integration, automatic remediation, machine-readable
review states, semantic matching, workflow orchestration, or escalation rules.
The scanner should continue to report explainable evidence and leave category
assignment to maintainers.
