# AGENTS.md

This repository uses `ctrl-alt-keith/ai-workflow-playbook` as the canonical
source for reusable cross-repo workflow guidance. This file is the thin
repo-local execution layer. Repo-local rules take precedence only for
repository-specific behavior.

## Repo Scope

- This repo contains lightweight reinforcement tools for the workflow ecosystem.
- It should surface operational signals, not automate remediation or own
  canonical workflow policy.
- Keep staging material, canonical playbook guidance, and repo-local execution
  guidance distinct.

## Startup And Interaction Mode

- Start with `ai-workflow-playbook/docs/start-here.md` before repository or
  software work.
- Before acting, select the interaction mode from
  `ai-workflow-playbook/docs/repo-readiness.md`: implementation, review/audit,
  or orchestration/prompt-authoring.
- Implementation agents make explicit repo changes and carry them through
  validation, commit, push, and PR delivery.
- Review/audit agents inspect and report findings without mutating the repo.
- Orchestration/prompt-authoring agents produce complete, self-contained
  handoffs or prompts unless explicitly asked to implement.

## File Placement

- Put scanner implementation in `enforcement/`.
- Put focused tests in `tests/`.
- Put concise explanatory docs in `docs/`.
- Put small runnable examples in `examples/`.
- Do not add CI, release automation, plugin systems, or orchestration
  frameworks unless a later repository task explicitly requires them.

## Local Execution

- Run commands from this repository working directory by default.
- Keep temporary workflow state repo-local, for example `.worktrees/`.
- Use direct command execution for ordinary repo commands such as `git ...`,
  `gh ...`, `make ...`, `python ...`, and repo-local scripts or tools.
- Before using `zsh`, `bash`, `sh`, `zsh -lc`, `bash -lc`, `sh -c`, aliases, or
  equivalent wrapper shells, check whether the command has a direct form and
  use that direct form when it does.
- Use shell wrappers only when shell syntax is genuinely required, such as
  pipelines, redirection, glob expansion, command chaining, scoped environment
  assignment, compound commands, or shell builtins.

## Validation

- Use `make check` as the canonical local validation entrypoint.
- `make check` runs the repository's focused Python unit tests.
- Do not substitute alternate validation commands for readiness reporting.

## Branches

- Branch from current `origin/main`.
- Use focused, purpose-based names such as `docs/<short-name>` or
  `chore/<short-name>`.
- Keep branch scope limited to this repository.

## Pull Requests

- Target `main`.
- Keep changes scoped to this repository.
- Include validation results, known limitations, and any explicitly deferred
  future reinforcement ideas.

## Playbook Reference

- Start with `ai-workflow-playbook/docs/start-here.md`.
- Use the playbook for reusable workflow rules instead of duplicating them
  here.
