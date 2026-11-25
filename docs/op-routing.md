# Managed 1Password CLI routing

The managed `op` command sends routine `read`, `inject`, and JSON
`item get` commands to Connect. Supply the approved HTTPS origin as
`CAK_CONNECT_HOST` and the existing reader through the protected
`CAK_CONNECT_TOKEN` binding. The router sets Connect variables only for the
native CLI process and removes its direct-account credentials. Missing
bindings, an unavailable Connect service, or a denied vault stop the read.
See [1Password's supported Connect commands](https://www.1password.dev/connect/connect-cli).

Use `op --direct ...` for bootstrap, account selection, account metadata,
recovery, and other native commands under their existing authority.
`CAK_OP_ROUTE=direct` selects the same route for a bounded caller that
retains that variable. A caller that constructs a restricted child environment
must preserve its native identity deliberately. In Cloud, prepend
`/workspace/.onboarding-tools/direct/bin` to that caller's `PATH` if passing a
route selector through its child environment is impractical. Credentials
remain in their existing custody.

`op run` starts a child program that inherits Connect authentication
variables. It requires explicit execution context and is outside routine read
routing. Use the documented native execution path for that workload.

## Local entrypoint

Resolve the installed native CLI before changing `PATH`. Place the reviewed
`enforcement/op_runtime.py` at `op` in the operator's managed bin directory,
with executable permissions. Bind `CAK_OP_NATIVE` to the absolute native CLI
path and put the managed directory first in the local task's login and non-login
`PATH`. Supply the two Connect bindings through the approved local session
channel. The local Connect process uses host TLS and network routing. Direct
mode keeps the native desktop/account configuration.

Before selecting the default for real tasks, verify executable resolution, the
private Connect state directory, and the intended Connect and explicit direct
routes in an approved local task. Use the approved Connect reader in already
qualified operator sessions.
