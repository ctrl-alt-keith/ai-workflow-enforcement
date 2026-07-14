# Codex Safe Recursive Removal

`codex-safe-rm` is a versioned enforcement control for narrowly approving
recursive removal of literal directory paths beneath the invocation working
directory. It exists because Codex `prefix_rule` entries can match a fixed
argument prefix but cannot validate an arbitrary number of suffix operands.

The reviewed source is `enforcement/safe_rm.py`. For a clean installation,
`make install` reads the immutable Git blob at
`HEAD:enforcement/safe_rm.py` and copies those exact bytes to the stable
Codex-facing path
`~/.local/bin/codex-safe-rm`; the installed executable is not maintained as an
independent script.

## Threat Model

The control rejects absolute paths, `.` and `..` components, `.git` at any
depth, shell variables and substitutions, tilde expansion, globs, brace
expansion, redirections, pipes, chaining syntax, quotes, backticks, whitespace,
and other characters outside its small literal pathname alphabet. It also
guards against a relative path escaping through a symlinked component.

The helper never runs a shell and never falls back to ordinary pathname-based
recursive deletion. It pins the invocation working directory with a file
descriptor, opens existing parent components fd-relatively without following
symlinks, and removes the leaf with Python's symlink-resistant, fd-relative
`shutil.rmtree` implementation.

## Guarantees

- The only deletion form is `codex-safe-rm -rf -- TARGET [TARGET ...]`.
- Every operand is validated and preflighted before the first removal.
- Targets must be literal relative directory paths contained beneath the
  invocation working directory.
- A target resolving to the working directory or outside it is rejected.
- `.git`, including nested forms such as `repo/.git/objects`, is rejected.
- Existing top-level targets must be real directories, not files or symlinks.
- Missing targets are successful no-ops.
- The runtime must support fd-relative operations and report
  `shutil.rmtree.avoids_symlink_attacks = True`; otherwise the helper fails
  closed with a diagnostic.
- A symlink inside a removed tree is unlinked rather than traversed.

## Non-Guarantees

The helper enforces containment and invocation safety. It does not decide
whether a directory is genuinely disposable. Names such as `src`, `docs`,
`tests`, or `lib` receive no subjective special treatment. Reviewers and
callers remain responsible for deciding whether an otherwise valid target may
be deleted.

Deletion of multiple directory operands is not transactional. Although all
operands are validated first, an operational failure after removal begins can
leave earlier operands deleted. Concurrent hostile mutation may also cause the
operation to fail closed; the fd-relative design prevents the helper from
silently falling back to a less safe removal method.

## Codex Rule Delegation

Use the rule fixture in `examples/codex-safe-rm.rules`. Direct `rm` remains
prompt-gated, while the stable helper path is allowed only with the fixed
`-rf --` prefix:

```python
prefix_rule(pattern=["rm"], decision="prompt")
prefix_rule(
    pattern=["/Users/keith/.local/bin/codex-safe-rm", "-rf", "--"],
    decision="allow",
)
```

The rule engine still cannot inspect the remaining operands. The reviewed
helper is the validation boundary for that dynamic suffix.

## Review, Install, And Verify

Changes follow the repository's normal PR review and `make check` validation
flow. After a reviewed change is merged and the enforcement checkout is clean:

```sh
make install
make verify-install
```

The default destination is `~/.local/bin/codex-safe-rm`. Installation copies
the reviewed source to a temporary file in the destination directory, sets
permissions, flushes and fsyncs its contents, and atomically replaces the
executable. Metadata is prepared the same way and published atomically, then
the destination directory is fsynced where supported.

The executable and metadata are two separate files, so they cannot be
published as one fully transactional unit. `verify-install` fails closed on a
missing file, ownership or version mismatch, digest drift, or any executable
and metadata inconsistency.

Installation metadata records the source commit, source and installed SHA-256,
control version, and `source_dirty`. A dirty source checkout is refused unless
separately authorized:

```sh
make install ALLOW_DIRTY=1
```

The clean path never installs bytes read from the working tree: it pins `HEAD`
and installs the Git blob addressed by that commit, so a concurrent working-tree
change cannot be mislabeled as clean committed source. With `ALLOW_DIRTY=1`,
the installer snapshots the working-tree bytes, hashes that exact snapshot,
compares it with the pinned Git blob, records `source_dirty` accordingly, and
fails if `HEAD` changes during capture.

`FORCE=1` only authorizes destination replacement. It does not authorize a
dirty source checkout:

```sh
make install FORCE=1
make install FORCE=1 ALLOW_DIRTY=1
```

## Uninstall And Rollback

```sh
make uninstall
```

Normal uninstall removes only an installation whose executable and metadata
form a recognized, digest-consistent pair. An unrelated or modified
destination is preserved unless removal is explicitly forced:

```sh
make uninstall FORCE=1
```

Rollback consists of checking out the reviewed enforcement revision to
restore, rerunning `make install`, and then running `make verify-install`.

## Platform Constraints

The control requires Python 3.11 or newer in practice, a POSIX-style runtime
with directory file descriptors, `O_DIRECTORY`, and `O_NOFOLLOW`, and a Python
`shutil.rmtree` implementation that advertises symlink-attack resistance.
Unsupported runtimes fail closed.
