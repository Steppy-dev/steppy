# @steppy/player

The Lit `<steppy-guide>` web component that renders interactive guide manifests
(`guide.json`), plus the shared cursor animator — and the in-app tour overlay
`<steppy-tour-host>` (the `./tour` entry). Framework-agnostic, fully offline,
no CDN.

```bash
npm install @steppy/player
```

The guide player is the package's main entry; the tour overlay is
`@steppy/player/tour`.

## Development

- `npm run build` — four bundles: the guide player ESM bundle
  (`dist/steppy-player.js`), the legacy cursor IIFE
  (`dist/steppy-cursor-legacy.js`), the video renderer IIFE
  (`dist/steppy-video.js`), and the tour overlay (`dist/steppy-tour.js` with
  declarations in `dist/types/`), plus the JSON Schema. Re-vendor the first three
  into `src/steppy/_player_bundle/` after changing `src/` (CI checks them byte
  for byte and does it on release); the tour overlay is never vendored.
- `npm run size:tour` — the tour overlay's size budget (min + gzip).
- `npm run check:pack` — publish-readiness: every file `package.json` points at
  (`main`, `types`, `exports`, `typesVersions`) is in what `npm pack` would ship,
  and nothing from `src/` or `test/` is. `prepack` builds the package. Declarations for the guide entry and for
  `./tour` come from `tsc -p tsconfig.types.json` (`npm run build:types`).
- `npm test` — Vitest (schema, animator, positioning, component, templates,
  Python↔TS bridge, the shared `src/ui/` modules, the tour overlay).
- `npm run typecheck` — `tsc --noEmit`.
- Dev harness: `dist/` built, then open `dev/index.html`.

## Presentation templates

The engine (step state, cursor animation, target highlighting, i18n,
navigation, keyboard, locale/layout switching) is one thing; how a step is
*presented* is another. Presentation is a swappable **template** — one of
**`overlay`** (default: full-bleed preview + floating callout pinned to the
target), **`sidebar`** (left rail with a vertical step timeline + the current
step's description), or **`inline`** (slim bottom caption bar). All three render
the same engine state; only the chrome differs.

### The `template` attribute + resolution order

`template` is a runtime-switchable, persisted viewer setting:

```html
<steppy-guide src="./guide.json" template="sidebar"></steppy-guide>
```

The effective template is resolved (highest precedence first):

1. the explicit `template` attribute/property,
2. the persisted choice in `localStorage['steppy:active-template']`,
3. the manifest's top-level `defaultTemplate` (the per-guide default),
4. `'overlay'`.

Switching templates (via the built-in segmented control or by setting the
attribute) preserves the current step, locale, layout, and highlight — it is a
pure re-render of engine state, never a reset. The choice is persisted; an
explicit attribute or the switcher persists, the initial resolution does not.

### Per-guide default (`defaultTemplate`)

Authored in the build config, not hardcoded in the component:

```ts
// steppy.guide.config.ts
defineGuideConfig({
  defaultTemplate: 'sidebar',          // project-wide default
  guides: [
    { slug: 'onboarding', /* … */ template: 'inline' }, // per-guide override
  ],
});
```

`steppy build` resolves `entry.template || config.defaultTemplate || 'overlay'`
and emits it as the manifest's `defaultTemplate`; it is also set as the initial
`template` attribute on the generated host's `<steppy-guide>`.

### Engine → template API contract

Templates are pure functions in `src/templates/{overlay,sidebar,inline}.ts`,
each exporting `render(ctx: TemplateContext): TemplateResult` and a `styles`
`CSSResult`. The component (`steppy-guide.ts`) owns all state and builds an
immutable `TemplateContext` (`src/templates/types.ts`) per render:

- `steps: StepView[]` — the full ordered list, each with `index`, localized
  `title` / `description` / `subLabel`, `status` (`completed|current|upcoming`),
  and `reachable` (the `goTo` gate);
- `currentIndex`, `total`, `durationLabel`, `guideTitle`, `locale`, `ui`;
- `waitingForInput`, `completed`;
- nav callbacks (`onNext`/`onPrev`/`onRestart`/`onGoTo`) + precomputed
  `prevDisabled`/`nextDisabled` so templates never re-implement gates;
- switcher data + handlers (`locales`/`layouts`/`templates`, `onLocaleChange`/
  `onLayoutChange`/`onTemplateChange`);
- `renderStage()` — a thunk returning the live stage subtree (screenshot +
  cursor/ripple/kbd + hotspot + miss-toast), shared verbatim so the refs and
  handlers stay bound to the component.

To add a template: add a module, register it in `src/templates/index.ts`
(`TEMPLATES` + `ALL_TEMPLATES`), and extend `TEMPLATE_MODES` in
`manifest/schema.ts` (the `.strict()` schema rejects unknown ids).

**Stage re-mount invariant:** switching templates swaps the whole top-level
template, so Lit recreates the `.steppy-stage` element. `updated()` detects the
new element identity and calls `_reattachStage` — re-pointing the `CursorAnimator`
(via `animator.setStage`) and the `ResizeObserver`, then recomputing the hotspot
— without touching step state, so an in-flight animation survives the switch.

### Adding a chrome string

UI chrome strings live in `src/i18n/ui-strings.ts`: add the key to the
`UiStrings` interface and a value to **every** locale in `DEFAULT_UI_STRINGS`
(`en`, `fr`, `nl`, `de`). Use `format(ui.key, vars)` for `{placeholder}`
interpolation. Hosts can override per-locale via the `uiStrings` property. A
regional locale falls back to its base language (`fr-BE` reads `fr`), then to
English. `next` and `progress` come from `CHROME_STRINGS` in `src/ui/strings.ts`,
shared with the tour overlay.

### Design tokens

The warm palette (cream surfaces, near-black text, rust-orange accent, green
completed) and the serif/sans/mono type scale are CSS custom properties on
`:host` in `src/templates/base-styles.ts` (system font stacks only — no web
fonts). Override any `--steppy-*` variable from the host to re-theme. Animations
respect `prefers-reduced-motion`.

The `sidebar` template adds four **layout** tokens (declared inline in
`src/templates/sidebar.ts`, not on `:host`, since they mean nothing to the other
templates). Everything the rail cap touches is a token, because the shadow DOM
has no `part=` attributes — a host that cannot live with a default has no other
way in, and finding that out costs a release:

| Token | Default | What it does |
|---|---|---|
| `--steppy-rail-width` | `340px` | Left rail width; the rest of the row is the preview |
| `--steppy-rail-max-height` | `calc(100vh - 32px)` | Rail cap; the timeline scrolls inside it |
| `--steppy-sidebar-offset` | `16px` | `top` for the sticky rail |
| `--steppy-rail-max-height-narrow` | `60vh` | Rail cap once collapsed to one column (≤640px) |

**An embedded host should override `--steppy-rail-max-height`.** The `100vh`
default assumes the player owns the viewport, which is true for the static
target's `interactive.html` and false for the Angular target, where the host's
own header and nav sit above the component — there `calc(100vh - <chrome>)` is
the correct value and only the host knows it.

## In-app tour overlay (`./tour`)

`import '@steppy/player/tour'` registers `<steppy-tour-host>` (once, even
when two copies of the bundle load) and exports its types, the contract values
(`TOUR_EVENTS`, `TOUR_PARTS`, `ADVANCE_KINDS`, …) and the validators. The user
documentation — host API, events, theming, CSP — is `docs/tours.md` at the
repository root.

Layout of the code:

- `src/ui/` is shared with the guide player: design tokens (`tokens.ts`, which
  `templates/base-styles.ts` interpolates — the guide's stylesheet hash is pinned
  by `test/ui/guide-styles-stable.test.ts`), chrome strings and the locale lookup
  (`strings.ts`), the inline Markdown renderer with its link policy
  (`markdown.ts`), `placeTooltip` (`placement.ts`) and the product names
  (`names.ts`). It never registers an element and never imports zod.
- `src/tour/engine.ts` is a pure reducer: `reduce(state, event)` returns the next
  state and the effects to run. `host.ts` interprets the effects through DOM
  adapters — `loader`, `resolver`, `triggers`, `tracker`, `layer`, `storage` —
  and `dom.ts` is the seam tests swap (`SteppyTourHost.dom`): happy-dom has no
  layout, no popover API and a requestAnimationFrame outside fake timers, so
  `test/tour/helpers.ts` provides a stub DOM with settable rects and a manual
  scheduler.
- `src/tour/validate.ts` is the security boundary: every manifest and tour file
  is validated whole, by hand (no zod, for size). It runs the same golden cases
  as the Python producer (`contracts/fixtures/tours/cases.json`).
- Geometry is written through CSSOM custom properties only — no `style=` binding
  and no `styleMap` — so the overlay runs under a strict CSP;
  `test/tour/boundaries.test.ts` enforces that and the import boundary.
- Real browser behaviour (top layer, hit-testing, focus, keyboard, remote loading
  with CORS and SRI) is covered by the opt-in Python suites in
  `tests/integration/test_tour_*_it.py` against `fixtures/tour-demo/`.

Every chrome string the overlay shows exists in `en`, `fr`, `nl` and `de`
(`src/tour/strings.ts`); add a key to all four. Adding a *language* means
adding it to `CHROME_STRING_LOCALES` in `src/tour/contract.ts` **and**
`src/steppy/tour_spec.py` (the compiler warns `chrome-strings-missing` for
every other tour language) and checking `npm run size:tour`. The layer carries
the tour file's `lang` and `dir` (`textDir` in `strings.ts`), so the bubble's
inner layout uses logical properties only — `test/tour/small-modules.test.ts`
rejects a physical `margin-left/right`. A running tour reloads when the host's
`locale` (else `<html lang>`) changes; see `_swapLocale` in `host.ts`. Unlike the
guide player, the overlay exposes `part=` attributes for `::part()` styling.
Its `--steppy-tour-*` tokens are one table, `TOUR_THEME_KEYS` in `theme.ts`: the
stylesheet, the host's `theme` property and the docs all follow it, and
`appearance="light"`/`"auto"` swap the defaults (`TOUR_LIGHT` in `ui/tokens.ts`,
AA-checked like the dark ones). Add a token there, never as a literal in `styles.ts`.
