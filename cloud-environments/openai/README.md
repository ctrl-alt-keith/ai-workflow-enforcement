# OpenAI cloud environment source

The Git commit of this directory versions the intended nonsecret setup for the
saved ctrl-alt-keith OpenAI cloud execution environment.
The installer supports Linux x86_64 only.
`install.sh` is the saved build script; `workspace-AGENTS.md` is delivered as
`/workspace/AGENTS.md`; `configuration.json` records restricted mode, preset,
and custom domains. The saved editor owns the explicit repository selection;
the runtime catalog records its resolved commits.
The source commit does not identify a saved or published environment version.

## Runtime credential binding

Use the [managed CLI routing and enrollment contract](../../docs/op-routing.md)
for agent commands and Connect bindings.

Keep the required `OP_SERVICE_ACCOUNT_TOKEN` runtime binding for
`secrets-lifecycle`'s [GitHub PAT](https://github.com/ctrl-alt-keith/secrets-lifecycle/blob/main/docs/gh-pat-cli.md),
[commit signing](https://github.com/ctrl-alt-keith/secrets-lifecycle/blob/main/docs/cloud-git-signing.md),
and [Claude review](https://github.com/ctrl-alt-keith/secrets-lifecycle/blob/main/docs/cloud-claude-review.md)
helpers. In Setup, select a reviewed `/workspace/secrets-lifecycle` checkout
containing the broker compatibility probe. Require the probe to pass before
replacing the managed entrypoint.

If authorized, bind the optional mirrors documented by those helper owners:
`CAK_CLOUD_GITHUB_PAT`, `CAK_CLOUD_SIGNING_KEY`, and
`CAK_CLOUD_CLAUDE_OAUTH_TOKEN`. Fill each selected binding from its owning
field before publication. Omit unused bindings. The owner approves
additional mirrors only when measured usage justifies exposure to task
processes. Exclude the hosted GitHub App key.
Verify a rotated mirror in a fresh task before retiring its predecessor.

## Apply and verify

The saved-environment owner performs the following checks before
[publishing](https://learn.chatgpt.com/docs/environments/cloud-environments#create-and-publish-an-environment).

1. Run `make check` and ShellCheck. Read back the saved editor's complete
   installer and workspace instructions and compare them to the build source.
   Verify the selected repositories, refs and selection mode in the editor.
   Use main-tracking unless the owner approves an exact reviewed pin.
   Compare network mode, preset and domains to `configuration.json`.
   Verify the required `OP_SERVICE_ACCOUNT_TOKEN` binding's name, status and
   injection type, the approved Connect origin and reader, and any selected
   mirror bindings.
   Use saved-editor readback for saved settings. Stop on mismatch or missing
   required readback. A binding's source reference may be unknown if the
   editor omits it.
2. Apply [build source authority](#build-source-authority) before Setup and
   again immediately before publication. Record the exact build commit as
   `source_commit`. Use the supported Setup filesystem interface to install
   `workspace-AGENTS.md` from that commit at
   `/workspace/AGENTS.md` with readable ownership and permissions.
   Verify the saved `start_skill` separately against the owning
   setup task's exact instruction text. After the last Setup change and
   immediately before publication, require
   `/workspace/.onboarding-tools/repositories.json` to match the editor-selected
   repositories and refs. Compare the installed `/workspace/AGENTS.md` to
   `workspace-AGENTS.md` and both installed `op` entrypoints to
   `enforcement/op_runtime.py` byte-for-byte at `source_commit`. Stop if the
   catalog is absent or any comparison differs.

Keep a private record per published version outside this source tree.
Retain these identities and checks:

- Saved configuration ID and published version ID.
- `source_commit`, selection mode, procedure commit, and observed main with any
  safeguard-disposition reference.
- Source hashes for `install.sh`, `workspace-AGENTS.md`, and
  `configuration.json`. Separately record editor installer and instruction
  hashes, the intended `start_skill` reference and matching readback, and the
  installed workspace instructions hash.
- Editor repository and network readbacks, exact generated catalog pins,
  the `secrets-lifecycle` checkout commit and PAT, signing and review broker
  probe result, and resolved tool versions.
- Verified binding names, required status and injection types as applicable,
  with the approved Connect origin reference and matching editor readback.
  Record whether any optional bindings were selected. Record nonsecret
  binding source references when available. Keep credential values and hashes
  out of the record, source, output and logs.

In a separately authorized task restored from that exact published version,
verify `/workspace/AGENTS.md` readability and the recorded installed hash.
Verify delivery of the saved `start_skill`, login and non-login tool resolution,
private `op` state, network and repositories. Follow
[task startup](workspace-AGENTS.md) and record the refresh result separately
from restored catalog pins, including fetched-main alignment and advancement
when applicable. Verify one approved read through each installed CLI
entrypoint. Verify that each selected binding is ready and the PAT, signing,
and review helpers work through their intended bindings, including any selected
mirrors, using the owner guidance in [Runtime credential binding](#runtime-credential-binding).

## Build source authority

These checks govern Setup builds. Ordinary new-task refresh remains owned by
[`workspace-AGENTS.md`](workspace-AGENTS.md); active or resumed checkouts stay
untouched. Do not invoke task-start refresh to realign a running Setup build.

- **Main-tracking:** the saved selection's resolved commit, generated catalog
  and clean checkout `HEAD` must also equal freshly observed hosted `main`.
  Advancement of main blocks the build until supported Setup realigns them.
- **Deliberately pinned:** an owner-authorized, explicitly reviewed full commit
  is authoritative even when main advances. Saved selection, generated catalog,
  clean checkout and reviewed build commit must agree exactly. A restored pin
  alone is not approval to use that source. The build commit must be an ancestor
  of current main; an unmerged or divergent commit blocks the build.

The checkout's branch name (`work` is valid) does not select the authority.
To advance a pin, review the new commit, change only the intended saved selection
before provisioning fresh Setup, then verify its generated catalog and clean
checkout. Preserve other settings. A stale catalog or an unprovisionable
selection blocks the build: report the platform limitation; do not move checkout
refs, hand-edit the catalog or repeatedly retarget an already-running Setup.

Record `procedure_commit` separately: it identifies the exact reviewed procedure
being applied, which can be newer than the pinned build inputs. Without newer
procedure acquisition, record `procedure_commit = source_commit`. Neither
identity changes a saved selection or authorizes publication.

Read current main with `git ls-remote origin refs/heads/main` through the existing
authorized Git route; cached `origin/main` is insufficient. Fetch that exact
object without checking it out. Compare the build commit with this main commit,
review all relevant build-input and safeguard changes, and retain their
dispositions outside Git. Unresolved changes or a required safeguard that the
pinned build would lose block the build. Main is a separate review target,
not an automatic replacement for the pin. If main advances after review, review
the new delta before continuing.
For settings or installed-file differences, the owner may accept the complete
reviewed pin as-is only if it loses no required safeguard, advance to a reviewed
pin, or choose main-tracking. Mixed-source builds are invalid. Do not silently
change settings to match either revision; all outcomes must pass steps 1 and 2.

`python3 -m enforcement.cloud_setup_source` checks these enforcement-entry
identities read-only. Supply `--selection-mode main` and `--selected-commit`
from saved-editor readback, plus `--catalog` and `--workspace-root`.
Pinned mode additionally requires `--reviewed-commit` and `--reviewed-main`.
The latter must match fresh main at both observations. The command lists all
changed paths for review; its `aligned` result verifies asserted identities,
not that the operator actually reviewed them or has publication authority.
Full saved-selection/catalog reconciliation for the other repositories remains
required by step 1. Serialize Setup work and rerun the checks after any change;
observations do not lock the editor, filesystem or remote.

### Acquire a newer procedure from an older mount

When the mounted pin lacks this procedure, use a reviewed, merged procedure
commit from the same repository. Read this complete guide from that exact commit.
Verify the selected mount's origin still identifies
`ctrl-alt-keith/ai-workflow-enforcement` before fetching.
With Python 3.12+ and the existing authorized Git transport,
[fetch](https://git-scm.com/docs/git-fetch) and
[archive](https://git-scm.com/docs/git-archive) its package into a fresh private
directory outside the mounted checkout, using the
[data extraction filter](https://docs.python.org/3.12/library/tarfile.html#tarfile.data_filter). This
changes only Git object/FETCH_HEAD metadata and the private export. It leaves
the mount, checkout `HEAD`, saved selection and generated catalog intact.

```bash
set -euo pipefail
setup_checkout='<selected-enforcement-checkout>'
setup_procedure_commit='<reviewed-merged-full-commit>'
setup_procedure_dir=$(mktemp -d)
git --no-replace-objects -C "$setup_checkout" fetch --no-tags origin "$setup_procedure_commit"
test "$(git --no-replace-objects -C "$setup_checkout" rev-parse "${setup_procedure_commit}^{commit}")" = "$setup_procedure_commit"
git --no-replace-objects -C "$setup_checkout" archive --format=tar "$setup_procedure_commit" enforcement |
  python3 -c 'import sys,tarfile; tarfile.open(fileobj=sys.stdin.buffer,mode="r|").extractall(sys.argv[1],filter="data")' "$setup_procedure_dir"
```

Run both blocks in the same retained Bash shell. Stop if fetching, identity
verification, extraction or ancestry verification fails. Run the module from
the export with its import path explicitly selected:

```bash
set -euo pipefail
: "${setup_checkout:?}" "${setup_procedure_commit:?}" "${setup_procedure_dir:?}"
setup_reviewed_main='<separately-reviewed-current-main-full-commit>'
git --no-replace-objects -C "$setup_checkout" fetch --no-tags origin "$setup_reviewed_main"
git --no-replace-objects -C "$setup_checkout" merge-base --is-ancestor "$setup_procedure_commit" "$setup_reviewed_main"
(
  cd "$setup_procedure_dir" || exit
  env -u PYTHONSAFEPATH PYTHONPATH="$setup_procedure_dir" python3 -B -m enforcement.cloud_setup_source \
    --workspace-root '<workspace-root>' --catalog '<generated-catalog-path>' \
    --selection-mode pinned --selected-commit '<saved-editor-full-commit>' \
    --reviewed-commit '<reviewed-build-full-commit>' \
    --reviewed-main "$setup_reviewed_main"
)
```

Keep all build/editor/installed-file comparisons bound to `source_commit`, not
the exported procedure's commit. Do not run the installer from the export in
place of the pinned installer. Retain the procedure identity and source review
with the publication record; dispose only of the owned private export afterward.

Exact build replay requires the prior package versions, integrity inputs and
fresh-runtime verification.
AWS CLI v2 downloads from `awscli.amazonaws.com`; its archive signature must
verify against the pinned AWS CLI Team key before extraction. A missing managed
`aws` executable triggers a clean reinstall of its managed directory.
Existing Go, Terraform, or kubectl version mismatches stop the build.
