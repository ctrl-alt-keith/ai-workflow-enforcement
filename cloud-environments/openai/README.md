# OpenAI cloud environment source

The Git commit of this directory versions the intended nonsecret setup for the
saved ctrl-alt-keith OpenAI cloud execution environment.
The installer supports Linux x86_64 only.
`install.sh` is the saved build script; `workspace-AGENTS.md` is delivered as
`/workspace/AGENTS.md`; `repository-selection.json` records the selected set
and desired `main` channel;
`configuration.json` records restricted mode, preset, and custom domains.
The source commit does not identify a saved or published environment version.

## Apply and verify

1. Run `make check` and ShellCheck. In the saved editor, compare the complete
   installer and workspace AGENTS bytes with this commit. Compare selected
   repository names and mounts with `repository-selection.json`, and network
   mode, preset, and custom domains with `configuration.json`. Select `main`
   where the editor supports a moving ref; otherwise resolve its current
   commit for this build. Verify editor behavior rather than assuming `main`
   advances automatically. Stop if an editor value cannot be read back or
   differs. Runtime status and editor-local prompt previews do not establish
   saved editor state.
2. For the build, require a clean selected `ai-workflow-enforcement` checkout
   on integrated `main` containing these source files. Record its exact `HEAD`
   as `source_commit` and compare file hashes with the saved editor readback.
   A pre-merge PR head is a review identity, not the post-squash source commit.
   Build and publish with the supported interface. Read exact snapshot pins
   from `/workspace/.onboarding-tools/repositories.json`; stop if it is absent
   or mismatches the selected set. Store one record per published version
   outside this source tree, using this shape:

   ```json
   {
     "source_commit": "<full Git SHA>",
     "source_file_sha256": {
       "install.sh": "<SHA-256>",
       "workspace-AGENTS.md": "<SHA-256>",
       "repository-selection.json": "<SHA-256>",
       "configuration.json": "<SHA-256>"
     },
     "editor_readback": {
       "installer_sha256": "<SHA-256>",
       "workspace_agents_sha256": "<SHA-256>",
       "repository_selection": "<comparison evidence>",
       "http_network_policy": "<comparison evidence>"
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
   Catalog pins are the startup floor; refresh targets fetched `main`.

The installer-generated `op` launcher, executor PATH settings, runtime
credential bindings, and authenticated state are not source files. Rebuilding
an old source commit resolves then-current stable packages; exact replay needs
prior package versions and integrity inputs plus fresh-runtime verification.
Existing Go, Terraform, or kubectl version mismatches stop the build.
