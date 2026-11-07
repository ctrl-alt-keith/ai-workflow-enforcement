# AGENTS.md

This repository uses `ctrl-alt-keith/ai-workflow-playbook` as the canonical
source for reusable cross-repo workflow guidance. This file is the thin
repo-local execution layer. Repo-local rules take precedence only for
repository-specific behavior.

## Repo Scope

- This repo contains lightweight reinforcement tools for the workflow ecosystem.
- It should surface operational signals and does not own canonical workflow
  policy or remediation authority. The narrow Hosted Stewardship Engine
  exception may create one validated, review-ready pull request through its
  fixed strategies; it must not merge, enable auto-merge, update a proposal, or
  otherwise accept its own change. Human merge remains the remediation boundary.
- The exact-tree PR publisher's private host and activation contract are in
  `docs/pr-publisher.md`.
- Keep staging material, canonical playbook guidance, and repo-local execution
  guidance distinct.

## Startup

- Start with `ai-workflow-playbook/docs/start-here.md` before repository or
  software work.

## File Placement

- Put scanner implementation in `enforcement/`.
- Put focused tests in `tests/`.
- Put concise explanatory docs in `docs/`.
- Put small runnable examples in `examples/`.
- Do not add CI, release automation, plugin systems, or orchestration
  frameworks unless a later repository task explicitly requires them.

## Validation

- Use `make check` as the canonical local validation entrypoint.
- `make check` runs the repository's focused Python unit tests.

## Pull Requests

- Include known limitations and any explicitly deferred
  future reinforcement ideas.
