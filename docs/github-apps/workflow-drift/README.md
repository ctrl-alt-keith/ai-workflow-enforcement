# Workflow-drift GitHub App policy audit

This directory and `policy/github-apps/workflow-drift/` define reviewed,
non-secret bootstrap intent for the existing dedicated workflow-drift GitHub
App. They do not create, configure, install, or reconcile the App.

Run the advisory audit with a non-secret receipt and, when available, a
non-secret App/JWT capture:

```sh
python3 -m enforcement.github_app_policy_audit --receipt receipt.json --live-state live-state.json
```

`--fetch-installation-repositories` makes the sole direct provider read:
`GET /installation/repositories`, using the current installation token. It
records the installation-visible repository-set hash. That read can verify a
scope snapshot, but cannot prove the organization chose **All repositories**;
the same visible set can result from a selected-repository installation.

The audit reports every field as `match`, `drift`, or `unable-to-verify`.
App registration fields (URLs and webhook settings), App identity, installation
selection, effective permissions, and events require an App JWT capture path
that this implementation deliberately does not create or operate. They are
therefore never reported clean merely because the manifest or receipt agrees.

The receipt schema permits identifiers, selection/scope hash, effective
permissions/events, a key fingerprint, secret-manager reference/version,
approval metadata, and representative validation evidence. It rejects common
secret-bearing keys. Never record PEM/private-key material, webhook secrets,
or installation tokens. The manifest and receipt are desired-state and
historical evidence, not authoritative live state.

Outside this implementation: GitHub App creation or registration changes,
installation/scope changes, key rotation, secret-manager selection, Terraform
resources or apply, and any repository or organization configuration change.
