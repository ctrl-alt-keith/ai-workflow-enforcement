# Repo Settings Audit

`enforcement.repo_settings_audit` is a read-only GitHub repository settings
audit. It compares effective hosted governance behavior with the central
repo-family policy in `config/repo-settings-policy.json`, repo-specific central
overrides, and any explicit repo-local governance declarations read from one
GitHub source-of-truth ref, defaulting to `main`.

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
- Dependabot expected weekly when a supported ecosystem is present

Repository visibility defaults to `public` intentionally. Repositories must opt
into `private` in the central policy file so private repositories remain
intentional and reviewable.

Required check names remain repo-specific. The central baseline requires hosted
status checks, but exact check-name comparison happens only when
`required_checks` is set in central repo overrides or exact checks are declared
in repo-local governance docs.

## Audited Surfaces

The audit reports expected, actual, status, source, and suggested human
follow-up for effective governance behavior, regardless of whether GitHub
currently enforces that behavior through classic branch protection or rulesets:

- repository visibility
- default branch
- default branch enforcement through classic branch protection or active
  branch rulesets
- required status checks, including exact documented check names when clearly
  declared in central overrides or explicit required-status-check governance
  sections
- required pull request settings
- branch up-to-date or strict status-check requirements
- force-push and default-branch deletion restrictions
- required approving review count and administrator bypass policy, when
  supported by branch-protection or ruleset-detail inspection
- Actions workflow presence and hosted workflow state
- Dependabot config presence and weekly update entries for supported
  ecosystems
- merge method settings as hosted values
- repo-local governance docs and canonical local validation

Statuses are advisory:

- `match`: hosted or local state matches the documented expectation
- `drift`: state differs from the documented expectation
- `unknown`: no documented expectation was found or a partial hosted surface
  could not be inspected

Before reporting hosted-state unknowns, the audit performs one bounded
read-only retry for optional hosted API surfaces that commonly affect branch
protection classification. This includes branch protection, branch rulesets,
required status checks, pull-request review settings, force-push/deletion
details, administrator enforcement, and Actions workflow state. A missing
branch-protection endpoint reported as GitHub `404` is treated as absent hosted
protection, not as transient unknown; if policy expects protection, that
absence is reported as drift.

Unknown report text distinguishes the broad cause where feasible:

- `unknown_policy`: the effective central/repo-local policy does not declare an
  expectation for that setting
- `unknown_unavailable`: a hosted response was readable but did not expose the
  needed field
- `unknown_after_retry`: the audit retried the relevant read-only hosted call
  and the surface was still unavailable or incomplete

Required-status-check name comparison is intentionally conservative. The audit
extracts exact hosted check names from central repo overrides or explicit
repo-local declarations such as `required status checks:`, `require these
status checks:`, or structured required-check lists under governance or
branch-protection sections. Prose-only mentions, workflow filenames, historical
notes, command examples, and generic local validation guidance do not become
exact check-name expectations.

## Mechanism-Neutral Interpretation

Classic branch protection and GitHub rulesets are treated as interchangeable
hosted enforcement mechanisms when they produce equivalent effective behavior.
Policy describes the intended outcome, not the GitHub implementation style.

The audit normalizes these surfaces across both mechanisms:

- pull-request requirement
- required status check names
- strict/up-to-date status checks
- required approving review count
- administrator bypass or self-merge
- force-push restrictions
- default-branch deletion restrictions

When more than one mechanism is active, the effective result is the strongest
applicable hosted behavior. For example, any active non-fast-forward rule or
disabled classic force pushes means force pushes are effectively restricted;
any active deletion rule or disabled classic deletions means branch deletions
are effectively restricted; the maximum required review count is used; and
administrator bypass is considered enabled only when all active mechanisms
allow the relevant bypass.

Human follow-up text may mention that hosted implementation differs by
mechanism, but drift classification is based on effective policy behavior.

## Central Overrides

Central repo-specific overrides live under `repositories` in
`config/repo-settings-policy.json`. Use them for intentional exceptions, such
as private repositories, exact hosted required-check names, or Dependabot
exceptions.

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
    },
    "ctrl-alt-keith/example-no-dependabot": {
      "dependabot": {
        "enabled": false
      }
    },
    "ctrl-alt-keith/example-daily-dependabot": {
      "dependabot": {
        "schedule": "daily"
      }
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

## Dependabot Baseline

The central baseline sets Dependabot to `auto`. In `auto` mode, the audit
expects `.github/dependabot.yml` or `.github/dependabot.yaml` only when the
source-of-truth ref contains supported ecosystem signals:

- `github-actions` when `.github/workflows/*.yml` or `.yaml` exists
- `pip` when Python packaging metadata such as `pyproject.toml`, `setup.cfg`,
  or `setup.py` exists

Expected Dependabot updates must use `schedule.interval: weekly`. If no
supported ecosystem is detected, Dependabot is reported as not applicable with
status `match`, rather than `unknown`.

Repo-specific central overrides may disable Dependabot:

```json
{
  "repositories": {
    "ctrl-alt-keith/example": {
      "dependabot": {
        "enabled": false
      }
    }
  }
}
```

Dependabot YAML parsing is intentionally conservative. The audit reads the
validated source-of-truth ref, extracts `updates[].package-ecosystem` and
`updates[].schedule.interval`, and reports drift when required ecosystems are
missing or non-weekly. If the config shape is unsupported or malformed, the
setting reports `unknown_unavailable` with follow-up text instead of guessing.
Repo-specific central overrides may also customize the expected schedule.

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
