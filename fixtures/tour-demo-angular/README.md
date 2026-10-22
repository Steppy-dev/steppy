# Angular tour demo

The invoice demo of `fixtures/tour-demo/`, rebuilt as an Angular 21 app on
zone.js, with PrimeNG (a `p-dialog` for the new-invoice form, a `p-select` for
the customer) and ngx-translate (en/fr). It mounts the in-app tour overlay
through `@steppy/angular`:

- `provideSteppyTours()` in `src/app/app.config.ts`, with the tours'
  language following ngx-translate;
- `<steppy-tours />` once in the shell (`src/app/app.ts`);
- the `steppyTourStart` directive on one help button and the
  `SteppyTours` service on the other.

It keeps the plain demo's routes and test-ids, so the plain demo's Steppy
project (`fixtures/tour-demo/project`) drives both apps: same scenarios, same
tours, same generated replays. `/demo-config.json` takes the same test knobs as
the plain demo, plus `blockDetail` (a route guard that refuses
`/invoices/:invoiceId`).

## Build and run

```bash
npm --prefix packages/player ci
npm --prefix packages/player run build
npm --prefix packages/angular ci
npm --prefix packages/angular run build
npm --prefix fixtures/tour-demo-angular/app ci
npm --prefix fixtures/tour-demo-angular/app run build
python fixtures/tour-demo/serve.py --port 8772 --style-src "'unsafe-inline'" --app-dir fixtures/tour-demo-angular/app/dist/tour-demo-angular/browser
```

Open `http://127.0.0.1:8772`. The server serves the golden tour files
(`contracts/fixtures/tours/valid/`) at `/assets/tours/`, with a strict
Content-Security-Policy whose only relaxation is inline styles (Angular and
PrimeNG inject `<style>` elements; scripts stay `'self'`).

The adapter and the overlay are `file:` dependencies on `packages/`;
`preserveSymlinks` in `angular.json` keeps the build from loading a second
copy of Angular from `packages/angular/node_modules`.

## Tests

`tests/integration/test_tour_angular_it.py` (opt-in,
`STEPPY_TOUR_ANGULAR_E2E=1`; CI job `tour-angular-e2e`).
