# Changelog

All notable changes to Steppy are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-10-24

In-app tours can start on a page the guide reaches by clicking, and
`steppy bundle verify` reports every kind of archive damage.

### Added
- **In-app tours: `tour: start` and `tour: end`.** A guide can walk its reader
  to a page by clicking while the tour starts on that page: mark the tour's
  first step `- tour: start` (and its last `- tour: end`, optional). With a
  start marker, `tour.entry` is only the page's route and no longer has to match
  a `navigate`; without it, the entry is inferred from the last `navigate` when
  nothing in between may change the page. The replay starts the tour where the
  setup left the browser and fails if the overlay had to navigate, then runs
  the steps after `tour: end` once the tour completed. The step key `tour`
  also takes a list (`[end, keep]`). New diagnostics: `range-duplicate`,
  `range-reversed`, `start-on-navigation` (errors) and `entry-unknown`
  (warning). Scenarios without the markers compile and replay as before.

### Fixed
- **`steppy bundle verify` caught only some damage.** A corrupt archive could
  raise a raw `zlib.error` or `tarfile.ReadError` instead of a verify error
  (which one depended on where the damage sat, so the outcome varied between
  zlib builds), and damage that touched no member's bytes, such as a tar
  header, passed as intact. Every reader (`inspect`, `verify`, `steppy report`)
  now reports a damaged archive as one, `verify` reads the stream to its end so
  the gzip checksum is checked, and zstd bundles carry a frame checksum (4
  bytes; older bundles still read).

## [0.4.0] - 2026-10-23

Fixes found by running Steppy against an app that keeps a live connection open
and puts its own login page in front of the identity provider: navigation and
login no longer require a network-idle page, form login gets past that page, and
`count(...)` comparisons wait like every other assertion. Upgrading changes the
generated code for comparison counts; regenerate with `steppy generate`.

### Added
- **Form login past the app's own login page.** Two optional `auth.form` keys:
  `login_path` (where automatic login starts, default `/`) and `start_selector`
  (a "Log in" button on the app's own page, clicked when it shows up before the
  provider's form). The `login` step clicks it too.
- **`steppy auth login <role> --auto`** logs a `form` role in headless with its
  configured credentials and saves the session. It needs no terminal, so it works
  in CI and scripts.
- `steppy validate` warns when a scenario `timeout:` is above 60 s (see the note
  under Changed).

### Changed
- **`count(sel, >=N)` and the other comparator forms now wait.** They re-count
  until the comparison holds, within the step's timeout, like every other
  assertion. Before, they read the page once, so a list that was still loading
  failed with 0. Every comparison count generates different code
  (`_wait_until(...)` instead of `_n = ….count()`); `count(sel, N)` is
  unchanged. `<` and `<=` still hold at once on a page that has not rendered.
- **A scenario's `timeout:` is the default timeout of each of its steps.** This
  already shipped in 0.3.0 without a note; before 0.3.0 the field did nothing. It
  is not a limit on the whole test, so a large value (projects had set 3–5
  minutes) now makes every failing step wait that long. The docs say so now, and
  `steppy validate` warns above 60 s.

### Fixed
- **`navigate` honours `timing.navigate_wait` and `wait:` for the page load
  itself.** `goto()`/`reload()` always waited for the `load` event (30 s) before
  the configured wait ran, so `domcontentloaded` and `none` could only add
  waiting. They now pass `wait_until` (`none` becomes `commit`). `networkidle`
  and `load` generate the same code as before.
- **Form login no longer requires `networkidle`.** After submitting, it waits for
  `timing.navigate_wait` (`none` skips the wait), so an app that keeps an
  EventSource or WebSocket open can log in. This covers the generated tests'
  automatic login, the `login` step, the TOTP step and `steppy.auth`.
- **`auth.form.post_login_url_pattern` is read.** It was parsed and ignored, and
  the fallback, any page under the app's `url`, already matches a provider
  served on the same origin (Keycloak behind the app's proxy). Automatic login
  waits for it, and so does the `login` step when it is set. `steppy init` no
  longer writes the `**/callback**` placeholder, and loading a config that still
  has it prints a warning: on a typical SPA that URL has already redirected away
  when login is checked, so keeping it would fail every login. Remove it, or set
  the URL your app lands on.
- **Generated tests honour `auth.form.preset`.** They read `config.yaml` as it
  is written, so `preset: keycloak` with no explicit selectors left the Auth0
  defaults in place, and login waited for a field Keycloak never renders. The
  conftest now carries the preset table and resolves it the way the config model
  does: explicit keys win.
- **`steppy auth login` without a terminal** fails at once with a hint to use
  `--auto`, instead of `Login failed: EOF when reading a line`. Interactively, it
  saves the session only once the browser has left the login page (type `save`
  to override).

## [0.3.0] - 2026-10-22

The first public release, under the Apache-2.0 licence. This entry captures
everything since 0.1.2, the last tagged build: a pre-release hardening pass,
in-app tours and the other new commands, grouped by theme rather than by
individual commit.

### Added
- **In-app tours (opt-in).** The scenarios that test and document a flow now also
  compile into guided popovers over the live application — and CI replays every
  tour. Enable `tours:` in `config.yaml` and add `tour: true` to a scenario:
  `steppy generate` writes the tour files (`output/tours/<build-id>/` plus a flat
  copy into your app's assets) and appends `test_<slug>__tour` to the scenario's
  test, which follows the tour the way a user would and fails — naming the tour,
  the step and the test-id — when the app no longer matches it. `steppy tours`
  writes the files alone (`--check` gates CI on a committed app-assets copy whose
  content is stale, compared for the build id that copy records, so a fresh
  checkout under `git-sha` passes); `steppy build` (static) republishes them for
  remote mode. A step points at one test-id value,
  never a selector; text comes from `guide-text` or a new `tour-text`, with
  `tour: skip|keep`, `tour-advance` and `tour-placement` to adjust inference. The
  overlay is `<steppy-tour-host>`, a new `@steppy/player/tour` entry with
  type declarations: data-only tour files validated whole, test-id lookup in the
  light DOM, top-layer stacking, keyboard and screen-reader support, resume
  across page loads, remote loading pinned by SRI, and no inline script or style
  (strict CSP and Trusted Types work). Full reference: `docs/tours.md`; design:
  `docs/design/in-app-tours.md`; a working demo: `fixtures/tour-demo/`. Also part
  of the feature:
  - **Localization.** One file per tour and locale with text in it; text falls
    back exact tag → base language, never to another language (a step with no
    text shows a generic hint in its own language). The overlay picks the file
    from the host's `locale` or `<html lang>`, follows a language switch
    mid-tour (including one made while the tour loads), and sets `lang`/`dir`
    on the popover (right-to-left languages mirror). Buttons and hints ship in
    English, French, Dutch and German; other tour languages get a
    `chrome-strings-missing` warning and take the host's `strings` property.
    New warnings `locale-not-emitted` and "did you mean" for locale keys that
    differ only in case or `-`/`_`.
  - **Phones, drawers and dialogs.** On a small screen the popover is a sheet at
    the top or bottom, away from what the user taps; it follows a drawer that
    slides in and a target scrolled into view. A step with nothing to point at
    (a `confirm` kept with `tour: keep`) is centred, except over an open app
    dialog: there it becomes a sheet, so it never hides the question it is
    about.
  - **Remote files are pinned by the bundle.** When a bundled manifest of the same
    build exists, any other manifest (a guide site or a same-origin proxy) only
    locates files: tour, locale and SRI hash come from the bundle.
  - **Writes into your app are guarded.** An `app-assets` folder is never a
    project root or a folder Steppy owns, a foreign `tours.manifest.json` stops
    the run, and only Steppy tour files are ever overwritten or deleted.
  - New config key `output.tours_dir` (default `./output/tours`); the generated
    `tests/e2e/_steppy_tours.py` replay helper; `steppy guide --all` and
    `steppy validate` compile tours too.
  - With tours enabled, generated tests set `__STEPPY_TOURS_DISABLED__` in
    every browser context (an app that auto-starts a tour cannot cover what a test
    clicks); `steppy run --guide` deselects the replays.
  - The static build gains a `tours` key in the site `manifest.json`, a CORS block
    for `/tours/*` in `_headers`, and reserves the guide slug `tours`.
  - `steppy tours --check` says when committed tour files differ only in line
    endings (git converted them on checkout, which regenerating cannot fix);
    `guide-origin` accepts a URL and keeps its origin; `STEPPY_TOUR_EXPECT_BUILD_ID`
    reaches the replays under `--workers N`.
  - **Angular adapter, `@steppy/angular`** (`packages/angular`, Angular 20–22,
    zone.js or zoneless): `provideSteppyTours()`, a `<steppy-tours>`
    component placed once in the shell, a `SteppyTours` service (`start`,
    `stop`, `next`; `active`, `current` and `lastError` signals; an `events`
    stream) and a `steppyTourStart` directive. It reaches entry routes through
    the Angular router inside the zone — a refused or redirected navigation fails
    the tour at once — keeps the overlay's work outside the zone, follows the
    app's i18n through a locale signal, and loads nothing on the server.
    `fixtures/tour-demo-angular/` is the demo again in Angular, PrimeNG and
    ngx-translate, driven unchanged by the plain demo's scenarios and replays.
  - `@steppy/player` and `@steppy/angular` are published to npm. The player
    gains type declarations for the guide entry too, a `typesVersions` fallback
    for the tour entry, LICENSE, NOTICE and the third-party licenses in the
    package, and a `check:pack` script that fails when `package.json` points at
    a file `npm pack` would not ship. The `./manifest` subpath, which never
    resolved, is gone. The release workflow publishes both once an `NPM_TOKEN`
    secret exists.
- **Steppy is on PyPI**, as `steppy-dev`: `pip install steppy-dev`.
- **Steppy is open source under Apache-2.0.** `LICENSE` + `NOTICE` at the root,
  declared as a PEP 639 `license = "Apache-2.0"` expression (hence
  `hatchling>=1.27`) and shipped in the wheel's `dist-info/licenses/`. The npm
  packages move to the same license (the player was `UNLICENSED`, the wrappers
  MIT). The player bundles embed Lit (BSD-3-Clause) and Zod (MIT), whose full
  texts now ship beside them as `_player_bundle/THIRD_PARTY_LICENSES.txt`.
  `SECURITY.md` gains a private-reporting policy and `CONTRIBUTING.md` states the
  inbound license. `pyproject.toml` now carries a description, readme, keywords
  and classifiers.
- **`tests/test_no_private_names.py`** — scans every file git would publish for
  client project names, private machine paths and the former example domain.
  The deny-list lives in a gitignored local file rather than in the repository,
  because a list there, even hashed, would publish what it guards; without the
  file the check skips.
- **Neutral example domain.** Every example, fixture, test and doc that used the
  former example domain now uses a generic one: roles `editor` and
  `reviewer`, records `customer`, `invoice` and `product`. French and Dutch
  strings were translated rather than swapped word for word. The demo fixture's
  role and guide are now `fixtures/demo-guide/**/editor/invoices`.
- **CI:** a ruff lint + format job, a Python 3.12/3.13 matrix, and a check that the
  committed player bundles match a fresh build of `packages/player/src/` (the
  build is reproducible, so a mismatch means someone forgot to re-vendor).
- **`.gitattributes`** forcing LF on every OS, so the byte-mirror guard tests
  cannot fail on a Windows checkout.

- **Six SEO follow-ups**, each of which the sitemap change surfaced and
  deliberately left alone.
  - **`manifest.json` and `<role>.nav.json` no longer advertise a mode with no
    page.** Both emitted a guide's declared `modes` verbatim, so a guide
    declaring `video` without `steppy build --video` told any consumer there was
    a video page to link. The Angular toggle and the guide page's mode row
    already filtered it; all four now go through one `_emitted_modes` helper.
    *Behaviour change for anyone reading either file.*
  - **Published PDFs are no longer the only indexable form of a `noindex`
    page.** `handbook/<locale>.pdf` is Chromium printing a page that is
    deliberately out of the index, and `<slug>/<locale>.pdf` competes with its
    own HTML — but a PDF has no `<head>` to carry a robots meta. Every static
    build now emits a `_headers` sidecar (`X-Robots-Tag: noindex` on `/*.pdf`
    public, `/*` private). **It does nothing on GitHub or GitLab Pages**, which
    cannot serve custom headers and are what the shipped CI scaffolds target;
    Netlify and Cloudflare Pages honour it. Same bargain as `.nojekyll`.
  - **A build warning when `seo.baseUrl` carries a path.** `robots.txt` and
    `_headers` are honoured only at the origin root, and a GitHub Pages *project*
    site lives under `/<repo>/` — so the correct `baseUrl` there silently
    disarmed both. Canonical links, `hreflang` and `sitemap.xml` were and are
    unaffected.
  - **The capture side validates locales too.** `config.yaml`'s `locales:`,
    `steppy run --locales` and `steppy guide --locale` now reject a tag that is
    not usable BCP-47, matching what `steppy build` has done since the hreflang
    work. Each of them becomes a screenshot directory segment the other side
    reads back, so accepting one only moved the failure somewhere it could not
    be explained. `--layouts` is deliberately still unvalidated.
  - **Regional locales resolve content through their bare language.**
    `guide-text: { fr: … }` now serves `fr-BE` instead of failing the build with
    `Missing guide-text for locale 'fr-BE'`, and the same rule reaches the
    interactive manifest (which silently emitted nulls for the identical miss)
    and the capture-time i18n switch. An exact `fr-BE:` key still wins.
    *Behaviour change: a build that used to stop now succeeds.*
  - **A locale the app cannot be switched into is warned about, not skipped.**
    When `i18n.localstorage.values` has no entry for the active locale — not even
    by language — the run previously returned quietly and captured a full set of
    screenshots **in the app's default language**, filed under the locale's own
    directory. Nothing at any stage reported it. The branch also had no test.
  - **`interactive.html` links the page it canonicals to.** All three renderings
    of a guide now carry the same format row from one shared partial, replacing
    the interactive page's missing link and the video page's hand-rolled one
    (whose label did not match `MODE_LABELS`). `.steppy-mode-links` also gained
    the CSS it never had — the row had been rendering as a bulleted list.
  - Internal: the three duplicated "link to the site index" ternaries in the
    templates collapse to one `_index_href`, including the dead one that compared
    `default_locale` with itself.
- **Standards-correct `sitemap.xml`, and one canonical URL per guide** (static
  target, `seo.mode: 'public'`). Two problems, one change.
  - **The sitemap is generated rather than templated.** `src/steppy/sitemap.py`
    is a new stdlib-only leaf that builds the document with
    `xml.etree.ElementTree`; `templates/static/sitemap.xml.j2` is no longer
    bundled. Escaping is the weaker half of the argument — Jinja's `| e` and
    ElementTree behave identically on element text — but the template
    interpolated `hreflang="{{ alt.hreflang }}"` with **no filter at all** under
    an `autoescape=False` environment, from a `locales` list nothing validated.
    The stronger half: a schema-bearing machine format should have a generator,
    `<lastmod>` becomes structural rather than textual, and the whole thing is
    now a pure function with its own unit tests. Characters no XML document can
    carry are rejected outright instead of silently emitted.
  - **A project `templates/static/sitemap.xml.j2` still wins.** Nothing is
    bundled, but an override is still the way to reach the parts of the protocol
    Steppy does not emit (`<image:image>` over a guide's screenshots,
    `<video:video>`, a `<sitemapindex>` split), and the template context is
    unchanged, so any existing override keeps working untouched.
  - **`interactive.html` and `video.html` now canonical to the guide's
    default-locale step-by-step page** and are excluded from the sitemap. They
    render the same steps in a different shell, so a guide was occupying three
    or four near-identical indexable URLs.
  - **The site root is canonical as `https://…/`, not `https://…/index.html`.**
    `index.html` is still written and still serves those bytes; only the URL
    Steppy declares changed, so the home page stops being indexed twice.
    Internal links deliberately still point at `index.html` — the canonical does
    the consolidating, and rewriting them would break opening a build over
    `file://`.
  - **Guides get a `<slug>/index.html` redirect stub**, which groups, onboarding
    and the handbook already had. A bare `/<slug>/` used to 404. In public mode
    every stub now carries an absolute canonical and, deliberately, no
    `noindex`: a noindex page canonicalling into an indexable cluster is a known
    footgun, and a zero-delay meta refresh already reads as a redirect.
  - **New `seo.lastmod`** — a W3C datetime stamped on every sitemap entry.
    Unset by default, which keeps a build byte-reproducible. There is
    deliberately no `'auto'` mode: `actions/checkout` stamps every file at clone
    time, so an mtime-derived value reads as "now" on every build. Feed it
    `git log -1 --format=%cI`. `<changefreq>` and `<priority>` are still not
    emitted — Google and Bing ignore them.
  - **`locales` entries are validated as BCP-47** at config load, because one
    malformed `hreflang` makes a search engine discard the *whole* alternates
    cluster. The tag you write is kept **verbatim** in filenames and screenshot
    directories — it is also your app's i18n key and your `guide-text:` map key
    — while `hreflang`, `<html lang>` and `og:locale` get the canonical
    spelling. So `fr_BE` is accepted and produces `<html lang="fr-BE">` at
    `…/fr_BE.html`. Two locales that normalise to the same tag are rejected, as
    is a locale that also names a layout.
  - **`pick_locale` falls back to the bare language subtag.** Without it a site
    on `['fr-BE', 'nl-BE']` whose `LocalizedText` maps are written `{fr:…, nl:…}`
    served the *French* meta description on every `nl-BE` page while declaring
    `lang="nl-BE"` — two URLs, identical text, different declared languages,
    which is exactly what makes a crawler drop the cluster.
  - **Behaviour change:** `og:locale` is now emitted only for a
    `language_TERRITORY` locale. A public `openGraph: true` site on `locales:
    ['fr']` loses `<meta property="og:locale" content="fr">` — that value was
    never a valid Open Graph locale, and inventing `fr_FR` would be a guess.
- **Exclusion groups (`exclusive:` in scenario frontmatter)** — mark scenarios that
  must never run at the same time. Two scenarios naming the same group are
  serialised against each other under `steppy run --workers N`; different groups
  still run concurrently, and ungrouped scenarios are untouched. Work stealing
  assumed every scenario was independent of every other; the only remedy for two
  that are not was `--workers 1`, which serialises the whole suite.
  - **A partition, not a protocol.** The coordinator collapses each group into a
    single queue task before any container exists, and one worker runs its members
    sequentially — one pytest process each, exactly as for an ungrouped scenario.
    So there is no lock to leak when a worker is killed, no lease, no timeout to
    tune, and no worker that ever waits on another. The only run-time primitive is
    the O_EXCL claim that was already there.
  - **Results, logs, JUnit and events stay keyed per scenario** even inside a
    group, which is why failure artifacts, the last-failed cache, run history, the
    flaky verdict and the renderer needed no changes at all — and why a worker
    dying mid-group reduces to "which member has no result file".
  - It costs wall clock: a run can never finish faster than its heaviest group's
    total duration, so the seed order now weights a group by the **sum** of its
    members' recorded medians. `steppy validate` warns about a group with a single
    member, which is what a typo looks like.
  - Two hazards closed on the way: a selection that collapses to one task used to
    hit a branch that returned **0 without running anything** (the most obvious
    first use of the feature — "every scenario hits one staging database" — would
    have gone green having executed nothing), and a task that no worker ever
    claimed was invisible to both orphan detection and result collection. Both now
    fail the build, on both exit-code branches. A scenario that never started and
    then passed on retry is reported as passed, not FLAKY, so the history
    `steppy flaky` ranks from stays honest.
- **`steppy report`** — render one run to a self-contained static site: outcomes,
  failure causes grouped so one broken selector across forty scenarios reads as
  one row, **the failing step named** with its timeline and captures, coverage
  buckets, a versioned `report.json` and a shields.io `badge.json`. Publishing it
  to GitHub or GitLab Pages is a copy; both shipped CI templates carry the job,
  commented out, with the warnings that decision deserves.
  - **Three inputs, one report.** A live `output/` tree, a `.tar.zst`/`.tar.gz`
    bundle, or a directory somebody unpacked one into. Archives are read in two
    passes — `inspect_bundle` for the manifest, then one sequential streaming pass
    for exactly the members requested — because the zstd stream is non-seekable
    and there is no rewinding.
  - **It never parses a path.** `bundle create` already stamped
    scenario/locale/layout/step/frame onto every manifest row, so the whole report
    is a manifest transform. Two read-side rules follow from a bundle possibly
    coming from elsewhere: destinations are computed from validated components
    rather than taken from a manifest path (streaming tar members bypasses
    Python's extraction filters, and an arcname is attacker-controlled), and no
    absolute filesystem path reaches `report.json` or the page footer, both of
    which get published.
  - **Honest about what it cannot know.** A `capabilities` block distinguishes "no
    per-step timeline was recorded" from "the timeline was empty", a run with no
    outcomes reads `unknown` rather than `passed`, and coverage rebuilt from a
    detached bundle's raw records refuses to report assert *strength* — the
    scenario files needed to count assert steps never travel, so every endpoint
    would otherwise read as weakly covered.
  - **The exit code describes rendering, not the run.** A report of a failing run
    exits 0. A report step that goes red for a red run is a step people delete.
  - `--screenshots failed` by default: the guide site is where every capture
    lives, and `all` on a 4-locale × 2-layout corpus is hundreds of megabytes
    pushed to Pages on every run. Captures identical across layouts copy once.
- **`coverage.non_ui`** — declare in committed config which endpoints and routes
  can never have a UI test (`[<METHOD> ]<glob>`; health checks, webhooks, OAuth
  callbacks, internal cron). The equivalent triage flags live in the gitignored
  `.steppy/coverage.db`, so a fresh CI checkout had none and every such endpoint
  counted against the denominator — a percentage wrong for reasons nobody could
  fix from CI. Applied as a read-time overlay that never writes to the store, so
  merge and refresh still never touch `triage`; an explicit `ui_testable` flag
  still outranks a pattern, and a pattern matching nothing is reported rather than
  silently inflating the number. Settles the badge as
  **`tested / (declared − non_ui)`**, subtracted from *both* sides so it cannot
  exceed 100%. `steppy coverage report` gained `--badge` / `--badge-surface`.
- **A third packaged Claude skill, `steppy-ci-report`**, installed by `steppy init`
  beside `steppy-scenario-author` and `steppy-template`. The skills are split by
  **job**, and shipping results is a third one: neither existing skill had a CI
  section at all, and the coverage material in the author skill was four lines
  that predated `non_ui`, the badge and the report. It covers the CI loop, the
  report and its three inputs, publishing to Pages with the warnings that decision
  deserves, the coverage map end to end (record → merge → refresh → gaps), the
  badge formula, the four coverage tiers, and a troubleshooting table for the
  symptoms that are otherwise silent — "badge says n/a", "coverage reads lower in
  CI than locally", "report has no step detail". `steppy-template` gained the run
  report's stylesheet (including the two ways `report.css` bites: it is parsed as a
  template, and its environment autoescapes), and the author skill now routes
  shipping questions rather than half-answering them.
- **A fourth packaged Claude skill, `steppy-app-tours`**, for the half of in-app
  tours that happens in the app's own code: serving the tour files, installing the
  packages, `@steppy/angular` (and Angular without it), plain JavaScript, React and
  Vue, starting tours with entry params, the language and chrome strings, where
  test-ids go in components, CSP and Trusted Types, events, the kill switch, and a
  troubleshooting table for the running app. The author skill had eight lines on it,
  and they were installed in the Steppy project, which is often not where the app's
  code is. `steppy init --app-dir DIR` installs the skill into the app's repository
  and records it in the project's manifest (as `../<app>/…`), so `steppy update`
  refreshes it with the rest. The author skill's tour table gained the four
  diagnostics it was missing (`no-tours`, `app-url-unresolved`,
  `layout-media-overlap`, `app-assets-skipped`). A new guard,
  `tests/test_skills_sync.py`, fails when a visible command, a tour diagnostic, or
  the overlay's or the adapter's public API grows without the skills.
- **`steppy init --ci-gitlab`** and a shipped `templates/ci/gitlab-ci.yml`. A
  second boolean rather than `--ci <vendor>`, because `--ci` is one people already
  type. Worth knowing: GitLab Pages can be restricted to project members, which
  is the one place it beats GitHub's for publishing a run report.
- **`steppy video cache`** — report, prune or clear the two render caches.
  `.steppy/tts-cache/` and `.steppy/video-cache/` are content-addressed, which
  is what makes them correct and what makes them grow forever: every edited
  sentence leaves its old clip behind, a Piper upgrade orphans all of them at
  once, and nothing ever collected them. `--prune-older-than <days>` and
  `--max-size <MB>` evict least-recently-**used** entries first (the lookup
  paths now touch what they read, so a clip rebuilt into every release stays
  young), `--clear` empties, `--tts`/`--video` scope any of those to one cache,
  and stale staging files are always swept. An entry is its primary file *plus*
  its sidecars — dropping a `.mp4` and orphaning its `.vtt` would leave the
  directory looking populated while every lookup missed. Mechanics live in the
  stdlib-only leaf `steppy/video_cache.py`.
- **`steppy bundle upload`** — push one run's results to a Steppy control plane.
  The client half of the content-addressed protocol the bundle contract
  describes: the manifest is offered, the server returns presigned PUT URLs
  **only for digests it does not already hold**, and finalize verifies every one
  of them before ingestion is queued. On a real 240 MiB capture (3204 files
  across 4 locales and 2 layouts) that is 130 MiB sent the first time and
  **nothing at all** the second.
  - **No archive is written.** The protocol is per-file, so a `.tar.zst` would
    be created only to be read back and discarded. With no argument the command
    uploads straight from `output/`; pass it a `.tar.zst`/`.tar.gz` to upload
    one an earlier CI step built, and the two produce an identical digest set.
  - **The endpoint comes from `--url` → `STEPPY_CLOUD_URL` → a new optional
    `cloud.url` in `config.yaml`. The token comes from `--token` →
    `STEPPY_CLOUD_TOKEN` and nowhere else** — there is deliberately no config
    key for it, because that file is committed. Same line
    `_check_credential_hygiene` already draws for app credentials.
  - **Interrupted uploads resume for free.** The offer is keyed on the run and
    reports what is already held, so running the command again sends only what
    is missing; a presigned URL that expires mid-upload triggers one re-offer
    and carries on. The bundle's own run id is the idempotency key, so a retry
    attaches to the run the first attempt created rather than burning a second
    run number.
  - Steppy still works with **no control plane at all**, and nothing outside
    `steppy.cloud` may assume one exists. The new package is lazy-imported and
    guarded (`tests/test_no_cloud_import_on_default_path.py`), which keeps both
    the `httpx` import cost and that boundary off the default path.
- **Results bundles** (`steppy run --bundle` + `steppy bundle create|inspect|verify`).
  One run's artifacts as a single portable file: a `.tar.zst` holding the `output/`
  tree **exactly as Steppy wrote it** — no path rewriting, so `tar -xf` followed by
  `steppy guide` or `steppy build` works unchanged on a downloaded CI artifact —
  plus a `manifest.json` describing and hashing every file, a merged `junit.xml`,
  and per-test `events/*.jsonl`. Useful with nothing else installed: a
  self-describing run archive to attach to a build. The manifest is a **versioned
  wire contract** (`src/steppy/contracts/`, published as
  `contracts/bundle-manifest.schema.json`), deliberately `extra="allow"` where the
  config models are `extra="forbid"` — a config typo must fail loudly, a newer
  producer's extra field must not.
  - **Two opt-in artifact sinks, because a plain run produces neither.** Per-test
    event records were previously `STEPPY-EVT::` lines scraped from container
    stdout in parallel mode only, and JUnit XML was single-worker-only at a
    user-configured path. `--bundle` turns on both, keyed by run id
    (`output/events/<run-id>/`, `output/junit/<run-id>/`) so a previous run's
    records can never end up in this run's bundle, and per *scenario* rather than
    per worker so N workers sharing one `output/` mount do not overwrite each
    other. Off by default: it changes the pytest invocation, and `steppy run` with
    no flags stays byte-identical.
  - **Attribution is read from pytest, never parsed out of a node id.** The events
    plugin takes `(scenario, locale, layout)` from `item.callspec.params` and the
    module's baked `SCENARIO`. Parsing would be wrong rather than merely awkward:
    a cross-role scenario parametrizes two page fixtures and reads
    `[fr-mobile-fr-mobile]`, and a locale like `fr-BE` contains the id's own
    separator. Artifacts named after a *sanitised* node id (failure screenshots,
    traces, videos) are attributed through a forward map instead, since that
    sanitiser is lossy and cannot be inverted.
  - **Archives are byte-reproducible**, which is what makes the content-addressed
    upload protocol worth having — an unchanged run must not re-send everything.
    Members sorted, timestamps and ownership normalised, PAX format, no directory
    entries, the run's creation time taken from the run rather than the clock, and
    the gzip header stripped of both its timestamp and the embedded output
    filename.
  - `truncated` in the manifest means the size budget dropped something that
    belonged in the bundle — never an artifact the user excluded by flag. Without
    that distinction a consumer cannot tell "step 7 never ran" from "we dropped
    step 7's screenshot".
  - New optional extra: `pip install 'steppy-dev[bundle]'` adds `zstandard` for
    `.tar.zst`. Without it bundles fall back to `.tar.gz` automatically — the free
    path must not depend on an extra — though an explicitly named `.tar.zst` output
    errors rather than silently producing gzip content.
  - **Per-step events, so a bundle names the step a test died in.**
    `manifest.tests[].failed_step` was declared from the start and could never be
    populated: the event stream was test-granular, and a consumer's only fallback
    was "steps with a screenshot were reached" — a guess that breaks as soon as
    screenshots are off. Every generated step body is now bracketed by
    `_steppy_mark_step` / `_steppy_end_step`, gated on `STEPPY_STEP_EVENTS=1`
    (which `--bundle` sets; export `STEPPY_STEP_EVENTS=0` to opt out). The
    markers cost one env lookup each when the gate is off, and they are emitted
    unconditionally so a generated file never depends on the environment it was
    generated in.
    - **The unclosed marker is the answer**, rather than "the last step
      reached". A step that raises never reaches its `_steppy_end_step`, so it
      stays open; a test that dies in fixture teardown closed every step it ran
      and correctly reports no failed step at all. The number reported is the
      scenario's own step number, so steps skipped by `only-in:`/`layouts:`
      cannot shift it — their markers are inside the guard and never run.
    - Bracketing is a `bracket_step` filter in `generator.py`, **not** two lines
      in the template, because whether a captured step body ends with a newline
      turns out to depend on which action branch rendered — a branch whose last
      literal is followed by `{%- elif %}` has it stripped when the template is
      lexed. A template-level marker therefore glued onto the previous statement
      for `wait` and nothing else, a `SyntaxError` in exactly the scenarios that
      used it. `tests/test_step_events_codegen.py` now brackets every action in
      `VALID_ACTIONS`.
    - Regenerating adds two lines per step to every `test_*.py`. Runtime
      behaviour is unchanged with the gate off.
- **Video guide mode** (`modes: ['video']` + `--video` on `steppy guide` and
  `steppy build`). A third guide mode that *synthesizes* narrated walkthrough
  videos from artifacts a run already captured — one MP4 per (guide × locale ×
  layout), on **both** build targets. Nothing is screen-recorded: a new
  `packages/player/src/video/` module lays the interactive manifest onto an
  explicit millisecond timeline whose state at any instant is a pure function of
  its inputs, and `steppy/video.py` steps that timeline frame by frame in
  headless Chromium, piping stills to ffmpeg. So a guide renders identically on
  a laptop and on a throttled CI runner, which is what makes "regenerated on
  every release" a claim the tool can cash. Frames that change nothing (holds,
  voiceover pauses — most of a narrated video) reuse the previous still instead
  of re-screenshotting, and whole renders are content-addressed in
  `.steppy/video-cache/`, so an unchanged guide costs nothing on the next build.
  Each MP4 ships with a WebVTT caption track and a poster frame; the static
  target emits a `<slug>/video.html` player page and Angular a
  `<slug>-video.component.ts` wired into the existing mode toggle.
  - **Narration is dependency-injected from day one.** Voiceover text is the
    per-locale `guide-text` already authored — no new authoring surface — and
    the engine is chosen entirely in `config.yaml`. The default `none` gives
    burned-in captions with no install, no network and no bill; `piper` (free,
    offline, neural) and `espeak` run as subprocesses; ElevenLabs, OpenAI, Azure
    and Google are *data rows* over one generic HTTP provider rather than a
    class each, so an unsupported vendor needs only `video.tts.http` filled in,
    and a third party can register one through a `steppy.tts_providers` entry
    point. No code path branches on vendor. Clips are cached by content (keyed
    including the engine version, since a local engine's output shifts between
    builds) so a paid provider is never re-billed for unchanged text, and each
    step is stretched to fit its voiceover so narration is never cut off.
  - Piper is invoked as a subprocess and never imported: piper1-gpl is GPL-3.0,
    so it stays a user-installed tool rather than a dependency of a
    permissively-licensed wheel — the same posture already taken with ffmpeg.
  - Two gates by design: the mode is the content decision, `--video` the cost
    one. Declaring the mode without the flag is inert; passing the flag with no
    declaring guide warns and does nothing. New `steppy video check` verifies
    ffmpeg, Chromium, the provider and the API key in about five seconds, rather
    than letting each fail separately partway through a long render.
  - Fixed in passing: `interactive.html` was emitted and listed in `sitemap.xml`
    but linked from nowhere. Static guide pages now carry a mode-links row
    surfacing whichever of the interactive tour and the video the guide declares.
- **PDF export** (`--pdf` / `--handbook` on `steppy guide` and `steppy build`).
  `--pdf` prints every generated guide page through headless Chromium — the
  templates' existing `@media print` rules are the PDF layout, so a themed
  project's PDFs inherit its theme. `--handbook` assembles every guide into one
  document (cover, contents, each guide on a fresh page) as HTML **and** PDF:
  per (role, locale, layout) for `steppy guide`, per locale for `steppy build`,
  where it is linked from the index and each page gains a "PDF" link. Paper
  settings live in a new `pdf:` config block (format, margins, scale,
  backgrounds, footer page numbers). Static build target only; a dry run never
  launches a browser. Print CSS gained a `--steppy-print-max-image-height` cap
  (Chromium can't split an image, so a tall full-page capture used to be clipped
  at the page break) and hides the site chrome, which also fixes Ctrl+P on a
  published guide site.
- **Scenario-level `layouts:` now narrows `steppy build` too.** The frontmatter
  declaration already scoped capture; the build still rendered every configured
  layout for every guide, so a desktop-only flow shipped a "Mobile" toggle that
  swapped between identical screenshots. Each guide is now built for
  `layouts ∩ scenario.layouts` — one wrapper, no toggle, one screenshot copy,
  a single-layout interactive manifest — and `manifest.json` reports the
  effective layouts per guide. A disjoint declaration is now a build error.
- **Parallel-run reliability & triage** — six changes aimed at making a
  several-hundred-scenario `steppy run --workers N` produce a short, honest,
  actionable summary:
  - **Report flags now reach the workers.** `--trace`, `--video`, `--retries`
    and the `--` pytest passthrough were silently dropped in parallel mode —
    resolved host-side but never forwarded to the containers. They now travel as
    `STEPPY_TRACE` / `STEPPY_VIDEO` / `STEPPY_RETRIES` / `STEPPY_PYTEST_EXTRA`.
    The worker image bakes in the `[retry,report]` extras so `--reruns` actually
    works in-container.
  - **Retry pass + FLAKY classification** (`parallel.retry`, `--retry-pass N`).
    After the queue drains, failed scenarios are re-run at reduced concurrency
    (default: 1 worker) in the still-running stacks. Fail-then-pass is reported
    as **FLAKY** in its own section and does **not** fail the build
    (`--strict-flaky` opts into strict); fail-twice is a real failure. Orphaned
    scenarios (worker died mid-run) are retried instead of being a hard loss.
    Flaky scenarios are excluded from the `lastfailed` cache, so `steppy rerun`
    only re-runs genuine failures.
  - **Failures grouped by cause.** The summary leads with a *Failure causes*
    block that buckets failures by a normalized fingerprint of their tail —
    forty scenarios sharing one broken selector print as one line, not forty
    stack traces. `--verbose` restores the full per-scenario dump.
  - **Run history + `steppy flaky`.** Parallel runs append one record per
    scenario to `.steppy/history.jsonl` (outside `output/`, so cleanup can't
    wipe it). `steppy flaky` ranks repeat offenders by flake rate with
    median/p95 durations; `--last`, `--min-rate`, `--json`, `--prune`.
  - **Contention reduction.** `parallel.isolate_auth` gives each worker a
    private, pre-seeded `.auth` dir instead of sharing one read-write across all
    N (mutually exclusive with `pre_auth`). The queue now honours seed order and
    the coordinator seeds longest-first from recorded durations, so a slow
    scenario is no longer claimed last and left running alone.
  - **Circuit breaker** (`parallel.circuit_breaker`, `--fail-fast/--no-fail-fast`).
    Aborts a run once ≥10 scenarios have completed and ≥90% of them failed —
    that is infrastructure, not tests, and it should not cost the full wall
    clock. On by default; skips the retry pass when it trips.
- **`steppy update`** — reconciles a scaffolded project with the installed Steppy
  version: refreshes Steppy-owned assets (config schema, Claude skills, CI workflow)
  from the manifest — pristine files silently, user-edited files alongside as
  `<file>.new` — then reports deprecated usage and changelog highlights. A skill
  shipped after the project was scaffolded is installed (not into a project
  scaffolded with `--no-skills`, nor one whose skills were all deleted), and the
  skill folders people copied in by hand before Steppy packaged them (`steppy`,
  `steppy-angular-export`) are reported, never removed. `steppy update --check` is
  a report-only, CI-able compat scan (non-zero exit on breakage).
- **`steppy init`** — scaffolds a new project: a commented `config.yaml` (with a
  `# yaml-language-server: $schema=…` header for editor autocomplete), a runnable
  example scenario, `credentials.yaml.example`, a generated `steppy-config.schema.json`,
  and a `.steppy/scaffold.json` manifest; appends the right `.gitignore` entries and
  never overwrites existing files. `--ci` also writes a GitHub Actions workflow.
  Re-running it is safe, and is how a project set up by hand adopts the scaffold:
  it merges the manifest rather than replacing it, and leaves the starter scenario
  out when `scenarios/` already has some.
- **Config JSON Schema** — `steppy-config.schema.json`, generated from `SteppyConfig`
  and drift-guarded by a test, turns `extra = "forbid"` from hostile into helpful
  (inline autocomplete + typo squiggles in YAML-aware editors).
- **CI reporting** — a `report:` config block (JUnit XML, HTML report via the
  `steppy-dev[report]` extra, Playwright trace) and `steppy run --trace`; a bundled
  GitHub Actions template.
- **Auth** — `form` auth mode (a rename of `auth0`, which works against any
  fillable login form); IdP selector presets (`auth0`, `keycloak`); implemented
  `token` `header` injection.
- **Parallel pre-authentication** — an optional `parallel.pre_auth` block (`command`,
  `timeout`, `image`) runs a login command **in-network** before `steppy run --workers N`
  dispatches, gated (Compose `service_completed_successfully`) to finish before any worker
  claims a scenario and deduped once per run across stacks (O_EXCL lock). Fixes
  `mode: storage-state` auth in worker mode, where a host-captured, origin-bound
  `.auth/<role>.json` can't authenticate a worker reaching the app at its `internal_url`.
  Unset ⇒ dispatch is byte-identical to before.
- **Selector lint** — `steppy validate` warns on brittle selectors (positional
  pseudo-classes, deep chains, text-coupled, and structure-only tag chains) across
  every author-written CSS surface (`selector`, `within`, `guide-cursor.target`, and
  `capture` `from.selector`), with combinator-aware (`>`/`+`/`~`) hop counting —
  without failing.
- **Scenario-format version** — optional `steppy:` frontmatter key with a supported-
  version check, so future format changes can be detected rather than mistaken for typos.
- **Security** — `${VAR}` config interpolation, credential-hygiene warnings, opt-in
  shell capture, screenshot redaction (`screenshot.mask`); see `SECURITY.md`.
- **UI-kit presets** — `preset: primeng | material | primevue | mui | vuetify | shadcn`
  for the component-driver blocks. `primeng` is authoritative; the rest are best-effort
  starter kits to refine against real markup.
- **React/Vue guide embedding** — `@steppy/react` and `@steppy/vue`, thin starter wrapper
  packages around the framework-agnostic `<steppy-guide>` web component (props →
  attributes, `steppy:*` events → callbacks/emits). Pair them with the `static` build
  target to embed interactive guides in any React/Vue app.
- **`steppy record`** — a click-to-Markdown recorder: wraps `playwright codegen` and
  post-processes its output into a **draft** `scenarios/<slug>.md`. Never bakes secrets —
  a username/password/submit sequence collapses to `- action: login`, password/token
  fields become `${VAR}` refs (resolved at run time by a new `_interp_val` conftest
  helper) and drive an auto-suggested `input[type='password']` screenshot mask; the
  selector lint runs on the output. Loads `.auth/<role>.json` for an authenticated
  session. A foundational V1 (CLI plumbing) — the client-facing recorder is a later phase.
- **Docs** — `STEPPY-REFERENCE.md` (the authoritative scenario-format spec),
  `SECURITY.md`, `CONTRIBUTING.md`, this `CHANGELOG.md`; README Troubleshooting,
  Continuous integration, and Real-world scenarios sections.
- **Example scenarios** — the in-repo `scenarios/` set now covers every action
  family and is validated by a CI test so it can't rot.
- **Opt-in video recording** — `report.video` (`off`/`on`/`on-failure`)
  and `steppy run --video` record a Playwright video, saving `output/videos/<test>.webm`
  (retain-on-failure by default). Off by default ⇒ `run` output byte-identical.
- **Flaky-retry** — `report.retries: N` (or `steppy run --retries N`) reruns
  each failed test up to N times via the new `steppy-dev[retry]` extra (pytest-rerunfailures);
  warns and skips when the plugin is absent, never fails the run.
- **TOTP/MFA login** — automated multi-factor login: a `totp_secret`
  (env-ref) on app credentials + a `totp_selector` on `auth.form` (with `auth0`/`keycloak`
  preset starters) fills a time-based one-time code after the password submit. Implemented
  with a stdlib RFC 6238 helper — **no new dependency**, and the generated conftest stays
  self-contained (consumers need nothing extra).
- **Brand.** A mark (a checked box) and an app icon in `docs/brand/`, with the
  palette Pin / Lichen / Balise / Granite. The run report carries the mark, a
  favicon and the palette. The generated guide sites keep their neutral styling
  on purpose, because they document the user's application, not Steppy.

### Changed
- **The Python package is now named `steppy-dev`.** `steppy` on PyPI is an
  unrelated project, so pip needs another name; the command, the import and
  everything else stay `steppy`. Every install line and extras hint now says
  `steppy-dev` (`pip install 'steppy-dev[report]'`, `steppy-dev @ git+…`),
  including the CI workflows `steppy init --ci` scaffolds, which `steppy update`
  refreshes. **If you installed an earlier build**, run `pip uninstall steppy`
  before installing this one, since both claim the `steppy` folder and command.
  Also rename the package in any requirement line that points at the git
  repository: pip refuses a `@ git+…` line whose name does not match the package
  it builds.
- **The guide player and video renderer bundles were rebuilt.** They now share
  their tokens, strings, Markdown renderer and tooltip placement with the in-app
  tour overlay (`packages/player/src/ui/`). The guide's styles are byte-identical;
  cached videos re-render once, because the renderer's hash is part of the cache
  key. A regional locale now falls back to its base language for the player's
  chrome strings (`fr-BE` reads the French ones; it used to read English).
- **`STEPPY_RUN_ID` is now sortable in parallel mode too.** The coordinator used to
  export its Compose-project id (a bare `uuid4().hex[:8]`) as the run identity, but
  `coverage.merge.latest_run_id` resolves "the newest recorded run" by taking the
  lexicographically last directory name — correct for the timestamped id the
  single-worker path mints, arbitrary for a random one. The two are now separate:
  a short `stack_id` names the Compose projects and the tmp dir, and the run's
  identity is the same sortable `<timestamp>-<hex>` form everywhere. **Visible
  effect:** under `--workers N --coverage`, `output/coverage/raw/<dir>` is named
  differently than before, and `steppy coverage merge` with no `--run-id` now picks
  the right one.
- **Default build target is now `static`** (was `angular`). The static site is
  framework-agnostic and embeds anywhere, including via the new `@steppy/react` /
  `@steppy/vue` wrapper packages. **Migration:** projects that build Angular components
  must now set `target: 'angular'` explicitly in `steppy.guide.config.ts` — a build
  config that omitted `target` and relied on the Angular default will otherwise emit a
  static site.
- **Auth mode `auth0` → `form`** and **step action `auth0-login` → `login`** — the
  old spellings remain kept-forever deprecated aliases (see `src/steppy/compat.py`).
- **Template tree** — the bundled `src/steppy/templates/` is now generated from the
  canonical root `templates/` at build time; edit only the root tree.
- **Internal maintainability** (output byte-identical) — the screenshot-emit
  predicate that was copy-pasted 36× in `test_scenario.py.j2` is now one registered
  `emits_screenshot` Jinja filter; the scenario/locale/layout selection logic shared
  by single-worker and parallel runs was extracted into `src/steppy/selection.py` so
  `--workers N` can't drift from `--workers 1`; and the escaping-fuzz suite was
  extended to frontmatter (title/description docstrings, seed path) and config layout
  names, hardening those source-baked sites against quote/backslash injection.
- **De-branding** — CLI help, example config/scenarios, and fixtures use generic
  `admin`/`editor`/`viewer` roles.
- **Versions aligned** — the Python package, npm types package, and player now share
  one version, guarded by a test and the release workflow.

### Fixed
- **Guide text no longer turns protocol-relative links into links.** The player's
  Markdown renderer accepted `[label](//host/path)` and `[label](/\host)`, which
  browsers resolve to another site; both now stay literal text, like any other
  refused URL.
- **The release workflow now vendors `steppy-video.js`.** It copied only two of
  the three player bundles, so a release wheel shipped whatever video renderer
  happened to be committed; the wheel check now requires all three. Releases also
  attach an sdist.
- `contracts/report.schema.json` regenerated. It was out of sync with the models
  after a pydantic upgrade, so the drift guard failed.
- **A multi-role scenario ran once per *pairing* of its roles' locales and layouts,
  and the mixed runs filed screenshots in the wrong directory.** The
  `pytest_generate_tests` hook parametrized each role page fixture with a
  separate call, and separate calls on independent argnames multiply. Under
  `--layouts desktop,mobile`, a two-role scenario collected 4 items and a
  three-role one 8. With `--locales fr,nl` as well, a two-role scenario collected
  16, one page in French and the other in Dutch. The extra items were also wrong:
  `CURRENT_LOCALE`/`CURRENT_LAYOUT` are one global that every role fixture
  writes, so the last one set up decided the screenshot directory for a page
  built at the other role's viewport. The artefact was a desktop capture under
  `fr/mobile/` whose sidecar claimed `viewport: {390, 844}` beside a
  `bbox.x` of 1435. All role pages now go under one `parametrize` call and
  move in lockstep. A real suite under `--layouts desktop,mobile` went from 1118
  collected items to 616, and single-role ids are byte-identical. As hardening,
  the sidecar's `viewport` (and the in-viewport test in multi-frame fill-form)
  now reads `page.viewport_size`, the space the bboxes were actually measured
  in, and falls back to the layout lookup only for a context without a
  viewport. *Behaviour change: multi-role tests collect fewer items, and
  their node ids lose the repeat (`[fr-mobile]`, not `[fr-mobile-fr-mobile]`), so
  a single-worker `lastfailed` cache written before the upgrade will not match
  them on the first `steppy rerun`.*
- **Files under `output/coverage/` that were not raw records were silently dropped
  from every bundle.** `classify._coverage_file` returned `None` for anything not
  matching `raw/<run_id>/…`, and the walker reads `None` as "belongs to a different
  run, skip entirely" — the reading it was written for. So `output/coverage/gaps.json`,
  and any file dropped there by hand, vanished without a warning, contradicting
  that module's own rule that carrying a file nobody labelled beats dropping one
  nobody noticed. `None` is now reserved for the previous-run case, `gaps.json`
  ships as the new `coverage_gaps` kind (never dropped by the size budget — it is
  kilobytes, and the only way a detached bundle can report a real coverage
  surface), and anything else lands as `other`. Found by writing the report that
  needed to read it, which is what the results-bundle acceptance test is for.
- **A non-ASCII character in Steppy's own output could kill the command.** Steppy's
  summaries use `→`, `×` and `…`; on a Windows console still running a legacy code
  page `sys.stdout` is cp1252, and the first arrow raised `UnicodeEncodeError` from
  inside Rich — so `steppy coverage merge` and every other command with an arrow in
  it died at the moment it tried to report success, with a charmap traceback that
  said nothing about the run. The shared console now asks for UTF-8 with
  `errors="replace"`, guarded on every axis.
- **The interactive player's `sidebar` template no longer degrades with step
  count.** The left rail had no height cap and its timeline no scroll, so rail
  height was linear in the number of steps: at 19 steps — the longest guides in a
  real project — a ~1200px rail stood beside a 394px screenshot, ~800px of dead
  column, worsening with every step added. The rail is now capped and its
  *timeline* is what scrolls, so the head (title, meta, switchers) and foot
  (prev/next/counter) stay pinned however long the guide is. The load-bearing
  line is `min-height: 0` on the timeline: a flex item's default `min-height:
  auto` refuses to shrink below its content, so without it the cap is inert and
  nothing scrolls. Sticky went on the **rail**, not the preview — a tall mobile
  capture renders ~1363px, and sticking *that* would pin its top and put its
  bottom permanently out of reach, whereas sticking the rail keeps navigation in
  view while the image scrolls and is a no-op the rest of the time. The preview
  is now centred in its grid area, which turns the remaining slack into padding
  instead of a void below the image; this is safe for the cursor and hotspot
  because they are positioned inside `.steppy-stage` and scaled from the image's
  live rect, so they travel with it. Advancing scrolls the current row into view
  (`block: 'nearest'`), which is required once the timeline scrolls and also
  absorbs the reflow from the description being injected into only the current
  row. Rail width, rail cap and sticky offset are now `--steppy-*` tokens: the
  shadow DOM exposes no `part=`, so without them a host that cannot live with a
  default has to wait for a release. The static `interactive.html` shell widened
  from `1024px` to `min(1440px, 100vw - 32px)` — at 1024px the sidebar spent 340
  of it on the rail and rendered a 1440px capture at 44%; it now reaches ~73%,
  and the overlay/inline stage goes from 990px to essentially native. That makes
  the interactive page deliberately wider than the 900px editorial guide pages:
  it is an app-like widget, not prose.
- **Interactive guides can express the desktop/mobile twin-branch pattern again,
  and each layout now gets its own wording.** `prepare_interactive_manifest`
  required every layout to cover the *same scenario step indices*, which
  complementary `layouts:` whitelists — one logical step authored twice because
  the DOM path differs — can never satisfy; it failed 18 of 24 guide-enabled
  scenarios in a real project. Divergent indices are the pattern working as
  intended; the merge is positional, and what the manifest genuinely cannot
  express is a divergent step *count* per layout, which the pre-existing count
  guard already caught and now reports with the per-layout coverage breakdown.
  Underneath that, the merge had been taking `title`/`text`/`subtitle` from the
  canonical (default-layout) branch alone, so mobile viewers read "click the row
  of the table" beneath a screenshot of a card list — silently, in 34 of 199
  steps in the same project. `captures[locale][layout]` now carries an optional
  atomic `content: {title, text, subtitle}` override, emitted only where it
  differs from the step level, so a guide with no twins stays byte-identical and
  the video render cache does not churn. Manifest version `1.0` → `1.1` (the
  player gates on the major, so old manifests still load — but `Capture` is a
  strict schema, so emitter and vendored bundle must ship together). Twins that
  disagree on `action` now warn instead of failing the build; the cursor-presence
  check stays fatal, because that one really does change playback.
- **A killed video/narration render can no longer poison a cache permanently.**
  ffmpeg wrote both the cached `.mp4` and the cached `.wav` *directly to the key
  path*, and both lookups only asked `is_file()` — so a Ctrl-C, a full disk or an
  encoder crash left a truncated artifact that every later run copied out and
  reported as a hit, with `ok=True, cached=True`, forever. Every cache write now
  stages a sibling temp file and `os.replace`s it into position. A hit is also
  validated rather than trusted: a truncated WAV keeps its 44-byte header, so
  `getnframes()` still reports the *intended* length and the timeline would be
  sized against audio that is not there — the payload is now compared against the
  bytes on disk, and a bad entry is discarded and re-made instead of raising a
  bare `wave.Error` that killed the build.
- **Re-captured screenshots now invalidate the cached video.** The render
  fingerprint hashed the manifest, which carries screenshot *URLs* — so a
  recapture after a UI change that did not move the target element (a colour, a
  logo, a label outside the bbox) produced the same key, and the build shipped a
  video of the old UI and called it cached. Screenshot bytes are now hashed in,
  memoized per batch so a guides × locales × layouts corpus reads each file once.
- **Setting an API key no longer re-encodes every video in the project.** The
  fingerprint dumped the whole `VideoConfig`, nested `tts` block included, so
  `api_key`, `timeout`, `ffmpeg_path` and `cache_dir` all invalidated renders
  they cannot change a pixel of. They are excluded by an explicit denylist —
  a field added later still invalidates by default.
- **Editing `video.tts.http` now re-synthesizes.** The HTTP engine's `version()`
  returned only the model name, so changing the request body (ElevenLabs
  `stability`/`style`, Azure SSML prosody, a different `format`) left every
  existing clip a hit even though the audio would differ. It now folds in a hash
  of the resolved spec — taken *pre-substitution*, so it carries `{api_key}`
  placeholders rather than the secret and rotating a key does not churn the cache.
- **Re-wrapping a `guide-text` line is no longer a re-bill.** Narration text is
  whitespace-normalized before it is sent and hashed, so a hard wrap moved by one
  word costs nothing. (One-time full miss on the first render after upgrade.)
- **`video.tts.cache: false` no longer bills once per layout.** Narration does
  not vary by layout, but each job re-synthesized from scratch with the on-disk
  cache off; a batch now shares a process-local memo, so "do not persist" stops
  meaning "pay N times".
- **`tts-cache/work/` no longer grows forever.** It kept every pre-transcode
  container a provider ever returned; the normalized WAV is the artifact, so the
  raw file is now deleted once it has been converted.
- A guide whose narration is empty now emits an empty WebVTT track instead of no
  file at all — the embedding page advertises a `<track src=…>` decided at build
  time, long before the render knows whether there is anything to say, so the
  missing file was a 404.
- **`steppy-config.schema.json` no longer drifts on a pydantic upgrade.** The
  committed schema is a generated artifact whose bytes are byte-compared against
  the models by a guard test — but `pydantic>=2.0.0` is unbounded, and two of its
  outputs vary across 2.x releases (`additionalProperties: true` on open dicts,
  and `0` vs `0.0` in numeric bounds). Both leaked into the file, so the guard
  failed for anyone whose pydantic differed from whoever last regenerated it,
  reporting a model change that had not happened. The generator now canonicalises
  those away, so the schema depends on the models and nothing else.
- Unescaped scenario strings (apostrophes, quotes) no longer break generated code.
- `steppy validate`/`list`/`generate` no longer report success as failure
  (`typer.Exit` handling); unexpected errors show a friendly message, full traceback
  only under `--verbose` / `STEPPY_DEBUG=1`.
- Malformed step lines and missing required fields now fail at `validate` time
  instead of silently dropping intent.

### For contributors
- **Releases can publish to PyPI.** A `publish-pypi` job in `release.yml` uploads
  the wheel and sdist the release job built, through Trusted Publishing, once the
  `PYPI_PUBLISH` repository variable is `true`. The release job also runs
  `twine check --strict` on them first. The one-time setup for PyPI and npm is in
  CONTRIBUTING.md, "Releasing". `tests/test_distribution_name.py` fails when an
  install hint names plain `steppy`.
- **In-app tours bring their own guards**: a stdlib-only-leaves check, the
  tours lazy-import guard, Python↔TypeScript name sync, a shared golden case
  table run by both the producer check and the overlay's validator, a docs sync
  test, a vendored-bundle pin (`tests/test_player_bundle_files.py`), the overlay's
  size budget in the `player` CI job, and a new `tour-e2e` CI job running the
  Chromium suites. The step loop of `test_scenario.py.j2` moved into a
  `step_body` macro — byte-identical output, proven by `tests/_codegen_snapshot.py`
  — so the tour replay renders exactly the same action code.
- **The tour browser suites measure what the user sees.** Both demos gained a
  phone layout (a menu drawer instead of the header links), a long page and a
  confirmation, and every demo scenario runs at 1280x800 and 390x844, replays
  included. `tests/integration/tour_geometry.py` checks each step in the real
  browser: the spotlight frames the target, the bubble stays on screen and off
  it, a click at the target reaches it, and no open dialog is covered — by mouse
  on a computer and by touch on a phone, through the drawer, Escape in it, a
  focus trap that pulls focus back, and a language switch.
- **The Angular adapter is tested three ways**: the CLI's Vitest builder runs a
  zoneless suite and a zone.js suite (`npm --prefix packages/angular test`) against
  a fake overlay and the real one; `tests/test_angular_adapter_sync.py` keeps its
  tag, event names and property writes in step with the overlay; and the
  `tour-angular-e2e` CI job runs the generated replays and the router, zone and
  i18n checks against the Angular demo in Chromium
  (`tests/integration/test_tour_angular_it.py`). A new `angular-adapter` job
  builds the package and dry-runs its `npm pack`.
- **The lint gate is pinned, and now actually runnable.** `uv run ruff check .` —
  the command CONTRIBUTING documents — failed with "program not found", so people
  reached for whatever ruff was on PATH. That is not harmless: the formatter
  changed how it wraps a long `assert x, "msg"` in 0.9, and an older ruff does not
  skip the rule, it reformats the other way, quietly rewriting files the repo just
  formatted. Ruff now resolves from a `dev` dependency group, `required-version`
  refuses an incompatible one, and the lint rule set is **explicit** rather than
  inherited — ruff 0.16 widened its defaults and took the unchanged tree from
  clean to 745 errors on upgrade. Markdown is excluded from the formatter, which
  0.16 would otherwise rewrite (the docs are full of deliberately-bad "BEFORE"
  snippets that exist to be ugly).
- **`config.output.screenshots_dir` and `guides_dir` actually do something now**
  They were validated, schema'd and scaffolded — and read by
  nothing, so setting one changed exactly nothing while four independent copies
  of the same hardcoded literal decided where artifacts really went. The capture
  root joined the rest of the capture-path grammar in `captures.py`, and every
  consumer resolves through it: the generated conftest (at *runtime*, like
  `coverage.records_dir` — so editing `config.yaml` needs no re-`generate`, and
  the generated file stays identical for every project), `steppy guide`'s two
  path flags, `steppy build` (explicit `screenshotsDir` still wins), the
  parallel failure harvest, and `steppy bundle create`. A project that sets
  nothing keeps byte-identical paths.

  Pointing a root outside `output/` is allowed and honoured, but three things
  only ever see `output/` and each now says so: a bundle warns the artifacts are
  not in the archive, `--workers N` warns only `output/` is mounted back from
  the container, and the config comments name the CI upload and `.gitignore`.

  `output.reports_dir` is **deprecated and inert** — nothing ever wrote
  `output/reports` (traces and videos are top-level; `report.junit_xml` /
  `report.html` carry their own paths). It still parses, so no existing config
  stops loading; `steppy validate` warns and `steppy update --check` reports it.
  `output:` also gained `extra: forbid`, so a `screenshot_dir:` typo now fails
  loudly instead of being swallowed.

[Unreleased]: https://github.com/Steppy-dev/steppy/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/Steppy-dev/steppy/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Steppy-dev/steppy/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Steppy-dev/steppy/releases/tag/v0.3.0
