# Codex Policy For Governed Claude Reviews

[`../examples/codex-claude-review.rules`](../examples/codex-claude-review.rules)
is the portable source for two narrow Codex execution-policy grants. They let
Codex launch the Playbook's governed Claude authentication preflight and
substantive review outside the workspace sandbox without asking for approval
on every attempt.

The grants do not cover permission-hook, termination-request, termination-
decline, graceful-termination, or forced-termination invocations. Those remain
subject to the caller's normal approval policy. Shell wrappers, pipelines, and
redirections also do not match; supply the review prompt directly on standard
input to the launcher.

## Safety Dependency

The rule relies on `scripts/claude-review` rejecting combinations of review or
authentication mode with any lifecycle-control mode. Do not install the rule
for an older launcher that permits mixed modes. Keep `--auth-preflight` or
`--review-config` immediately after `./scripts/claude-review`, as shown in the
rule's positive examples, so the intended direct command matches.

The rule grants execution of the launcher, not unrestricted Claude or shell
execution. The launcher remains responsible for validating the governed review
configuration, exact command posture, source graph, attempt evidence, and
lifecycle contract.

## Install On macOS Or Linux

From a reviewed checkout of this repository:

```sh
mkdir -p "${CODEX_HOME:-$HOME/.codex}/rules"
cp examples/codex-claude-review.rules \
  "${CODEX_HOME:-$HOME/.codex}/rules/claude-review.rules"
```

If the destination already exists, compare it before replacing it. Restart
Codex after installing or changing a rule file.

Validate the installed policy without launching Claude:

```sh
codex execpolicy check --pretty \
  --rules "${CODEX_HOME:-$HOME/.codex}/rules/claude-review.rules" \
  -- ./scripts/claude-review --auth-preflight --claude-bin claude

codex execpolicy check --pretty \
  --rules "${CODEX_HOME:-$HOME/.codex}/rules/claude-review.rules" \
  -- ./scripts/claude-review --terminate /tmp/live-state.json \
  --termination-authority operator-approved
```

The first command should report `allow`. The second should report no matching
allow rule. These checks inspect policy only; they do not run the launcher.

Machine-local installation remains local runtime state. The versioned example
is the transferable source to review and install on another macOS or Linux
system.
