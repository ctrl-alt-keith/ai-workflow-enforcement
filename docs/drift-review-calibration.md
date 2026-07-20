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

## Context-Sensitive Policy Signals

The scanner favors grammatical and artifact context over broad keyword
suppression:

- Worktree findings require imperative setup wording, a `git worktree add`
  command outside a command-history record, or an explicit worktree-per-lane
  rule. Historical narrative, observed phase signals, analytical discussion,
  negative guidance, and attempts that stopped before worktree creation are
  evidence about prior execution rather than setup instructions.
- Authority findings target direct claims that the current noncanonical note,
  prompt, file, artifact, or surface governs workflow or is a canonical source.
  Questions, negative statements, analytical discussion, check-family names,
  and explicitly frozen historical evidence artifacts do not compete with
  Playbook ownership. Past tense and owner names such as GitHub are not blanket
  exemptions: a direct claim remains a finding in an active note, while the
  same preserved wording can be suppressed when the document-level context
  identifies a frozen historical evidence record. Question-typed ownership is
  represented by question semantics, not by exempting every claim about a
  particular owner. Direct local claims and explicit attempts to replace or
  supersede the Playbook remain findings.
- Frozen proposal and review records remain overlap candidates when their text
  duplicates current Playbook guidance. The scanner adds frozen-historical
  context and recommends verifying the owner reference before preserving the
  recorded application. It does not suppress the candidate, because a frozen
  label alone cannot prove that duplication is intentional.

These distinctions came from the completed July 20 Incubator triage. The
authority regression corpus is separated into confirmed false positives,
confirmed genuine drift in active notes, and frozen historical evidence. Each
suppression class is paired with positive fixtures for imperative worktree
setup and competing canonical-source language.

## Ignored-Path Accounting

Ignored-path reporting counts unique excluded roots or files, not every file
contained inside an excluded directory. Directory traversal stops at ignored
roots such as `.git/`, `.worktrees/`, `.venv/`, `__pycache__/`, and configured
archives. The same ignored path is counted once when an explicit notes root is
also present in workspace inventory.

This makes the metric describe scan scope instead of generated-file volume.
In the July 20 audit comparison, nine worktrees created between runs accounted
for 13,183 of the 13,726 additional ignored-file entries; `.venv` and
`__pycache__` growth explained most of the remainder. Counting excluded roots
keeps those intentionally ignored trees visible without making checkout size
look like a scope change.

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
