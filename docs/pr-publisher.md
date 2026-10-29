# Exact-tree PR publisher candidate

`enforcement/pr_publisher.py` is an inactive delivery component. It can take a
reviewed staging PR in one `ctrl-alt-keith` repository, verify the current
default branch, exact source commit and tree, a complete changed-file manifest,
and a successful pull-request workflow run bound to that source. It signs the
source tree with the pinned bot identity and creates or updates one App-authored
draft PR in the **same** repository. An identical tree and base return the
existing PR. Updates use the observed branch SHA as a lease. Uncertain writes
stop for remote inspection; readback only rechecks a previously observed head.
It never checks out or executes staged files in the credentialed process.

The target boundary is the `ctrl-alt-keith` organization, verified against
GitHub's repository owner, numeric repository ID, and an installation token
whose visible repository scope is exactly the target. No central list of
repository names is maintained. The current candidate limits each change to
128 regular-file paths and 8 MiB of new bytes, and excludes workflow-file
changes. The source test run must belong to the same PR, repository, branch,
and exact source SHA. The caller selects the relevant workflow ID; a successful
run proves that workflow completed, not that every needed test was selected.
The cloud worker remains responsible for review, test adequacy, final CI, and
the merge-ready decision. Human approval remains required for merge.

The hosted entrypoint is `python3 -m enforcement.pr_publisher`. It accepts a
JSON request in `PUBLISH_REQUEST_JSON` with `repository`, `repository_id`,
`base_sha`, `source_pr`, `source_sha`, `source_tree`, `manifest_sha256`,
`test_run_id`, and `test_workflow_id`. The runner supplies the short-lived App
token as `PUBLISH_TOKEN` and the private signer file path as
`PUBLISH_SIGNER_KEY`. `PUBLISH_HOST_SHA` must equal the executing main SHA;
the entrypoint also requires a first-attempt GitHub Actions run in the existing
incubator host. Its output contains only repository, branch, PR, commit, tree,
base, and action. The host revision check binds trusted code; it does not
establish owner approval for a particular publication.

There is no hosted entrypoint or credential setup in this repository yet. The
smallest deployment path is to keep the existing incubator hosted executor and
broker custody, and check out the **public** Enforcement implementation at a
reviewed, pinned commit. A future workflow mode must mint the existing
automation App token for only the target repository, with Contents write,
Pull requests write, and Actions read; the latter is needed to verify a
private target's
[workflow run](https://docs.github.com/en/rest/actions/workflow-runs#get-a-workflow-run).
It must verify the minted token's scope before publishing, supply the existing
bot signing key only to the privileged job,
and keep staged-code testing in a separate unprivileged run. The current
incubator pilot and its three-file limit remain active only within their
existing approval boundary. No App permission, installation, secret, or
workflow authorization is changed by this candidate.

The host should serialize runs by destination repository and source PR without
cancelling an in-flight run. The lease and readbacks remain necessary even with
that queue.

Before activation, verify whether the existing App registration already has
Actions read permission; obtain approval for a grant change if it does not.
The hosted caller also needs a reviewed finalize operation that checks the
exact output SHA and current CI/review state before marking the draft ready.
Those deployment and finalize steps are not implemented here; this module
alone does not complete unattended delivery to a merge decision.
