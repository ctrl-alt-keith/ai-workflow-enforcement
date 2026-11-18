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

`OP_SERVICE_ACCOUNT_TOKEN` is a required runtime environment variable for the
`secrets-lifecycle` [GitHub PAT](https://github.com/ctrl-alt-keith/secrets-lifecycle/blob/main/docs/gh-pat-cli.md),
[commit signing](https://github.com/ctrl-alt-keith/secrets-lifecycle/blob/main/docs/cloud-git-signing.md),
and [Claude review](https://github.com/ctrl-alt-keith/secrets-lifecycle/blob/main/docs/cloud-claude-review.md)
helpers. The installer and task-start refresh do not require it.

The reviewed `secrets-lifecycle` helpers can also accept
`CAK_CLOUD_GITHUB_PAT`, `CAK_CLOUD_SIGNING_KEY`, and
`CAK_CLOUD_CLAUDE_OAUTH_TOKEN`. None is provisioned by this source. Each is a
separate, owner-managed copy of an authoritative 1Password field, injected as
a direct environment variable and available to task processes. To stage the
PAT and Claude copies, prompt the saved editor to create empty entries for
`CAK_CLOUD_GITHUB_PAT` and `CAK_CLOUD_CLAUDE_OAUTH_TOKEN`. The owner
fills them from the helpers' authoritative 1Password fields before publishing.
The helpers own those field references; this source does not sync values.
Absent bindings use 1Password; empty or malformed present bindings stop the
helper, so do not publish incomplete entries. On rotation, update the
1Password field and saved copy, republish, then verify in a fresh task before
retiring the old credential.
Add other copies only when measured usage justifies the wider exposure. The
hosted GitHub App key stays outside this set. Keep values out of Git, saved
source, output, and logs.

## Apply and verify

During [environment onboarding](https://learn.chatgpt.com/docs/environments/cloud-environments#create-and-publish-an-environment),
commands executed in the setup session prepare the filesystem that publication
snapshots for later tasks.

1. Run `make check` and ShellCheck. In the saved editor, compare the complete
   installer and workspace AGENTS bytes with the build commit selected below. Read back the
   selected repositories and refs, then compare network mode, preset, and
   custom domains with that commit's `configuration.json`. Read back the required runtime
   binding's name, required status, and injection type; record any nonsecret
   provider source reference. Record selected optional binding names and
   injection types, or none, without values. Identify whether the saved selection
   tracks `main` or deliberately pins an explicitly reviewed full commit.
   Default to main-tracking unless an owner explicitly authorizes a reviewed pin.
   Verify editor behavior rather than inferring the mode from the restored catalog.
   Stop if a required setting cannot be read back or differs; the binding's
   provider source reference may be recorded as `unknown` if not exposed.
   Runtime status and editor-local prompt previews do not establish saved
   editor state. Never record credential values or hashes.
2. Apply [build source authority](#build-source-authority) before building and
   again immediately before publication. Require the source files at the verified
   build commit; record it as `source_commit` and compare their
   hashes with the saved editor readback. A pre-merge PR head is a review
   identity, not the post-squash source commit.
   Through the supported setup-session filesystem execution interface, install
   the complete `workspace-AGENTS.md` from `source_commit` at
   `/workspace/AGENTS.md`, preserving appropriate ownership and permissions
   so the task agent can read it. The saved `start_skill` field is a separate
   agent-instruction surface: saving it does not establish that
   `/workspace/AGENTS.md` changed.
   Verify both independently; record the `start_skill` readback against the
   intended text specified by the owning environment setup task, including a
   reference to that exact text, separately from the workspace file's editor hash.
   Complete the build with the supported interface. Read exact snapshot pins
   from `/workspace/.onboarding-tools/repositories.json`; stop if it is absent
   or mismatches the editor-selected set. After the final setup or build
   filesystem change, immediately before publication, read the installed file
   back and verify exact byte equality with `workspace-AGENTS.md` at
   `source_commit`; record its SHA-256 separately from the editor readback hashes.
   Stop on mismatch. Publish with the supported interface. Publication alone
   does not prove correct file restoration or that future tasks received and
   followed their injected instructions. Store one record per published version
   outside this source tree, using this shape:

   ```json
   {
     "source_commit": "<full Git SHA>",
     "selection_mode": "<main or pinned>",
     "procedure_commit": "<exact reviewed procedure commit>",
     "main_review": {
       "commit": "<freshly observed main SHA>",
       "disposition_reference": "<review of build-input and safeguard differences, or none>"
     },
     "source_file_sha256": {
       "install.sh": "<SHA-256>",
       "workspace-AGENTS.md": "<SHA-256>",
       "configuration.json": "<SHA-256>"
     },
     "editor_readback": {
       "installer_sha256": "<SHA-256>",
       "workspace_agents_sha256": "<SHA-256>",
       "start_skill_source_reference": "<setup task reference to exact intended text>",
       "start_skill_readback": "<comparison evidence for intended startup guidance>",
       "repository_selection": "<comparison evidence>",
       "http_network_policy": "<comparison evidence>",
       "required_runtime_binding": {
         "name": "OP_SERVICE_ACCOUNT_TOKEN",
         "required": true,
         "injection_type": "<saved editor readback>",
         "provider_source_reference": "<nonsecret editor readback or unknown>"
       },
       "optional_runtime_bindings": []
     },
     "installed_workspace_agents_sha256": "<SHA-256>",
     "selected_repository_pins": [{"name": "<owner/repo>", "commit": "<SHA>"}],
     "resolved_tool_versions": {"<tool>": "<version>"},
     "saved_config_id": "<provider ID>",
     "published_version_id": "<provider ID>"
   }
   ```

3. In a separately authorized fresh-task qualification from that exact published
   version, verify restored files. Require `/workspace/AGENTS.md` to be readable
   by the task agent and its SHA-256 to match `installed_workspace_agents_sha256`.
   Verify network status, tool versions and paths in login and non-login shells,
   writable private `op` config, repository availability, and workspace AGENTS
   delivery and normal startup behavior, including injected instructions.
   Run the task-start refresh once and record its result separately,
   including fetched-main alignment and actual advancement when applicable.
   Verify selected binding readiness and helper operation.
   Catalog pins are the startup floor; refresh targets fetched `main`.

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

The installer-generated `op` launcher, executor PATH settings, runtime
credential bindings, and authenticated state are not source files. Rebuilding
an old source commit resolves then-current stable packages; exact replay needs
prior package versions and integrity inputs plus fresh-runtime verification.
AWS CLI v2 downloads from `awscli.amazonaws.com`; its archive signature must
verify against the pinned AWS CLI Team key before extraction. A missing managed
`aws` executable triggers a clean reinstall of its managed directory.
Existing Go, Terraform, or kubectl version mismatches stop the build.
