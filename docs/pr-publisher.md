# PR publisher candidate

`enforcement.pr_publisher` is an inactive CLI; no hosted workflow invokes it.
It publishes an exact tested tree from an open staging PR to an App-authored
draft PR in the **same** `ctrl-alt-keith` repository. The commit uses the pinned
bot signer. An identical tree and base return the existing PR; updates use a
branch lease. Uncertain writes stop for remote inspection. Closed or merged
destination PRs need manual resolution. The module cannot merge or enable
auto-merge.

The request binds repository name and numeric ID, current base SHA, staging PR
and head SHA/tree, complete changed-file manifest, and one successful
first-attempt `pull_request` run for that PR, branch, commit, and selected
workflow. The caller owns review and test selection; a successful run alone
does not establish test adequacy. Changes are limited to 128 regular-file
paths and 8 MiB of new bytes. `.github/workflows/` changes are rejected;
other automation paths require caller review. The credentialed process never
checks out or executes staged files. Human approval remains required for merge.

The CLI reads `PUBLISH_REQUEST_JSON` (the fields of `Request`),
`PUBLISH_TOKEN`, and `PUBLISH_SIGNER_KEY`. It requires a first-attempt Actions
run on incubator `main` with `PUBLISH_HOST_SHA` equal to the host
`GITHUB_SHA`. This environment check catches mismatches; the hosted workflow
must separately pin and verify the Enforcement checkout.

To activate, use the existing incubator executor and broker custody. Mint and
verify an App token scoped to **one target repository** with Contents write,
Pull requests write, and Actions read. Confirm the App grant before activation;
any expansion needs approval. Keep the signer on the privileged runner and
staging tests in an unprivileged job. Serialize runs by repository and source
PR without cancelling in-flight work. Add a finalize step that verifies the
published SHA, current CI, and review before marking the draft ready. The host
and finalize steps are not implemented here; the current incubator pilot remains
the live path within its three-path limit.
