# Fixed Prompt-Delivery DAG Pilot

`enforcement.prompt_delivery_dag` implements one bounded issue-owned Codex
prompt-delivery path. It is an executable pilot, not a reusable workflow
engine.

## Fixed graph

The only nodes and dependencies are:

```text
freeze_input
  -> validate_scope
  -> upload_prompt
  -> verify_artifact
  -> mint_download_link
  -> render_handoff
```

Each node runs at most once. A blocked node leaves every descendant `NOT_RUN`.
There is no automatic retry, alternate provider, inline fallback, second
upload, plugin hook, dynamic node registration, or workflow selection.

The first two nodes freeze exact bytes, compute the ordinary whole-file
SHA-256 and Dropbox content hash, and validate the caller-supplied issue,
recipient, contained versioned destination, acting account, non-secret
attestation, and integrity values. Upload uses Dropbox `add` mode with
`strict_conflict=true` and `autorename=false`, so an occupied destination
blocks instead of overwriting, accepting identical content, or creating a
renamed copy.

Verification re-observes the created file and exact-matches its file ID, path,
revision, size, and Dropbox content hash before link creation. Ordinary
SHA-256 and Dropbox content hash remain separately labeled. Dropbox documents
`content_hash` as the metadata field for comparing local content with the
server copy in its [File Access Guide](https://developers.dropbox.com/dbx-file-access-guide)
and [Content Hash reference](https://www.dropbox.com/developers/reference/content-hash).

The final renderer accepts only `VerifiedArtifact`, `ReceiverLink`, recipient,
and bootstrap values. It has no prompt bytes, file path reader, Dropbox client,
preview callback, or content-retrieval capability.

## Running one attempt

Use the repository's normal Python module entry point. All digest values must
be computed from the exact input file before invocation:

```sh
python3 -m enforcement.prompt_delivery_dag \
  --prompt-file [exact-prompt-file] \
  --issue CAK-123 \
  --destination /issues/CAK-123/example-prompt-v1-2026-09-02.md \
  --recipient codex \
  --acting-email [expected-dropbox-account] \
  --namespace-id [dropbox-namespace-id] \
  --access-token-env DROPBOX_ACCESS_TOKEN \
  --expected-size [byte-count] \
  --expected-sha256 [whole-file-sha256] \
  --expected-dropbox-content-hash [dropbox-content-hash] \
  --non-secret \
  --checkout /required/existing/checkout \
  --receipt-file [fresh-attempt-local-path]/receipt.json
```

The receipt path must not already exist. The command writes a machine-readable
receipt there with no prompt body, access token, hidden canary, or raw receiver
URL. On success, standard output is the transient receiver handoff and does
contain the temporary receiver URL; do not redirect or retain that stream as a
durable log. Promote the byte-free receipt under the governing evidence
contract, then dispose of only attempt-local mechanics under the applicable
cleanup rules.

Dropbox's official
[`get_temporary_link` contract](https://dropbox.github.io/dropbox-sdk-java/api-docs/v2.0.x/com/dropbox/core/v2/files/DbxUserFilesRequests.html#getTemporaryLink-java.lang.String-)
says the returned download link expires after four hours. It does not document
that download link as single-use, so the pilot records `single_use=false` and
makes no stronger claim. This differs from Dropbox's separately documented
one-time temporary *upload* links.

## Result boundary

The durable receipt records the fixed node order, per-node status and checks,
terminal `SUCCESS` or `BLOCKED`, verified artifact identity, and whether one
temporary link was created. The URL remains only in the transient handoff.

The receipt, prompt, provider object, link, validation, and successful run
grant zero authority and perform no lifecycle transition. Human review still
controls merge, adoption, and any decision to generalize the pilot.
