# Repo Settings Audit

`enforcement.repo_settings_audit` is a read-only GitHub repository settings
audit. It compares hosted settings with the central repo-family policy in
`config/repo-settings-policy.json`, repo-specific central overrides, and any
explicit repo-local governance declarations read from one GitHub
source-of-truth ref, defaulting to `main`.

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

Hosted settings are validated against an effective policy:

1. central baseline defaults from `config/repo-settings-policy.json`
2. repo-specific central overrides from the same policy file
3. optional explicit repo-local governance declarations from the target repo's
   validated source-of-truth ref

Repo-local declarations override central defaults only when they use supported
explicit declaration formats. Prose-only governance does not silently change
the effective policy.

The audit fetches the target repo's source-of-truth ref through `gh api`,
resolves its commit SHA, and reads governance files from that SHA before
applying repo-local declarations.

The local checkout is never used as the hosted-settings expectation source.
Instead, local state is reported separately:

- local current branch `HEAD` versus the source-of-truth SHA
- local current branch governance docs versus source-of-truth docs
- local working-tree governance docs versus source-of-truth docs

If local governance docs differ from the validated remote ref, the audit reports
drift explicitly. This is meant to catch stale-local-state failures where a
checkout is behind `origin/main` or has uncommitted governance edits.

## Central Baseline Policy

The central baseline currently means:

- repository visibility: `public`
- default branch: `main`
- pull requests required before merge
- status checks required before merge
- required status checks are strict/up-to-date
- force pushes on `main` disabled
- branch deletions on `main` disabled
- merge policy: squash-only
- required approving reviews: `0`
- administrator/owner self-merge allowed

Repository visibility defaults to `public` intentionally. Repositories must opt
into `private` in the central policy file so private repositories remain
intentional and reviewable.

Required check names remain repo-specific. The central baseline requires hosted
status checks, but exact check-name comparison happens only when
`required_checks` is set in central repo overrides or exact checks are declared
in repo-local governance docs.

## Audited Surfaces

The audit reports expected, actual, status, source, and suggested human
follow-up for:

- repository visibility
- default branch
- default branch protection or active branch rulesets
- required status checks, including exact documented check names when clearly
  declared in central overrides or explicit required-status-check governance
  sections
- required pull request settings
- branch up-to-date or strict status-check requirements
- force-push and default-branch deletion restrictions
- required approving review count and administrator bypass policy, when
  supported by hosted inspection
- Actions workflow presence and hosted workflow state
- Dependabot config presence, when documented
- merge method settings as hosted values
- repo-local governance docs and canonical local validation

Statuses are advisory:

- `match`: hosted or local state matches the documented expectation
- `drift`: state differs from the documented expectation
- `unknown`: no documented expectation was found or a partial hosted surface
  could not be inspected

Required-status-check name comparison is intentionally conservative. The audit
extracts exact hosted check names from central repo overrides or explicit
repo-local declarations such as `required status checks:`, `require these
status checks:`, or structured required-check lists under governance or
branch-protection sections. Prose-only mentions, workflow filenames, historical
notes, command examples, and generic local validation guidance do not become
exact check-name expectations.

## Central Overrides

Central repo-specific overrides live under `repositories` in
`config/repo-settings-policy.json`. Use them for intentional exceptions, such
as private repositories or exact hosted required-check names.

Example:

```json
{
  "repositories": {
    "ctrl-alt-keith/example-private-repo": {
      "visibility": "private"
    },
    "ctrl-alt-keith/example-ci-repo": {
      "required_checks": [
        "check"
      ]
    }
  }
}
```

Prefer central overrides for repo-family settings that should be reviewed in
one place. Use repo-local governance declarations when the target repo needs
local rationale, a repo-specific exception, or a transition note that should
travel with that repo.

## Explicit Governance Declarations

Repo-local governance declarations are optional. They override central policy
only when explicit. Ambiguous or prose-only governance does not alter inherited
policy.

Supported declaration formats include:

- `repository visibility: private` or `repository visibility: public`
- `default branch: main`
- `require pull requests before merge: yes`
- `require status checks before merge: yes`
- `required status checks:` followed by a structured list of exact hosted check
  names
- `require branches up to date before merge: yes`
- `required approving reviews: 0`
- `administrator bypass: enabled`
- `force pushes on main: disabled`
- `deletions on main: disabled`
- `merge policy: squash-only` or `merge methods: squash-only`
- `squash merge: enabled`
- `merge commits: disabled`
- `rebase merge: disabled`
- `solo-operator review policy: enabled`

`solo-operator review policy: enabled` is shorthand for the solo-maintainer
review posture used by this repo family:

- pull requests are required before merge
- status checks are required before merge
- required approving reviews are intentionally `0`
- administrator/owner self-merge is allowed

It does not declare exact required check names. Add an explicit
`required status checks:` list when exact hosted check comparison is intended.

Example governance block:

```md
## Hosted Repository Settings

- repository visibility: private
- default branch: main
- require pull requests before merge: yes
- require status checks before merge: yes
- require branches up to date before merge: yes
- required approving reviews: 0
- administrator bypass: enabled
- force pushes on main: disabled
- deletions on main: disabled
- merge policy: squash-only
```

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
