# Repo Settings Audit

`enforcement.repo_settings_audit` is a read-only GitHub repository settings
audit. It compares hosted settings with governance docs and config from one
explicit GitHub source-of-truth ref, defaulting to `main`.

Run an audit for one repository:

```sh
python3 -m enforcement.repo_settings_audit --repo ctrl-alt-keith/example
```

Use an explicit governance ref when `main` is not the intended source:

```sh
python3 -m enforcement.repo_settings_audit \
  --repo ctrl-alt-keith/example \
  --source-ref reviewed-governance-ref
```

Machine-readable output is available:

```sh
python3 -m enforcement.repo_settings_audit \
  --repo ctrl-alt-keith/example \
  --output-format json
```

## Source-Of-Truth Model

Hosted settings must be validated against governance docs and config from the
same validated remote ref. The audit fetches that ref through `gh api`, resolves
its commit SHA, and reads governance files from that SHA before comparing
hosted settings.

The local checkout is never used as the hosted-settings expectation source.
Instead, local state is reported separately:

- local current branch `HEAD` versus the source-of-truth SHA
- local current branch governance docs versus source-of-truth docs
- local working-tree governance docs versus source-of-truth docs

If local governance docs differ from the validated remote ref, the audit reports
drift explicitly. This is meant to catch stale-local-state failures where a
checkout is behind `origin/main` or has uncommitted governance edits.

## Audited Surfaces

The audit reports expected, actual, status, source, and suggested human
follow-up for:

- repository visibility, when documented
- default branch, when documented
- default branch protection or active branch rulesets
- required status checks, including exact documented check names when present
- required pull request settings
- branch up-to-date or strict status-check requirements, when documented
- force-push and default-branch deletion restrictions, when documented
- Actions workflow presence and hosted workflow state
- Dependabot config presence, when documented
- merge method settings, when documented
- repo-local governance docs and canonical local validation

Statuses are advisory:

- `match`: hosted or local state matches the documented expectation
- `drift`: state differs from the documented expectation
- `unknown`: no documented expectation was found or a partial hosted surface
  could not be inspected

## Safety Model

The tool uses the local `gh` CLI authentication and API runtime. It performs
read-only repository, content, branch-protection, ruleset, and Actions workflow
API calls. It does not write repository state, change branch protection, enable
rulesets, change settings, create commits, push branches, open pull requests, or
modify automation configuration.

If hosted inspection cannot run because `gh` is unauthenticated, unauthorized,
or unable to read required metadata, the command fails clearly instead of
guessing from local files.

Use `--fail-on-drift` only when a caller intentionally wants a non-zero exit for
drift findings. Unknowns remain advisory.

## Automation Notes

Suggested maintenance automation identity:

- Display name: 🧭 Repo Settings Audit
- ID: `repo-settings-audit`
- Mode: report-only advisory audit
- Target scope: selected visible repositories where hosted governance settings
  should be compared with source-of-truth governance docs

The automation prompt should name the repository set and source ref. It should
not ask the tool or agent to remediate hosted settings. Hosted changes such as
branch-protection updates, ruleset edits, visibility changes, and merge-method
changes remain human/org-admin follow-up unless a separate, explicit task
authorizes mutation.
