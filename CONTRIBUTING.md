# Contributing to Steppy

Thanks for helping improve Steppy. This guide covers the dev setup and the few
invariants a change must respect. Everyone taking part follows the
[code of conduct](CODE_OF_CONDUCT.md).

## Dev setup

```bash
# Python package (editable install also regenerates the bundled template tree)
python -m pip install -e '.[report,retry,bundle]'

# Run the unit suite (tests/e2e/ is generated output — skip it)
python -m pytest tests/ --ignore=tests/e2e -q

# Lint + format gate
uv run ruff check . && uv run ruff format --check .

# Player workspace (TypeScript / Lit / Vitest)
npm --prefix packages/player install
npm --prefix packages/player run build
npm --prefix packages/player test
```

You'll also want Playwright browsers for anything that runs generated tests:

```bash
playwright install chromium --with-deps
```

The in-app tour browser suites are opt-in locally (CI runs them in the
`tour-e2e` job). They need the player build above:

```bash
STEPPY_TOUR_E2E=1 python -m pytest tests/integration/test_tour_replay_it.py tests/integration/test_tour_player_it.py -q
```

The Angular tour adapter has its own workspace (CI: the `angular-adapter` job),
and its browser suite runs against the Angular demo (CI: `tour-angular-e2e`),
which needs the player and the adapter built first:

```bash
npm --prefix packages/angular ci
npm --prefix packages/angular test
npm --prefix packages/angular run build
npm --prefix fixtures/tour-demo-angular/app ci
npm --prefix fixtures/tour-demo-angular/app run build
STEPPY_TOUR_ANGULAR_E2E=1 python -m pytest tests/integration/test_tour_angular_it.py -q
```

**Run ruff through `uv run`, not whatever is on your PATH.** `pyproject.toml`
pins `required-version = ">=0.9"` and resolves ruff from the `dev` dependency
group, because the formatter changed how it wraps a long `assert x, "msg"` in
0.9 — an older ruff does not skip that rule, it reformats the other way, so one
`ruff format` silently rewrites files the repo just formatted and the churn looks
like your change. The lint rule set is pinned explicitly for the same reason: ruff
0.16 widened its defaults, and the unchanged tree went from clean to 745 errors on
upgrade. Widening it is a decision to make in a diff, not one that arrives with a
version bump.

## The one rule about templates

`templates/` at the repo root is the **single source of truth** for the Jinja2
templates. The bundled copy at `src/steppy/templates/` is **generated** from it by
`hatch_build.py` on every build (wheel, sdist, and `pip install -e .`) and is
gitignored — **never edit or commit it**. Edit the root `templates/` tree; the
bundled copy regenerates automatically.

The same rule applies to `skills/` (the packaged Claude skills that `steppy init`
installs): edit `skills/<name>/SKILL.md` at the root; `src/steppy/skills/` is the
generated, gitignored copy. Keep the skills free of any private/client domain —
`tests/test_skills_neutralized.py` enforces this. Repository-wide,
`tests/test_no_private_names.py` does the same for every published file; write
examples in the neutral `editor`/`reviewer`/`customer`/`invoice`/`product` domain.

`CHANGELOG.md` is likewise vendored into the package: the build hook copies the
repo-root `CHANGELOG.md` to `src/steppy/CHANGELOG.md` (gitignored) so `steppy
update` can print release highlights at runtime. Edit only the root file.

## Guard-test invariants (keep these green)

Several tests mechanically enforce architectural invariants. A PR must keep them
passing (or update them deliberately, with the reasoning in the PR):

- **Byte-identical `run`** (`tests/test_run_byte_identical.py`) — the default
  `steppy run` (no flags / `--workers 1`) must produce an unchanged pytest command
  and `STEPPY_*` env. Any new flag/env/report/trace behavior must be **off by
  default**.
- **No lazy-module imports on the default path** —
  `test_no_{playwright,parallel,coverage,video,bundle,cloud,migrate,report,tours}_import_on_default_path.py`.
  Keep `coordinator`, `worker`, `_parallel/*`, `coverage/*`, `video`/`tts`,
  `bundle/*`, `cloud/*`, `migrate/*`, `report/*` and `tours/*` lazy-imported. The
  bundle guard also asserts a bare CLI import pulls in neither `zstandard` nor
  `tarfile`. `models.py` imports only stdlib leaves (`assertions`, `durations`,
  `exclusive`, `locales`, and for in-app tours `naming`, `tour_spec`, `testids`,
  `buildid` — `test_tour_leaves_stdlib_only.py` checks those four).
- **Config schema drift** (`tests/test_config_schema.py`) — after changing
  `SteppyConfig`, regenerate `steppy-config.schema.json`:
  ```bash
  python -c "from steppy.config_schema import schema_json; open('steppy-config.schema.json','w',encoding='utf-8',newline='\n').write(schema_json())"
  ```
  The emitted schema is canonicalised (`config_schema._canonicalise`) so its bytes
  depend on the models and not on which pydantic 2.x you have installed. If a
  future pydantic introduces another purely-cosmetic difference, add a rule there
  — do not reach for it to paper over a real schema change.
- **Bundle contract drift** (`tests/test_bundle_schema.py`) — the results-bundle
  manifest is a **published wire contract**. After changing anything in
  `src/steppy/contracts/`, regenerate the committed schema:
  ```bash
  python -c "from steppy.contracts.bundle import schema_json; open('contracts/bundle-manifest.schema.json','w',encoding='utf-8',newline='\n').write(schema_json())"
  ```
  Those models are `extra="allow"`, deliberately inverting the `extra="forbid"`
  used everywhere else: config protects a hand-written file where a typo must fail
  loudly, a wire format needs the opposite. Widening a **required** field to
  optional is a contract-version decision — an older consumer rejects the whole
  manifest, not just the row.
- **In-app tours contract** (`tests/test_tours_schema.py`, `tests/test_tours_golden.py`) —
  `contracts/tour.schema.json` and `contracts/tours-manifest.schema.json` are
  generated from `src/steppy/contracts/tours.py`:
  ```bash
  python -c "from steppy.contracts.tours import manifest_schema_json, tour_schema_json; open('contracts/tours-manifest.schema.json','w',encoding='utf-8',newline='\n').write(manifest_schema_json()); open('contracts/tour.schema.json','w',encoding='utf-8',newline='\n').write(tour_schema_json())"
  ```
  `contracts/fixtures/tours/valid/` is the compiler's real output for
  `fixtures/tour-demo/project`; regenerate it with
  `STEPPY_REGEN_TOUR_GOLDEN=1 python -m pytest tests/test_tours_golden.py`, and
  update `cases.json` with it — both the Python check and the player's validator
  run every case.
- **Shared names across Python and TypeScript** (`tests/test_tour_contract_sync.py`,
  `tests/test_tour_replay_helpers_mirror.py`) — `naming.py` / `tour_spec.py` ↔
  `packages/player/src/ui/names.ts` / `src/tour/contract.ts` ↔
  `templates/_steppy_tours.py`. One literal per line, no backslashes in the TS
  patterns; change all copies together.
- **Tour docs stay true** (`tests/test_tours_docs_sync.py`) — `docs/tours.md` must
  name every config key, annotation, event, error reason and `::part`, and its
  `<!-- golden: … -->` blocks must equal the files they name.
- **Vendored player bundles** (`tests/test_player_bundle_files.py`, plus the CI
  `cmp`) — exactly three bundles are vendored (guide player, legacy cursor, video
  renderer); the tour overlay never is. A change under `packages/player/src/ui/`
  changes the guide and video bundles: rebuild, re-vendor, and refresh
  `fixtures/demo-guide/public/assets/editor/steppy-player.js` with them. The
  guide's stylesheet hash is pinned (`test/ui/guide-styles-stable.test.ts`).
- **Tour overlay size budget** (`npm --prefix packages/player run size:tour`, CI
  `player` job) — growth past the budget is a decision, not a bump.
- **npm packages stay publishable** (`npm --prefix packages/player run check:pack`,
  `tests/test_versions_aligned.py`) — every file `package.json` points at is packed,
  the packages carry the root LICENSE, and the versions move together.
- **The Angular adapter follows the overlay** (`tests/test_angular_adapter_sync.py`)
  — its tag, event names and property writes equal the overlay's, it reaches the
  overlay only through a dynamic `import()`, and neither it nor its demo uses
  `CUSTOM_ELEMENTS_SCHEMA`, `bypassSecurityTrust*` or `innerHTML`.
- **Byte-mirrored helpers** — `totp._totp_now` ↔ `conftest.py.j2` (guard:
  `tests/test_totp.py`) and `captures.sanitise_nodeid` ↔ `conftest.py.j2`'s
  `_sanitise_nodeid` (guard: `tests/test_captures.py`). Both name the same things
  on disk from two processes; apply any change to both copies.
- **Version alignment** (`tests/test_versions_aligned.py`) — bump `pyproject.toml`,
  `src/steppy/__init__.py`, `packages/steppy/package.json`, and
  `packages/player/package.json` together.
- **Example scenarios validate** (`tests/test_example_scenarios_validate.py`) — the
  shipped `scenarios/` must always parse against the repo `config.yaml`.

## Adding a scenario action / config block

- New frontmatter/config blocks are just Pydantic models (`src/steppy/models.py`);
  the parser passes nested dicts through as `**kwargs`. Add validators to the model,
  not to the parser.
- Codegen for a new action goes in `templates/test_scenario.py.j2` (root tree only).
- If the action appears in the interactive guide manifest, add it to the `.strict()`
  Zod enum in `packages/player/src/manifest/schema.ts` and rebuild + re-vendor the
  player bundle.
- Say what the action becomes in an in-app tour: one row in `ACTION_TOUR_KIND`
  (`src/steppy/tours/actions.py`); `tests/test_tour_action_table.py` fails if a
  `VALID_ACTIONS` member has none.
- Add a minimal sample body to `_ACTION_BODIES` in
  `tests/test_step_events_codegen.py`. Per-step event markers are emitted by the
  step loop rather than per action, so a new action inherits them for free — that
  table is what proves it, and `test_action_bodies_cover_every_action` fails if you
  forget.

CLAUDE.md's **"Adding a new action"** section is the canonical checklist; the notes
above are the parts a first-time contributor trips over.

See [CLAUDE.md](CLAUDE.md) for the fuller architecture notes and
[STEPPY-REFERENCE.md](STEPPY-REFERENCE.md) for the scenario format.

## Releasing

A release is a tag. Bump the version in `pyproject.toml`, `src/steppy/__init__.py`
and every `packages/*/package.json` (`tests/test_versions_aligned.py` fails until
they agree, and until the CI templates pin the new tag), write the CHANGELOG
section, then push `vX.Y.Z`. The release workflow builds and vendors the player,
builds and checks the wheel and sdist, and creates the GitHub Release. Two jobs
after it publish to the registries, and each stays off until its one-time setup
is done.

**PyPI (`steppy-dev`).** Publishing goes through Trusted Publishing, so no token
is stored anywhere. The publisher names the repository, so set it up once the
repository lives under `Steppy-dev`.

1. On <https://pypi.org/manage/account/publishing/> (PyPI accounts need 2FA),
   add a *pending* publisher: project `steppy-dev`, owner `Steppy-dev`, repository
   `steppy`, workflow `release.yml`, environment `pypi`. A pending publisher does
   not reserve the name; the first upload does.
2. In the GitHub repository, set the Actions variable `PYPI_PUBLISH` to `true`.
   The `pypi` environment is created on first use; giving it required reviewers
   makes each upload wait for an approval.

To rehearse, build locally and upload to TestPyPI with a TestPyPI token: `uv build`,
then `uv publish --publish-url https://test.pypi.org/legacy/`. Neither index
accepts the same version twice, even after it is deleted.

**npm (`@steppy/player`, `@steppy/angular`).** Create the `steppy` organisation on
npmjs.com (free for public packages), make a granular access token that can
publish to it, and store it as the `NPM_TOKEN` Actions secret. The player is
published first, because the adapter names it as a peer at the same version.

## License of contributions

Steppy is licensed under the [Apache License 2.0](LICENSE). Unless you state
otherwise, any contribution you intentionally submit for inclusion is licensed
under the same terms, without any additional conditions (Apache-2.0 §5). You
keep the copyright on your contribution; there is no separate CLA to sign.

Security issues go through the private channel in [SECURITY.md](SECURITY.md),
not a public issue or pull request.
