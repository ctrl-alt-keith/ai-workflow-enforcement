# Cloud environment source

These files record the intended nonsecret setup for the saved ctrl-alt-keith
cloud environment. The Git commit containing them is the setup source version.
The published environment version is a separate provider identity.

- `install.sh`: saved installer command body. It resolves stable Claude, GitHub
  CLI, and 1Password CLI releases during a build.
- `workspace-AGENTS.md`: workspace-level `/workspace/AGENTS.md` delivered to
  fresh tasks. It invokes safe refresh once at a new isolated task boundary.
- `repositories.json`: selected repository catalog and snapshot commits
  observed in the published runtime. The provider's selection is saved
  separately.
- `configuration.json`: restricted HTTP policy, selected preset, and custom
  domain allowlist observed in the published runtime.

The installer generates the `op` launcher and executor PATH configuration. Keep
its private 1Password configuration behavior and the inherited PATH links when
rebuilding. The generated files, runtime credential bindings, authenticated
state, and secret values are not setup source.

## Review and publish

1. Review one source commit and run the repository's `make check`. Check
   `install.sh` with ShellCheck where available. Record the commit and SHA-256
   of each source file in a nonsecret build record outside this source tree.
2. In the saved environment editor, read back the complete installer and
   workspace startup file. Compare their bytes with this commit. Read back the
   selected repository names and any exposed refs against `repositories.json`.
   Read back restricted mode, preset, and custom domains against
   `configuration.json`. Stop on an unavailable readback or a mismatch; do not
   infer editor state from a task runtime or an editor-local prompt preview.
3. Build from that saved configuration, then record the resolved tool versions
   and the selected catalog snapshot pins. Publish manually through the
   supported environment interface. Record its actual saved configuration ID
   and published version ID from provider readback, never a predicted value.
4. In a fresh task from that published version, compare the source files with
   restored files and the custom network settings with runtime status. Verify
   login and non-login tool paths and versions, writable private `op` config,
   selected repository availability, and prompt delivery of workspace AGENTS.
   Run the startup refresh once and record its result separately from the
   build record. Check that refreshed checkouts match fetched `main`; an
   all-current run does not prove advancement. Follow Playbook routing.

The build record needs: source commit and file hashes; selected repository
names and exact snapshot commits; resolved tool versions; saved configuration
ID; published environment version ID; and the editor, build, and fresh-task
comparison results. Keep each task's refresh report separate. Compare this
record with the reviewed commit, saved editor state, and published runtime to
report drift. The provider may expose some settings only in its editor; record
the actual readback or mark that comparison unverified.

Rebuilding an older source commit still resolves then-current stable packages.
Exact replay requires retained version and integrity inputs and a fresh-runtime
verification; a source commit alone does not guarantee an exact rollback.
Publication and rollback remain operator actions outside this repository.
