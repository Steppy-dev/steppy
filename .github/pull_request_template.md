## What this changes

<!-- What the change does and why. Link the issue it closes, if any. -->

## Checklist

- [ ] `python -m pytest tests/ --ignore=tests/e2e -q` passes
- [ ] `ruff check . && ruff format --check .` passes
- [ ] Templates were edited in `templates/` only, never in the generated `src/steppy/templates/`
- [ ] If `packages/player/src/` changed: `npm --prefix packages/player test` passes, and the bundles were rebuilt and re-vendored into `src/steppy/_player_bundle/`
- [ ] A user-visible change has an entry under `[Unreleased]` in `CHANGELOG.md`, and the docs and skills say the same thing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the guard tests and what each one protects. By opening this pull request you agree that your contribution is licensed under the Apache License 2.0, as described there.
