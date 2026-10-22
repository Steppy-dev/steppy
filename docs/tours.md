# In-app tours

**Status:** v1, opt-in. Off unless `config.yaml` has `tours: {enabled: true}`.

The Markdown scenarios that drive your Playwright tests and your guides also
compile into **in-app tours**: popovers over the live application that walk a
user through a flow, pointing at the real buttons and fields. The overlay is a
small web component, `<steppy-tour-host>`, that you mount once in your app.

The promise: **CI replays every tour.** `steppy generate` appends a replay to
each tour's scenario test, which follows the tour the way a user would. When a
developer renames a test-id the tour points at, the build fails, naming the
tour, the step and the test-id, instead of the tour quietly breaking in
production.

- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Scenario annotations](#scenario-annotations)
- [How a scenario becomes a tour](#inference)
- [Output files](#output-files)
- [Commands](#commands)
- [Adding the player to your app](#player)
- [Angular](#angular)
- [React and Vue](#react-and-vue)
- [Localization](#localization)
- [Theming and accessibility](#theming)
- [Security](#security)
- [CI replay](#ci-replay)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)

## How it works

```text
scenarios/*.md + config.yaml (tours:)
  ├─ steppy generate ─▶ tests/e2e/test_<slug>.py      (+ test_<slug>__tour, the CI replay)
  │                     └▶ output/tours/<build-id>/     (canonical: manifest + <tour>.<locale>.json)
  │                     └▶ <your app>/…/tours/          (app-assets target: a flat copy)
  ├─ steppy tours    ─▶ the same tour files, without touching tests
  └─ steppy build    ─▶ <guide site>/tours/<build-id>/   (static target: a byte copy)

your app bundle: import '@steppy/player/tour'
  <steppy-tour-host fallback-manifest="/assets/tours/tours.manifest.json">
  host.start('create-invoice')
```

Five principles hold everywhere:

1. **Data, never code.** A tour file is JSON validated against a strict
   contract. It cannot carry HTML, scripts, selectors or URLs outside two
   allowed shapes. The overlay's code comes only from your app's bundle.
2. **Test-ids only.** A step points at one `data-testid` value (the attribute
   is configurable). Never a CSS selector, never an XPath.
3. **The user acts.** The overlay highlights and explains. It never clicks,
   types or submits for the user.
4. **Non-blocking.** The page stays fully usable: the overlay lets clicks
   through, and every tour can be closed at any time.
5. **Locked to a build.** Each tour set carries the build id of the app it was
   compiled for, so an app never runs tours meant for another version.

## Quick start

1. Enable tours and point an `app-assets` target at a folder your app serves:

   ```yaml
   # config.yaml
   tours:
     enabled: true
     targets:
       - type: app-assets
         path: ../invoice-app/public/tours
   ```

2. Opt a scenario in:

   ```markdown
   ---
   title: Create an invoice
   tour: true
   ---
   ```

3. Compile and check:

   ```bash
   steppy validate    # tour errors fail validation
   steppy generate    # tests (+ replays) and tour files
   ```

4. Add the overlay to your app. It is not on npm yet: build it once and
   depend on it through `npm pack` (see [Installing the module](#player)).
   In an Angular app, use the [adapter](#angular) instead. Otherwise mount
   the host in your app shell and start a tour from a button:

   ```html
   <steppy-tour-host fallback-manifest="/tours/tours.manifest.json"></steppy-tour-host>
   <button id="show-me-how">Show me how</button>
   ```

   ```js
   // In your app's bundle (no inline script: the overlay runs under a strict CSP).
   import '@steppy/player/tour';
   document.querySelector('#show-me-how').addEventListener('click', () =>
     document.querySelector('steppy-tour-host').start('editor--create-invoice'),
   );
   ```

   Without `tour.id`, the tour id is the scenario's file name.

5. Rebuild and restart the app, so it serves the new tour files. Then run the
   scenario; its tour replay runs with it:

   ```bash
   steppy run --scenario editor--create-invoice
   ```

A complete working example lives in `fixtures/tour-demo/`: a small invoice app
with the overlay mounted (a dialog, a dropdown, and on a phone a menu drawer, a
long page and a confirmation), the Steppy project that compiles its three
tours at two layouts, and a server (`python fixtures/tour-demo/serve.py`).

## Configuration

All keys live in a `tours:` block in `config.yaml` (snake_case, like the rest
of the file). An absent block and `enabled: false` behave identically:
nothing tour-related is written, and every generated file is byte-identical to
a project without tours.

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `false` | Master switch. |
| `default` | bool | `false` | Opt every scenario in. A scenario's own `tour:` still wins. |
| `build_id` | string | `git-sha` | See [Build ids](#build-ids). |
| `locales` | list | `config.locales` | Locales to emit. |
| `default_locale` | string | `config.default_locale` | Always emitted; the fallback when a user's locale has no file. |
| `guide_base_url` | URL | none | Where your published guide lives. Gives tours an "Open the full guide" link and is the only origin step-text links may point at. |
| `test_id_attribute` | string | `data-testid` | The attribute targets are looked up by (`data-*` only). |
| `layouts` | map | none | Layout name → media query, for scenarios with layout-specific steps. See [Layouts](#layouts). |
| `replay` | bool | `true` | Append a CI replay to each tour's scenario test. |
| `targets` | list | — | At least one; see [Targets](#targets). |

`output.tours_dir` (default `./output/tours`) is where the canonical tree goes.

### Build ids

A build id names the version of the app a tour set belongs to:
`^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?$`. It becomes a path segment
(`tours/<build-id>/`), so `..` and separators cannot occur.

| Value | Resolves to |
|---|---|
| `git-sha` | The first 12 hex digits of `HEAD` in the project's repository. |
| `git-sha:<path>` | The same for the repository at `<path>` — when your scenarios live beside the app, not inside it. |
| a literal | Itself: `2026.10.1`, `release-42`. |
| `"${APP_BUILD_ID:-git-sha}"` | An environment variable, falling back to the git form. |

An unresolved `${…}` loads fine (so `steppy run` works anywhere) and fails
when an id is actually needed, naming the variable. There is no dirty-tree
suffix: two runs over the same tree produce identical bytes.

Recipes:

- **One repository.** `git-sha`. Run `steppy tours` as part of the app's
  build, so the committed tour files carry the commit they were generated from.
- **Scenarios in another repository.** `git-sha:../invoice-app`.
- **CI-stamped builds.** `build_id: "${APP_BUILD_ID:-git-sha}"` with the same
  variable your app build uses. On pull requests, `GITHUB_SHA` is the merge
  commit, not the branch head.

`steppy tours`, `steppy generate` and `steppy build` must resolve the
same id; `build` refuses a tree for another one.

### Targets

**`app-assets`** — a flat copy, written next to the canonical tree:
`tours.manifest.json` plus one `<tour-id>.<locale>.json` per tour and locale.

```yaml
targets:
  - type: app-assets
    path: ../invoice-app/public/tours   # Angular 18+: public/…; older apps: src/assets/…
```

- The folder must be served by your app at a URL you then give the host's
  `fallback-manifest`. Steppy never derives that URL.
- It is pruned on each run, but only of files the previous manifest listed,
  and only when that manifest is a Steppy one: a foreign file in the folder
  is never touched, and a foreign `tours.manifest.json` stops the run.
- It must not sit inside `steppy build`'s `outputDir`/`assetsDir`, which the
  build deletes. `build` refuses to run when it does.
- When the app's checkout is missing (CI that has only the scenarios),
  `generate` warns and skips the copy (`app-assets-skipped`); `steppy tours`
  treats it as an error.
- Tour files are pinned by SHA-384 hashes over their exact bytes. If your app
  repository converts line endings, add `*.json -text` (or `eol=lf`) for that
  folder to its `.gitattributes`.

**`guide`** — `steppy build` (static target) republishes the canonical tree
at `<site>/tours/<build-id>/`, for [remote mode](#remote). The build only copies
it, after checking it: it must exist, be for the configured build id, and every
file must still match its hash. Each build publishes its own id only. The
Angular build target cannot host tour files and warns instead.

```yaml
targets:
  - type: app-assets
    path: ../invoice-app/public/tours
  - type: guide
guide_base_url: https://guide.example.com/
```

### Layouts

A scenario can author one logical step twice, once per layout (`layouts:
[desktop]` on a sidebar click, `layouts: [mobile]` on the drawer twin). A tour
needs to know which twin applies on the user's screen, so `tours.layouts` maps
each layout name to a media query:

```yaml
screenshot:
  layouts:
    desktop: {width: 1280, height: 800}
    mobile: {width: 390, height: 844}
tours:
  layouts:
    desktop: "(min-width: 768px)"
    mobile: "(max-width: 767.98px)"
```

The overlay skips steps whose layouts do not match, and the replay skips the
same steps. The compiler checks each query against the layout's
`screenshot.layouts` width when it can parse it (`layout-media-mismatch`), and
warns when two queries can match the same width.

## Scenario annotations

**Frontmatter** — `tour:` opts the scenario in:

```yaml
tour: true                         # or false, or a block:
tour:
  enabled: true                    # default true in a block; `tour: false` opts out
  id: create-invoice               # default: the scenario slug
  title: {en: Create an invoice, fr: Créer une facture}   # default: the scenario title
  entry: /invoices/:invoiceId      # the route the tour starts on (see below)
  role: editor                     # required when the scenario uses several roles
  guide_slug: create-invoice       # the guide page "Open the full guide" links to
```

- `id` is what your app passes to `start()`: 1–64 characters `[A-Za-z0-9_-]`,
  starting with a letter or digit, and not a Windows device name (`con`,
  `nul`, …), because it names files. Ids are unique case-insensitively.
- `title` is shown by apps that list tours (it is in the manifest). A map is
  read per locale: the exact tag, its base language, then the default locale's
  entry, then the scenario title (`title-fallback`).
- `guide_slug` defaults to the scenario slug when the scenario has
  `guide: {enabled: true}`; otherwise there is no guide link unless you set it.
  Either way the link needs `tours.guide_base_url`.

**Step keys** — override what inference decided for one step:

| Key | Values | Effect |
|---|---|---|
| `tour` | `skip`, `keep` | `skip` leaves the step out; `keep` keeps a step inference would skip (shown centred if it has no test-id). |
| `tour-text` | like `guide-text` | The step's tour text, per locale. Wins over `guide-text`. |
| `tour-advance` | `click`, `input`, `change`, `visible`, `manual` | What moves the tour on. Must be something the step's own action does (`advance-not-replayable`). |
| `tour-placement` | `top`, `bottom`, `left`, `right`, `auto` | The preferred side of the target. |

`tour-*` keys on a step inference leaves out are an error unless the step also
says `tour: keep`. Frontmatter `tour:` and step `- tour:` are different keys:
the first opts the scenario in, the second overrides one step. `tour: keep` is
refused on a step with `when:` or `optional:` (`keep-not-allowed`): such a step
may not happen, and a tour cannot branch.

**Advance kinds.** `click` waits for a click on the target; `input` enables
Next once the field has a value and also advances on its `change`; `change`
waits for the control's value or state to change; `visible` shows the step
(centred until its element exists) and advances as soon as the step's own
target is visible — for something that appears as a result of the previous
step (if it is already on screen when the step is reached, the step passes at
once without showing); `manual` waits for Next. Which ones a step accepts depends on its action,
because the CI replay must be able to perform it:

| The step's action | Accepted `tour-advance` |
|---|---|
| `click`, `expand`, a `custom`/`call` with a click cursor | `click` (default), `manual`, `visible` |
| `fill`, `fill-date`, each `fill-form` field, a `custom`/`call` with a type cursor | `input` (default), `change`, `manual` |
| `toggle`, `select`, `select-option`, `pick-date`, `pick-time` | `change` (default), `click`, `manual` |
| `upload` | `change` (default; `manual` behind a `guide-cursor` trigger), `manual` |
| an information step (an assertion or a `custom` with text), a step kept by `tour: keep` | `manual` (default), `visible` |

**Test-ids** are values of `^[A-Za-z0-9_.:/-]{1,128}$` — colons and slashes
are allowed, quotes and spaces are not.

### Worked example

<!-- golden: fixtures/tour-demo/project/scenarios/editor--create-invoice.md -->
```markdown
---
title: Create an invoice
description: An editor opens the invoice list, creates an invoice in the dialog and lands on its detail page.
tags: [tour-demo]
layouts: [desktop, mobile]
guide:
  enabled: true
tour:
  id: create-invoice
  title: {en: Create an invoice, fr: Créer une facture}
  guide_slug: create-invoice
---

### 1. Open the invoice list
- role: editor
- action: navigate
- target: /invoices

### 2. Wait for the list
- role: editor
- action: wait-for
- selector: '[data-testid="invoice-list"]'

### 3. Start a new invoice
- role: editor
- action: click
- selector: '[data-testid="new-invoice-btn"]'
- tour-placement: bottom
- guide-text:
    en:
      title: New invoice
      text: Click **New invoice** to open the invoice form.
    fr:
      title: Nouvelle facture
      text: Cliquez sur **Nouvelle facture** pour ouvrir le formulaire.

### 4. Name the invoice
- role: editor
- action: fill
- selector: '[data-testid="invoice-title"]'
- value: "Quarterly maintenance"
- guide-text:
    en: "Type a title, for example Quarterly maintenance."
    fr: "Saisissez un titre, par exemple Quarterly maintenance."
- tour-text:
    en:
      title: Title
      text: Give the invoice a short title your customer will recognise.
    fr:
      title: Titre
      text: Donnez à la facture un titre court que votre client reconnaîtra.

### 5. Pick the customer
- role: editor
- action: select-option
- selector: '[data-testid="customer-select"]'
- option: 'text:"Acme Corp"'
- guide-text:
    en:
      title: Customer
      text: Choose the customer to bill.
    fr:
      title: Client
      text: Choisissez le client à facturer.

### 6. Save the invoice
- role: editor
- action: click
- selector: '[data-testid="save-invoice-btn"]'
- expect: visible([data-testid="invoice-detail"])
- guide-text:
    en:
      title: Save
      text: Click **Save**. The invoice opens on its own page.
    fr:
      title: Enregistrer
      text: Cliquez sur **Enregistrer**. La facture s'ouvre sur sa propre page.

### 7. The invoice is created
- role: editor
- action: assert
- expect: visible([data-testid="invoice-detail"])
- guide-text:
    en:
      title: Done
      text: Your invoice is saved as a draft. You can send it from this page.
    fr:
      title: Terminé
      text: Votre facture est enregistrée en brouillon. Vous pouvez l'envoyer depuis cette page.
```

It compiles to five steps: New invoice (click, waiting for the list), Title
(input, with the `tour-text`), Customer (change), Save (click), and the detail
page (manual — the assertion has text, so it becomes an information step).
The fill value "Quarterly maintenance" stays out of the tour: tours show
instructions, never test data.

When a scenario logs in first, the tour starts at the first `navigate` after
the last `login`; everything before it is setup the replay runs but the tour
never shows.

<a id="inference"></a>

## How a scenario becomes a tour

**Start point.** With `tour.entry`, the tour starts right after the `navigate`
whose target matches it (`{{var}}` in a target matches `:param` in the
pattern). Without it, right after the first `navigate` following the last
`login`. Steps before the start are setup.

**Each step**, by action:

| Scenario step | In the tour |
|---|---|
| `click`, `expand` | click (`expand` warns `may-not-change`: it only clicks when the section is closed) |
| `toggle` | change (`may-not-change` when `state:` is set) |
| `select`, `select-option`, `pick-date`, `pick-time` | change |
| `fill`, `fill-date` | input |
| `fill-form` | one input step per field, with the same text |
| `upload` | with `guide-cursor`: the cursor's target, manual. Otherwise: change (warns when the target is the bare file input) |
| `assert`, `wait-for`, a trailing `expect:` | no text: a hidden gate — the next step waits for that test-id (`waitFor`), unless a skipped step in between may change the page (`gate-stale`). With text: an information step (manual) |
| `custom`, `call` | with `guide-cursor` click/type: click/input. Text without a cursor: a centred manual step (`unguided-action`). Otherwise skipped |
| `confirm` | skipped (`unguided-action`). With `tour: keep` and text: a manual note shown beside the open dialog, never over it |
| `navigate`, `login`, a reload after the start | skipped (`navigate-in-tour`, `reloads-page`) |
| `hover`, `scroll`, `keyboard`, `wait`, `wait-for-text`, `capture`, steps with `when:` or `optional:` | skipped |
| `only-in: test` steps | dropped |
| `only-in: guide` steps | kept |

A kept step's selector must reduce to one test-id: `[data-testid="x"]`,
`button[data-testid="x"]` and `[data-testid="x"] input` all reduce to `x`;
`match: first` is accepted. Anything else is the error
`target-not-test-id`, with the fix: add a test-id in the app, or mark the step
`tour: skip` or `tour: keep` (centred).

**Text**, per locale L: `tour-text[L]`, then `guide-text[L]` (the exact tag,
then its base language: `fr-BE` reads `fr`). A step with neither shows a
generic instruction in L ("Click the highlighted element.") and the compiler
warns (`generic-text`). Languages are never mixed. A locale is emitted when at
least one step has text in it; the default locale always is.

**Step ids** are the target's test-id, numbered on repeats (`save-btn`,
`save-btn-2`), or a slug of the step heading. Never positions, so inserting a
step does not shift every id.

### Compile errors and warnings

Every message names the tour, the scenario, the step and its heading, and ends
with a fix. **Errors** fail `steppy validate` (exit 1) and stop `generate`
and `tours` before anything is written. **Warnings** never change an exit code.
**Infos** only print with `steppy tours --explain`: they say why a step was
left out.

| Code | Severity | Cause | Fix |
|---|---|---|---|
| `target-not-test-id` | error | A kept step's selector is not one test-id | Add a test-id in the app, or `tour: skip` / `tour: keep` |
| `entry-invalid` | error | `tour.entry`, or the navigation the entry is inferred from, is not a same-origin route pattern (a hash route, another origin, outside the app's base path, a `.`/`..` segment, `%2e` included) | Set `tour.entry` to a pattern such as `/invoices/:invoiceId` |
| `entry-no-match` | error | No `navigate` matches `tour.entry` | Fix the pattern or the step's target |
| `no-entry` | error | No `navigate` to start from (warning when `replay: false`) | Add `tour.entry` or a `navigate` step |
| `tour-role-required` | error | Several roles and no `tour.role` | Set `tour.role` |
| `tour-role-unknown` | error | `tour.role` is not a role the scenario uses | Fix `tour.role` |
| `tour-no-role` | error | No step names a role, so there is no app to run in | Give the steps a `role:` |
| `tour-id-invalid` | error | The id (or the file name used as one) breaks the id grammar | Set `tour: {id: …}` |
| `tour-id-duplicate` | error | Two scenarios produce the same tour id (case-insensitively) | Set distinct `tour.id`s |
| `advance-not-replayable` | error | `tour-advance` the step's action cannot perform (warning when `replay: false`) | Choose an advance the action does (see [Advance kinds](#scenario-annotations)) |
| `advance-needs-target` | error | An advance other than `manual` on a step with no target | Use `manual`, or give the step a test-id |
| `annotation-on-skipped-step` | error | `tour-*` keys on a step the tour leaves out | Add `tour: keep` or remove the keys |
| `annotation-outside-tour` | error | `tour*` keys on a step before the start, on an `only-in: test` step, or on another role's step | Remove them; they would have no effect |
| `skip-and-annotations` | error | `tour: skip` together with other `tour-*` keys | Keep one or the other |
| `keep-not-allowed` | error | `tour: keep` on a `when:`/`optional:` step | Remove it: a tour cannot branch |
| `no-kept-steps` | error | Nothing left to show | Check the start point and annotations |
| `layouts-unmapped` | error | A step restricted by `layouts:` uses a layout `tours.layouts` does not map | Add the layout to `tours.layouts` |
| `layout-media-mismatch` | error | A layout's query disagrees with its screenshot width | Fix `tours.layouts` |
| `limit-exceeded` | error | Over a size cap (100 steps, 200-character title, 2000-character text, 256 KiB file, 1 MiB manifest) | Shorten |
| `scenario-unreadable` | error | A scenario in the directory does not parse or names an unknown role/layout, or an opted-in scenario lies outside `--scenarios-dir` | Fix the file; tours compile from every scenario |
| `build-id` | error | The build id is malformed, cannot be resolved (`git-sha` outside a repository, an unset `${VAR}`), or a checked copy records an invalid one | See [Build ids](#build-ids) |
| `target-path` | error | An output folder is unsafe (the project root, a folder Steppy owns, a project-looking folder), holds a foreign manifest or a foreign file under a tour file's name, or is missing for `steppy tours` | Point the target at a folder of its own |
| `fixture-leak` | warning | Step text repeats a value the test types | Write `tour-text` without it |
| `generic-text` | warning | A step has no text in an emitted locale; the generic hint will show | Add `guide-text` or `tour-text` for it |
| `no-authored-text` | warning | No step has text in the default locale | Add text: every step would show a generic hint |
| `locale-not-emitted` | warning | A configured locale has no text anywhere in the tour, so no file is written for it | Add text in that language, or accept that its users get the default |
| `chrome-strings-missing` | warning | A tour locale has no built-in buttons and hints (only en, fr, nl, de do) | Set the host's `strings` for it (see [Localization](#localization)) |
| `title-fallback` | warning | A `tour.title` map lacks a locale, or a multi-language tour has no `tour.title` at all (reported once for the project) | Add the missing titles |
| `tour-text-locale-untargeted` | warning | A `tour-text` locale the tours do not emit, or a `guide-text`/`tour-text` key that differs from a tour locale only in case or `-`/`_` | Fix the key (`fr-BE`, not `fr_be`) or drop it |
| `unguided-action` | warning | A step the overlay cannot point at (`confirm`, `custom` code that clicks) | Add `guide-cursor`, or accept the centred step |
| `may-not-change` | warning | A conditional action may not fire on replay (`expand`, `toggle` with `state:`) | `tour-advance: manual` or `tour: skip` |
| `upload-target-may-be-hidden` | warning | The tour points at a bare file input, which apps hide | Add `guide-cursor` naming the visible trigger |
| `navigate-in-tour` | warning | A `navigate` after the start: the tour cannot take the user there | Start later (`tour.entry`) or let the user click there |
| `reloads-page` | warning | A reload or a second login inside the tour | Move it before the start if you can; the tour resumes otherwise |
| `escape-in-tour` | warning | An Escape key press inside the tour may dismiss it | Check the replay |
| `entry-query-dropped` | warning | The entry navigation has a query string, which tours ignore | Set `tour.entry` without it |
| `entry-id-segment` | warning | The inferred entry route contains fixture-looking ids | Set `tour.entry` with a `:param` |
| `placement-ignored` | warning | `tour-placement` on a centred step | Remove it |
| `text-link-dropped` | warning | A link to neither an app route nor the guide (or one that resolves off-site, such as `/.//host`) | It renders as text |
| `app-url-unresolved` | warning | `apps.<role>.url` or `tours.guide_base_url` still holds an unset `${VAR}` at compile time | Set the variable where you compile |
| `layout-media-overlap` | warning | Two layout queries match the same width | Make the queries disjoint |
| `app-assets-skipped` | warning | The app-assets folder's parent is missing (`generate` only) | Check out the app, or ignore in scenario-only CI |
| `no-tours` | warning | Tours are enabled but no scenario opts in | Add `tour: true` to a scenario |
| `step-skipped` | info | A step inference leaves out (a hover, a wait, `tour: skip`, …) | — |
| `conditional-skipped` | info | A `when:`/`optional:` step, left out | — |
| `gate-dropped` | info | A check whose selector is not a test-id, so the next step does not wait for it | Point the check at a test-id |
| `gate-stale` | info | A skipped step between a gate and the next tour step may change the page, so that step no longer waits for the gate: any action but `assert`, `wait-for`, `wait`, `wait-for-text`, `hover`, `scroll` and `capture`; `custom` code that calls anything but locating, reading, waiting and asserting; a `call` without `page: false` | Add a `wait-for` on a test-id after that step |
| `reduced-to-host` | info | A `[data-testid="x"] input`-style selector was reduced to its host test-id | — |
| `layout-media-unchecked` | info | A layout query too complex to compare with its screenshot width | The replay still checks it |

## Output files

`tours.manifest.json` lists every tour with its files and their hashes:

<!-- golden: contracts/fixtures/tours/valid/tours.manifest.json -->
```json
{
  "buildId": "demo-0001",
  "defaultLocale": "en",
  "guideOrigin": "https://guide.example.com",
  "kind": "steppy-tours-manifest",
  "layouts": {
    "desktop": "(min-width: 768px)",
    "mobile": "(max-width: 767.98px)"
  },
  "locales": [
    "en",
    "fr"
  ],
  "schema": 1,
  "testIdAttribute": "data-testid",
  "tours": [
    {
      "entry": {
        "route": "/invoices"
      },
      "files": {
        "en": "create-invoice.en.json",
        "fr": "create-invoice.fr.json"
      },
      "id": "create-invoice",
      "integrity": {
        "en": "sha384-1laaSS95oLd/KTWvJEQOHwe6RNHp8BPziMwT5OMOw1IDktCQQYdm6wPW56/pN65T",
        "fr": "sha384-CCTucHAOJC5uWK8CxybLUB2Mp4ScM4dP7fIvxST+rrFDjWcPeC+qohRp+pxdfk90"
      },
      "role": "editor",
      "steps": 5,
      "title": {
        "en": "Create an invoice",
        "fr": "Créer une facture"
      }
    },
    {
      "entry": {
        "route": "/invoices"
      },
      "files": {
        "en": "remove-inactive-customers.en.json",
        "fr": "remove-inactive-customers.fr.json"
      },
      "id": "remove-inactive-customers",
      "integrity": {
        "en": "sha384-vAtAuxDcy+zEybFMnERuU36ErEDkRkNiCj+jkQ1olf9U8s3h6Hdwgz6iTipOwXeT",
        "fr": "sha384-54jR98OGGVfOxcPUKB42eBv/LNw4D6Wbn3NCiJatMWFp79VlaTfEqlsRrZ3urvt1"
      },
      "role": "editor",
      "steps": 6,
      "title": {
        "en": "Remove inactive customers",
        "fr": "Retirer les clients inactifs"
      }
    },
    {
      "entry": {
        "route": "/invoices/:invoiceId"
      },
      "files": {
        "en": "send-invoice.en.json",
        "fr": "send-invoice.fr.json"
      },
      "id": "send-invoice",
      "integrity": {
        "en": "sha384-2dmpOhvm4dI0hCJmSG7Y9Zk9sSU3IVUkvEdnNYQmvZKNbWgmvpeiI5WoCiA1xPCj",
        "fr": "sha384-yOMQ1zFQfu3qQMPy6vkAAG+9qTjpNRL/sNKCNPOX+DsYEdQpu3Z/Z+GhM58PaFnA"
      },
      "role": "editor",
      "steps": 4,
      "title": {
        "en": "Send an invoice",
        "fr": "Envoyer une facture"
      }
    }
  ]
}
```

One file per tour and locale:

<!-- golden: contracts/fixtures/tours/valid/create-invoice.en.json -->
```json
{
  "buildId": "demo-0001",
  "entry": {
    "route": "/invoices"
  },
  "guideUrl": "https://guide.example.com/create-invoice/en.html",
  "id": "create-invoice",
  "kind": "steppy-tour",
  "locale": "en",
  "schema": 1,
  "steps": [
    {
      "advance": {
        "on": "click"
      },
      "id": "new-invoice-btn",
      "placement": "bottom",
      "target": "new-invoice-btn",
      "text": "Click **New invoice** to open the invoice form.",
      "title": "New invoice",
      "waitFor": "invoice-list"
    },
    {
      "advance": {
        "on": "input"
      },
      "id": "invoice-title",
      "target": "invoice-title",
      "text": "Give the invoice a short title your customer will recognise.",
      "title": "Title"
    },
    {
      "advance": {
        "on": "change"
      },
      "id": "customer-select",
      "target": "customer-select",
      "text": "Choose the customer to bill.",
      "title": "Customer"
    },
    {
      "advance": {
        "on": "click"
      },
      "id": "save-invoice-btn",
      "target": "save-invoice-btn",
      "text": "Click **Save**. The invoice opens on its own page.",
      "title": "Save"
    },
    {
      "advance": {
        "on": "manual"
      },
      "id": "invoice-detail",
      "target": "invoice-detail",
      "text": "Your invoice is saved as a draft. You can send it from this page.",
      "title": "Done"
    }
  ],
  "title": "Create an invoice"
}
```

| Field | Meaning |
|---|---|
| `schema` | Contract major version (1). An overlay rejects a major it does not know. |
| `kind` | `steppy-tours-manifest` / `steppy-tour` — marks Steppy's files, so pruning never touches anyone else's. |
| `buildId` | The app build these tours belong to. |
| `integrity` | SHA-384 SRI value of each file's exact bytes. |
| `entry.route` | The route the tour starts on, relative to the app's base URL. |
| `target` / `waitFor` | Test-id values (not selectors). |
| `advance.on` | `click`, `input`, `change`, `visible`, `manual`. |
| `guideUrl` | The "Open the full guide" link, on `guide_base_url`'s origin only. |

Files are canonical: sorted keys, two-space indent, UTF-8, LF line endings, a
trailing newline and no timestamp, so the same scenarios always produce the
same bytes. JSON Schemas are published in `contracts/tour.schema.json` and
`contracts/tours-manifest.schema.json`.

## Commands

- **`steppy validate`** compiles tours and reports their errors (exit 1) and
  warnings (exit unchanged).
- **`steppy generate`** writes tests, appends a replay to each tour's
  scenario test, writes the replay helper `tests/e2e/_steppy_tours.py`, and
  writes the tour files. It compiles *every* opted-in scenario even when you
  generate one file or one tag, because the manifest lists them all.
  `--dry-run` shows what would change.
- **`steppy tours`** writes only the tour files: `--dry-run`, `--check`,
  `--build-id ID` (this command only: `generate` and `build` read
  `tours.build_id`), `--explain`, `-d/--scenarios-dir`. Each run — like
  `generate` — also removes the other build ids' trees under
  `output.tours_dir`.
- **`steppy tours --check`** is the CI freshness gate. It writes nothing
  and exits 1 when an `app-assets` copy — the files your app commits — is
  missing or no longer matches what the scenarios compile to. It compares each
  copy *for the build id that copy records*, and prints a note when that id
  differs from the one resolved now: under `git-sha` a committed copy always
  carries an earlier commit, so the id is not what goes stale. (To check that
  the running app serves the right build, use the replay's
  `STEPPY_TOUR_EXPECT_BUILD_ID`.) The gitignored canonical tree is not
  checked, and with no `app-assets` target there is nothing to compare (exit 0
  when the tours compile). The comparison runs under the environment of the
  check: a `${VAR}` in `guide_base_url` or an app URL must resolve the same as
  where the files were written.
- **`steppy build`** (static) copies the canonical tree into the site; see
  the `guide` [target](#targets). The guide slug `tours` is reserved.
- **`steppy guide --all`** also compiles and writes the tour files.
- **`steppy run`** runs the replays with their scenarios; `--guide` runs
  skip them.

<a id="player"></a>

## Adding the player to your app

This work happens in the app's own code, often in another repository than the
scenarios. The packaged Claude skill `steppy-app-tours` walks an AI assistant
through it: `steppy init --app-dir ../invoice-app`, run in the Steppy project,
installs it in the app's repository, and `steppy update` keeps it current.

### Installing the module

The overlay is the `./tour` entry of `@steppy/player`
(`dist/steppy-tour.js`, with type declarations). The package is ready
to publish but not on npm yet: build `packages/player` and depend on it with
a `file:` path or an `npm pack` tarball. Bundle it with your app — never load
it from a CDN or from the guide site. Angular apps add `@steppy/angular`
on top (see [Angular](#angular)).

```bash
npm --prefix packages/player install && npm --prefix packages/player run build
npm pack ./packages/player     # then: npm install ./steppy-player-<version>.tgz
```

<a id="plain-js"></a>

### Plain HTML / JavaScript

With a bundler, import the module from your own code:

```html
<steppy-tour-host fallback-manifest="/tours/tours.manifest.json"></steppy-tour-host>
<script type="module" src="/js/app.js"></script>
```

```js
// app.js
import '@steppy/player/tour';            // registers <steppy-tour-host>
const host = document.querySelector('steppy-tour-host');
// Client-side routing: let the tour use your router.
host.navigate = (path, { appPath }) => router.navigate(appPath);
host.addEventListener('tour-complete', (e) => console.log('done', e.detail.tourId));
document.querySelector('#help').addEventListener('click', () => host.start('create-invoice'));
```

Without a bundler, serve `dist/steppy-tour.js` (it carries its own copy of
Lit) from your app's origin and load it as a module before your code:
`<script type="module" src="/vendor/steppy-tour.js"></script>`. The demo in
`fixtures/tour-demo/app/` does exactly that.

Mount one host per page, inside your app shell. Importing the module twice (two
bundles on one page) is harmless: the element is registered once. Under
Trusted Types, see [Trusted Types](#trusted-types) for the one policy flag two
copies need.

<a id="host-api"></a>

### Host element reference

| Property | Attribute | Default | Meaning |
|---|---|---|---|
| `fallbackManifest` | `fallback-manifest` | — | The bundled manifest (same origin). |
| `manifest` | `manifest` | — | A remote manifest URL; may contain `{buildId}`. See [remote mode](#remote). |
| `buildId` | `build-id` | the bundled manifest's | The app's build id, for `{buildId}`. |
| `locale` | `locale` | `<html lang>` | Preferred locale; falls back to its base language, then the default. Changing it (or `<html lang>`, while `locale` is unset) mid-tour reloads the running tour in that language. See [Localization](#localization). |
| `guideOrigin` | `guide-origin` | from the bundled manifest | The origin trusted for guide links. A URL is reduced to its origin (`https://guide.example.com/` works). |
| `targetTimeout` | `target-timeout` | `8000` | Milliseconds to wait for a step's element (500–120000). |
| `baseZIndex` | `base-z-index` | `2147483000` | Stacking where the top layer is unavailable. |
| `disabled` | `disabled` | `false` | Turns tours off; stops a running one. |
| `navigate` | — | `location.assign` | `(path, {appPath, tourId}) => …` — how the tour changes routes. `path` includes `<base href>`; `appPath` does not. |
| `source` | — | — | `(tourId, locale, {buildId, manifestUrl}) => file` — supply tour files yourself. Return the JSON text, a `Response`, or the parsed object, directly or as a promise; it is validated like a fetched file. The manifests are still fetched. |
| `strings` | — | — | Chrome string overrides per locale. See [Localization](#localization). |

Methods:

- `start(tourId, {params, step})` resolves `true` once the tour started and
  never rejects: it resolves `false` when the tour could not start (a
  `tour-error` says why) or tours are [turned off](#kill-switch). `params` fill the
  entry route; `step` starts at that step id instead of the first. Starting a
  tour ends any other one on the page (`via: "replaced"`).
- `stop(reason)` ends the tour; `reason` is passed through to `tour-dismiss`.
- `next()` advances the current step, whatever its kind.

State: `isActive`, `current` (`{tourId, stepId, index, total}` or `null`).

<a id="exports"></a>

### Module exports

`@steppy/player/tour` registers the element on import and exports, with
type declarations:

| Export | What |
|---|---|
| `SteppyTourHost`, `defineTourHost()` | The element class, and its idempotent registration (already called on import). |
| `validateManifest(raw)`, `validateTour(raw, ctx)` | The overlay's own validator — the security boundary — for a `source` hook or a server that wants to check files the same way. |
| `DEFAULT_TOUR_STRINGS`, `TourStrings`, `TourStringOverrides` | The built-in chrome strings and their shape. |
| `TOUR_EVENTS`, `TourEventDetailMap`, `TOUR_ERROR_REASONS`, `TOUR_PARTS` | Event names and details, error reasons, `::part` names. |
| `TOUR_HOST_TAG`, `TOURS_KILL_SWITCH_GLOBAL` | `steppy-tour-host` and `__STEPPY_TOURS_DISABLED__`. |
| `TourHostApi`, `TourStartOptions`, `TourPosition`, `TourNavigate`, `TourSource`, … | Types for adapters. |
| `ADVANCE_KINDS`, `PLACEMENTS`, `TOUR_SCHEMA_VERSION` | Contract vocabulary. |

### Events

Dispatched on the host, bubbling and composed. The names have no prefix, so
listen on the host element. Details are plain JSON (`TourEventDetailMap` in the
types):

| Event | Detail |
|---|---|
| `tour-start` | `tourId, buildId, locale, total, source` (`bundled`/`remote`/`custom`)`, resumed, entryNavigated` |
| `tour-step` | `tourId, buildId, locale, stepId, index, total, advance, reason` (`start`/`advance`/`jump`/`skip`/`resume`/`recovered`) |
| `tour-complete` | `tourId, buildId, locale, total` |
| `tour-dismiss` | `…, stepId, index, total, via` (`close`/`escape`/`end`/`api`/`replaced`/`disabled`)`, reason?` |
| `tour-offpath` | `…, stepId, index, total, reason: "target-lost"` |
| `tour-error` | `reason, fatal, message`, and when known `tourId, stepId, testId, kind` (`target`/`waitFor`)`, missing, issues` |

`index` and `total` count only the steps that apply to the current layout.

### Errors

| Reason | Fatal | When |
|---|---|---|
| `target-timeout` | no | A step's element did not appear in time. The popover shows "Can't find this element" with Skip and End; the tour recovers by itself if the element appears. |
| `entry-params` | yes | The entry route needs a parameter neither `start()` nor the URL gives, or navigation failed. `missing` lists the names. |
| `load-failed` | yes | A file could not be fetched, the tour id is unknown, or an HTML page came back instead of JSON. |
| `schema-unsupported` | yes | The file's schema major is newer than this overlay. |
| `invalid-data` | yes | The file broke a contract rule; `issues` says which. Nothing renders. |

<a id="entry"></a>

### Entry routes and params

A tour with an entry route starts there. The current path is matched against
the pattern relative to the document's base URL. When it matches (and agrees
with any params passed to `start()`), the tour starts in place; otherwise the
host builds the route from `start()`'s params plus the current URL's and
navigates through `navigate`. The overlay never navigates any other way.

```js
host.start('send-invoice', { params: { invoiceId: 1001 } });  // → /invoices/1001
```

<a id="remote"></a>

### Remote mode and fallback

The overlay loads the bundled manifest (`fallback-manifest`, same origin)
first, then a remote one (`manifest`) if set, filling `{buildId}` with the
app's build id. The remote copy wins when it loads and validates; any remote
failure falls back to the bundled copy (with a console warning). Remote
requests carry no credentials and refuse redirects.

When the bundled manifest is for the same build, the remote manifest only says
*where* the files are: the bundle decides which tours and locales exist and
pins every remote file to its SRI hash. A tour, a locale or a case variant the
bundle does not list is never fetched from the remote, and a tampered file is
rejected by the browser. This applies to any manifest other than the bundled
one, a same-origin proxy included.

| Deployment | Configure | Notes |
|---|---|---|
| Bundled only (recommended) | `fallback-manifest` | Tours ship and roll back with the app. |
| Remote only | `manifest` + `build-id` | No tour JSON in the app bundle. The guide host is then fully trusted for tour text: nothing pins its files. |
| Both | `manifest` + `fallback-manifest` | The guide site serves, the bundle pins and backs it up. |

For a given build id the remote content equals the bundled content; remote
mode exists to keep JSON out of the app bundle or to share one guide site.

### Resume

The current tour, step and entry params are kept in `sessionStorage`
(`steppy:tour`, 30 minutes), so a full page load — a classic form post, a
link the app does not route — picks the tour up where it was, in the page's
current language. It is cleared when the tour completes, is dismissed or
fails; an expired or malformed record is removed when read.

<a id="kill-switch"></a>

### Turning tours off

Set `disabled` on the host, or `globalThis.__STEPPY_TOURS_DISABLED__ = true`
before your app starts a tour: `start()` then does nothing. Steppy's
generated tests set that global in every browser context when tours are
enabled, so a tour your app starts on its own never covers what a test clicks;
only the tour replays lift it. Other E2E stacks should set it too.

## Angular

`@steppy/angular` (`packages/angular`) mounts the overlay the Angular way:
Angular 20–22, zone.js or zoneless, safe under server rendering, and your app
needs no `CUSTOM_ELEMENTS_SCHEMA`. Like the overlay, it is not on npm yet:
build `packages/player` and `packages/angular`, then depend on
`packages/player` and `packages/angular/dist` — `file:` paths (with
`"preserveSymlinks": true` in the build options, so Angular is not loaded
twice) or `npm pack` tarballs.

```ts
// app.config.ts
provideSteppyTours({
  fallbackManifest: 'tours/tours.manifest.json', // an app-assets target in public/tours
  // Optional: follow your i18n library (default: <html lang>).
  locale: () => {
    const translate = inject(TranslateService);
    return toSignal(translate.onLangChange.pipe(map((e) => e.lang)), { initialValue: translate.getCurrentLang() ?? '' });
  },
}),
```

```ts
// app.component.ts
@Component({
  selector: 'app-root',
  imports: [RouterOutlet, SteppyToursComponent, SteppyTourStart],
  template: `
    <button steppyTourStart="create-invoice">Show me how</button>
    <router-outlet />
    <steppy-tours (tourComplete)="onDone($event)" />
  `,
})
export class AppComponent {}

// anywhere else
inject(SteppyTours).start('send-invoice', { params: { invoiceId: 1001 } });
```

| API | What |
|---|---|
| `provideSteppyTours(config)` | In the root providers. `fallbackManifest`, `manifest`, `buildId`, `guideOrigin`, `targetTimeout`, `baseZIndex`, `disabled`, `strings`, `source` as on the [host](#host-api); `locale`: a string, a signal, or a function run once in an injection context that returns either; `navigate`: `'router'` (default), `'document'` or your own function; `readyTimeout` (10 s). |
| `<steppy-tours>` | The overlay, placed once in the shell. Its inputs override the config. Outputs `tourStart`, `tourStep`, `tourComplete`, `tourDismiss`, `tourOffpath` and `tourError` carry the [event details](#events). |
| `SteppyTours` | `start(id, {params, step})` waits for the overlay to mount and load (up to `readyTimeout`), lets the last call win, and resolves `false` — never rejects — when a tour cannot start. `stop(reason)`, `next()`; signals `active`, `current`, `lastError`; `events`, an Observable delivered inside the zone. |
| `steppyTourStart` | `<button steppyTourStart="create-invoice" [steppyTourParams]="…" steppyTourStep="…">` starts a tour on click. |

What the adapter takes care of:

- **The router.** An entry route is reached with `Router.navigateByUrl(appPath)`,
  run inside the Angular zone — outside it, a zone.js app never renders the
  routed page and the tour times out on its target. A navigation a guard
  refuses or redirects fails the tour at once (`tour-error`, reason
  `entry-params`).
- **Zones.** The overlay's observers, polls and frames run outside the zone, so
  they never trigger change detection; events, outputs and the router come
  back inside it.
- **Loading.** Nothing renders or loads on the server; the overlay module is
  imported in the browser after the first render, and the element is on the
  page before it is defined, so properties and listeners are in place for a
  resumed tour.
- **Destroying `<steppy-tours>`** suspends a running tour (its
  [resume](#resume) record stays); call `stop()` first to end it.

Hash routing (`withHashLocation()`) is not supported by in-app tours. Angular
18+ serves `public/`: point the app-assets target at `public/tours` and use a
base-relative `fallbackManifest` (`tours/tours.manifest.json`) when the app is
deployed under a sub-path. Under Trusted Types, see
[Trusted Types](#trusted-types).

`fixtures/tour-demo-angular/` is a complete integration (Angular, PrimeNG and
ngx-translate) that the plain demo's scenarios drive unchanged; CI runs their
replays against it.

### Without the adapter

The element also works directly. Four things the adapter does are then yours:

```ts
@Component({
  selector: 'app-root',
  imports: [RouterOutlet],
  schemas: [CUSTOM_ELEMENTS_SCHEMA],
  template: `<router-outlet /><steppy-tour-host #tours fallback-manifest="tours/tours.manifest.json" />`,
})
export class AppComponent {
  private tours = viewChild.required<ElementRef>('tours');
  private router = inject(Router);
  private zone = inject(NgZone);
  private translate = inject(TranslateService);

  constructor() {
    afterNextRender(() => {
      // Browser only, after the first render: the module touches HTMLElement at load.
      const host = this.tours().nativeElement;
      // 1. Navigate inside the zone, or a zone.js app never renders the route.
      host.navigate = (_path: string, { appPath }: { appPath: string }) =>
        this.zone.run(() => this.router.navigateByUrl(appPath));
      // 2. A string, never undefined.
      host.locale = this.translate.getCurrentLang() ?? '';
      void import('@steppy/player/tour');
    });
    this.translate.onLangChange
      .pipe(takeUntilDestroyed())
      .subscribe(({ lang }) => (this.tours().nativeElement.locale = lang));
  }

  async startTour(id: string): Promise<boolean> {
    // 3. A click can come before the module has defined the element.
    await customElements.whenDefined('steppy-tour-host');
    // 4. Outside the zone: the overlay's polls must not drive change detection.
    return this.zone.runOutsideAngular(() => this.tours().nativeElement.start(id));
  }
}
```

<a id="angular-adapter-contract"></a>

### Adapter contract

What `@steppy/angular` follows, and any other framework adapter must:

- import the module lazily and in the browser only;
- map `navigate` to the router with `appPath`, never to a full reload, inside
  the framework's change detection; reject when the router refuses or
  redirects the navigation, so the tour fails at once;
- keep the host's `locale` in sync with the app's language, as a string;
- build its state from the six events, not by reading the overlay's DOM;
- call `start()`, `stop()` and `next()`, and write the host's properties,
  outside the framework's change detection in zone.js apps (the overlay's
  observers and polls would otherwise trigger it);
- never inject scripts or use `bypassSecurityTrust*`; consumers must not need
  `CUSTOM_ELEMENTS_SCHEMA`;
- work zoneless and under SSR (render nothing on the server).

## React and Vue

There are no tour wrappers for React or Vue yet (the `@steppy/react` and
`@steppy/vue` packages wrap the guide player only). The element works
directly; the two things to know are that `navigate`, `source` and `strings`
are **properties**, not attributes, and that the module must load in the
browser only (in Next.js or Nuxt, import it from client-side code).

```tsx
// React
import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import '@steppy/player/tour';
import type { SteppyTourHost } from '@steppy/player/tour';

export function Tours({ locale }: { locale: string }) {
  const ref = useRef<SteppyTourHost>(null);
  const navigate = useNavigate();
  useEffect(() => {
    ref.current!.navigate = (_path, { appPath }) => navigate(appPath);
  }, [navigate]);
  useEffect(() => {
    ref.current!.locale = locale; // a change mid-tour reloads the tour in that language
  }, [locale]);
  return <steppy-tour-host ref={ref} fallback-manifest="/tours/tours.manifest.json" />;
}
// TypeScript: declare 'steppy-tour-host' in JSX.IntrinsicElements once.
// Start a tour from anywhere: document.querySelector('steppy-tour-host')?.start('create-invoice').
```

```vue
<!-- Vue: tell the compiler the tag is a custom element, e.g. in vite.config.ts:
     vue({ template: { compilerOptions: { isCustomElement: (tag) => tag.startsWith('steppy-') } } }) -->
<script setup lang="ts">
import { onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import '@steppy/player/tour';
import type { SteppyTourHost } from '@steppy/player/tour';

const props = defineProps<{ locale: string }>();
const host = ref<SteppyTourHost>();
const router = useRouter();
onMounted(() => {
  host.value!.navigate = (_path, { appPath }) => router.push(appPath);
  host.value!.locale = props.locale;
});
watch(() => props.locale, (locale) => host.value && (host.value.locale = locale));
</script>

<template>
  <steppy-tour-host ref="host" fallback-manifest="/tours/tours.manifest.json" />
</template>
```

<a id="localization"></a>

## Localization

**Which languages a tour has.** A tour gets one file per locale in
`tours.locales` (default: the project's `locales`) that at least one of its
kept steps has text in; the default locale always gets one. A configured
locale with no text anywhere in the tour gets no file, and the compiler says so
(`locale-not-emitted`): its users get the tour in its base language if that has
a file, else in the default locale.

**Step text**, per locale L: `tour-text[L]`, then `guide-text[L]`, each read as
the exact tag, then its base language (`fr-BE` reads `fr`). A query is never
widened: `fr` does not read `fr-BE`. A step with no text in an emitted locale
shows a generic hint in that locale ("Click the highlighted element.") and the
compiler warns `generic-text` — the overlay never borrows another language's
text. Keys are compared exactly at compile time, so write `fr-BE`, not
`fr_be` or `FR-be` (`tour-text-locale-untargeted` suggests the right tag).

**Tour titles** (`tour.title` as a map): the exact tag, its base language, the
default locale's entry, then the scenario title (`title-fallback`). A
multi-language tour without `tour.title` shows the scenario title in every
language, and the compiler warns once for the project.

**At run time** the host loads the file for `locale` — `<html lang>` when
`locale` is unset — trying the exact tag (case and `-`/`_` do not matter),
then its base language, then the manifest's default locale.

**Switching language mid-tour.** When your app changes language, set the
host's `locale` (or update `<html lang>` while `locale` is unset): the running
tour reloads in that language at the same step and is announced again. A
switch made while a tour is still loading is applied as soon as it has loaded;
when switches overlap, the last one wins; if the new file cannot load, the
tour stays in its current language and the console says why.

**Buttons, progress and generic hints** ("chrome strings") ship in English,
French, Dutch and German, and follow the *tour file's* language, so a popover
never shows two languages. For any other tour language the compiler warns
`chrome-strings-missing`: set that language's strings on the host, or its users
see English buttons around their text. Overrides are sparse and per locale;
missing keys fall back to the built-in strings for that language, else
English. An `fr` override also serves `fr-BE`, unless an `fr-BE` override
exists (overrides do not cascade).

```js
host.strings = {
  es: {
    next: 'Siguiente',
    done: 'Listo',
    progress: 'Paso {current} de {total}',
    close: 'Cerrar el recorrido',
    // …every key below
  },
};
```

| Key | English | Where |
|---|---|---|
| `next` | Next | The primary button |
| `done` | Done | The primary button on the last manual step |
| `progress` | Step {current} of {total} | Footer; keep both placeholders |
| `close` | Close the tour | The × button's accessible name |
| `skipStep` | Skip this step | "Can't find" fallback |
| `endTour` | End the tour | "Can't find" fallback |
| `resume` | Resume tour | The paused pill |
| `paused` | Tour paused | The paused pill's label and announcement |
| `dialogLabel` | Guided tour | The popover's accessible name when a step has no title |
| `openGuide` | Open the full guide | The guide link |
| `notFoundTitle` | Can't find this element | "Can't find" fallback |
| `notFoundBody` | The page may have changed. Skip this step or end the tour. | "Can't find" fallback |
| `instructionClick` | Click the highlighted element. | Generic hint, `click` steps |
| `instructionInput` | Fill in the highlighted field. | Generic hint, `input` steps |
| `instructionChange` | Choose a value in the highlighted field. | Generic hint, `change` steps |
| `instructionVisible` | Wait for the highlighted element to appear. | Generic hint, `visible` steps |
| `instructionManual` | Select Next to continue. | Generic hint, `manual` steps |

`DEFAULT_TOUR_STRINGS` (exported) holds the four built-in tables.

**Language and direction of the popover.** The overlay sets `lang` and `dir`
on its layer from the tour file's locale. Screen readers then pronounce the
tour in its own language even where it differs from the page's (a fallback to
the default locale), and right-to-left languages (Arabic, Hebrew, Persian,
Urdu, …, or any `-Arab`/`-Hebr` script) lay the popover out right to left.
`tour-placement: left`/`right` stay physical sides of the target.

<a id="theming"></a>

## Theming and accessibility

The overlay reads public custom properties, so you can set them on `:root` or
on the element:

| Property | Default | |
|---|---|---|
| `--steppy-tour-bubble-bg` | `#1f1d1a` | falls back to `--steppy-tooltip-bg` |
| `--steppy-tour-bubble-fg` | `#fbf9f4` | falls back to `--steppy-tooltip-fg` |
| `--steppy-tour-muted` | `#c9c5bd` | progress text |
| `--steppy-tour-accent` | `#a8410e` | primary button, spotlight ring |
| `--steppy-tour-accent-fg` | `#ffffff` | primary button text |
| `--steppy-tour-focus` | `#f09a6a` | focus ring |
| `--steppy-tour-scrim` | `rgba(31, 29, 26, 0.45)` | the dimming around the spotlight |
| `--steppy-tour-radius` | `6px` | |
| `--steppy-font-family` | inherited | the bubble uses your app's font by default |

The defaults meet WCAG AA; keep that when you override them. Every visible
piece is a `::part()`: `layer`, `spotlight`, `bubble`, `title`, `body`,
`progress`, `button`, `primary`, `secondary`, `next`, `close`, `skip`, `end`,
`resume`, `pill`, `guide-link`.

```css
steppy-tour-host::part(bubble) { border-radius: 12px; }
steppy-tour-host::part(primary) { font-weight: 600; }
```

Chrome strings (buttons, progress, generic hints) and right-to-left layout:
see [Localization](#localization).

- **Stacking.** The layer uses the browser's top layer (`popover`) where
  available, so no z-index of yours sits above it; elsewhere it uses
  `baseZIndex`.
- **Keyboard.** Escape ends the tour unless your app uses that Escape (an open
  dialog, popup or `preventDefault`). Focus moves into the popover only for
  manual steps and the "can't find" fallback; otherwise it stays in your app.
- **Screen readers.** Each step is announced through a polite live region; the
  popover is a labelled, non-modal dialog.
- **Small screens** get a bottom (or top) sheet instead of an anchored bubble.
- **Open dialogs.** A step with nothing to point at is centred, except over an
  open dialog of your app (`role="dialog"`, `role="alertdialog"`, `<dialog open>`
  or `aria-modal="true"`): a step kept on a `confirm` is about that dialog, so
  the bubble becomes a sheet above or below it instead of hiding it.
- **Reduced motion** removes transitions and smooth scrolling.

## Security

The overlay is built for a strict page. What it accepts:

- **Targets** are test-id values matched by `[data-testid="…"]` with
  `CSS.escape`, in the light DOM only. Never a selector from data.
- **Routes** are same-origin, base-relative patterns (no `//host`, no
  `javascript:`, no `.` or `..` segment, `%2e` spellings included); the tour
  only ever navigates through your `navigate` hook.
- **Text** is rendered as text with a small Markdown subset; links work only
  for app routes (same tab, through `navigate`) and the trusted guide origin
  (new tab, `rel="noopener noreferrer"`). An app-route link is checked after
  the browser would resolve it, so `/.//host` or `/a/..//host`, which collapse
  into `//host`, stay literal like everything else.
- **Files** are rejected whole on any rule violation, including an unknown
  `advance.on` or `placement` value, any field over its size cap, a missing SRI
  hash, and locales that differ only in case or `-`/`_`.
- **Remote files** are pinned by the bundled manifest of the same build (see
  [Integrity](#integrity)).

It stores one `sessionStorage` key (`steppy:tour`) and nothing else. It
sends nothing anywhere: events stay in the page for your analytics, if any.

On the producer side, an `app-assets` folder belongs to your app: Steppy
only overwrites or deletes a file there when it is itself a Steppy tour file
(its JSON `kind`), and stops with `target-path` otherwise.

<a id="csp"></a>

### Content Security Policy

No inline scripts, no inline `style` attributes, no `eval`. Styles use
constructable stylesheets (`adoptedStyleSheets`); on engines without them, Lit
falls back to a `<style>` element, which needs `litNonce` under a strict
`style-src`. Remote mode needs the guide origin in `connect-src`:

```text
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self';
  connect-src 'self' https://guide.example.com
```

### Trusted Types

Lit creates a `lit-html` Trusted Types policy:
`require-trusted-types-for 'script'; trusted-types lit-html`. The tour bundle
carries its own copy of Lit, so when your app uses Lit too (or two overlay
bundles load), the policy is created twice; allow that with
`trusted-types lit-html 'allow-duplicates'`, or module load fails. Add
`lit-html` to the policies your app already lists rather than replacing
them — an Angular app with Trusted Types keeps its own, e.g.
`trusted-types angular angular#bundler lit-html`.

<a id="cors"></a>

### CORS for public remote mode

The guide host must answer the tour files with
`Access-Control-Allow-Origin` for your app (or `*`: the files are public and
fetched without credentials). `steppy build` writes that into the static
site's `_headers` (Netlify, Cloudflare Pages); GitHub and GitLab Pages ignore
`_headers`. That `*` is written for private builds too: anyone who can reach
the guide host and guess a build id can read the tour text, which is why tour
text must hold nothing confidential. Check a host with:

```bash
curl -sI -H "Origin: https://app.example.com" \
  https://guide.example.com/tours/<build-id>/tours.manifest.json | grep -i access-control
```

Serve the files as `application/json` (the overlay sniffs and rejects an HTML
page whatever its type). Caching: a `<build-id>/` folder never changes once
published, so its tour files can be cached for long; the overlay always
revalidates manifests (`cache: no-cache`) and same-origin files, which matters
for the app-assets copy served at a fixed URL across deploys.

<a id="integrity"></a>

### Integrity

Each file's SHA-384 hash is in the manifest. When the bundled manifest and
another one (remote, or a same-origin proxy) are for the same build, the other
manifest only locates files: the tour, the locale and the hash all come from
the bundle, and the file is fetched with that hash as its `integrity`. A
tampered or stale copy is rejected by the browser and the bundled copy is used;
a tour or locale only the other manifest lists is never fetched from it.

Without a bundled manifest of the same build (remote only, or an explicit
`build-id` for another build), the remote manifest's own hashes are used:
they catch a file changed after the manifest, not a compromised host. Bundle
the manifest if the guide host is less trusted than your app.

<a id="auth-walled"></a>

### Auth-walled guides

The overlay fetches remote files without credentials, so a guide behind a login
cannot serve them directly. Three patterns:

1. **Bundled only** — no remote mode at all (recommended).
2. **A same-origin proxy** in your app that fetches the guide with its own
   credentials; point `manifest` at the proxy. Its files are pinned by the
   bundled manifest like any remote's, so the proxy must relay the bytes
   unchanged (no JSON re-serialisation, no line-ending conversion). A
   same-origin manifest's `guideOrigin` is trusted when neither the host's
   `guide-origin` nor a bundled manifest sets one: set `guide-origin` with a
   proxy in remote-only mode.
3. **The `source` hook** — supply files from your own authenticated client.

Tour text is public wherever the guide is: keep confidential data out of
`guide-text` and `tour-text`.

## CI replay

For each tour, `steppy generate` appends `test_<slug>__tour` to the
scenario's own test file (so selection, parallel workers, `lastfailed` and
JUnit treat it like its scenario). It:

1. runs the steps before the tour's start point, as the scenario test does;
2. starts the tour through the host's API;
3. for each tour step, checks the popover shows that step, anchored on the
   element the step acts on (the spotlight around its centre, the popover not
   covering it); performs the scenario's own action for it — the exact code
   the scenario test runs; and checks the tour moved on;
4. checks `tour-complete` fired and no `tour-error` did.

Build the app with its tours *before* starting it, then `generate`, then `run`.
A failure reads:

```text
TourReplayError: [tour create-invoice] step 'invoice-title' (scenario step 4 "Name the invoice", fill,
test-id "invoice-title"): target-timeout: the overlay never found [data-testid="invoice-title"]
  hint: the test-id was renamed or removed in the app, or the element appears too late
  page: /invoices  tour: active, current 'invoice-title'  bubble: fallback  navigations since start: 0
  last events: tour-start(bundled) | tour-step(new-invoice-btn) | tour-error(target-timeout, invoice-title)
```

- `-k "not __tour"` skips replays; `tours.replay: false` stops generating them.
- `steppy run --guide` deselects them (guide captures are not tours).
- Replays record no coverage.
- `STEPPY_TOUR_EXPECT_BUILD_ID=<id>` makes a replay also check the app
  serves tours for that build (with `--workers N` too: it is passed to every
  worker).
- A replay re-runs its scenario's seed and steps: the flow must be repeatable.
- In run reports, a replay is a row of its own under the scenario; when both
  fail in the same variant they share the failure screenshot.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `target-timeout` in production | The test-id changed and the tours were not regenerated, or the element renders late (raise `target-timeout`). |
| `target-not-test-id` at compile time | The step's selector is a CSS path. Point it at the element's test-id, or `tour: skip` / `tour: keep`. |
| `unguided-action` | A `custom` step clicks through JavaScript. Use `click` with `synthetic: true`, or add `guide-cursor`. |
| `fixture-leak` | Step text repeats a value the test types. Write `tour-text` without it. |
| `build` says the tours tree is missing | Run `steppy generate` or `steppy tours` first, with the same `build_id`. |
| An E2E suite (not Steppy's) clicks into the overlay | Set `globalThis.__STEPPY_TOURS_DISABLED__ = true` in its browser contexts. |
| CSP or Trusted Types errors at module load | See [Security](#csp). |
| Remote mode always falls back | CORS on the guide host, the build id, or a hash mismatch after a line-ending conversion. |
| Buttons in English around Spanish (or other) text | The overlay ships buttons in en, fr, nl and de only; set the host's `strings` for that language (`chrome-strings-missing`). See [Localization](#localization). |
| The tour stays in the old language after the app switches | Set the host's `locale` on every switch, or keep `<html lang>` current and `locale` unset. |
| `steppy tours --check` fails in CI | The committed `app-assets` copy is older than the scenarios: run `steppy tours` and commit the result. A differing build id alone never fails the check. |
| `--check` says files "differ only in line endings" | Git converted them on checkout (`core.autocrlf` on Windows). Regenerating cannot fix that: mark the folder `-text` in the app repository's `.gitattributes`. |
| A tour that starts on another route never shows its first step in an Angular app | `navigate` runs outside the Angular zone: use `@steppy/angular`, or wrap the router call in `NgZone.run` (see [Angular](#angular)). |

## Limitations

- Tours are linear: no branches, and no Back button.
- Targets must be in the light DOM of the main document: not inside another
  component's shadow root, not in iframes.
- Navigating to another origin ends the tour.
- Hash routing (`#/invoices`, Angular's `withHashLocation()`) is not
  supported: entry routes and the resume record read the URL's path.
- A native `<dialog>` opened with `showModal()` makes everything outside it
  inert, the overlay's buttons included, until it closes. Steps advanced by
  your app's own events still work. Non-native dialogs are unaffected.
- When several elements share a test-id, the overlay picks the first visible
  one; Playwright picks the first in DOM order. The replay catches a mismatch.
- No analytics backend and no auto-start: both are yours, through the events
  and `start()`.
- Changing a tour's text means regenerating and redeploying the tour files.
