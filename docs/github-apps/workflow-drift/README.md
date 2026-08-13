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

## Credential delivery decision

The runtime is GitHub-hosted Actions and already has a narrow supported seam:
`actions/create-github-app-token@v3` accepts `WORKFLOW_DRIFT_APP_PRIVATE_KEY`
directly from the GitHub Actions repository-secret store. CAK-118 makes that
the approved delivery mechanism. The workflow passes the secret only to the
action's `private-key` input; it never maps the key into a shell environment,
file, output, artifact, receipt, Terraform value, or repository file. A
missing or rejected managed secret causes the required token-generation action
to fail, so the workflow classifies the run as **Unable to verify**; there is
no plaintext or alternate-credential fallback.

This retains the existing runtime-only installation-token flow. The action
receives explicit `metadata: read` and `contents: read` permissions, its token
is used only by the three read-only steps that need it, and its default post
step revokes the token when the job finishes. Do not set `skip-token-revoke`.
The runtime has no webhook consumer, so it deliberately has no webhook-secret
delivery path.

GitHub Actions secrets are encrypted before GitHub receives them and are
redacted in workflow logs when supplied through the `secrets` context. The
direct action input also avoids the less-safe decode-to-shell workaround.
Automatic redaction is not a license to print transformed values: do not add
shell tracing, `echo`, outputs, or artifacts containing credentials.

This choice is deliberately narrower than introducing a cloud key vault or
Terraform secret integration: this repository has no existing cloud identity
or vault runtime seam, and adding one would require a broader provider and
control-plane decision. GitHub's own documentation notes that a sign-only key
vault can be stronger; that remains a future platform decision, not evidence
to bypass the current managed-secret seam.

For a completed adoption receipt, use only safe metadata:

- `key.secret_manager_reference`: `github-actions:repository-secret:WORKFLOW_DRIFT_APP_PRIVATE_KEY`
- `key.secret_manager_version`: the repository-secret `updated_at` value or a
  human-approved rotation identifier (never the secret value)
- `key.fingerprint`: GitHub's displayed SHA-256 key fingerprint

## Approval-gated key rotation

No rotation is performed by this repository or workflow. An authorized human
operator must approve and perform each provider-side step:

1. Generate a second GitHub App key, retaining the prior key during the
   validation window. GitHub supports multiple active keys, which permits this
   overlap.
2. Immediately store the new PEM as the value of the existing repository
   secret `WORKFLOW_DRIFT_APP_PRIVATE_KEY`; do not put it in a file, terminal
   command, Terraform value, issue, receipt, log, or artifact.
3. Update only the non-secret receipt metadata with the new GitHub key
   fingerprint, managed-secret reference/version, approver, timestamp, and
   rotation identifier.
4. With approval, dispatch one representative read-only workflow-drift audit.
   Verify that token generation succeeds, the audit completes, and workflow
   logs and retained evidence contain no credential material.
5. After that validation and a separate explicit approval, revoke the prior
   GitHub App key and record the retirement decision in the receipt or its
   linked operational record. Do not delete the replacement secret as part of
   that revocation.

If validation fails, retain the prior active GitHub App key while the operator
restores the previously approved managed-secret value; do not revoke either
key until a safe recovery path and approval are established. GitHub App keys
do not expire automatically, so periodic review and this explicit retirement
step are required.

Authoritative references: [GitHub Actions secrets](https://docs.github.com/en/enterprise-cloud@latest/actions/concepts/security/secrets),
[secure Actions use](https://docs.github.com/en/actions/reference/security/secure-use),
[the token action](https://github.com/actions/create-github-app-token), and
[GitHub App private-key management](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps).

Outside this implementation: GitHub App creation or registration changes,
installation/scope changes, managed-secret creation/update, key generation,
rotation or revocation, Terraform resources or apply, and any repository or
organization configuration change.
