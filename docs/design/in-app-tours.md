# In-app tours: design note (M0)

Status: accepted for v1, updated as built. Date: 2026-10-10.

Scope: M0 (this note), M1 (compiler), M2 (overlay and plain-HTML demo), M4 (CI
replay) and M5 (remote target and docs) in this repository. M3, the Angular
adapter, was planned for a separate repository and was built here instead
(`packages/angular`, see §11 and DV51). The first consumer is an Angular 21 +
PrimeNG application.

User-facing documentation: [docs/tours.md](../tours.md). This note records
what the code said before the work started, the decisions the spec delegated,
every deviation from the spec, and what is left.

## 1. Summary

- Tours are compiled by `steppy generate` and a new `steppy tours`;
  `steppy build` only republishes the compiled tree.
- A step stores one test-id value, never a selector; anything else is a
  compile error with a fix.
- The tour data is JSON under a versioned contract (`schema: 1`): a strict
  pydantic producer check, published open JSON Schemas, and a hand-written
  validator in the overlay, all run over one shared case table.
- Each tour set is locked to a build id (`git-sha`, a literal or an env
  variable), and its files are pinned by SHA-384 SRI hashes.
- The overlay is a fourth bundle of `packages/player`, `@steppy/player/tour`
  (`<steppy-tour-host>`), with no zod and no guide code; it shares
  tokens, strings, Markdown and placement with the guide through `src/ui/`.
- Each tour gets a replay, `test_<slug>__tour`, appended to its scenario's own
  test file; it performs the scenario's exact action code between tour checks.
- Generated tests set a kill switch so an app-started tour never covers what an
  ordinary test clicks; replays lift it for themselves.
- With tours disabled, every generated test, conftest and build output is
  byte-identical to before, except the re-vendored guide and video bundles
  (shared UI code) and the regenerated config schema.
- A static guide build republishes the tree at `<site>/tours/<build-id>/` for
  remote mode, with CORS headers in `_headers`.
- Browser behaviour is covered by opt-in Chromium suites run in a new CI job.

## 2. What the code says

Answers to the spec's §10 questions, as found before the work (line numbers as
of this commit).

### 2.1 Where compiler targets live

Two pipelines exist:

- `generate`: `cli.py:273` calls `SteppyGenerator.generate_all`
  (`generator.py:535`), rendering Jinja from the project's `templates/` or the
  bundled copy (`_templating.py`).
- `build`: `cli.py:2088` calls `BuildAssembler.build_all` (`build.py:738`). It
  reads a TypeScript config through `npx tsx`, requires captured screenshots
  (`build.py:718`), and deletes its `outputDir` and `assetsDir` before writing
  (`build.py:776`). Its targets are `static` and `angular`; `stepbystep`,
  `interactive` and `video` are per-guide modes.

So tours cannot be a build mode: compiling them must not need screenshots or
Node, and their app-assets output must survive builds. They are compiled by
`generate`/`tours`, and `build` copies the result.

### 2.2 How scenarios are parsed

- Frontmatter becomes `Scenario(**metadata)` (`parser.py:56`), with
  `extra="forbid"` models.
- Steps are `### N. Title` headings (`parser.py:254`) followed by `- key: value`
  lines; indented blocks are YAML. Step fields are checked per action by
  `_UNIVERSAL_FIELDS` / `_ALLOWED_FIELDS` / `_FIELD_ALIASES` in `models.py`.
- The scenario directory scan is flat; the slug is the file stem.

New keys therefore need model fields and table entries, nothing in the parser.

### 2.3 Instruction text versus fixture values

Values live in action fields (`value`, `fields`, `option`); instruction text
lives only in `guide-text` (`models.py:53`, `GuideText.get` at `:100`). The
guide never interpolates text, but the interactive guide manifest carries
values, so it is not reused for tours. Text may still repeat a value, hence the
`fixture-leak` warning and the `tour-text` override.

### 2.4 The test-id attribute

`data-testid` is a convention, not a setting: `record_cli.py:169` hard-codes
it, and generated tests pass authored CSS to first-match Playwright calls.
Hence `tours.test_id_attribute`, validated as `data-*`.

### 2.5 Locales and fallback

Three locale defaults existed (`models.py`, `build_models.py`, the guide CLI).
`GuideText.get` resolves a regional tag through its base language; the guide
player's UI strings did not (`fr-BE` read English). Tours use one rule
everywhere: exact tag, then base language, then the default locale, never
widening (`fr` does not match `fr-BE`).

### 2.6 The guide player and what is shared

- `<steppy-guide>` registers with an unguarded `@customElement`
  (`steppy-guide.ts:79`) and fetches with a plain `fetch` (`:384`).
- zod accounts for most of its weight; the tour bundle must not include it.
- Its Markdown renderer accepted protocol-relative `//host` and `/\host` links.
- Shared now through `packages/player/src/ui/`: tokens, chrome strings and the
  locale lookup, the Markdown renderer (with a link-policy parameter, which
  also fixes the `//host` links for the guide), and `placeTooltip`.

### 2.7 Packaging

`@steppy/player` is private (no npm publish); three bundles are vendored
into `src/steppy/_player_bundle/` and compared byte for byte in CI. The
Angular build target injects scripts and trusted HTML at runtime — precedents
the overlay avoids.

### 2.8 Configuration format

`config.yaml` is snake_case, `extra="forbid"`, drift-tested against
`steppy-config.schema.json`, with `${VAR}` interpolation. The build's
TypeScript config needs Node. Tours are configured in `config.yaml`.

## 3. Decisions the spec delegated

| # | Question | Decision | Why | Enforced by |
|---|---|---|---|---|
| DD1 | Package shape | A fourth entry `./tour` of `@steppy/player`, with `.d.ts`; shared code in `src/ui/` | One build, one version; no zod or guide code in the overlay | `test/tour/boundaries.test.ts`, `scripts/check-tour-size.ts` |
| DD2 | When does an `input` step advance? | Next enabled once the field is non-empty; advances on Next, on a native `change` with a value, or on a click on a later step's target | Playwright `fill` fires no `change`, which keeps the replay deterministic; a pre-filled field never auto-advances | `engine.test.ts`, the replay helper |
| DD3 | Target timeout | 8000 ms (`target-timeout`), not the tests' 30 s | A user should not wait 30 s for "can't find this element" | `contract.ts` |
| DD4 | Directory layout | Canonical and guide trees per build id; the app-assets copy flat | Apps reference a fixed URL; sites may serve several builds | `tours/emit.py`, `tours/publish.py` |
| DD5 | How the replay drives the tour | The host's element API after `whenDefined`; events through an init script plus `expose_binding` (precedent `conftest.py.j2:1248`) | No test hooks in the overlay | `templates/_steppy_tours.py` |
| DD6 | Naming | `steppy-tour-host`, `steppy-tours-manifest`, `steppy-tour`, `__STEPPY_TOURS_DISABLED__`, `steppy:tour`, all derived from one product constant | A rename edits two files | `naming.py` / `ui/names.ts`, `test_tour_contract_sync.py` |
| DD7 | Config home | `config.yaml` `tours:` | No Node needed to compile | `ToursConfig` |
| DD8 | Contract source of truth | Pydantic (`contracts/tours.py`) plus committed schemas and goldens; the TS validator hand-written | The producer and consumer are checked against the same cases | `contracts/fixtures/tours/cases.json` |
| DD9 | Step ids | The test-id (`-2`, `-3` on repeats) or a heading slug; never a position | Inserting a step must not shift ids | `tours/ids.py` |
| DD10 | Build id grammar | `^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?$`, not a Windows device name | It is a path segment | `buildid.py` |
| DD11 | Size caps | 500 tours, 100 steps, 32 locales, 16 layouts, 200/2000-character title/text, 256 KiB file, 1 MiB manifest | Bounded parsing on the client | `tour_spec.py` / `contract.ts` |
| DD12 | Shared names and patterns | Mirrored literals in Python and TS, regex-synced | No build step between the two languages | `test_tour_contract_sync.py` |
| DD13 | Demo | A framework-free app under `fixtures/tour-demo/` with a stdlib server | Reproduces the shapes real apps put in a tour's way | the integration suites |
| DD14 | Docs | `docs/tours.md` (user), this note (design) | | `test_tours_docs_sync.py` |

## 4. Deviations from the spec

| # | The spec says | We do | Why |
|---|---|---|---|
| DV2 | Tours emitted by the build | By `generate` and `tours`; `build` copies | §2.1 |
| DV3 | `version`, `guideBaseUrl` | `build_id`, `guide_base_url`; new `default_locale`, `test_id_attribute`, `layouts`, `replay`, `output.tours_dir` | `config.yaml` is snake_case |
| DV4 | One file per configured locale | A locale is emitted when some step has text in it; the default always | No file of generic hints only |
| DV5 | Annotation names | Frontmatter `tour:` (bool or block); step keys `tour` (`skip`/`keep`), `tour-text`, `tour-advance`, `tour-placement` | The repo's kebab-case step keys |
| DV6 | An explicit entry route | Inferred: the first `navigate` after the last `login`, or `tour.entry`; `{{var}}` becomes `:var`, queries dropped | Most scenarios already say where they start |
| DV7 | Assertions are not tour steps | An assertion with text becomes an information step; without text a `waitFor` gate | Authors already write "the invoice is created" |
| DV8 | `change` events | A value signature (controls, ARIA state, label text), compared once the user interacted | Component libraries set values without `change` |
| DV9 | Fill advance | DD2 | |
| DV10 | — | Rules for every action the spec does not list (`docs/tours.md` inference table) | 24 actions exist |
| DV11 | A test-id | `^[A-Za-z0-9_.:/-]{1,128}$` | Real ids use `:` and `/` |
| DV12 | Exact `[data-testid=…]` | Also `tag[attr=…]`, `[attr=…] <bare tag>`, `match: first` reduce to the id | Common authored forms |
| DV13 | — | Layout-restricted steps and `tours.layouts` media queries, skip-only | Twin steps exist (maintainer decision) |
| DV14 | One role | `tour.role` for multi-role scenarios | The other roles' steps run in the replay only |
| DV15 | `generatedAt` | No timestamp; canonical LF bytes | Byte reproducibility, SRI |
| DV16 | Manifest fields | Adds `kind`, `testIdAttribute`, `guideOrigin`, `layouts`, `role`, `title`, `steps`, always-present `integrity` | Marker, trust, validation |
| DV17 | `guideUrl` | `<guide_base_url>/<slug>/<locale>.html` | The static build's page layout |
| DV18 | Reject unknown fields | Unknown keys ignored within a major; unknown enum values and majors rejected | Forward compatibility without executable data |
| DV19 | floating-ui | Own placement (`ui/placement.ts`) | Size budget; shared with the guide |
| DV20 | zod | A hand-written validator | Size budget |
| DV21 | — | Many `::part`s, `:root`-settable custom properties, inherited font | Theming without piercing shadow DOM |
| DV22 | Listeners on targets | Document capture listeners with `composedPath` | Targets are re-rendered by frameworks |
| DV23 | Trusted events only | Untrusted events accepted | The replay and synthetic clicks |
| DV24 | — | A click on a later click step counts as performed (jump-ahead) | Users skip ahead |
| DV25 | Escape dismisses | Unless the app uses it (dialog, open popup, `preventDefault`) | Escape closes app dialogs |
| DV26 | Focus on each step | Only manual and fallback steps take focus | Focus belongs to the app |
| DV27 | Accent `#d24f2c` | `#a8410e` | 4.29:1 fails AA on buttons; 6.13:1 passes |
| DV28 | z-index | Top layer (`popover="manual"`), re-raised each step; `baseZIndex` fallback | Above any app z-index |
| DV29 | — | `sessionStorage` resume across full page loads | Classic form posts |
| DV30 | — | Kill switch and `disabled` | E2E suites and opt-out |
| DV31 | Host API | Adds `source`, `navigate` with `{appPath}`, `strings`, `current` | Routers, proxies, i18n |
| DV32 | — | Cross-origin requests omit credentials and refuse redirects; same-origin manifests `no-cache` | Fixed paths are overwritten per deploy |
| DV33 | Markdown links | Tour policy: app routes and the guide origin only; the guide's renderer refuses `//host` too | Open redirects |
| DV34 | A separate replay file | `test_<slug>__tour` in the scenario's file | Selection, workers, lastfailed and JUnit keyed by file |
| DV35 | Steps chosen at run time | Baked at generate time; layout steps keep their runtime guard | Deterministic replays |
| DV36 | — | The replay uses the test's locale parametrisation | |
| DV37 | Byte identity with tours off | Except the re-vendored guide and video bundles (cached videos re-render once) and the config schema | Shared UI code |
| DV38 | Remote content can differ | For a build id, remote equals bundled | SRI pinning |
| DV39 | — | The guide target on the static build only | The Angular target has no file hosting |
| DV40 | M1…M5 in order | M4 and M5 before M3 | M3 is another repository |
| DV41 | Code only from npm | Not yet published; `file:` or `npm pack` | Publishing is pending |
| DV42 | — | Kebab-case reasons and colon-free events, unlike the guide's `steppy:*` | Angular template syntax |
| DV43 | — | Integer `schema`, unlike the guide's `"major.minor"` | One rule: unknown major rejected |
| DV44 | A PrimeNG dialog test | Emulated by the plain demo; a real `p-dialog` and `p-select` in the Angular demo (M3) | The plain demo stays framework-free |
| DV45 | — | Gates that do not reduce to a test-id are dropped (reported with `--explain`) | Not every check is a UI condition |
| DV46 | A CI freshness check | `tours --check` compares only the app-assets copies, by content, for the build id each copy records; a differing id is a note | The canonical tree is gitignored, and under `git-sha` a committed copy always carries an earlier commit; `STEPPY_TOUR_EXPECT_BUILD_ID` gates ids at run time |
| DV47 | Remote pinned by the bundled SRI | Any manifest other than the bundled one (remote or same-origin proxy) only locates files; the bundle picks tour, locale and hash | A tampered remote could otherwise add a locale, a case variant or a tour the bundle never pinned |
| DV48 | — | The layer carries the tour file's `lang` and `dir`; the bubble's inner layout uses logical properties; placement sides stay physical | WCAG 3.1.2 when the tour falls back to another language than the page; RTL languages |
| DV49 | — | A mid-tour language switch follows the host's `locale`, else `<html lang>` (observed while a tour runs); a switch during loading is applied once loaded; the last request wins | Apps switch language asynchronously, often after a resumed tour started |
| DV50 | Chrome strings per configured locale | Four bundled languages (en, fr, nl, de); any other tour locale warns `chrome-strings-missing` and takes the host's `strings` | The size budget; no contract change |
| DV51 | M3 in a separate repository | `packages/angular` in this repository, version-locked with the overlay | The overlay is not on npm, so another repository could not install it; the adapter ships with the overlay version it was tested against, and one PR can change both |
| DV52 | — | The adapter's router bridge rejects a navigation the router refused or redirected, read from `router.url` | `navigateByUrl` resolves `false` for the current URL too and `true` for a guard redirect; a rejection fails the tour at once (`entry-params`) instead of after `targetTimeout` |
| DV53 | — | Destroying `<steppy-tours>` suspends a running tour rather than dismissing it | The overlay's teardown keeps the resume record; a shell swap must not end a user's tour |
| DV54 | — | A gate is dropped when a skipped step between it and the next tour step may change the page: any action but checks, waits, hovers, scrolls and captures; `custom` code that calls anything but locating, reading, waiting and asserting; a `call` that gets the page. Reported as `gate-stale` (`--explain`) | A gate checked on one page and awaited on the next shows "Can't find this element" and fails the replay; a dropped gate only lets the step show sooner. An allowlist for `custom` code because a denylist missed `evaluate` and helpers on the consumer corpora |

## 5. Fixed decisions: compatibility check

| # | Fixed decision | Holds? | Caveat |
|---|---|---|---|
| 1 | Generated, never hand-written | Holds | |
| 2 | Test-ids only | Holds with caveat | The overlay takes the first *visible* light-DOM match; Playwright the first DOM match and it pierces shadow roots. The replay's spotlight check catches a mismatch. |
| 3 | The user acts | Holds | |
| 4 | Non-blocking | Holds with caveat | A native `showModal()` dialog makes the overlay's buttons inert until it closes (§9). |
| 5 | Data, not code | Holds | |
| 6 | Code from npm only | Holds with caveat | Not published yet (DV41). |
| 7 | Versioned with the app | Holds with caveat | `build` deletes its output, so a guide site keeps only the current build id; older apps fall back to their bundled copy. |
| 8 | Locked to a build id | Holds with caveat | Committed tour files carry their parent commit's id; split repositories need `git-sha:<path>`; pull-request builds use merge commits. |
| 9 | Strict validation | Holds | |
| 10 | CI replays every tour | Holds | |
| 11 | Disabled means unchanged | Holds with caveat | DV37. |

## 6. Architecture

### 6.1 Data flow

```text
scenarios/*.md + config.yaml (tours:)
  │ steppy.tours: settings → inference → text → compiler → emit (canonical JSON, SRI)
  ├─ generate ─▶ tests/e2e/test_<slug>.py (+ test_<slug>__tour), _steppy_tours.py
  │           └▶ output/tours/<build-id>/ and each app-assets target
  ├─ tours    ─▶ the tour files only (--check for CI)
  └─ build    ─▶ <site>/tours/<build-id>/ (verified, byte copy), manifest.json "tours", _headers
app: import '@steppy/player/tour' → <steppy-tour-host> → loader (bundled, then remote)
     → validate → engine (pure) ↔ resolver / triggers / tracker / layer → popover
CI: test_<slug>__tour drives the host API and checks the popover around the scenario's actions
```

### 6.2 Module map

| Area | Modules |
|---|---|
| Leaves (stdlib only, imported by `models.py`) | `naming.py`, `tour_spec.py`, `testids.py`, `buildid.py` |
| Contract | `contracts/tours.py`, `contracts/tour.schema.json`, `contracts/tours-manifest.schema.json`, `contracts/fixtures/tours/` |
| Compiler (lazy) | `tours/`: `settings`, `routes`, `ids`, `actions` (`ACTION_TOUR_KIND`), `inference`, `text`, `compiler`, `diagnostics`, `emit`, `paths`, `pipeline`, `replay`, `publish` |
| CLI | `tours_cli.py` (`steppy tours`, the `validate`/`generate` hooks) |
| Replay | `templates/test_scenario.py.j2` (`step_body` macro, replay section), `templates/_steppy_tours.py`, the conftest block |
| Build | `build.py` (`_arm_tours`, `_emit_static_tours`), `templates/static/_headers.j2` |
| Overlay | `packages/player/src/tour/` (engine, validate, loader, routes, resolver, triggers, tracker, layer, signature, storage, strings, styles, templates, events, host), `src/ui/` shared with the guide |
| Demo | `fixtures/tour-demo/` (app, project, `serve.py`) |

### 6.3 Product names

Everything the product name appears in at runtime derives from
`naming.PRODUCT` and its TypeScript twin `ui/names.ts`. CLI names and config
keys are not derived: they are an interface users type.

### 6.4 Build ids

Grammar and resolution are in `buildid.py`; recipes in
[docs/tours.md#build-ids](../tours.md#build-ids). The git forms take 12 hex
digits of `HEAD`, a fixed length on purpose (`--short` grows with the repo).

### 6.5 Reproducibility and byte identity

The tour files are canonical JSON (sorted keys, indent 2, LF, no timestamp), so
the same scenarios give the same bytes and the same SRI hashes. With tours
disabled, a snapshot harness (`tests/_codegen_snapshot.py`) proved generated
tests and conftests byte-identical over the repo's scenarios, a synthetic
matrix of every action and option, and three real projects' corpora.

## 7. Security model

The overlay is the boundary: it validates every file whole, treats targets as
values (`CSS.escape`, light DOM), accepts only same-origin base-relative routes
(no dot segment, `%2e` included) and two link shapes (an app route is checked
after URL resolution, so `/.//host` stays text), renders text without HTML,
needs no inline script or style, and fetches remote files without credentials,
pinned by the bundled manifest's SRI hashes (DV47). On the producer side, an
app-assets folder is someone else's: only Steppy tour files are replaced or
deleted there. See
`SECURITY.md` (In-app tours) and [docs/tours.md#security](../tours.md#security).

## 8. Testing and CI

- Python unit tests for the leaves, contract, inference, text, CLI, emit,
  publish, replay codegen, helpers and the conftest block.
- Vitest for the shared UI (parity oracles for the guide) and the overlay
  (engine, validator, loader, resolver, tracker, host in happy-dom).
- `contracts/fixtures/tours/cases.json`, run by both sides.
- Opt-in Chromium suites (`STEPPY_TOUR_E2E=1`): the replays against the demo
  app (including a renamed test-id failing the build) and the overlay (top
  layer, keyboard, remote modes, a built guide site, CSP with Trusted Types),
  run by the `tour-e2e` CI job. The `player` job enforces the bundle size.
- Contracts a control plane consumes: `output/tours/` is a new subtree, which a
  results bundle archives as an ordinary file — an additive change.

## 9. Known limitations (v1)

- Linear tours; no Back button.
- Light DOM only; no iframes; another origin ends the tour.
- A native `showModal()` dialog makes the overlay inert (verified in Chromium:
  re-raising the popover does not lift it).
- Duplicate test-ids: first visible match (fixed decision 2 caveat).
- The tour bundle is about 26 KB min+gzip against the spec's 25 KB target (Lit
  is about 6 KB of it). The budget is the first complete build's measurement,
  25,965 bytes, plus 3%: 26,750 bytes (`scripts/check-tour-size.ts`).
- Keyboard activation within 300 ms of a step appearing is ignored, so one
  keypress cannot advance two steps.
- Chrome strings ship in four languages (DV50); other tour languages need the
  host's `strings`, or show English buttons.
- Right-to-left tours mirror the popover's layout, but `tour-placement:
  left`/`right` are physical sides of the target.
- `steppy tours --check` re-renders under the environment it runs in: an
  unresolved `${VAR}` in `guide_base_url` or an app URL that resolved
  differently where the files were written reads as stale.

## 10. Follow-ups

- Publish `@steppy/player` and `@steppy/angular` to npm. Both are
  publish-ready; the release workflow publishes them once an `NPM_TOKEN`
  exists. (The pre-publish check of the adapter on Angular 20 and 22 is done:
  see §11.)
- Keep several build ids on a guide site (today each build publishes its own).
- Shadow-DOM test-ids.
- Read the test-id attribute from config in `steppy record`
  (`record_cli.py:169` hard-codes it).
- React and Vue adapters, alongside the Angular one (docs show the element
  used directly).
- Compile-time chrome strings (`tours.strings` in `config.yaml`, embedded in
  the tour files) if per-app `strings` overrides prove awkward — an additive
  contract change.

## 11. M3 — the Angular adapter

Built in this repository (DV51): `packages/angular`, `@steppy/angular`.

- **Versions.** Peer range Angular `>=20 <23`; built and tested on Angular 21
  with PrimeNG 21 and ngx-translate 17, like the consumer apps (zone.js).
  The rest of the range was checked once, on 2026-10-17, in throwaway
  `ng new` apps that installed the two `npm pack` tarballs: Angular 20.0.0
  (TypeScript 5.8) and 20.3 (TypeScript 5.9), both zone.js, and 22.2
  (zoneless, TypeScript 6.0). `ng build` passed in all three, with no type
  errors and no partial-linker `minVersion` errors, and the overlay stayed a
  lazy chunk. In the browser, a directive-started tour reached its entry
  route through the router and ran to `tour-complete`. A tour whose test-ids
  were missing ended in a non-fatal `target-timeout`, and `stop()` and the
  service's `start()` worked. There was no console output. CI still builds
  and tests on 21 only.
- **Shape.** `provideSteppyTours(config)` in the root providers, a
  `<steppy-tours>` component placed once in the shell (inputs override the
  config; one output per event), a root `SteppyTours` service (`start`
  waits for the overlay, the last call wins, never rejects; `stop`, `next`;
  `active`/`current`/`lastError` signals; an `events` Observable), and a
  `steppyTourStart` directive. The locale comes from a string, a signal or
  a factory run in an injection context, so ngx-translate stays the app's own
  dependency.
- **The rules it follows** are the [adapter contract](../tours.md#angular-adapter-contract):
  the overlay only as a dynamic `import()`, the element created before it is
  defined (properties, listeners, and the replay's `whenDefined` +
  `querySelector`), navigation through the router inside the zone with a
  rejection on refusal or redirect (DV52), everything else outside the zone,
  nothing on the server, no `CUSTOM_ELEMENTS_SCHEMA`.
- **Tests.** A zoneless and a zone.js Vitest suite through the Angular CLI's
  unit-test builder (a fake overlay, plus one spec against the real one);
  `tests/test_angular_adapter_sync.py` against the overlay's names and
  properties; and `fixtures/tour-demo-angular/` — the demo rebuilt in Angular,
  PrimeNG and ngx-translate with the same routes and test-ids, so the plain
  demo's scenarios and their replays run against it unchanged in the
  `tour-angular-e2e` CI job.

Still open: annotating the first consumer's scenarios (its add-member
scenario needs `tour.entry`, `tours.layouts` and one `tour: skip` on a
confirm button without a test-id).

## 12. Points confirmed with the maintainer

- Scope of this run: everything in this repository; M3 later.
- A step without text in a locale shows a generic localized hint; languages are
  never mixed; the compiler warns per step.
- Layout twins are supported, skip-only, through `tours.layouts`.
- M3 (2026-10-16): in this repository; Angular 20–22 peers, built on 21;
  component + service + provider; publish-ready, not published.
