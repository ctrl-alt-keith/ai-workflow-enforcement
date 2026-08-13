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

Run a hosted-only organization audit across visible repositories:

```sh
python3 -m enforcement.repo_settings_audit --org ctrl-alt-keith
```

Include local-source comparison only when the corresponding checkouts are
available under one workspace root:

```sh
python3 -m enforcement.repo_settings_audit \
  --org ctrl-alt-keith \
  --workspace-root ~/src/ctrl-alt-keith
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

Organization-wide audits default to hosted-only reporting and do not run local
checkout comparisons unless `--workspace-root` is supplied. When a workspace
root is supplied, the audit maps each `owner/name` repository to
`<workspace-root>/<name>` and only reads that path. It does not switch branches,
clean worktrees, fetch refs, delete stale worktree metadata, or mutate local or
hosted repository state.

## Central Baseline Policy

The central baseline currently means:

- repository visibility: `public`
- default branch: `main`
- pull requests required before merge
- status checks required before merge
- strict/up-to-date required checks disabled by default
- force pushes on `main` disabled
- branch deletions on `main` disabled
- merge policy: squash-only
- auto-merge disabled
- delete branch on merge enabled
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

Maintained repositories are expected to have at least one meaningful validation
check. Docs-only, org-profile, and lightweight stub repositories can satisfy
that baseline with markdown/docs validation instead of application tests.

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
- branch up-to-date or strict status-check posture
- force-push and default-branch deletion restrictions
- required approving review count and administrator bypass policy, when
  supported by branch-protection or ruleset-detail inspection
- Actions workflow presence and hosted workflow state
- Dependabot config presence and weekly update entries for supported
  ecosystems
- merge method and merge-hygiene settings as hosted values, including the
  repository auto-merge capability
- repo-local governance docs and canonical local validation

Statuses are advisory:

- `match`: hosted or local state matches the documented expectation
- `drift`: state differs from the documented expectation
- `unknown`: no documented expectation was found or a partial hosted surface
  could not be inspected

Reports include both the legacy all-item `summary` and separate
`hosted_governance_summary` and `local_source_summary` buckets. Use the hosted
governance bucket for organization-level hosted drift conclusions. Local-source
drift is still useful stale-checkout signal, but it does not mean the target
repository's hosted governance has drifted.

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
- strict/up-to-date status-check posture
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

## Strict Checks Policy

The central solo-operator baseline disables strict/up-to-date required checks
by default. Requiring a pull request, requiring hosted checks, protecting the
default branch from force pushes and deletion, and using squash-only merges are
the baseline governance controls.

Strict checks can still be useful for high-concurrency repositories because
they require a pull request branch to be current with `main` before merge. In
this repo family they are disabled by default because most repositories are
maintained by a solo operator and the extra update/retry loop adds routine
maintenance friction without materially changing the required validation gate.

Repos may still opt into strict checks with an explicit repo-local governance
declaration such as `require branches up to date before merge: yes`. When a
repo explicitly declares that requirement, the audit treats disabled hosted
strict checks as drift.

## Minimum Validation

The minimum maintained-repo validation baseline is one lightweight, meaningful
hosted check. For documentation-only repositories, the repo-family default is a
markdown validation workflow with required check name `markdownlint`.

The existing repo-family pattern is the playbook's Markdown Lint workflow: run
on pull requests and pushes to `main`, use read-only contents permissions, set
up Node, install `markdownlint-cli2`, and run `make check`. The corresponding
Makefile target may use `markdownlint-cli2 "**/*.md" "#dist"` or an equivalent
markdownlint invocation.

Maintained repositories without application tests should add this markdown/docs
validation rather than carry a required-check exception. A central exception is
reserved for repositories that are explicitly archived, unmanaged, or otherwise
accepted as not maintained under the repo-family governance baseline.

## Solo-Operator Merge Hygiene

The central baseline keeps auto-merge disabled and enables delete branch on
merge.

The central `auto_merge` policy field accepts only `enabled` or `disabled`.
Repositories inherit the baseline unless their entry under `repositories`
defines an explicit override. Invalid baseline or selected-repository values
fail the audit instead of silently removing the expectation.

Auto-merge stays disabled because it can move a pull request from reviewed to
merged without a final explicit operator action once checks pass. That is useful
for some high-throughput team queues, but it works against the repo family's
human-controlled merge posture.

`ctrl-alt-keith/ai-workflow-incubator` carries an explicit central
`auto_merge: enabled` exception for deterministic generated dashboard-update
pull requests. GitHub's repository-level permission is broader than that
intended workflow: only the designated dashboard automation should enable
auto-merge on its pull requests, other pull requests remain manually merged,
and required status checks remain the acceptance boundary. A failed check
stops the merge and requires operator attention. The scanner enforces this
through the ordinary central override; it does not hard-code the repository or
use an invocation-time suppression.

Delete-branch-on-merge is baseline-enabled because the repo family uses
squash-only pull requests and short-lived topic branches. Deleting merged
branches reduces routine cleanup without changing review, validation, or merge
authority. Repos that need long-lived merged branches should document and carry
a central policy exception.

## Central Overrides

Central repo-specific overrides live under `repositories` in
`config/repo-settings-policy.json`. Use them for intentional exceptions, such
as private repositories, exact hosted required-check names, or Dependabot
exceptions. Fields not present in an override continue to inherit the central
baseline.

Organization audits also reconcile these override keys against current GitHub
organization membership. They report an orphan only after every REST result
page has been read and the authenticated user is verified as an active
organization owner, whose GitHub role has access to every organization
repository. If that evidence is unavailable, visibility-limited, malformed, or
otherwise incomplete, the reconciliation is `unknown` and reports no orphan.
The policy file remains an overlay: a current repository with no override is
valid baseline-only behavior, and the audit never deletes or rewrites an
orphaned key. Override keys are current `owner/name` locators; because this
policy schema does not retain stable repository IDs, a rename cannot be
distinguished automatically from a transfer or deletion and requires human
review when reported.

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
    "ctrl-alt-keith/example-generated-prs": {
      "auto_merge": "enabled"
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
hosted governance drift findings. Local-source drift and unknowns remain
advisory.

Use `--fail-on-error` for unattended automation that must fail when audit
coverage is incomplete. In organization mode, per-repo runtime failures are
aggregated into the org report's `errors` field so the report can still show
which repositories succeeded; `--fail-on-error` turns those errors into a
non-zero process exit. This is separate from drift classification: local-source
drift remains advisory, and hosted governance drift is still controlled by
`--fail-on-drift`.

## Automation Notes

Suggested maintenance automation identity:

- Display name: 🧭 Repo Settings Audit
- ID: `repo-settings-audit`
- Mode: report-only advisory audit
- Target scope: selected visible repositories where hosted governance settings
  should be compared with source-of-truth governance docs

The automation prompt should name the repository set and source ref. For
organization-wide reporting, prefer `--org` without `--workspace-root` when the
goal is hosted governance drift reporting. Add `--workspace-root` only when the
automation intentionally wants a separate advisory local-source freshness lane
for matching local checkouts. Unattended org audits should use
`--fail-on-error` so partial repository coverage cannot look operationally
healthy.

The prompt should not ask the tool or agent to remediate hosted settings.
Hosted changes such as branch-protection updates, ruleset edits, visibility
changes, and merge-method changes remain human/org-admin follow-up unless a
separate, explicit task authorizes mutation.
