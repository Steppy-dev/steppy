# @steppy/angular

The Angular adapter for Steppy's in-app tours: guided popovers over your
live app, compiled from the same Markdown scenarios your end-to-end tests run.
The overlay itself is `<steppy-tour-host>` from `@steppy/player/tour`; this
package mounts it the Angular way.

- `provideSteppyTours({...})`: configuration, in `app.config.ts`.
- `<steppy-tours />`: the overlay, placed **once** in the app shell.
- `SteppyTours`: a root service to `start()` / `stop()` / `next()` tours,
  with `active`, `current` and `lastError` signals and an `events` stream.
- `steppyTourStart`: a directive that starts a tour on click.

Angular 20–22, zone.js or zoneless, SSR-safe (nothing renders or loads on the
server). Your app needs no `CUSTOM_ELEMENTS_SCHEMA`.

## Install

```bash
npm install @steppy/angular @steppy/player
```

`@steppy/player` is a peer dependency at the same version as the adapter.

To use an unreleased build, build both from a Steppy checkout:

```bash
npm --prefix packages/player ci
npm --prefix packages/player run build
npm --prefix packages/angular ci
npm --prefix packages/angular run build
```

Then, in your app, either depend on the folders
(`"@steppy/player": "file:<checkout>/packages/player"`,
`"@steppy/angular": "file:<checkout>/packages/angular/dist"`, and
`"preserveSymlinks": true` in the build options so Angular is not loaded twice),
or install the tarballs `npm pack` makes from those two folders.

## Use

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
      // Written by `steppy tours` (an app-assets target in public/tours).
      fallbackManifest: 'tours/tours.manifest.json',
      // Optional: follow your i18n library. Without it the tours follow <html lang>.
      locale: () => {
        const translate = inject(TranslateService);
        return toSignal(translate.onLangChange.pipe(map((e) => e.lang)), { initialValue: translate.getCurrentLang() ?? '' });
      },
    }),
  ],
};
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
```

```ts
// anywhere
const tours = inject(SteppyTours);
await tours.start('send-invoice', { params: { invoiceId: 1001 } });
```

### What the adapter does for you

- **Lazy, browser-only loading.** The overlay module is imported after the
  first render, and only in the browser.
- **Router navigation.** A tour that starts away from its entry route
  navigates with `Router.navigateByUrl`, inside the Angular zone, so the routed
  view renders in zone.js apps. A guard that refuses or redirects the
  navigation fails the tour at once (`tour-error`, reason `entry-params`)
  instead of leaving it waiting for an element that will never render. Set
  `navigate: 'document'` for full page loads, or pass your own function.
- **Zones.** The overlay's observers, polls and frames run outside the Angular
  zone, so they never drive change detection; events, outputs and the router
  come back inside it.
- **`start()` from anywhere.** It waits for `<steppy-tours>` to mount and the
  overlay to load (`readyTimeout`, 10 s by default), the last call wins, and it
  resolves `false` — never rejects — when a tour cannot start.

Destroying `<steppy-tours>` suspends a running tour rather than dismissing
it (the overlay keeps its resume record). Call `tours.stop()` first to end it.

Inputs on `<steppy-tours>` override the `provideSteppyTours()` settings:
`fallbackManifest`, `manifest`, `buildId`, `guideOrigin`, `locale`,
`targetTimeout`, `baseZIndex`, `disabled`, `strings`, `source`. Outputs:
`tourStart`, `tourStep`, `tourComplete`, `tourDismiss`, `tourOffpath`,
`tourError`.

Hash routing (`withHashLocation()`) is not supported by in-app tours.

The full reference — authoring tours, the overlay's options and events,
remote mode, CSP and Trusted Types — is `docs/tours.md` in the Steppy
repository.

## Develop

```bash
npm ci
npm test        # the zoneless suite, then the zone.js suite (ng test --configuration zone)
npm run build   # ng-packagr → dist/; publish dist/, never this folder
```

The overlay is replaced by a fake element in the specs (`src/testing/`), except
in `real-overlay.spec.ts`. The browser test against a real Angular + PrimeNG
app is `tests/integration/test_tour_angular_it.py` (the demo is
`fixtures/tour-demo-angular/`).
