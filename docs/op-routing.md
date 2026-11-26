# Managed 1Password CLI routing

Use managed `op` for Connect-backed `read`, `inject`, and JSON `item get`.
Stop on a failed Connect read. Never retry it through direct credentials.
[1Password Connect CLI reference](https://www.1password.dev/connect/cli)

In the managed Cloud environment, complete [task startup](../cloud-environments/openai/workspace-AGENTS.md)
before using either installed route.

Select `op --direct ...` for authorized bootstrap, account selection,
account metadata, recovery, and unsupported native commands.
Use `--account` on `whoami`, `read`, or `item get` only to pin the
workflow's required native account.

Use `CAK_OP_ROUTE=direct` for a bounded caller that preserves it. For a
filtered child environment, select the absolute native CLI locally or
`/workspace/.onboarding-tools/direct/bin/op` in Cloud. If the caller resolves
`op` through `PATH`, prepend the corresponding bin directory for that caller.

Use `op --direct run` only under the workload's existing authority. Keep
`OP_CONNECT_*` out of the launch environment.

## Enrollment

Bind the approved lowercase HTTPS origin on port 443 as `CAK_CONNECT_HOST`.
Omit the port and path from the URL. Deliver the existing reader through
the protected `CAK_CONNECT_TOKEN` binding using the approved
[Connect custody and recovery route](https://github.com/ctrl-alt-keith/lke-image-lab/blob/main/docs/connect.md#independent-bootstrap-and-recovery).

For local enrollment, require `/usr/bin/python3`. Resolve the native CLI
before changing `PATH` and set `CAK_OP_NATIVE` to its absolute path. Install
the reviewed `enforcement/op_runtime.py` as executable `op` in the managed
bin directory. Put that directory first in the task's login and non-login
`PATH`.

If an enrolled local session lacks the reader binding, confirm the reader
vault and field against the custody contract above. Load it once into the
protected task environment with
`op --direct read --no-newline` in the authorized native desktop session.
Complete bootstrap before attempting a Connect read. Never start bootstrap
in response to a missing or denied read. Keep the reader out of output and
startup files.

Before selecting the default for real tasks, verify executable resolution, the
private Connect state directory and the approved Connect and explicit direct
routes in a local task.
