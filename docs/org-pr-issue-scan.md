# Org PR And Issue Scan

`enforcement.org_pr_issue_scan` is a report-only maintenance automation helper
for listing current open pull requests and issues across the visible
repositories in the `ctrl-alt-keith` GitHub organization.

Run the default organization scan:

```sh
python3 -m enforcement.org_pr_issue_scan
```

Run a machine-readable scan:

```sh
python3 -m enforcement.org_pr_issue_scan --output-format json
```

Ask automation callers to fail on incomplete coverage:

```sh
python3 -m enforcement.org_pr_issue_scan --fail-on-error
```

## Safety Model

The tool uses the local `gh` CLI authentication and API runtime. It does not
write repository state, create issues, modify pull requests, open branches,
commit, push, or update automation configuration.

The scan flow is:

1. enumerate organization repositories dynamically with `gh api`
2. fetch open pull requests for each repository
3. fetch open issues for each repository
4. exclude pull requests returned by the issues endpoint
5. group results by repository, with pull requests and issues separated
6. report skipped or partially inaccessible repository scopes with reasons

Repository, pull request, and issue collection uses `gh api --paginate --slurp`
with `per_page=100` endpoints so paginated responses are included in a single
report.

Each pull request and issue entry includes the repository name, number, title,
URL, author, labels, assignees, and `updated_at` when GitHub returns those
fields.

The default exit behavior is advisory and non-blocking, including when the
report includes repository enumeration errors or skipped per-repository scopes.
Use `--fail-on-error` when a scheduled automation or local check should return
exit code 1 for incomplete coverage while still printing the report.

## Automation Notes

Suggested maintenance automation identity:

- Display name: 🔎 Org PR and Issue Scan
- ID: `org-pr-issue-scan`
- Mode: report-only
- Target scope: visible repositories in the `ctrl-alt-keith` GitHub
  organization

The active Codex automation configuration is local runtime state. If this
automation is installed locally, keep its full schedule, prompt, and execution
paths in the local automation config. Keep playbook inventory updates concise
and high-level, matching `ai-workflow-playbook/docs/maintenance-automations.md`.
