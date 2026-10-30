# Exact-tree PR publisher

`enforcement.pr_publisher` copies the tested tree of an open staging PR into a
separate App-authored draft PR in the same `ctrl-alt-keith` repository. The
commit uses the pinned bot signer. Identical requests return the existing PR;
updates use a branch lease. Uncertain writes stop for remote inspection.

The request binds repository name and ID, current base SHA, staging PR and
head/tree, complete changed-file manifest, and one successful first-attempt
`pull_request` test run for that exact source. The caller chooses adequate
tests. The privileged process reads staged files as data and never executes
them. It accepts up to 128 regular-file paths and 8 MiB of new bytes;
`.github/workflows/` changes are excluded.

`enforcement.hosted_publisher` validates the request before broker access,
checks a reviewed Enforcement checkout, reads the existing App and signer keys
on the private runner, and mints a token for only the destination repository.
The token requires Contents and Pull requests write plus Actions and Checks
read. Finalize rechecks the exact output commit, review evidence, current
successful required checks, and draft state before marking it ready. Neither
operation merges or enables auto-merge.

The thin private workflow in `secrets-lifecycle` supplies the protected runner,
exact host and Enforcement revisions, per-source serialization, and 1Password
binding. Its repository environment must be restricted to reviewed `main`.
Only the reviewed, pinned workflow may receive the broker secret. Qualify the
new host before retiring the Incubator route; keep one active publisher route
during cutover. Human approval remains required for merge.
