---
name: steppy-app-tours
description: |
  How to wire Steppy's in-app tours into an application's own code — the work done in
  the app's repository, not in the Steppy project. Use whenever a user asks to "add
  in-app tours to the app", "mount the tour overlay", "add a Show me how button that
  starts a tour", "set up @steppy/angular" or `provideSteppyTours`, mounts
  `<steppy-tour-host>` or `<steppy-tours>`, imports `@steppy/player/tour`, adds
  `data-testid`s so a tour can point at an element, or debugs a tour in the running app:
  "the tour does not start", "Can't find this element", a `tour-error`,
  `target-timeout`, CSP or Trusted Types errors at load, buttons in the wrong language.
  Covers Angular (with the adapter or without), plain JavaScript, React and Vue, serving
  the tour files, starting tours, the language, test-ids in components, security headers,
  events and analytics, turning tours off, and checking that the wiring works.

  Writing or changing the tours themselves (the `tour:` frontmatter, `tour-text`, compile
  errors and warnings) is `steppy-scenario-author`. Restyling the overlay is
  `steppy-template`. The CI replay and `steppy tours --check` are `steppy-ci-report`.
---

# Wiring Steppy's in-app tours into an app

An in-app tour is a sequence of popovers over the live app, pointing at the real
buttons and fields. Steppy compiles each tour from a Markdown scenario, the same file
that drives an end-to-end test, and the CI replays every tour against the app. The app
shows them with one web component, `<steppy-tour-host>`, from `@steppy/player/tour`.
Angular apps mount it through `@steppy/angular`.

The work is split between two places. Know which one you are in before changing anything.

| The Steppy project (`config.yaml`, `scenarios/`) | The app (this skill) |
|---|---|
| Decides which tours exist, their steps and their text | Serves the compiled tour files |
| `steppy generate` / `steppy tours` write `tours.manifest.json` and one `<tour-id>.<locale>.json` per tour and language into the app's folder | Mounts the overlay once, in the app shell |
| Replays every tour in CI | Starts tours from its own UI (a help button, a menu) |
| | Puts the `data-testid`s on the elements tours point at |
| | Keeps the overlay's language in step with the app's |

Five rules the overlay is built on, and that the app must not work around:

1. **Data, never code.** A tour file is JSON validated whole against a strict contract.
   It holds no HTML, no script, no selector. The overlay's code comes only from the
   app's own bundle.
2. **Test-ids only.** A step points at one `data-testid` value, never a CSS selector.
3. **The user acts.** The overlay highlights and explains. It never clicks, types or
   submits.
4. **Non-blocking.** Clicks pass through to the app, and a tour can be closed at any time.
5. **Locked to a build.** A tour set carries the build id of the app it was compiled for.

## Before you start

Find out from the Steppy project (or ask):

- **Where the tour files land.** `tours.targets` in `config.yaml` has an `app-assets`
  entry whose `path` is a folder in this app, e.g. `../invoice-app/public/tours`. That
  folder holds `tours.manifest.json`.
- **The tour ids**, which are what the app passes to `start()`. They are in
  `tours.manifest.json` (`tours[].id`). Without a `tour.id`, a tour's id is its
  scenario's file name.
- **Each tour's entry route** (`tours[].entry.route`, e.g. `/invoices/:invoiceId`). A
  tour started elsewhere navigates there first, and `:params` must come from the app.
- **The test-id attribute** (`testIdAttribute` in the manifest, default `data-testid`).

If the folder is empty, run `steppy tours` in the Steppy project. Never write or edit
tour JSON by hand: the manifest pins every file by its SHA-384 hash, `steppy tours
--check` fails CI on any difference, and the next compile overwrites it.

## 1. Serve the tour files

- The `app-assets` folder must be served by the app as static files. Angular 18+
  serves `public/` (point the target at `public/tours`); older Angular serves
  `src/assets/`; Vite and most bundlers serve `public/`.
- The URL that folder is served at is what the overlay's `fallback-manifest` gets, e.g.
  `/tours/tours.manifest.json`. Steppy never derives it. Under a sub-path deployment
  (`<base href="/app/">`), use a base-relative URL: `tours/tours.manifest.json`.
- Commit the folder with the app. CI checks it with `steppy tours --check`.
- Mark it `-text` in the app repository's `.gitattributes`
  (`public/tours/*.json -text`). A line-ending conversion on checkout changes the bytes
  the hashes cover, and the check then fails with "differ only in line endings".
- The app must be rebuilt and restarted to serve new tour files, after every
  `steppy tours` or `steppy generate`.

## 2. Install the packages

`@steppy/player` (the overlay is its `./tour` entry) and `@steppy/angular` are on npm.
Install them at the version of `steppy-dev` the Steppy project uses (`steppy --version`),
pinned exactly, so the overlay reads the tour files that version compiles:

```bash
npm install -E @steppy/player@<version>                      # any app
npm install -E @steppy/player@<version> @steppy/angular@<version>   # Angular apps
```

`@steppy/angular` pins `@steppy/player` to its exact version and needs Angular 20–22.
When `steppy-dev` is upgraded, upgrade both packages to the same version.

### Unreleased changes, from a Steppy checkout

Only to try a change that is not released yet. Build the packages:

```bash
npm --prefix packages/player ci && npm --prefix packages/player run build
npm --prefix packages/angular ci && npm --prefix packages/angular run build   # Angular apps only
```

Then depend on them from the app, either as folders or as tarballs:

```jsonc
// package.json — folders
"@steppy/player": "file:../steppy/packages/player",
"@steppy/angular": "file:../steppy/packages/angular/dist"   // dist/, never the folder itself
```

```bash
# tarballs
npm pack ./packages/player ./packages/angular/dist
npm install ./steppy-player-<version>.tgz ./steppy-angular-<version>.tgz
```

With `file:` folders in an Angular app, set `"preserveSymlinks": true` in the build
options of `angular.json`. Without it, Angular loads a second copy of `@angular/core`
from `packages/angular/node_modules` and fails with NG0203. Go back to the npm
packages once the change is released.

Bundle the overlay with the app. Never load it from a CDN or from the guide site.

## 3a. Angular, with the adapter (recommended)

`@steppy/angular` mounts the overlay the Angular way: zone.js or zoneless, safe under
server rendering, and no `CUSTOM_ELEMENTS_SCHEMA`.

```ts
// app.config.ts
import { inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { TranslateService } from '@ngx-translate/core';
import { map } from 'rxjs';
import { provideSteppyTours } from '@steppy/angular';

export const appConfig: ApplicationConfig = {
  providers: [
    provideRouter(routes),
    provideSteppyTours({
      fallbackManifest: 'tours/tours.manifest.json', // the app-assets folder, in public/tours
      // Optional. Without it, tours follow <html lang>.
      locale: () => {
        const translate = inject(TranslateService);
        return toSignal(translate.onLangChange.pipe(map((e) => e.lang)), {
          initialValue: translate.getCurrentLang() ?? '',
        });
      },
    }),
  ],
};
```

```ts
// app.component.ts (the shell)
@Component({
  selector: 'app-root',
  imports: [RouterOutlet, SteppyToursComponent, SteppyTourStart],
  template: `
    <button type="button" steppyTourStart="create-invoice">Show me how</button>
    <router-outlet />
    <steppy-tours (tourComplete)="onDone($event)" />
  `,
})
export class AppComponent {}
```

```ts
// anywhere else
const tours = inject(SteppyTours);
await tours.start('send-invoice', { params: { invoiceId: 1001 } }); // resolves false, never rejects
```

| API | What |
|---|---|
| `provideSteppyTours(config)` | Root providers. Keys: `fallbackManifest`, `manifest`, `buildId`, `guideOrigin`, `targetTimeout`, `baseZIndex`, `disabled`, `strings`, `theme`, `appearance`, `source` (as on the host, below); `locale` — a string, a signal, or a function run once in an injection context that returns either; `navigate` — `'router'` (default), `'document'` (full page loads) or your own function; `readyTimeout` — how long `start()` waits for the overlay (10 s). |
| `<steppy-tours />` (`SteppyToursComponent`) | The overlay. Place it **once**, in the shell, never in a routed page. Its inputs override the config; outputs `tourStart`, `tourStep`, `tourComplete`, `tourDismiss`, `tourOffpath`, `tourError` carry the event details. |
| `SteppyTours` (root service) | `start(id, {params, step})` waits for the overlay, lets the last call win, resolves `false` when a tour cannot start. `stop(reason)`, `next()`. Signals `active`, `current`, `lastError`; `events`, an Observable delivered inside the zone. |
| `steppyTourStart` (`SteppyTourStart`) | `<button steppyTourStart="create-invoice" [steppyTourParams]="{invoiceId: id}" steppyTourStep="…">` starts a tour on click. |
| `routerNavigate(router)` | The router bridge behind `navigate: 'router'`: it settles only once the router is where the tour wants to be, and rejects a refused or redirected navigation. For a `navigate` function of your own that still uses the router. |

What the adapter already does, so do not redo it: imports the overlay lazily and in the
browser only; navigates entry routes with `Router.navigateByUrl` **inside** the zone (a
guard that refuses or redirects fails the tour at once, `tour-error` `entry-params`);
runs the overlay's polls outside the zone; keeps the locale in sync. Destroying
`<steppy-tours>` suspends a running tour rather than ending it (call `stop()` first to
end it).

Not supported: hash routing (`withHashLocation()`), and targets inside a component that
uses `ViewEncapsulation.ShadowDom` (the overlay looks in the light DOM only).

### Angular without the adapter

Four things the adapter does are then yours: load the module in the browser only after
the first render; set `host.navigate` to call the router inside `NgZone.run` (outside
it, a zone.js app never renders the routed page and the tour times out on its target);
keep `host.locale` a string, never `undefined`; and call `start()` outside the zone
after `customElements.whenDefined('steppy-tour-host')`. The component also needs
`CUSTOM_ELEMENTS_SCHEMA`. Prefer the adapter.

## 3b. Plain JavaScript (any bundler)

```html
<steppy-tour-host fallback-manifest="/tours/tours.manifest.json"></steppy-tour-host>
<button id="show-me-how">Show me how</button>
<script type="module" src="/js/app.js"></script>
```

```js
// app.js — from the app's own bundle: no inline <script>, the overlay runs under a strict CSP
import '@steppy/player/tour'; // registers <steppy-tour-host>
const host = document.querySelector('steppy-tour-host');
host.navigate = (path, { appPath }) => router.navigate(appPath); // client-side routing only
document.querySelector('#show-me-how').addEventListener('click', () => host.start('create-invoice'));
```

Without a bundler, serve `dist/steppy-tour.js` (it carries its own Lit) from the app's
origin and load it with `<script type="module" src="/vendor/steppy-tour.js">` before
the app's code. Mount **one** host per page, in the shell. Importing the module twice is
harmless.

## 3c. React and Vue

There are no tour wrappers yet (`@steppy/react` and `@steppy/vue` wrap the guide player
only). Use the element directly, and remember two things: `navigate`, `source`,
`strings` and `theme` are **properties**, not attributes; and the module must load in the browser
only (in Next.js or Nuxt, import it from client-side code).

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
    ref.current!.locale = locale;
  }, [locale]);
  return <steppy-tour-host ref={ref} fallback-manifest="/tours/tours.manifest.json" />;
}
// TypeScript: declare 'steppy-tour-host' in JSX.IntrinsicElements once.
```

```vue
<!-- Vue: vite.config.ts needs
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

Any other framework follows the same contract: lazy, browser-only import; `navigate`
mapped to the router with `appPath` and inside the framework's change detection
(rejecting when the router refuses or redirects); `locale` kept in sync as a string;
state built from the six events, never by reading the overlay's DOM; `start()`,
`stop()`, `next()` and property writes outside change detection in zone-based apps; no
injected scripts and no sanitizer bypass.

## The host element

| Property | Attribute | Default | Meaning |
|---|---|---|---|
| `fallbackManifest` | `fallback-manifest` | — | The bundled manifest (same origin). Almost always the one to set. |
| `manifest` | `manifest` | — | A remote manifest URL, may contain `{buildId}`. Remote mode, below. |
| `buildId` | `build-id` | the bundled manifest's | The app's build id, for `{buildId}`. |
| `locale` | `locale` | `<html lang>` | The preferred language; falls back to its base language, then the default. |
| `guideOrigin` | `guide-origin` | the bundled manifest's | The origin trusted for "Open the full guide" links. |
| `targetTimeout` | `target-timeout` | `8000` | Milliseconds to wait for a step's element (500–120000). |
| `baseZIndex` | `base-z-index` | `2147483000` | Stacking where the browser's top layer is unavailable. |
| `disabled` | `disabled` | `false` | Turns tours off and stops a running one. |
| `navigate` | — | `location.assign` | `(path, {appPath, tourId}) => …`. `path` includes `<base href>`, `appPath` does not. |
| `source` | — | — | `(tourId, locale, {buildId, manifestUrl}) => file`: supply tour files yourself (JSON text, a `Response` or the object, or a promise of one). Validated like a fetched file. |
| `strings` | — | — | Chrome string overrides per language (below). |
| `appearance` | `appearance` | `dark` | The default palette: `dark`, `light`, or `auto` (follows the user's colour scheme). |
| `theme` | — | — | `{ accent: '#0b5cad', width: '360px', … }`: `--steppy-tour-*` values written onto the element, winning over the app's stylesheet. Keys and look: the `steppy-template` skill. |

Methods: `start(tourId, {params, step})` resolves `true` once the tour started and
`false` when it could not (a `tour-error` says why) — it never rejects, and starting a
tour ends any other one. `stop(reason)` ends the tour. `next()` advances the current
step. State: `isActive`, and `current` (`{tourId, stepId, index, total}` or `null`).

## 4. Start tours from the app's UI

- Start from something the user chooses: a "Show me how" button, a help menu, an empty
  state. The overlay never starts by itself, and an automatic start on every visit is
  a product decision, not a default.
- **Entry params.** A tour whose entry route has `:params` needs them when it starts
  away from that route: `start('send-invoice', {params: {invoiceId: 1001}})`. Started
  on a matching URL, it reads them from the URL. Missing ones fail with `entry-params`.
- `step` starts at a step id instead of the first.
- Only offer a tour to the users whose role it was written for (`tours[].role` in the
  manifest): its steps point at what that role sees.

## 5. Language

- Keep the overlay's language in step with the app's: set `locale` on every switch
  (the adapter's `locale` option does this), or keep `<html lang>` current and leave
  `locale` unset. A switch mid-tour reloads the tour at the same step.
- Each tour ships in the languages its scenario has text for; any other language gets
  the base language's file, else the default one.
- Buttons and hints ("chrome strings") exist in English, French, Dutch and German. For
  any other language, set them, or its users see English buttons around their text:

  ```js
  host.strings = { es: { next: 'Siguiente', done: 'Listo', progress: 'Paso {current} de {total}', close: 'Cerrar el recorrido' /* … */ } };
  ```

  The keys are `next`, `done`, `progress` (keep both placeholders), `close`,
  `skipStep`, `endTour`, `resume`, `paused`, `dialogLabel`, `openGuide`,
  `notFoundTitle`, `notFoundBody` and the five `instruction*` hints
  (`DEFAULT_TOUR_STRINGS`, exported, holds the built-in tables). Missing keys fall back
  to the built-in strings for that language, else English.

## 6. Test-ids: the part only the app can do

Every step a tour keeps points at one test-id. When the Steppy project reports
`target-not-test-id`, the fix is usually here: give the element its own test-id, then
point the scenario's selector at it.

- **Put the test-id on the element the user acts on**, or on the host element of the
  component that wraps it. A click anywhere inside an element with the test-id counts,
  a text step reads the first field inside it, and a choice step watches the value,
  ARIA state and label inside it. So a component library's host works as is:
  `<p-select data-testid="customer-select">`.
- **One visible element per test-id** at any moment. When several share a value, the
  overlay takes the first *visible* one and Playwright the first in DOM order. The
  replay catches a mismatch.
- **In the light DOM of the main document**: not inside another component's shadow
  root, not in an iframe.
- **Stable.** A test-id is part of the tour's contract. Renaming one breaks the tour
  and the CI replay. Rename it in the scenario too, then regenerate.
- Bind it like any attribute: `data-testid="save-invoice-btn"`, or
  `[attr.data-testid]="…"` in Angular for a computed value. Keep values plain: no
  `{{…}}` the scenario would have to guess.

Dialogs, drawers and phones:

- **Mark dialogs as dialogs** (`role="dialog"` or `role="alertdialog"`, or
  `aria-modal="true"`, or a `<dialog>`). PrimeNG, Angular Material and CDK dialogs
  already are. Escape inside one is then the app's, not the tour's, and a centred step
  (a `confirm` kept with `tour: keep`) moves off an open one instead of hiding it.
- **A drawer that closes on Escape must consume it** (`preventDefault()`), or that
  Escape also ends the tour. PrimeNG's `p-drawer` does; a hand-made one may not.
- **A link in a drawer is its own test-id** (`drawer-customers`), not the header
  link's (`nav-customers`): the phone and desktop steps are twins with different
  targets. The overlay follows a drawer that slides in.
- **`tours.layouts` must match the app's breakpoint.** If the app swaps its header for
  a menu below 768px, the Steppy project's `mobile` query is `(max-width: 767.98px)`;
  otherwise a phone gets the desktop step pointing at a hidden link.

Hover menus (a sidebar that expands on hover, flyouts, mega menus, hover cards):

- **The overlay blocks no pointer events.** Its layer and spotlight let every hover
  and click through; only the popover takes the pointer. A menu that closes on
  `mouseleave` therefore closes when the popover lands on it, or when the user moves
  across the popover. Blocking clicks instead of movement would change nothing: the
  overlay already blocks neither.
- **On the scenario side, `tour-hold`** names the menu on each step into it
  (`tour-hold: '[data-testid="side-nav"]'`). The popover is then never drawn over the
  menu, re-placed when it expands, while it still points at the exact link. Give the
  menu container a test-id for it. Details in the `steppy-scenario-author` skill.
- **What no overlay can prevent:** reaching the popover's Next button (a `manual`
  step), or reading its text, takes the pointer off the menu — a genuine
  `mouseleave`. If a tour needs the menu open across such a step, keep it open from
  the app while a tour runs, between `tour-start` and `tour-complete` /
  `tour-dismiss`:

  ```js
  const pin = (on) => document.querySelector('[data-testid="side-nav"]')?.toggleAttribute('data-pinned', on);
  addEventListener('tour-start', () => pin(true));
  addEventListener('tour-complete', () => pin(false));
  addEventListener('tour-dismiss', () => pin(false));
  ```

  and treat `data-pinned` like hover in the menu's CSS and `mouseleave` handler. In
  Angular, set a signal from `<steppy-tours (tourStart)="…" (tourComplete)="…"
  (tourDismiss)="…">` instead. Pin only for the tours that need it (`detail.tourId`).

## 7. Security headers

The overlay runs under a strict page: no inline script, no inline `style` attribute, no
`eval`.

- **CSP.** `script-src 'self'` is enough. Styles use constructable stylesheets; on
  engines without them, Lit falls back to a `<style>` element, which needs `litNonce`
  under a strict `style-src`. Angular and PrimeNG inject `<style>` elements anyway, so
  such apps already allow `'unsafe-inline'` styles. Remote mode adds the guide origin to
  `connect-src`.
- **Trusted Types.** Add `lit-html` to the policies the app lists, e.g.
  `trusted-types angular angular#bundler lit-html`. When the app uses Lit too, or two
  overlay bundles load, also add `'allow-duplicates'`, or the module fails to load.
- Never use `bypassSecurityTrust*`, `innerHTML` or an inline script for the overlay.

## 8. Events and analytics

The host dispatches six events, bubbling and composed, with plain JSON details:
`tour-start`, `tour-step`, `tour-complete`, `tour-dismiss` (with `via`: `close`,
`escape`, `end`, `api`, `replaced`, `disabled`), `tour-offpath` and `tour-error` (with
`reason`: `target-timeout`, `entry-params`, `load-failed`, `schema-unsupported`,
`invalid-data`). Listen on the host (or on the adapter's outputs or `events`) and
forward them to the app's analytics, if any. The overlay itself sends nothing anywhere
and stores one `sessionStorage` key, `steppy:tour`, which resumes a tour across a full
page load for 30 minutes.

## 9. Turning tours off

- `disabled` on the host (or the adapter's `disabled`), e.g. behind a feature flag.
- `globalThis.__STEPPY_TOURS_DISABLED__ = true` before the app starts a tour:
  `start()` then does nothing. Steppy's own generated tests set it, so a tour never
  covers what a test clicks. **Any other end-to-end suite** (Cypress, a hand-written
  Playwright suite) must set it in its browser contexts too.

## Remote mode (optional)

By default the tour files ship with the app and roll back with it. To serve them from
the published guide site instead, set `manifest` to
`https://guide.example.com/tours/{buildId}/tours.manifest.json` and keep
`fallback-manifest`: the bundled manifest then pins every remote file to its hash, and
any remote failure falls back to the bundled copy. The guide host needs CORS for the
app's origin, and the app's CSP needs it in `connect-src`. A guide behind a login
cannot serve the files directly: use a same-origin proxy that relays the bytes
unchanged, or the `source` hook. Keep it bundled unless there is a reason not to.

## Check that it works

1. Rebuild and start the app, open it as the tour's role, and start the tour from its
   button. The first popover appears on its element.
2. Open the console: a `tour-error` names the step and the test-id it could not find.
3. In the Steppy project, run the tour's scenario: `steppy run --scenario <slug>` runs
   its replay, `test_<slug>__tour`, which follows the tour against the running app and
   fails naming the tour, the step and the test-id. `STEPPY_TOUR_EXPECT_BUILD_ID=<id>`
   also checks the app serves the tours for that build.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `start()` resolves `false`, `load-failed` | `fallback-manifest` does not point at the served file (an HTML page came back, or a 404), or the tour id is not in the manifest. |
| A tour that starts on another route never shows its first step (Angular) | `navigate` runs outside the zone: use `@steppy/angular`, or wrap the router call in `NgZone.run`. |
| A hover menu closes mid-tour; the replay says `<steppy-tour-host> intercepts pointer events` or "went off-path: its target disappeared" | The popover lands on the menu. Add `tour-hold` on the scenario steps, and pin the menu open during tours for `manual` steps inside it (section 6, "Hover menus"). |
| "Can't find this element" (`target-timeout`) | The test-id was renamed or removed, the element renders late (raise `targetTimeout`), it sits in a shadow root or iframe, or it is hidden. |
| `entry-params` | The entry route needs a param neither `start()` nor the URL gives, or a guard refused or redirected the navigation. |
| `invalid-data` / `schema-unsupported` | The file was edited by hand, or the overlay is older than the Steppy that compiled the tours: rebuild the packages. |
| The module fails to load: CSP or Trusted Types errors | Section 7. |
| English buttons around another language's text | That language has no built-in chrome strings: set `strings`. |
| The tour stays in the old language after a switch | Set `locale` on every switch, or keep `<html lang>` current with `locale` unset. |
| Two copies of Angular, NG0203 | `file:` dependencies without `"preserveSymlinks": true`. |
| `steppy tours --check` fails after a checkout | The JSON's line endings were converted: mark the folder `-text` in `.gitattributes`. |
| A native `<dialog>` opened with `showModal()` makes the popover's buttons inert | The browser makes everything outside a modal dialog inert until it closes. Steps advanced by the app's own events still work; non-native dialogs are unaffected. |
| Another E2E suite clicks into the overlay | Set `__STEPPY_TOURS_DISABLED__` in its browser contexts. |

## Limitations

Tours are linear (no branches, no Back button); navigating to another origin ends them;
hash routing is not supported; targets must be in the main document's light DOM;
changing a tour's text means regenerating the files and redeploying the app.

## Reference

In the Steppy repository: `docs/tours.md` (sections "Adding the player to your app",
"Angular", "React and Vue", "Localization", "Theming and accessibility", "Security",
"Troubleshooting"); `packages/angular/README.md`; and two working apps that the same
scenarios drive, `fixtures/tour-demo/app` (plain JavaScript) and
`fixtures/tour-demo-angular/app` (Angular, PrimeNG and ngx-translate).

This skill is installed by `steppy init --app-dir <this repository>`, run in the Steppy
project, and `steppy update` there keeps it current.
