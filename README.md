<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Steppy-dev/steppy/main/docs/brand/steppy-mark-dark.svg">
    <img alt="" src="https://raw.githubusercontent.com/Steppy-dev/steppy/main/docs/brand/steppy-mark.svg" width="96">
  </picture>
  <br>
  Steppy
</h1>

<p align="center"><em>Every walkthrough, proven.</em></p>

Python CLI that generates Playwright pytest files from Markdown scenario files, then turns the captured screenshots into multi-locale user guides (standalone HTML, Angular components, or a static site).

- **Code generator, not a runtime engine** — generated tests are standalone pytest files you can run with or without Steppy.
- **Markdown-first** — scenarios are `.md` files with YAML frontmatter, no DSL to learn.
- **Multi-locale + multi-layout capture** — one run produces screenshots for every (locale × viewport) combination.
- **Three guide modes** — `stepbystep` (annotated screenshots with an animated cursor), `interactive` (a Lit web component that replays the flow), and `video` (a narrated MP4 synthesized from the same captures, per locale and layout).
- **In-app tours (opt-in)** — the same scenarios compile into guided popovers over the live app, and CI replays every tour.
- **Optional parallel execution** — opt-in Docker Compose orchestration for large suites.

---

## Installation

The package is named **`steppy-dev`**, because `steppy` on PyPI is an unrelated
project, which a pip command naming plain `steppy` installs. Use `steppy-dev` wherever pip
reads a name, extras included (`steppy-dev[report]`). The command you run and the
module you import are both `steppy`.

### Option 1 — From PyPI (simplest)

```bash
pip install steppy-dev

# With the optional extras (HTML report, --retries, zstd bundles)
pip install "steppy-dev[report,retry,bundle]"
```

### Option 2 — Directly from the git repo

Pin a release tag rather than a branch — see the
[releases page](https://github.com/Steppy-dev/steppy/releases) for the latest (the
examples below use `vX.Y.Z` / `X.Y.Z` as placeholders). The player JS bundle is
committed to git, so `pip` can build a wheel from the source tree with no Node
toolchain involved:

```bash
# Pinned to a tag (recommended)
pip install "steppy-dev @ git+https://github.com/Steppy-dev/steppy.git@vX.Y.Z"

# With the optional extras (HTML report, --retries, zstd bundles)
pip install "steppy-dev[report,retry,bundle] @ git+https://github.com/Steppy-dev/steppy.git@vX.Y.Z"

# Latest main
pip install git+https://github.com/Steppy-dev/steppy.git
```

### Option 3 — From a GitHub Release wheel

Faster install (no clone, no build) and version-pinned:

```bash
pip install "https://github.com/Steppy-dev/steppy/releases/download/vX.Y.Z/steppy_dev-X.Y.Z-py3-none-any.whl"
```

### Option 4 — In CI / `requirements.txt`

Every form above works unchanged in CI — no token is needed. In `requirements.txt`:

```
steppy-dev[report,retry]==X.Y.Z
# or, from git:
steppy-dev[report,retry] @ git+https://github.com/Steppy-dev/steppy.git@vX.Y.Z
```

For GitHub Actions, `steppy init --ci` scaffolds a complete workflow, and
[`.github/workflows/steppy-e2e.yml`](.github/workflows/steppy-e2e.yml) is the same
recipe as a reusable workflow you can call with `uses:`.

### Option 5 — From source, editable (for Steppy development)

Required if you're contributing to Steppy itself.

```bash
git clone https://github.com/Steppy-dev/steppy.git
cd steppy

python -m venv venv
.\venv\Scripts\activate          # Windows
# source venv/bin/activate       # Linux/macOS
pip install -e .
```

If you modify the player TypeScript (`packages/player/src/`), rebuild and re-vendor the JS into the Python package, then commit the result:

```bash
npm --prefix packages/player install   # once
npm --prefix packages/player run build
for f in steppy-player steppy-cursor-legacy steppy-video; do
  cp packages/player/dist/$f.js packages/player/dist/$f.js.map src/steppy/_player_bundle/
done
git add src/steppy/_player_bundle/*.js src/steppy/_player_bundle/*.js.map
```

Only those three are vendored: `dist/` also holds the in-app tour overlay (`steppy-tour.js`), which has no Python consumer and is meant for apps through npm (`@steppy/player/tour`; publish-ready, not on npm yet: until then, depend on a built `packages/player` or its `npm pack` tarball).

The release pipeline rebuilds these from scratch for each tagged release, but the committed copies are what makes `pip install git+...` work for everyone else without needing Node.

### After install

```bash
# Install Playwright browsers (one-time)
playwright install chromium

# Verify
steppy --help
```

---

## Releasing a new version (maintainers)

The release pipeline lives in [.github/workflows/release.yml](.github/workflows/release.yml) and runs on tag push.

```bash
# 1. Bump version in pyproject.toml
#    version = "X.Y.Z"

git add pyproject.toml
git commit -m "Release vX.Y.Z"

# 2. Tag and push
git tag vX.Y.Z
git push origin main --tags
```

`packages/*/package.json` carry the same version (`tests/test_versions_aligned.py`
enforces it), so bump them in the same commit.

CI then:
1. Validates the tag matches `pyproject.toml`'s `version` and the npm packages' versions.
2. Builds the player bundles (`npm run build`).
3. Vendors all three into `src/steppy/_player_bundle/`.
4. Builds the Python wheel and sdist.
5. Sanity-checks that the three bundles, their third-party license file, and `LICENSE`/`NOTICE` are in the wheel (the build fails loudly if any is missing).
6. Creates a GitHub Release with the wheel and sdist attached.

---

## Quickstart

```bash
# 1. Scaffold a project (config.yaml, an example scenario, .gitignore, schema)
steppy init            # add --ci to also drop in a GitHub Actions workflow

# 2. Edit config.yaml (app URLs, roles, credentials) and write your scenarios
#    in scenarios/*.md (see STEPPY-REFERENCE.md)

# 2b. (optional) Bootstrap a scenario draft from a live click-through
steppy record https://app.example.com --role admin --slug my-flow

# 3. Validate them
steppy validate

# 4. Generate the pytest files
steppy generate

# 5. Run them
steppy run --scenario my-flow

# 6. (optional) Capture screenshots and assemble a guide
steppy run --guide
steppy build
```

---

## CLI reference

### Scaffold

```bash
steppy init                                  # scaffold in the current directory
steppy init my-project                       # scaffold into a subdirectory
steppy init --ci                             # also write a GitHub Actions workflow
steppy init --no-skills                      # skip the packaged Claude skills
steppy init --app-dir ../invoice-app         # also put the app-side skill in the app's repository
```

`init` writes `config.yaml` (with a `$schema` header for editor autocomplete),
`scenarios/example--first-flow.md`, `credentials.yaml.example`,
`steppy-config.schema.json`, and a `.steppy/scaffold.json` manifest, and appends
the right `.gitignore` entries. It never overwrites existing files, so it is safe
to re-run in an existing project: it adds what is missing, merges the manifest, and
leaves the starter scenario out when `scenarios/` already has some.

By default it also installs four packaged Claude skills into `.claude/skills/`,
split by job so an AI assistant in the project is expert on the surface it is
actually being asked about:

| Skill | Covers |
|---|---|
| `steppy-scenario-author` | the scenario grammar, guides, `steppy build`, PDF and video |
| `steppy-template` | styling and theming — guides, the player, print, the run report |
| `steppy-ci-report` | CI wiring, `steppy report`, publishing to Pages, the coverage map and its badge |
| `steppy-app-tours` | wiring in-app tours into the app's own code: the overlay, the Angular adapter, test-ids, CSP |

Pass `--no-skills` to skip them. `--ci` / `--ci-gitlab` add the matching CI
pipeline. The app-side skill is only useful where the app's code is, which is often
another repository: `--app-dir <app>` installs it there too, and records it so
`steppy update` keeps it current.

```bash
steppy update                                # reconcile a project after upgrading steppy
steppy update --check                        # report-only: scan for deprecated usage (CI-able)
```

`update` runs **after** you upgrade the `steppy-dev` package (pip/uv/pipx own the
upgrade itself). It refreshes the Steppy-owned scaffold assets — the config
schema, the Claude skills, and the CI workflow — from the newly-installed
version: files you haven't touched are replaced silently, and any file you've
edited is left intact with the new version written alongside as `<file>.new` (so
you never lose edits, and never silently miss an update). Skills shipped since the
project was scaffolded are installed, and skill folders copied in by hand before
Steppy packaged them (`steppy`, `steppy-angular-export`) are reported for you to
delete. Starter files you own (`config.yaml`, your scenarios) are never touched. It then reports any deprecated
scenario/config usage and the changelog highlights since your last version.
`steppy update --check` is the report-only compat scan on its own — it writes
nothing and exits non-zero on breaking usage, so CI can gate on it.

### Record

```bash
steppy record https://app.example.com --role admin --slug my-flow   # record a draft scenario
steppy record --role admin --slug my-flow                           # start URL defaults to the role's app URL
steppy record --slug my-flow --no-auth                              # fresh session (e.g. to record the login flow)
steppy record --slug my-flow --placeholderize-all                   # turn every recorded value into a ${VAR} ref
```

`record` wraps `playwright codegen`: a headed browser opens, you perform the flow, and
closing the window writes a **draft** `scenarios/<slug>.md` — a reviewable starting point,
not a finished scenario. Refine it with the `steppy-scenario-author` skill, then `steppy
validate` / `steppy generate`.

It records as an **authenticated** session by default (loading `.auth/<role>.json` if
present — run `steppy auth login <role>` first) and **never bakes secrets**: a
username/password/submit sequence collapses to a single `- action: login` (credentials
come from `config.yaml`), password/token fields become `${VAR}` env refs, and the command
prints a ready-to-paste `screenshot.mask` block to redact passwords in screenshots.
Selectors prefer `data-testid`; the selector lint runs on the output.

> **V1 (CLI plumbing).** A foundational recorder — the client-facing browser-extension
> version with a visual blur picker and instant preview is a later phase. Recorded
> selectors and mappings are best-effort against Playwright codegen output; always review
> the draft before committing.

### Scenarios

```bash
steppy validate                              # validate all scenarios
steppy validate scenarios/my-flow.md         # validate a single file
steppy list                                  # tabular view of all scenarios
steppy list --tag e2e                        # filtered
```

### Generate

```bash
steppy generate                              # regenerate all test files
steppy generate scenarios/my-flow.md         # specific scenario
steppy generate --tag e2e                    # filter by tag
steppy generate --dry-run                    # preview changes
steppy generate --force                      # overwrite without prompting
```

### Run

```bash
steppy run                                   # run all generated tests
steppy run --scenario my-flow                # specific scenario
steppy run --tag e2e                         # filter by tag
steppy run --headed                          # visible browser
steppy run --screenshots                     # enable screenshot capture
steppy run --no-asserts                      # skip `expect()` calls
steppy run --locales fr,nl                   # parametrize over locales
steppy run --layouts desktop,mobile          # parametrize over viewports
steppy run --guide                           # guide-capture mode (see below)
steppy run --trace                           # save a Playwright trace per FAILED test
steppy run -- -v -x --tb=short               # passthrough args to pytest
```

See [Continuous integration](#continuous-integration-opt-in) for JUnit/HTML
reports and the shipped GitHub Actions template.

### Guide capture and assembly

```bash
steppy run --guide                           # runs every `guide.enabled` scenario
                                             # across all locales × layouts,
                                             # auto-enables screenshots + --no-asserts

steppy guide --tag user-guide-admin --locale fr
steppy guide --guide                         # auto-discovers all user-guide-* tags
                                             # from `guide.enabled` scenarios
steppy guide --guide --pdf                   # + one PDF next to each guide
steppy guide --guide --handbook              # + one handbook (HTML + PDF) per
                                             # role/locale/layout
steppy guide --guide --video                 # + a narrated MP4 (+ .vtt + poster)
                                             # next to each guide (needs ffmpeg)
```

Captures land in `output/screenshots/` and guides in `output/guides/`. Both are
configurable in one place — `output.screenshots_dir` / `output.guides_dir` in
`config.yaml` — and the capture side and every reader (`guide`, `build`,
`bundle`, the parallel failure harvest) resolve the same value, so they cannot
drift apart. `--screenshots-dir` / `--output-dir` still override for one
invocation. See [Artifact output paths](STEPPY-REFERENCE.md#8-artifact-output-paths)
for what happens if you point them outside `output/`.

See [PDF export](#pdf-export-opt-in) and [Video guide mode](#video-guide-mode-opt-in) below.

### Build

```bash
steppy build                                 # target chosen by steppy.guide.config.ts
steppy build --config path/to/config.ts
steppy build --dry-run
steppy build --pdf --handbook                # + per-page PDFs and a site handbook
                                             # (static target)
steppy build --video                         # + a narrated MP4 per locale/layout for
                                             # each guide declaring modes: ['video']

steppy video check                           # is ffmpeg/Chromium/the voice ready?
```

See [the `steppy build` section](#steppy-build) and [Video guide mode](#video-guide-mode-opt-in) below.

### In-app tours (opt-in)

With `tours: {enabled: true}` in `config.yaml`, `validate` also compiles the
tours of every `tour:`-enabled scenario, and `generate` writes their files and
appends a CI replay to each scenario's test.

```bash
steppy tours                     # write the tour files only (tests untouched)
steppy tours --check             # CI: exit 1 when the app's committed tour files are stale
steppy tours --build-id 2026.10  # stamp another build id (this command only)
steppy tours --explain           # also say why each step was left out
```

See [In-app tours](#in-app-tours-opt-in-1) below.

### Authentication

```bash
steppy auth login editor                      # interactive (headed) login
steppy auth login editor --auto               # headless, with a form role's credentials (CI, scripts)
steppy auth status                           # cached session state per role
steppy auth clear                            # clear everything
steppy auth clear editor                      # clear a single role
```

### Seed scripts

```bash
steppy seed base                             # run seed/base.py via subprocess
```

### Parallel execution (opt-in)

```bash
steppy run --workers 3                       # 3 isolated Docker stacks
steppy run --workers 3 --no-build            # skip auto-build of worker image
steppy stacks cleanup                        # rescue orphaned steppy-run-* projects
```

Requires a `parallel:` block in `config.yaml` + per-app `internal_url`. See [Parallel execution](#parallel-execution-opt-in) below.

### Coverage map (opt-in)

```bash
steppy run --coverage                        # record route/API/JS signals + merge into the map
steppy run --coverage --scenario my-flow     # works for a single scenario in isolation
steppy coverage refresh                      # pull declared surface from openapi.json (+ sitemap)
steppy coverage report --json gaps.json      # tested / declared-unreached / discovered-uncovered
steppy coverage triage set /webhooks/stripe --method POST --api my-api --flag non_ui
steppy coverage merge --run-id <id>          # rescue: re-merge a recorded run
```

Requires a `coverage:` block in `config.yaml` for manifest/API attribution. See [Coverage map](#coverage-map-opt-in-1) below.

### Results bundle (opt-in)

```bash
steppy run --bundle                          # record per-test events + JUnit XML alongside the run
steppy bundle create                         # package output/ into output/bundles/steppy-bundle-<id>.tar.zst
steppy bundle create --dry-run               # show what would be archived, write nothing
steppy bundle inspect <bundle>               # print the manifest without extracting
steppy bundle verify <bundle>                # re-hash every member against the manifest
```

A portable, self-describing archive of one run. See [Results bundles](#results-bundles-opt-in-1) below.

---

## `steppy build`

Generates guide artifacts from a TypeScript config. The config's `target` field picks the output:

- `static` (default) — self-contained, framework-agnostic static site (HTML pages,
  manifest, no runtime deps); embed anywhere, including via the `@steppy/react` /
  `@steppy/vue` wrapper packages
- `angular` — standalone Angular 21 components + routes + ngx-translate i18n bundles
  (set `target: 'angular'` explicitly)

### Config (`steppy.guide.config.ts`)

```ts
import { defineGuideConfig } from 'steppy';

export default defineGuideConfig({
  scenariosRoot: './e2e/scenarios',
  screenshotsDir: '../steppy/output/screenshots',
  outputDir: './src/app/features/guides/generated',
  assetsDir: './public/assets/editor',
  role: 'editor',
  locales: ['fr', 'nl'],
  defaultLocale: 'fr',
  layouts: ['desktop', 'mobile'],
  defaultLayout: 'desktop',
  site: { title: 'Demo Guides' },
  guides: [
    { slug: 'onboarding',    title: 'Onboarding',    scenario: 'editor/onboarding.md',    modes: ['stepbystep'] },
    { slug: 'invoices', title: 'Invoices', scenario: 'editor/invoices.md', modes: ['stepbystep', 'interactive', 'video'] },
  ],
  navbar: { order: ['onboarding', 'invoices'] },
});
```

Install the types package in your Angular app: `"steppy": "file:../path-to-steppy/packages/steppy"` in `devDependencies`. The host app also needs `@ngx-translate/core` — generated components inject `TranslateService` and resolve titles via the translate pipe.

> **Why two config languages?** Tests use `config.yaml` (no toolchain beyond
> Python); builds use `steppy.guide.config.ts` because it buys type-checked DX
> via `defineGuideConfig`, and `steppy build` already requires Node for `tsx`
> regardless. If you build guides without Node, a JSON config variant is a
> possible future addition.

### Angular output

```
outputDir/
  <role>.routes.ts             # exports <ROLE>_ROUTES (lazy-loaded)
  <role>.nav.json              # navigation tree (translation-key references)
  i18n/<role>/<locale>.json    # per-locale bundle, keyed under uppercase <ROLE>
  {slug}/
    {slug}.component.ts        # standalone component, ngx-translate driven
    {slug}.content.ts          # CONTENT[locale][layout] HTML fragments
    {slug}-interactive.component.ts  # if modes includes 'interactive'
    {slug}-video.component.ts        # if modes includes 'video' and --video was passed
    {slug}-stepbystep-{locale}.html  # human-readable debug fragment
  _groups/<group>.group.component.ts   # if groups configured
  _onboarding/onboarding.component.ts  # if onboarding enabled
```

Screenshots are copied to `assetsDir`. Image URLs are absolute `/assets/<role>/<slug>/[<locale>/][<layout>/]step-NN.png` — point `assetsDir` at your host's `public/assets/<role>/` to wire them up directly.

Integration:

```ts
// app.routes.ts
import { EDITOR_ROUTES } from './features/guides/generated/editor.routes';
export const routes: Routes = [
  { path: 'guides', children: EDITOR_ROUTES },
];

// app.config.ts — merge generated i18n bundles into TranslateService
import frTranslations from './features/guides/generated/i18n/editor/fr.json';
import nlTranslations from './features/guides/generated/i18n/editor/nl.json';
```

Steppy never touches `app.routes.ts` or your navbar — integration is purely via imports.

### Static output

Set `target: 'static'` in the config. Emits a self-contained site with HTML pages at `<slug>/<locale>.html`, an `index.html` per locale, a `manifest.json`, a shared `assets/guides.css`, `.nojekyll` and `_headers`. Screenshots co-located under `outputDir/assets/<slug>/`. Deploy anywhere static.

A guide gets one sibling page per extra mode — `<slug>/interactive.html` for the tour, `<slug>/video.html` for the narrated MP4 — and each guide page links whichever of them it has.

### SEO / indexing (`seo` block)

Static builds are **private by default** — every page gets `<meta name="robots" content="noindex,nofollow,noarchive">` and a `robots.txt` with `Disallow: /`, so an accidentally-exposed client guide site is not indexable. For a public showcase site, opt in:

```ts
export default defineGuideConfig({
  // …
  target: 'static',
  seo: {
    mode: 'public',                        // default: 'private'
    baseUrl: 'https://guides.example.com', // required when public
    description: { fr: 'Guides produit', nl: 'Producthandleidingen' }, // optional site-wide default
    openGraph: true,                       // optional OG / Twitter cards
    lastmod: '2026-09-14',                 // optional <lastmod> on every sitemap entry
  },
  guides: [
    { slug: 'onboarding', title: 'Onboarding', scenario: 'onboarding.md',
      description: 'Get started in 5 minutes' }, // optional per-guide meta description
  ],
});
```

Public mode emits `sitemap.xml` (absolute URLs + per-locale `hreflang` alternates), a `robots.txt` pointing at it, `<link rel="canonical">` + `hreflang` alternates (with `x-default`) on every indexable page, `<meta name="description">` (guide `description` → its title; index uses `seo.description` → site title), and — when `openGraph: true` — Open Graph / Twitter tags (`og:image` is a guide's first screenshot). Indexing controls are **defense-in-depth, not access control** — keep private sites behind auth. Static target only (the Angular host owns its own `<head>`).

**One canonical URL per guide.** A guide is rendered up to four ways — `<slug>/<locale>.html` per locale, plus `interactive.html` and `video.html` — from the same steps. The per-locale pages are canonical; `interactive.html` and `video.html` point `rel=canonical` at the default-locale one and stay out of `sitemap.xml`, so a guide occupies one indexable URL per language rather than three or four near-identical ones. The site root is canonical as `https://…/` rather than `https://…/index.html` for the same reason. Bare directory URLs (`/<slug>/`, `/<group>/`, `/getting-started/`) serve a meta-refresh stub to the default locale; stubs are never listed in the sitemap. The handbook (`--handbook`) carries `noindex` and is excluded too — it duplicates every guide on the site.

**`sitemap.xml` is generated, not templated.** Steppy ships no `sitemap.xml.j2`; the document is built by `src/steppy/sitemap.py`. Dropping a `templates/static/sitemap.xml.j2` into your project still takes over — that is the way to reach the parts of the protocol Steppy does not emit, such as `<image:image>` over a guide's screenshots, `<video:video>`, or a `<sitemapindex>` split. The context is `urls` (each with `.loc`, `.alternates[].hreflang` / `.href`, `.lastmod`) plus `lastmod`.

**`lastmod` is opt-in.** With `seo.lastmod` unset no `<lastmod>` is emitted at all, which is what keeps a build byte-reproducible. There is deliberately no `'auto'` mode: `actions/checkout` stamps every file at clone time, so an mtime-derived value would read as "now" on every build — which crawlers learn to ignore — and screenshots are not in git at all. Feed it the commit date instead (`git log -1 --format=%cI`). Note that a single site-wide value over-reports, since a commit touching one guide did not change the others; it buys standards-compliance and control, not ranking. `<changefreq>` and `<priority>` are not emitted — Google and Bing both ignore them.

**Locales must be BCP-47 — on both sides.** `config.yaml`'s `locales:`, `steppy run --locales`, `steppy guide --locale` and the build config's `locales` are all validated at load, because every one of them becomes a screenshot directory segment that the other side reads back; an unchecked entry only defers the failure to a place that cannot explain it. Each entry in `locales` is validated at config load (`fr`, `fr-BE`, `pt-BR`, `zh-Hant`, `es-419`, …), because a single malformed `hreflang` makes a search engine discard the *whole* alternates cluster. The tag you write is kept **verbatim** in filenames and screenshot directories — it is also your app's i18n key and your `guide-text:` map key — while `hreflang`, `<html lang>` and `og:locale` get the canonical spelling, so `fr_BE` is accepted and becomes `<html lang="fr-BE">` at `…/fr_BE.html`.

A regional locale resolves *content* through its bare language, so adding `fr-BE` does not mean rewriting every scenario: `guide-text: { fr: … }`, a `{fr: …}` `LocalizedText` map and an `i18n.localstorage.values` entry keyed `fr` all serve `fr-BE`, and an exact `fr-BE:` key still wins where you write one. When the app cannot be switched into a locale at all — no matching `i18n.localstorage.values` entry, not even by language — the run **warns** rather than skipping quietly, because the screenshots get captured and filed under that locale either way.

---

## PDF export (opt-in)

Two flags, on both `steppy guide` and `steppy build`. Steppy does not lay out a PDF itself — it prints the guide pages it already renders through headless Chromium, so the `@media print` rules in the templates are exactly what the PDF shows (no animated cursor, a step never split across a page break, tall captures scaled to fit instead of clipped).

| Flag | `steppy guide` | `steppy build` (static target) |
|---|---|---|
| `--pdf` | `<slug>.pdf` next to every generated guide page | `<slug>/<locale>.pdf` per guide page, plus a "PDF" link in the page header |
| `--handbook` | `<role>-handbook.html` + `.pdf` per (role, locale, layout) | `handbook/<locale>.html` + `.pdf` for the whole site, linked from the index |

```bash
steppy run --guide                           # capture first
steppy guide --guide --pdf --handbook
steppy build --pdf --handbook
```

The **handbook** is every guide in one document — cover, table of contents, then each guide starting on a fresh page (grouped under their group titles in the build target, ordered by slug in `steppy guide`). It is written as real HTML next to the PDF, so it doubles as a single-page read.

Paper settings live in `config.yaml` and are shared by both commands:

```yaml
pdf:
  format: A4            # A0–A6 | Letter | Legal | Tabloid | Ledger
  landscape: false
  margin: { top: 12mm, right: 12mm, bottom: 14mm, left: 12mm }
  print_background: true    # keep the step badges / role chip coloured
  scale: 1.0                # 0.1–2.0
  page_numbers: true        # footer: guide title + "N / M"
  timeout: 30000            # ms per rendered page
```

Notes:

- **Chromium is required** (`python -m playwright install chromium`); the error says so if it is missing.
- One browser renders the whole batch, so a large (scenarios × locales × layouts) project pays a single launch.
- A page whose screenshots don't finish loading is still printed, with a warning — a PDF missing one image beats no PDF.
- Multi-layout build pages print the guide's **default layout** (one PDF per guide and locale). The layout toggle is a viewing preference; the file name stays predictable.
- Chromium's print-to-PDF cannot emit PDF bookmarks or page numbers inside a table of contents, so the handbook's TOC links to in-document anchors only.
- The handbook page carries `noindex` (in both SEO modes) and stays out of `sitemap.xml` — it duplicates every guide's content, which search engines penalise. It is the one static page that does not include the shared SEO `<head>` partial, so it carries no canonical either. Override `templates/guide.book.html.j2` if you want it indexed.
- **A PDF cannot carry a `robots` meta**, so the published `handbook/<locale>.pdf` used to be the only indexable form of a page that is deliberately `noindex` — and `<slug>/<locale>.pdf` competes the same way with its own HTML. Every static build now emits a `_headers` sidecar setting `X-Robots-Tag: noindex` on PDFs (on everything, in private mode). **Read the limitation**: Netlify and Cloudflare Pages honour `_headers`; GitHub Pages and GitLab Pages — the two the shipped CI scaffolds target — ignore it, and neither can serve a custom header at all. It is emitted for the same reason `.nojekyll` is: meaningful where it is read, harmless where it is not, and not access control anywhere.
- `robots.txt` and `_headers` are only honoured at the **origin root**. A GitHub Pages *project* site is served under `/<repo>/`, so a correct `seo.baseUrl` there carries a path and both files land somewhere nothing reads them; the build warns when it detects this. Canonical links, `hreflang` and `sitemap.xml` are absolute and unaffected, and every page still carries its own robots meta.
- Angular builds reject `--pdf`/`--handbook`: that target emits components for a host app to render, so there is no page for Steppy to print.
- To retheme, override `--steppy-print-max-image-height` (default `180mm`) in your `templates/` copy — it is the cap that keeps a full-page capture on one page.

---

## Video guide mode (opt-in)

Narrated walkthrough videos, synthesized from the screenshots and cursor
choreography a run already captured — one MP4 per (guide × locale × layout).
Nothing is screen-recorded: Steppy replays the same timeline the interactive
player uses, frame by frame, in headless Chromium, and pipes the stills to
ffmpeg. So the video is a function of the scenario, not of how fast the render
machine happens to be, and it regenerates on every release in every language
alongside the tests that prove the flow still works.

> Not to be confused with `steppy run --video`, which records Playwright
> debugging footage of *failed tests*. Different command, different artifact.

**Two gates, both required.** The guide declares the mode (a content decision,
versioned in the build config); the flag arms the render (a cost decision — a
render is minutes per guide, and a paid TTS provider bills per character).

```ts
// steppy.guide.config.ts
guides: [
  { slug: 'invoices', title: 'Invoices', scenario: 'editor/invoices.md',
    modes: ['stepbystep', 'interactive', 'video'] },
]
```

```bash
steppy video check                 # verify ffmpeg / Chromium / voice, in 5s
steppy run --guide --locales fr,nl --layouts desktop,mobile
steppy build --video               # MP4 + WebVTT + poster per guide/locale/layout
steppy guide --video               # same, beside each generated guide page
steppy video cache                 # what the caches hold; --prune-older-than / --max-size / --clear
```

| Flag | `steppy guide` | `steppy build` |
|---|---|---|
| `--video` | `<slug>.mp4` + `.vtt` + `.jpg` next to every generated guide page | `<locale>[-<layout>].mp4` per guide, a `<slug>/video.html` player page (static) or a `<slug>-video.component.ts` host (Angular) |

Both build targets are supported. The static target emits a sibling
`video.html` (like `interactive.html`) with a locale/layout switcher; Angular
emits a component wired into the existing mode toggle, with the media served
from `assetsDir` like every other asset.

### Narration

Voiceover text is the per-locale `guide-text` you already wrote — there is no
new authoring surface. The provider is chosen entirely in config:

```yaml
video:
  fps: 24                    # cost knob: a render is frames × screenshot latency
  width: 1280                # height follows the capture aspect, so a mobile
                             # layout yields a portrait video. NOTE: cursor travel
                             # is timed over display distance, so changing width
                             # changes the video's duration.
  captions: band             # band | off
  crf: 23
  preset: medium
  intro_ms: 1200
  outro_ms: 1500
  min_step_ms: 1500          # floor on a step's on-screen time
  narration_pad_ms: 400      # breathing room after the voiceover
  branding: { logo: ./assets/logo.png, accent: '#d24f2c' }
  stamp:
    enabled: true            # burn a provenance line into every frame
    text: "{version} · {date}"
    version: ${STEPPY_RELEASE:-dev}
  tts:
    provider: none           # captions only — the default: no install, no bill
    voice: { fr: fr_FR-siwis-medium, nl: nl_NL-mls-medium }
    api_key: ${ELEVENLABS_API_KEY}
```

| `provider` | What it is |
|---|---|
| `none` *(default)* | Burned-in captions, silent track. No install, no network, no cost. |
| `piper` | Free, offline, neural, deterministic. `pip install piper-tts` ([OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl)); voices download on first use into `.steppy/voices/`. Run as a subprocess, never imported — it is GPL-3.0 and deliberately not a Steppy dependency. |
| `espeak` | Free, offline, no download. Audibly robotic; a fallback, not a finish. |
| `elevenlabs` `openai` `azure` `google` | Cloud voices. Set `api_key` (and `region` for Azure). |
| `http` | Any REST API, described directly under `video.tts.http` — no preset, no code, no Steppy release needed. |
| *your own* | Register a `steppy.tts_providers` entry point. |

Swapping providers is a config edit and nothing else: the vendor presets are
data rows over one generic HTTP engine, so no code path branches on vendor.

Notes:

- **ffmpeg is required**, including with `tts.provider: none` — a silent captioned video still has to be encoded. `steppy video check` tells you before a long render does.
- **Chromium is required** (`python -m playwright install --with-deps chromium`). Use `--with-deps`: a bare install has no CJK/Arabic fonts, and captions are burned into the frame permanently.
- Put the API key in the environment (`${ELEVENLABS_API_KEY}`), not in `config.yaml` — a plaintext key earns a hygiene warning, and an unresolved `${VAR}` is rejected with the variable name rather than becoming a confusing vendor 401.
- Frames are deduplicated: a hold or a voiceover pause reuses the previous still instead of re-screenshotting. Holds dominate a narrated video, so this is most of the render budget.
- Each step is stretched to fit its voiceover, so narration is never cut off mid-sentence.
- A WebVTT caption track and a poster frame are emitted beside every MP4.

### What an edit actually costs

Narration is synthesized **one clip per step**, keyed on the *text* rather than
the step's position, and assembled onto the timeline afterwards. So editing one
sentence re-synthesizes one sentence — reordering steps, inserting a step, or
adding a guide that reuses a line costs nothing at all, and a paid provider is
never re-billed for text that has not changed.

Videos are cached whole, because they have to be: a step's duration changes with
its narration, and every later step shifts with it, so no rendered frame
survives an edit. The two caches therefore invalidate on different things.

| Cache | Key includes | So it re-runs when |
|---|---|---|
| `.steppy/tts-cache/<sha>.wav` | provider, engine version (or the `http` request shape), text, locale, voice, model, speed, format | that step's wording changes — nothing else |
| `.steppy/video-cache/<sha>.mp4` | the manifest, **screenshot bytes**, clip bytes and durations, branding, the player bundle, and the pixel-affecting `video:` settings | any of those change. Re-capturing screenshots re-renders even if the manifest is identical; setting an API key or a timeout does **not** |

Whitespace is normalized before hashing, so re-wrapping a `guide-text` line is
not a re-bill. Both caches are written atomically — an interrupted render leaves
no entry rather than a truncated one that would be served forever.

Both grow monotonically. `steppy video cache` reports what they hold;
`--prune-older-than <days>` and `--max-size <MB>` reclaim least-recently-*used*
entries first (a lookup touches what it reads, so a clip rebuilt into every
release stays young), `--clear` empties them, and `--tts` / `--video` scope any
of those to one cache.

---

## In-app tours (opt-in)

The scenarios that test a flow and document it can also teach it inside the
app: a tour is a sequence of popovers over the live application, each pointing
at the element a step acts on. Steppy compiles tours from your scenarios —
never by hand — and **CI replays every tour**, so renaming a test-id a tour
depends on fails the build with the tour, the step and the test-id named,
instead of breaking the tour in production.

1. Enable tours and say where your app serves them from:

   ```yaml
   # config.yaml
   tours:
     enabled: true
     targets:
       - type: app-assets
         path: ../invoice-app/public/tours
   ```

2. Opt a scenario in with `tour: true` in its frontmatter (or a `tour:` block
   with an id, a title, an entry route). Steps can be fine-tuned with
   `tour: skip|keep`, `tour-text`, `tour-advance` and `tour-placement`.

3. Mount the overlay once in your app and start a tour from a button. Import
   `@steppy/player/tour` from your app's own code (it registers the element),
   then:

   ```html
   <steppy-tour-host fallback-manifest="/tours/tours.manifest.json"></steppy-tour-host>
   ```

   Angular apps use `@steppy/angular` instead: `provideSteppyTours()`,
   `<steppy-tours />` once in the shell, and the `SteppyTours` service.

| What | Where |
|---|---|
| Canonical tour files (manifest + one file per tour and locale, SRI-pinned) | `output/tours/<build-id>/` |
| The copy your app bundles | each `app-assets` target (flat) |
| The copy a static guide site serves, for remote mode | `<site>/tours/<build-id>/` (`steppy build`, `guide` target) |
| The CI replay | `test_<slug>__tour` in the scenario's own test file |

A step points at one test-id value, never a CSS selector; tour files are data,
never code; the overlay never clicks for the user. The full reference —
configuration, annotations, how steps are inferred, the host element's API and
events, [localization](docs/tours.md#localization) (buttons ship in English,
French, Dutch and German; the tour follows the app's language mid-tour), React,
Vue and Angular wiring, theming, CSP and CORS, the replay — is
[docs/tours.md](docs/tours.md). `fixtures/tour-demo/` (plain JavaScript) and
`fixtures/tour-demo-angular/` (Angular, PrimeNG, ngx-translate) are working
examples driven by the same scenarios.

## Parallel execution (opt-in)

`steppy run --workers N` spins up N isolated Docker Compose stacks pulling scenarios from a shared queue. **Strictly opt-in** — `steppy run` (no flags) and `steppy run --workers 1` never touch Docker.

### When to use it

A 1-hour sequential suite typically completes in ~20–25 min with `--workers 3`. Each stack runs its own copy of your app + chromium, so you need RAM + CPU headroom.

### Setup

1. Add a `parallel:` block to `config.yaml` and an `internal_url` per role:

   ```yaml
   apps:
     editor:
       url: "http://localhost:4200"           # host-facing (single-worker mode)
       internal_url: "http://editor-app:3000"  # inside the docker network
       # ...
   parallel:
     workers: 3                                # default if --workers omitted
     compose_file: "docker-compose.steppy.yml"
     # Optional — capture auth at the in-network origin before dispatching
     # workers. Needed for `mode: storage-state` (see "Pre-authentication in
     # worker mode" below); omit it entirely if you don't use storage-state auth.
     # pre_auth:
     #   command: "python auth_login.py --internal"
   ```

2. Make sure your `compose_file` declares healthchecks for every service referenced by `depends_on: condition: service_healthy`. The coordinator pre-flights this and refuses to start otherwise.

3. Run:

   ```bash
   steppy run --workers 3
   ```

   First parallel run auto-builds the `steppy-worker:<version>` image (~3 min). Subsequent runs reuse it.

### Prerequisites

- **Docker Compose 2.20+** (uses the `!reset` YAML tag to clear inherited port mappings).
- **Per-role `internal_url`** for every role used by selected scenarios.
- **Healthchecks** on every service referenced via `depends_on: condition: service_healthy`.

### Rescue command

If a parallel run is force-killed before tearing down its stacks:

```bash
steppy stacks cleanup
```

Lists every `steppy-run-*` Compose project and tears each one down.

### Parallel + guide mode

`steppy run --workers N --guide` composes cleanly. Each scenario is claimed by exactly one worker; that worker's pytest invocation handles every (locale × layout) cell sequentially in one process — browser launched once, auth amortized. Screenshots land in the shared `output/` bind mount at the same paths a single-worker `--guide` run produces, so `steppy guide --guide` and `steppy build` (pure disk readers) work unchanged.

### Scenarios that must not run concurrently (`exclusive:`)

Work stealing assumes every scenario is independent of every other. Some are not:
two flows that mutate the same record, drain the same mailbox, or consume the same
third-party sandbox cannot be in flight at once. Declare an **exclusion group** in
frontmatter and Steppy will never run two of its members at the same time:

```yaml
---
title: Checkout with a stored card
exclusive: billing
---
```

Any scenario naming `billing` is serialised against every other one that does.
Different groups still run concurrently, and ungrouped scenarios are untouched —
so `{s1, s3}` in `billing` and `{s2, s5}` in `mail` gives you `s1 ∥ s2` and
`s3 ∥ s5`, but never `s1 ∥ s3`.

Mechanically, the coordinator collapses each group into a **single queue task**
before any container starts; one worker claims it and runs the members in
sequence, one pytest process each. There is no lock and no waiting state, so a
worker dying mid-group is reported and retried like any other failure. The run
prints its census next to the queue line:

```
queued 11 scenario(s) in 6 task(s) across 4 worker(s)
exclusion groups: billing[3], mail[2] — 2 group(s) serialising 5 scenario(s) into 2 task(s)
```

Four things worth knowing before you declare one:

- **It costs wall-clock.** The run can never finish faster than the group's total
  duration. A group is free while that total stays under *(suite total ÷ workers)*;
  past that, the makespan is the heaviest group's total.
- **Non-overlap only** — not ordering, and not indivisibility. Members may run in
  any order, and a retry pass legitimately re-runs only the ones that failed.
- **The name is your assertion, and nothing verifies it.** Two differently-named
  groups that actually contend on the same system still run concurrently. If a
  flow touches two shared resources, every scenario touching *either* must use the
  same name — naming the combination (`stripe-and-mailbox`) does **not** exclude
  it against a scenario named `stripe`. `steppy validate` warns about a group with
  a single member, which is what a typo looks like.
- **Scope is one run.** Two concurrent `steppy run` invocations — CI plus a dev
  box against one shared sandbox — do not see each other's groups. And it is inert
  without `--workers N ≥ 2`, correctly: one pytest process is already sequential.

### Pre-authentication in worker mode (`pre_auth`)

`mode: storage-state` auth needs care in parallel mode. Playwright storage state is **origin-bound** — a session captured on the host (`http://localhost:4200`) does *not* authenticate a worker, which reaches the app at its `internal_url` (`http://web:80`). Left alone, every scenario in a worker run redirects to login.

`parallel.pre_auth` closes the gap: before dispatching workers, the coordinator runs your `command` in a one-off `steppy-preauth` container **inside the stack network**, gated (Compose `service_completed_successfully`) to finish before any worker claims a scenario. Point it at whatever logs in at the internal origin and writes `.auth/<role>.json`:

```yaml
parallel:
  pre_auth:
    command: "python auth_login.py --internal"   # runs in-network; captures the internal origin
    timeout: 600                                  # optional (default 600s)
    # image: my/custom:tag                        # optional (defaults to the worker image)
```

- The command runs with `STEPPY_BASE_URL_OVERRIDE` (`{role: internal_url}`) in its env, the project mounted read-only, and `.auth/` mounted read-write — so it can resolve each role's `internal_url` and write the cache all workers share.
- It's **deduped once per run** across the N stacks: an O_EXCL lock elects a single winner that logs in; the other stacks wait for its sentinel. Make the command idempotent (skip roles whose session is still fresh) to keep repeat runs cheap.
- The preauth container waits for every app service that declares a **healthcheck** before running — add a healthcheck to your web/frontend service so the login doesn't race startup.
- Leaving `pre_auth` unset renders no preauth service and keeps dispatch byte-identical to before. (`bypass` / `token` auth, or a host-and-internal same-origin setup, don't need it.)

### Known caveats

- **Shared `.auth/<role>.json`.** With `auth.storage_state_ttl > 0` (default 3600s), one worker's cached session may be reused by another worker whose stack has different signing keys. If each stack runs an isolated app + DB, set `auth.storage_state_ttl: 0` to force per-worker re-auth. For `mode: storage-state`, remember sessions are also **origin-bound** — use `pre_auth` (above) to capture the in-network origin, or workers will redirect to login.
- **Seed scripts run inside the worker container.** Hard-coded `http://localhost:5432` URLs are unreachable. Have seeds read the `STEPPY_BASE_URL_OVERRIDE` JSON env var (`{role: internal_url}`) and fall back to the host URL when unset (single-worker mode).
- **Only `output/` comes back from the container.** The project itself is mounted read-only and `output/` is the one writable bind mount, so an `output.screenshots_dir` pointing outside it cannot produce host-visible captures. The coordinator warns at startup if you have configured one.
- **v0.1 maturity.** Parallel mode is single-machine and Linux-container only (Docker + Compose 2.20+). A worker crash mid-scenario is fatal — the scenario is not requeued (`Queue.orphaned_running_tasks()` reports it). Use `steppy stacks cleanup` to tear down orphaned stacks after a force-kill.

---

## Continuous integration (opt-in)

Steppy runs anywhere pytest runs. Two pieces make it CI-native:

- **Reports** — set a `report:` block in `config.yaml` and `steppy run` emits the
  matching artifacts:

  ```yaml
  report:
    junit_xml: "./output/reports/junit.xml"   # native JUnit (no extra deps)
    html: "./output/reports/report.html"      # needs: pip install 'steppy-dev[report]'
    trace: on-failure                          # off | on | on-failure
  ```

  JUnit XML is ingested natively by most CI systems. `trace: on-failure` (or the
  `steppy run --trace` flag) saves a Playwright trace zip under `output/traces/`
  for each failed test — open it with `playwright show-trace <zip>` for a
  step-by-step, DOM-snapshotted replay. The per-failure full-page screenshot +
  console/network dump under `output/screenshots/_failures/` is always produced.

- **Workflow templates** — ready-to-copy pipelines ship in the package for both
  vendors: `templates/ci/github-actions.yml` and `templates/ci/gitlab-ci.yml`
  (checkout → install → `playwright install chromium` → `steppy generate` →
  `steppy run --bundle` → `steppy report` → upload). `steppy init --ci` writes the
  GitHub one, `--ci-gitlab` the GitLab one; map your credential secrets onto the
  `${VAR}` refs in `config.yaml`. Each carries a commented, opt-in Pages job — see
  [Publishing to Pages](#publishing-to-pages).

---

## Run report (opt-in)

`steppy report` turns one run into a self-contained static site: what passed, what
failed, **which step it failed on**, and the screenshot at that moment.

```bash
steppy run --bundle          # records per-test and per-step events
steppy report                # → output/report/index.html
```

It reads a live `output/` tree, a `.tar.zst`/`.tar.gz` results bundle, or a
directory somebody already unpacked one into — all three produce the same report:

```bash
steppy report                              # this project's output/
steppy report steppy-bundle-*.tar.zst      # a bundle downloaded from CI
steppy report ./unpacked-bundle -o public  # after tar -xf
```

### What it shows

- **Failure causes, grouped.** One broken selector across forty scenarios reads as
  one row with a count, not forty tracebacks — the same normalisation the parallel
  console summary uses. The full tail is shown once per cause.
- **The failing step, named.** `Failed at step 7 — Submit the order`, with the
  step timeline around it (title, action, elapsed) and the captures for each step.
  Step numbers are the scenario's own and stay sparse: a step skipped by
  `only-in:` or `layouts:` is a gap, never renumbered.
- **A scenario × (locale, layout) matrix**, built from what actually ran — a flow
  deselected by a disjoint frontmatter `layouts:` does not grow an empty column.
- **Coverage buckets and the e2e-surface percentage**, when the run recorded them.
- **`report.json`** — the same model, versioned (`steppy_report: 1`) and
  deterministic, so CI can diff two of them. Shape:
  [`contracts/report.schema.json`](contracts/report.schema.json).

### Screenshots

`--screenshots failed` (the default) copies the captures of variants that did not
pass, plus every failure screenshot. `all` copies everything — worth knowing that
on a 4-locale × 2-layout corpus that is hundreds of megabytes. `none` renders text
only. Captures identical across layouts are copied once.

Traces and videos are **named, not copied**: a Playwright trace needs
`playwright show-trace` to open, so a link would be a dead end.

### Honest about what it cannot know

A report of a run made without `--bundle` says so and lists the scenarios it found
captures for, rather than rendering an empty page. A run with no per-step events
says *that* instead of drawing an empty timeline. Coverage rebuilt from a detached
bundle's raw records refuses to report assert *strength*, because the scenario
files it would need to count assert steps in never travel — every endpoint would
otherwise read as weakly covered.

**The exit code describes rendering, not the run.** A report of a failing run exits
0. Gate your build on `steppy run`, or read `totals.exit_code` from `report.json`.

### Publishing to Pages

The site is relative and self-contained, so publishing it is a copy. Both shipped
CI templates carry the job, commented out, plus the warnings below.

> **Pages is public.** Your screenshots show your app with real-looking data in it.
> `screenshot.mask` redacts fields at capture time and the report marks every page
> `noindex` with a `Disallow: /` robots.txt — but a leak is permanent and cannot be
> taken back. This is for showcase and open-source repos; for client work, read the
> report from the CI artifact instead. GitLab Pages can be restricted to project
> members, which is the one place it beats GitHub's for this.

Deploy as an **artifact, never a `gh-pages` branch** — screenshots committed on
every run grow the repository's history forever.

### Coverage badge

With `--coverage` recorded, the site includes a shields.io endpoint at
`badge.json`:

```markdown
![e2e surface](https://img.shields.io/endpoint?url=https://<owner>.github.io/<repo>/badge.json)
```

The number is **`tested / (declared − non_ui)`** over your declared routes and API
endpoints — not line coverage. Declare `coverage.non_ui` in `config.yaml` first
(health checks, webhook receivers, OAuth callbacks, internal cron): triage flags
set with `steppy coverage triage` live in the gitignored `.steppy/coverage.db`, so
a fresh CI checkout has none and those endpoints would count against you.

---

## Results bundles (opt-in)

A **results bundle** is one run's artifacts in a single portable file: a `.tar.zst` (or `.tar.gz`) holding your `output/` tree **exactly as Steppy wrote it** — no path rewriting — plus three generated members.

```
manifest.json          what this run was, what it produced, sha256 of every file
junit.xml              every worker's JUnit XML merged into one document
events/                per-test records: <scenario>__<locale>-<layout>.jsonl
output/                screenshots, sidecars, coverage records, guides, failures, traces, videos
```

Because the tree is unrewritten, `tar -xf` then `steppy guide` or `steppy build` just works — the bundle is an archive of a run, not a new format to convert back out of.

### Producing one

```bash
steppy run --guide --bundle          # capture events + JUnit alongside the normal run
steppy bundle create                 # → output/bundles/steppy-bundle-<run-id>.tar.zst
```

`--bundle` is opt-in because it changes the pytest invocation. Without it a bundle is still valid, but `manifest.tests[]` will be empty — there is nothing on disk recording what passed.

It also records step boundaries, so a failure names the step rather than only the test:

```
outcome=failed  scenario=order-lifecycle  locale=fr  layout=mobile  failed_step=7
```

`failed_step` is the step that started and never finished, which is why a test that dies in fixture teardown reports `null` instead of blaming its last step. Export `STEPPY_STEP_EVENTS=0` to keep everything else and drop the per-step records.

It composes with everything else, including `--workers N`: each worker writes into the shared `output/` mount under per-scenario filenames, and `steppy bundle create` merges them.

### Reading one

```bash
steppy bundle inspect  bundle.tar.zst          # manifest summary; --json for the raw manifest
steppy bundle inspect  bundle.tar.zst --json   # feed it to jq, or to a CI annotation
steppy bundle verify   bundle.tar.zst          # re-hash every member; non-zero on any mismatch
```

### Compression

`.tar.zst` when the optional extra is installed, `.tar.gz` otherwise — automatically, with a notice:

```bash
pip install 'steppy-dev[bundle]'     # adds zstandard
steppy bundle create --format gz # or force gzip explicitly
```

An explicitly named output file decides the codec, so `-o run.tar.zst` never silently produces gzip content.

### The manifest is a published contract

`manifest.json` is versioned (`steppy_bundle: 1`) and its shape is committed at
[`contracts/bundle-manifest.schema.json`](contracts/bundle-manifest.schema.json),
generated from the models in `src/steppy/contracts/` and drift-guarded by a test.
Write a consumer against it in any language.

Two rules make it safe to build on: fields are only ever **added**, never
repurposed, and readers default what is absent. So a newer Steppy's bundle stays
readable by an older consumer, and an artifact `kind` you have never seen is
something to store rather than something to fail on.

### Useful properties

- **Reproducible.** The same `output/` tree bundles to the same bytes: members are sorted, timestamps and ownership are normalised, and the run's own creation time comes from the run rather than the clock.
- **Content-addressed.** Every archived file carries its sha256 in the manifest, so a consumer can fetch only what it does not already have.
- **Honest about what is missing.** `--max-size` drops the cheapest-to-lose artifacts first (videos, then traces, then guides — all regenerable), sets `truncated: true`, and lists every drop. Screenshots and their sidecars are never dropped.

### In CI

```yaml
- run: steppy run --guide --bundle
- run: steppy bundle create
- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: steppy-bundle
    path: output/bundles/
```

---

## Coverage map (opt-in)

`steppy run --coverage` records what each scenario actually exercises and merges it into a persistent map, so you can ask the inverse question: **what is NOT yet tested?** Everything is deterministic — no LLM calls in recording, merging, or gap computation — and framework-agnostic (URL, History API, V8 coverage, network requests; the app under test can be Angular, React, Vue, …).

Three signals per scenario run, captured by the generated conftest (env-gated on `STEPPY_COVERAGE=1`, zero overhead when off):

- **Route trail** — every URL the run visited, SPA-aware: Playwright's `framenavigated` fires for History-API changes, and an injected hook tags push/replace/pop/hash transitions. Dynamic segments cluster (`/invoices/123` + `/invoices/456` → `/invoices/:id`).
- **API endpoints** — every request's `{method, url}`, attributed to a configured API by base-URL prefix and matched against its OpenAPI path templates.
- **Source modules** — V8 JS coverage via CDP (Chromium), mapped back to source files through source maps (read from `coverage.sourcemap_dir` on disk; inline `data:` maps captured at record time). Degrades gracefully: no map → bundle-level granularity; non-Chromium → routes/endpoints still work.

### Flow

```bash
steppy run --coverage --scenario my-flow   # 1. record (works per-scenario) + auto-merge
steppy coverage refresh                    # 2. declared surface from openapi.json/sitemap.xml
steppy coverage report --json gaps.json    # 3. gap buckets
```

The report buckets every endpoint/route as **tested** (declared ∩ exercised; *strong* when an asserting scenario covered it, *weak* when only assert-free or `--no-asserts`/`--guide` runs did), **declared-unreached** (in the manifest, never hit — the key bucket for directing new scenarios), or **discovered-uncovered** (seen in a trail, weakly covered). Endpoints that aren't UI-reachable (payment webhooks, cron callbacks) get a persisted triage flag so they stop polluting the actionable list:

```bash
steppy coverage triage set /webhooks/stripe --method POST --api my-api --flag non_ui --note "payment callback"
```

**In CI, declare them in `config.yaml` instead.** The flag above is stored in
`.steppy/coverage.db`, which is gitignored — so a fresh checkout has an empty store,
every endpoint reads `unknown`, and any percentage computed there is wrong for
reasons nobody can fix from CI:

```yaml
coverage:
  non_ui:
    - "GET /health"
    - "/webhooks/*"
    - "POST /internal/cron/*"
```

Grammar is `[<METHOD> ]<glob>`. `*` crosses `/`, a pattern applies across every
configured API, and a method-qualified pattern never matches a route.
`steppy coverage report` warns about a pattern that matches nothing — a renamed
endpoint leaves a stale rule that silently inflates the number. An explicit
`triage set --flag ui_testable` still wins over a pattern; nothing is ever written
back to the store.

The percentage this settles is **`tested / (declared − non_ui)`**, printed by
`steppy coverage report` and available as a shields.io badge:

```bash
steppy coverage report --badge output/badge.json     # also --badge-surface endpoints|routes|all
```

### Storage

- **Raw records** (lossless observations): `output/coverage/raw/<run_id>/<scenario>/*.json` — one per page fixture. All interpretation happens at merge time, so normalization rules can evolve without recapturing.
- **The map**: sqlite at `.steppy/coverage.db` (configurable `coverage.store_path`). Lives outside `output/` so wiping run artifacts never deletes triage flags. Gitignore `.steppy/` (or commit it if you want team-shared triage).
- **Merge semantics**: keyed `(node, scenario_id)` and idempotent — re-running a scenario *replaces* its prior contribution; nothing accumulates stale rows. Merge↔refresh order doesn't matter (observed endpoints re-link onto newly declared templates).

### Config

```yaml
coverage:
  apis:
    - name: editor-api
      openapi: "http://localhost:8080/openapi.json"   # URL or local file path
      base_urls: ["http://localhost:8080", "http://editor-backend:8080"]
  sitemap: null                       # optional: declares the route surface
  sourcemap_dir: "../app/dist"        # local build output holding *.js.map
  store_path: "./.steppy/coverage.db"
  records_dir: "./output/coverage"
```

`base_urls` must mirror each OpenAPI `servers[].url` (including path prefixes like `/api`) and should include Docker-internal `internal_url` origins so records captured by parallel workers classify correctly. `--coverage` composes with `--scenario`, `--tag`, `--guide` (guide runs record as weak evidence), and `--workers N` (workers record into the shared `output/` mount; the coordinator merges after the run).

**v1 limits**: a mid-scenario full document navigation may drop pre-navigation JS coverage (CDP limitation; routes/endpoints unaffected); modules have no declared manifest, so they're reported as a covered inventory only; `sourcemap_dir` must correspond to the build actually being served (mismatch falls back to bundle granularity, never an error).

---

## Project layout

```
steppy/
├── src/steppy/
│   ├── cli.py              # CLI entry point (Typer app)
│   ├── init_cli.py         # `steppy init` — scaffold a project
│   ├── update_cli.py       # `steppy update` — reconcile with the installed version
│   ├── record_cli.py       # `steppy record` — click-to-Markdown recorder (draft scenarios)
│   ├── coverage_cli.py     # `steppy coverage` commands (lazy-imported)
│   ├── tours_cli.py        # `steppy tours` + the validate/generate tour hooks
│   ├── video_cli.py        # `steppy video check` — video toolchain readiness
│   ├── _cli_common.py      # shared CLI helpers (error handling, config load)
│   ├── config.py           # config loader (config.yaml)
│   ├── config_schema.py    # config JSON Schema generation + drift guard
│   ├── compat.py           # kept-forever deprecated aliases
│   ├── presets.py          # UI-kit selector presets (primeng/material/…)
│   ├── models.py           # pydantic models (scenarios + config)
│   ├── build_models.py     # pydantic models for the `steppy build` config
│   ├── parser.py           # scenario parser + selector lint
│   ├── generator.py        # codegen → tests/e2e/
│   ├── selection.py        # scenario/locale/layout selection (shared by run + parallel)
│   ├── auth.py             # auth manager
│   ├── totp.py             # RFC 6238 TOTP helper (MFA login)
│   ├── guide.py            # guide assembler
│   ├── build.py            # `steppy build` pipeline
│   ├── pdf.py              # print-to-PDF over generated guide HTML (lazy-imported)
│   ├── video.py            # narrated MP4 renderer (lazy-imported, leaf module)
│   ├── tts/                # voiceover providers: registry, presets, cache, track (lazy-imported)
│   ├── coordinator.py      # parallel-mode coordinator (lazy-imported)
│   ├── worker.py           # parallel-mode worker (lazy-imported)
│   ├── coverage/           # coverage map: store, normalize, sourcemap, merge, manifest, gaps (lazy-imported)
│   ├── tours/              # in-app tour compiler, emitter, replay planner, build publish (lazy-imported)
│   ├── contracts/          # versioned wire contracts: results bundle, run report, in-app tours
│   ├── naming.py           # product-derived names (tour element, kinds, kill switch); TS twin in player/src/ui
│   ├── tour_spec.py        # in-app tour enums, patterns and size caps (stdlib leaf)
│   ├── testids.py          # selector → single test-id extraction (stdlib leaf)
│   ├── buildid.py          # tour build-id grammar + git resolution (stdlib leaf)
│   ├── _parallel/          # queue, compose, image, lifecycle, progress
│   ├── _player_bundle/     # vendored player + cursor + video JS (committed to git)
│   ├── templates/          # bundled fallback templates — GENERATED from root templates/ (gitignored)
│   └── skills/             # bundled Claude skills — GENERATED from root skills/ (gitignored)
├── packages/
│   ├── player/             # TypeScript: Lit <steppy-guide> + cursor animator + video timeline,
│   │                       #   and the in-app tour overlay <steppy-tour-host> (./tour, not vendored)
│   ├── steppy/             # npm types package (defineGuideConfig)
│   ├── react/              # @steppy/react — thin <steppy-guide> wrapper
│   ├── vue/                # @steppy/vue — thin <steppy-guide> wrapper
│   └── angular/            # @steppy/angular — the in-app tour adapter (provideSteppyTours, <steppy-tours>)
├── templates/              # CANONICAL Jinja2 templates — the single source of truth;
│                           #   edit here (the bundled copy above regenerates on build)
├── skills/                 # CANONICAL Claude skills (scenario-author, template, ci-report, app-tours);
│                           #   installed by `steppy init`; bundled copy regenerates on build
├── contracts/              # generated JSON Schemas + shared golden fixtures (contracts/fixtures/)
├── docs/                   # docs/tours.md (in-app tours), docs/design/, brand assets
├── fixtures/               # demo-guide (build fixture), tour-demo (in-app tour demo app + project),
│                           #   tour-demo-angular (the same demo in Angular + PrimeNG)
├── scenarios/              # example .md scenario files
├── seed/                   # seed scripts (run via subprocess)
├── tests/                  # unit tests (tests/e2e/ is generated, gitignored)
├── output/                 # gitignored: screenshots, assembled guides
├── config.yaml             # global configuration
├── pyproject.toml
└── .github/workflows/release.yml   # tag push → build wheel → GitHub Release
```

---

## Real-world scenarios

The `scenarios/` directory ships a small, runnable set that between them exercise
every action *family* — a good starting point to copy from:

- [`admin--create-project.md`](scenarios/admin--create-project.md) — navigate, click, fill, `select-option`, `contains(...)`.
- [`admin--edit-settings.md`](scenarios/admin--edit-settings.md) — the component drivers (`expand`, `toggle`, `fill-date`, `pick-time`, `confirm`) + the `count(sel, >=N)` comparator.
- [`editor--manage-items.md`](scenarios/editor--manage-items.md) — `within`/`match` disambiguation, `only-in`/`layouts` conditional steps, `pick-date`, plain `select`.
- [`cross--task-approval.md`](scenarios/cross--task-approval.md) — a multi-role (editor → admin) flow.

---

## Troubleshooting

- **`playwright install`** — the generated tests need browser binaries. Run
  `playwright install chromium --with-deps` once (per machine / CI runner).
- **`steppy build` needs Node** — the guide build reads a TypeScript config via
  `npx tsx`, so Node ≥ 18 must be on `PATH`. `generate`/`run` do **not** need Node.
- **See the full error** — commands print a friendly one-liner by default. Add
  `--verbose` (or set `STEPPY_DEBUG=1`) to get the full traceback.
- **`validate` warnings vs errors** — brittle-selector notices print as yellow
  `WARNING`s and do **not** fail validation; unknown roles, malformed steps, and a
  too-new `steppy:` version are red `ERROR`s that do.
- **Config typos** — with the `$schema` header that `steppy init` writes, a YAML-aware
  editor flags unknown keys inline; otherwise `extra = "forbid"` reports them at load.
- **"In-app tours for build … not found"** (`steppy build`) — the guide target copies the tree
  `steppy generate` / `steppy tours` wrote; run one of them first, with the same `tours.build_id`.
- **An app's tour covers what a non-Steppy E2E suite clicks** — set
  `globalThis.__STEPPY_TOURS_DISABLED__ = true` in that suite's browser contexts (Steppy's own
  generated tests already do). See [docs/tours.md](docs/tours.md#kill-switch).

---

## Development

```bash
# Install in editable mode
pip install -e .

# Run unit tests
python -m pytest tests/ --ignore=tests/e2e -q

# Build the player workspace
npm --prefix packages/player install
npm --prefix packages/player run build
npm --prefix packages/player test          # Vitest suite
npm --prefix packages/player run size:tour # in-app tour overlay size budget

# In-app tours in Chromium (needs the player build above and `playwright install chromium`)
STEPPY_TOUR_E2E=1 python -m pytest tests/integration/test_tour_replay_it.py tests/integration/test_tour_player_it.py -q

# After editing player TS, re-vendor the three wheel bundles (not the tour overlay)
for f in steppy-player steppy-cursor-legacy steppy-video; do
  cp packages/player/dist/$f.js packages/player/dist/$f.js.map src/steppy/_player_bundle/
done
```

See [STEPPY-REFERENCE.md](STEPPY-REFERENCE.md) for the complete scenario file format reference and [CLAUDE.md](CLAUDE.md) for architectural notes.

---

## License

Steppy is licensed under the [Apache License, Version 2.0](LICENSE). See
[NOTICE](NOTICE) for attribution, including the third-party code bundled into the
player JavaScript.

Contributions are accepted under the same license — see [CONTRIBUTING.md](CONTRIBUTING.md).
