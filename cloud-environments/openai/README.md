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
for agent commands and origin requirements.
Configure both required Connect bindings under the saved editor's
[Environment variables](https://learn.chatgpt.com/docs/environments/cloud-environments#configure-environment-variables-and-network-secrets).

- `CAK_CONNECT_HOST` is a required direct environment variable for the approved
  HTTPS origin.
- `CAK_CONNECT_TOKEN` is a required vault-backed direct environment variable for
  the existing approved reader. Match the binding's source to the separately
  approved vault item and field. If the editor omits the source, confirm it
  with the credential owner. Reuse a matching binding. Stop if the source
  differs or cannot be confirmed.

Keep the required `OP_SERVICE_ACCOUNT_TOKEN` runtime binding for the explicit
broker route used by `secrets-lifecycle`'s [GitHub PAT](https://github.com/ctrl-alt-keith/secrets-lifecycle/blob/main/docs/gh-pat-cli.md),
[commit signing](https://github.com/ctrl-alt-keith/secrets-lifecycle/blob/main/docs/cloud-git-signing.md),
and [Claude review](https://github.com/ctrl-alt-keith/secrets-lifecycle/blob/main/docs/cloud-claude-review.md)
helpers. Require the synthetic compatibility probe from the verified
Secrets Lifecycle build source to pass before replacing the managed entrypoint.

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
   installer and workspace instructions and compare them to the reviewed `main`
   sources. Compare the separately executed installer for a branch build to
   the [reviewed branch](#publish-a-build-from-a-hosted-branch).
   Verify the selected repositories and refs in the editor. Keep Enforcement
   and Secrets Lifecycle on `main` tracking.
   Compare network mode, preset and domains to `configuration.json`.
   Verify the names, required status, and injection types of the
   [required runtime bindings](#runtime-credential-binding), the approved
   Connect origin and reader source, and any selected mirror bindings.
   Use saved-editor readback for saved settings. Stop on mismatch or missing
   required readback. Other bindings' source references may be unknown if the
   editor omits them.
2. Apply [build source authority](#build-source-authority) before Setup and
   again immediately before publication. Use the supported Setup filesystem
   interface to install `workspace-AGENTS.md` from the Enforcement build commit at
   `/workspace/AGENTS.md` with readable ownership and permissions.
   Verify the saved `start_skill` separately against the owning
   setup task's exact instruction text. After the last Setup change and
   immediately before publication, require
   `/workspace/.onboarding-tools/repositories.json` to match the editor-selected
   repositories and restored baseline. Compare the installed `/workspace/AGENTS.md`,
   `cloud-task-start.py`, and both installed `op` entrypoints byte-for-byte to
   `workspace-AGENTS.md`, `enforcement/cloud_task_start.py`, and
   `enforcement/op_runtime.py` at the Enforcement build commit. Stop if the
   catalog is absent, any comparison differs, or `.onboarding-tools/task-start.ready`
   exists in the Setup workspace. Require `.onboarding-tools/cloud-build.json`
   to match the final check's source evidence.

Keep a private record per published version outside this source tree.
Retain these identities and checks:

- Saved configuration ID and published version ID.
- Installer source evidence for both repositories, including build commits
  and the unchanged catalog hash. Retain source review and saved readback separately.
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

Use the existing Setup with both build repositories selected on `main`. Keep
the platform-generated catalog unchanged as the restored checkout baseline.
Verify the complete repository selection against saved-editor readback and
retain the baseline before advancing the build checkouts. Setup does not use
the [new-task refresh](workspace-AGENTS.md).

In Setup only, verify the Enforcement and Secrets Lifecycle mount roots,
origin identities and clean checkouts. Fetch `refs/heads/main` through each
existing authenticated Git remote. Require both the catalog commit and the
current checkout to be in the fetched history, with the catalog commit in the
checkout's history. Review build-input and safeguard changes against the last
published source. Resolve any required safeguard changes before fast-forwarding
only those two checkouts with `git merge --ff-only FETCH_HEAD`. Stop on missing
history, local changes, divergence, or an unverified origin.

Use the reviewed Enforcement `main` installer and apply step 1. The installer
runs `enforcement.cloud_setup_source --selection-mode main` with the generated
catalog and workspace root. It requires both clean checkouts to equal hosted
`main`, verifies the restored baselines are ancestors, and compares source
evidence again before changing managed CLI entrypoints. Repeat that read-only
check before publication and require the installer evidence to match. Treat
the returned commit IDs as evidence. If hosted main advances or Setup replaces
a build checkout, reacquire and review the build sources before continuing.
Serialize Setup work and repeat the checks after changes.

Managed `op` requests in a restored task require successful completion of the
[new-task refresh](workspace-AGENTS.md). Verify a restored task starts without
`task-start.ready`, blocks broker reads before refresh, and enables the managed
paths only after refresh succeeds. This checks the managed entrypoints. The
startup instructions also govern helpers using optional credential mirrors.

### Publish a build from a hosted branch

For one approved build, keep both Setup checkouts on verified `main`. From the
verified Enforcement origin, fetch `refs/heads/<branch>` and record the exact
`FETCH_HEAD` commit. Review that tip, including the installer, safeguards, and
changes from the previous publication. Verify the branch's tests for both
managed CLI startup gates. Pass `--build-branch <branch>` and
`--reviewed-branch-tip <observed-commit>` to the same source check before running
any branch code. The check verifies the live branch tip and requires its
workspace instructions and startup launcher to match `main` byte-for-byte.

In a private Setup directory, write the reviewed commit's
`cloud-environments/openai/install.sh` with `git show <observed-commit>:cloud-environments/openai/install.sh`.
Run that file with `bash` and the same two arguments. The installer verifies
its file against the hosted tip and reads the router and launcher from that
Git object. Keep the saved `main` installer and repository selections. Apply
the normal publication checks with the branch arguments, comparing installed
files and build evidence to the branch build.

In the authorized restored task, verify that the recorded branch build and
installed files survived publication, the catalog matches the Setup baseline,
and the repository refresh still reaches `main`. The launcher releases managed
`op` only after the refreshed Secrets Lifecycle synthetic probe passes against
the installed branch router. If the branch changes the startup contract or
needs branch-only helper code, stop. For a later default build, run the reviewed
`main` installer without branch arguments and verify its installed evidence.

## Audit an older pinned build

### Acquire a newer procedure from an older mount

For an older published build's pinned-source audit, use a reviewed, merged procedure
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

For the audit, bind build/editor/installed-file comparisons to the reported
`source_commit`. Retain the exported procedure identity separately and dispose
of the owned private export after the audit. Use the export only for verification.

## Installer verification

Exact build replay requires the prior package versions, integrity inputs and
fresh-runtime verification.
AWS CLI v2 downloads from `awscli.amazonaws.com`; its archive signature must
verify against the pinned AWS CLI Team key before extraction. A missing managed
`aws` executable triggers a clean reinstall of its managed directory.
Existing Go, Terraform, or kubectl version mismatches stop the build.
