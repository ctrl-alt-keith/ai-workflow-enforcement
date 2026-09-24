# Issue-owned Dropbox artifact capture

`enforcement.issue_artifact_capture` is a narrow writer for an already admitted
issue-owned artifact. The producing controller decides retention under the
[Playbook's governed-artifact candidate and storage-admission rules](https://github.com/ctrl-alt-keith/ai-workflow-playbook/blob/main/docs/evidence-lifecycle.md#governed-artifact-capture).
Use it for complete governed Claude/Codex review outputs, decision inputs, or
packages whose exact bytes are needed for downstream review or recovery and
whose issue storage contract permits Dropbox retention. A launcher output file
is only local attempt evidence until this admission and provider capture pass.
Selector canaries, health probes, routine sanity checks, and disposable scratch
normally stay ephemeral. A failed review output can be admitted when its exact
failure evidence is needed; record its non-verdict status separately.

The caller supplies a fresh receipt path, a local source file, the governing
Linear issue, a unique dated and versioned filename, the expected Dropbox
account and namespace, and the storage-admission authority reference:

```sh
python3 -m enforcement.issue_artifact_capture \
  --source-file [private-local-review-output] \
  --issue CAK-322 \
  --name cak-322-review-v1-2026-09-23.md \
  --authority 'Linear CAK-322 and admitted review output' \
  --acting-email [expected-dropbox-account] \
  --namespace-id [dropbox-namespace-id] \
  --receipt-file [fresh-attempt-local-path]/receipt.json
```

The credential named by `DROPBOX_ACCESS_TOKEN` must already be available to
the process. This command does not create the folder. Resolve an absent folder
through the authorized provider route first, then rerun only before any upload
attempt. Ambiguous provider state is never interpreted as absence.

The command derives `/issues/CAK-322/<name>` from the issue and filename. It
never derives a provider destination from `--source-file`, even if that file is
under a Dropbox synced directory. Before upload, it checks the acting account,
looks up the exact provider folder in the specified namespace, and resolves
the returned folder ID again to verify identity and containment. It then uses
Dropbox add mode with strict conflict and no autorename, and re-reads the new
file by provider ID to compare its path, revision, size, and Dropbox content
hash with the frozen local bytes. The receipt and standard output use the
provider path and identity; neither uses a local synced path as a locator.

The receipt is created exclusively before any provider effect. Preserve it on
the owning durable evidence surface under the governing storage contract.
If upload or readback fails, the receipt reports a blocked result and an
uncertain provider effect. Inspect provider state before any new attempt; do
not repeat an upload against the same target. A successful capture records
whole-file SHA-256 separately from Dropbox's content hash. This command
does not decide evidence acceptance, review correctness, or merge authority.

Provider behavior relies on Dropbox's [namespace guide](https://www.dropbox.com/developers/reference/namespace-guide),
[file access guide](https://developers.dropbox.com/dbx-file-access-guide), and
[content hash reference](https://www.dropbox.com/developers/reference/content-hash)
(checked 2026-09-23).
