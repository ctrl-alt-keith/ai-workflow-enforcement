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
fills them from `ai-automation-private/k2-cloud-github/token` and
`ai-automation-private/claude-code-review/token`, respectively, before
publishing. This source records names and references; it does not sync values.
Absent bindings use 1Password; empty or malformed present bindings stop the
helper, so do not publish incomplete entries. On rotation, update the
1Password field and saved copy, then verify before retiring the old credential.
Add other copies only when measured usage justifies the wider exposure. The
hosted GitHub App key stays outside this set. Keep values out of Git, saved
source, output, and logs.

## Apply and verify

1. Run `make check` and ShellCheck. In the saved editor, compare the complete
   installer and workspace AGENTS bytes with this commit. Read back the
   selected repositories and refs, then compare network mode, preset, and
   custom domains with `configuration.json`. Read back the required runtime
   binding's name, required status, and injection type; record any nonsecret
   provider source reference. Record selected optional binding names and
   injection types, or none, without values. Select `main` where the editor
   supports a moving ref; otherwise resolve its current commit for this build.
   Verify editor behavior rather than assuming `main` advances automatically.
   Stop if a required setting cannot be read back or differs; the binding's
   provider source reference may be recorded as `unknown` if not exposed.
   Runtime status and editor-local prompt previews do not establish saved
   editor state. Never record credential values or hashes.
2. For the build, require a clean selected `ai-workflow-enforcement` checkout
   whose `HEAD` matches freshly observed hosted `main`, regardless of its local
   branch name (`work` is valid). Compare `git rev-parse HEAD` with
   `git ls-remote origin refs/heads/main`, not cached `origin/main`. Require
   these source files at that `HEAD`; record it as `source_commit` and compare
   file hashes with the saved editor readback. A pre-merge PR head is a review
   identity, not the post-squash source commit.
   Build and publish with the supported interface. Read exact snapshot pins
   from `/workspace/.onboarding-tools/repositories.json`; stop if it is absent
   or mismatches the editor-selected set. Store one record per published version
   outside this source tree, using this shape:

   ```json
   {
     "source_commit": "<full Git SHA>",
     "source_file_sha256": {
       "install.sh": "<SHA-256>",
       "workspace-AGENTS.md": "<SHA-256>",
       "configuration.json": "<SHA-256>"
     },
     "editor_readback": {
       "installer_sha256": "<SHA-256>",
       "workspace_agents_sha256": "<SHA-256>",
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
     "selected_repository_pins": [{"name": "<owner/repo>", "commit": "<SHA>"}],
     "resolved_tool_versions": {"<tool>": "<version>"},
     "saved_config_id": "<provider ID>",
     "published_version_id": "<provider ID>"
   }
   ```

3. In a fresh task from that exact published version, verify restored files,
   network status, tool versions and paths in login and non-login shells,
   writable private `op` config, repository availability, and workspace AGENTS
   delivery. Run the task-start refresh once and record its result separately,
   including fetched-main alignment and actual advancement when applicable.
   Verify selected binding readiness and helper operation.
   Catalog pins are the startup floor; refresh targets fetched `main`.

The installer-generated `op` launcher, executor PATH settings, runtime
credential bindings, and authenticated state are not source files. Rebuilding
an old source commit resolves then-current stable packages; exact replay needs
prior package versions and integrity inputs plus fresh-runtime verification.
Existing Go, Terraform, or kubectl version mismatches stop the build.
