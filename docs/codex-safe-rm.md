# Codex Safe Recursive Removal Policy

This repository owns the Codex consumer policy for delegating narrowly scoped
recursive cleanup to the Playbook-managed `codex-safe-rm` helper. Codex
`prefix_rule` entries can constrain a fixed argument prefix but cannot validate
an arbitrary number of suffix operands, so the installed helper remains the
validation boundary for those operands.

The executable source, installation, verification, migration, provenance, and
source-focused behavior tests belong to `ctrl-alt-keith/ai-workflow-playbook`.
Its canonical surfaces are
[`scripts/codex-safe-rm`](https://github.com/ctrl-alt-keith/ai-workflow-playbook/blob/main/scripts/codex-safe-rm),
[`scripts/install-codex-safe-rm`](https://github.com/ctrl-alt-keith/ai-workflow-playbook/blob/main/scripts/install-codex-safe-rm),
and the
[`Playbook-Managed Recursive Cleanup`](https://github.com/ctrl-alt-keith/ai-workflow-playbook/blob/main/docs/tool-adapters/codex.md#playbook-managed-recursive-cleanup)
guidance. Enforcement does not carry or wrap a second source or installer.

## Consumer Policy

- Direct `rm` remains prompt-gated.
- Delegation is allowed only for the exact installed absolute executable path
  followed by the fixed `-rf --` prefix.
- The active rule must not rely on `~`, `$HOME`, environment expansion, PATH
  lookup, a relative executable, or shell wrapping as runtime authority.
- The Playbook-owned helper validates every dynamic suffix operand and owns its
  containment and deletion guarantees.
- Availability of the helper does not decide whether a target is disposable.
  Callers remain responsible for that judgment.

Use [`examples/codex-safe-rm.rules`](../examples/codex-safe-rm.rules) as a
template. Before activation, replace every
`__CODEX_SAFE_RM_ABSOLUTE_PATH__` token with the one exact resolved absolute
path verified by the Playbook installer. An unresolved token is not a valid
active rule.

The required boundary is:

```python
prefix_rule(pattern=["rm"], decision="prompt")
prefix_rule(
    pattern=["__CODEX_SAFE_RM_ABSOLUTE_PATH__", "-rf", "--"],
    decision="allow",
)
```

The rule engine still cannot inspect the remaining operands. If the exact
reviewed helper is unavailable or the template cannot be rendered and verified,
keep recursive removal approval-gated rather than substituting another command.
