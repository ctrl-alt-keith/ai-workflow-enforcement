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
- Prefer direct `git ...` and `gh ...` commands unless shell behavior is
  required.

## Validation

- Use `make check` as the canonical local validation entrypoint.
- `make check` runs the repository's focused Python unit tests.
- Do not substitute alternate validation commands for readiness reporting.

## Pull Requests

- Target `main`.
- Keep changes scoped to this repository.
- Include validation results, known limitations, and any explicitly deferred
  future reinforcement ideas.

## Playbook Reference

- Start with `ai-workflow-playbook/docs/start-here.md`.
- Use the playbook for reusable workflow rules instead of duplicating them
  here.

