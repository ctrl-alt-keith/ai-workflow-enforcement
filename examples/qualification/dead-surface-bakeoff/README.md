# Dead-surface bake-off evidence packs

These fixtures let the two CAK-283 hosted variants exercise the same bounded
Dead Surface Sweep semantics without manufacturing a production finding or
creating duplicate visible pull requests.

- `clean/` contains one direct implementation and no redundant surface.
- `controlled/` contains a canonical implementation plus an unused wrapper
  that only forwards to it. Removing the wrapper is the expected staged
  candidate.
- `ambiguous/` contains a compatibility adapter whose external consumer status
  is deliberately unresolved. The evidence is insufficient to remove it, so
  rejection is the expected result.

The fixtures are not imported by repository code. They exist only for manual,
staged qualification and must be described as fixtures in any preview output.
