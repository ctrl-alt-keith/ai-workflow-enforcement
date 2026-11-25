# Managed 1Password CLI routing

The managed `op` command sends routine `read`, `inject`, and JSON
`item get` commands to Connect. Bind the approved lowercase HTTPS hostname
on port 443 as `CAK_CONNECT_HOST`, with no port or path in the URL. Supply the
existing reader through the protected `CAK_CONNECT_TOKEN` binding. A failed
Connect read must never be retried through direct credentials.
See [1Password's supported Connect commands](https://www.1password.dev/connect/connect-cli).

Select `op --direct ...` for authorized bootstrap, account selection,
account metadata, recovery, and unsupported native commands.
`CAK_OP_ROUTE=direct` selects the same route for a bounded caller that
retains it. For Cloud callers with filtered child environments, prepend
`/workspace/.onboarding-tools/direct/bin` to the caller's `PATH` or configure
its explicit `op` path to that entrypoint. For local callers with filtered
environments, configure the absolute native CLI path or prepend its bin
directory for that caller.

Run `op --direct run` only under the workload's existing authority. Do not
export `OP_CONNECT_*` for `op run`, which passes its authentication
environment to the child.

## Local entrypoint

Verify that `/usr/bin/python3` is available. Resolve the installed native CLI
before changing `PATH`. Place the reviewed
`enforcement/op_runtime.py` at `op` in the operator's managed bin directory,
with executable permissions. Bind `CAK_OP_NATIVE` to the absolute native CLI
path and put the managed directory first in the local task's login and non-login
`PATH`. Supply the two Connect bindings through the approved local session
channel.

Before selecting the default for real tasks, verify executable resolution, the
private Connect state directory and the approved Connect and explicit direct
routes in a local task.
