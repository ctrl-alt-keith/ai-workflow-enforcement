# On-demand artifact-store integrity

`enforcement.artifact_store_integrity` is an unscheduled, explicitly
human-invoked Dropbox integrity check. It is read-only against Dropbox and
requires a caller-owned manifest that binds the permitted store root, the
authority record, and prior immutable identity evidence.

It is not a recurring audit, scheduler, background monitor, backup system, or
restore tool. It does not establish sharing membership, effective access,
retention, local synchronization, restore behavior, plan tier, independent
backup coverage, future availability, or the truth or authority of artifact
contents.

## Inputs and permissions

Copy `examples/artifact-store-integrity-manifest.json` to a permitted local
working location and replace the synthetic values with identities from an
authorized evidence record. The manifest requires:

- one numeric Dropbox namespace ID and a non-root path within it;
- an exact human or workflow authority record in `authority.source`;
- contained relative file paths;
- prior raw-byte size, whole-file SHA-256, and evidence source for every file;
- Dropbox content hash, file ID, and revision when the evidence record contains
  them; and
- optional sample tags for an explicitly selected sample.

Do not infer a store root, destination, missing digest, or authority source.
The command fails before provider access when any required value is ambiguous.
The token named by `--access-token-env` needs only Dropbox
`files.metadata.read` and `files.content.read` scopes. The token is used in
request headers and is never included in the report.

## Representative bounded invocations

Every invocation names exactly one scope and has explicit file and byte bounds.
The defaults are 100 files and 100 MiB; lower them when the intended check is
smaller. Discovery stops and reports `unverifiable` as soon as it observes more
files than the declared bound. A provider-reported object size larger than the
remaining byte budget is not downloaded, and partial transfer bytes count
against the run budget. Raise either bound only as a deliberate human choice
after checking the manifest and expected provider cost.

Verify one issue directory, including pagination-complete discovery of current
and deleted entries:

```sh
python3 -m enforcement.artifact_store_integrity \
  --manifest /permitted/local/cak-144-integrity-manifest.json \
  --issue CAK-144 \
  --max-files 40 \
  --max-bytes 10485760
```

Verify one package directory:

```sh
python3 -m enforcement.artifact_store_integrity \
  --manifest /permitted/local/integrity-manifest.json \
  --package CAK-106/package-017 \
  --max-files 12 \
  --max-bytes 5242880
```

Verify one exact object:

```sh
python3 -m enforcement.artifact_store_integrity \
  --manifest /permitted/local/integrity-manifest.json \
  --path CAK-144/cak-144-decision-packet-v2.md \
  --max-files 1 \
  --max-bytes 20000
```

Verify an explicitly curated, risk-stratified sample. Sample membership comes
from the manifest; the command does not silently invent a statistically
representative population:

```sh
python3 -m enforcement.artifact_store_integrity \
  --manifest /permitted/local/integrity-manifest.json \
  --sample risk-stratified-2026-08-26 \
  --max-files 25 \
  --max-bytes 6000000
```

The access token defaults to `DROPBOX_ACCESS_TOKEN`. To use another variable,
pass only its name:

```sh
python3 -m enforcement.artifact_store_integrity \
  --manifest /permitted/local/integrity-manifest.json \
  --path CAK-144/cak-144-decision-packet-v2.md \
  --access-token-env CAK_DROPBOX_READ_TOKEN
```

The complete machine-readable JSON report is written to standard output. A
one-line human summary is written to standard error. The command itself does
not persist either stream, because it cannot infer an authorized destination.
If a report becomes dependency-bearing, the caller must separately admit it
under the owning storage contract, use a no-overwrite destination, verify its
exact bytes, and create the required producing receipt.

## Interpretation

The process exits `0` only when every selected object passes. It exits `1` for
any non-pass integrity result and `2` for an invalid invocation, identity
manifest, or authority boundary.

Object statuses are:

- `pass`: all required comparisons matched and current metadata was complete;
- `changed`: at least one available size, digest, provider identity, revision,
  or same-observation comparison mismatched;
- `missing`: the provider did not return the expected path or explicitly
  reported it not found;
- `deleted`: the provider returned a deleted entry;
- `inaccessible`: the provider denied the read; and
- `unverifiable`: metadata, current bytes, identity evidence, pagination state,
  or another required observation was unavailable or ambiguous.

Unknown state is never counted as a pass. An active file discovered in an
issue or package scope but absent from the manifest is `unverifiable`; it is
not silently ignored.

Each successful byte read reports two separate evidence classes:

- `cross_time` compares current bytes and metadata with identities previously
  recorded in the manifest. Provider content hash, file ID, and revision are
  compared only when the prior record contains them.
- `same_observation` compares the listing/get-metadata response, download
  response metadata, raw byte count, and locally computed Dropbox content hash
  from this run. These checks can expose an internally inconsistent or racing
  observation, but they are not historical integrity evidence.

Dropbox content hashes use Dropbox's documented 4 MiB block-hash algorithm;
whole-file SHA-256 remains a separate raw-byte identity. Issue and package
discovery follows every `list_folder/continue` cursor until `has_more` is
false and asks Dropbox to include deleted entries. This can report visible
deletion evidence; it is not a deletion-history audit.

Provider semantics are based on Dropbox's official
[content-hash reference](https://www.dropbox.com/developers/reference/content-hash)
and [file-access guide](https://developers.dropbox.com/dbx-file-access-guide).

## Claim and authority boundary

The JSON includes `read_only: true`, `advisory: true`, the declared authority
source, exact scope and bounds, pagination/download counts, per-object evidence,
and the zero-authority notice. A clean result means only that the selected
comparisons passed during this observation. It does not authorize mutation,
planning transitions, implementation, merge, release, retention, sharing,
restore, or backup claims. Generated reports and later receipts remain evidence
and transfer no authority.
