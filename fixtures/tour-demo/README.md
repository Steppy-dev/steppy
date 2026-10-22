# In-app tour demo

A framework-free invoice app (`app/`) with the in-app tour overlay mounted in its
shell, and the Steppy project that compiles its tours (`project/`). It is what
the browser integration tests run against, and the quickest way to see a tour.

```bash
npm --prefix packages/player install      # once
npm --prefix packages/player run build    # builds dist/steppy-tour.js
python fixtures/tour-demo/serve.py         # http://127.0.0.1:8770
```

Click **Tour: create an invoice**, then follow the popovers with the mouse, or
with the keyboard. Switch the language select to French to run the same tour
in French. Narrow the window below 768px (or use the browser's device mode)
for the phone layout. The aside logs every tour event.

## What the app puts in the tour's way

It reproduces the shapes real apps have, so the overlay is exercised against
them rather than against a toy page:

- A History-API router. The tour's entry route and links in step text navigate
  through the host's `navigate` hook, without a reload.
- A dialog appended to `<body>` behind a z-index 1100 mask, with a focus trap.
  Escape closes it without `preventDefault`; the tour must not end.
- A PrimeNG-shaped dropdown (`p-select` class names, body-appended
  `.p-select-overlay`). It sets its value programmatically and fires no
  `change` event, so the overlay has to notice the label change.
- On a phone (below 768px), a menu button instead of the header links, opening
  a drawer appended to `<body>` over a mask (`role="complementary"`, a focus
  trap, sliding in from the left; Escape closes it and is consumed, as in
  PrimeNG's `p-drawer`).
- A long page whose button is below the fold, guarded by a confirmation shaped
  like PrimeNG's ConfirmDialog (centred, Yes / No without a test-id) and
  answered by a toast.
- A page served under a strict Content-Security-Policy (no inline script or
  style).

`fixtures/tour-demo-angular/app` is the same app in Angular with PrimeNG and
ngx-translate (`p-dialog`, `p-select`, `p-drawer`, `p-confirmdialog`, `p-toast`):
the same routes and test-ids, so this project drives both.

## The three tours

`project/` holds the scenarios and config. `steppy generate` (or
`steppy tours`) run from there writes the tour files; `contracts/fixtures/tours/valid/`
is that output with the default build id, and it is what `serve.py` serves at
`/assets/tours/` by default.

| Tour | Entry | Steps |
|---|---|---|
| `create-invoice` | `/invoices` | New invoice (click) → title (input) → customer (change) → Save (click) → detail (manual) |
| `send-invoice` | `/invoices/:invoiceId` | recipient and message (input, one step per field) → Send (click) → status (manual) |
| `remove-inactive-customers` | `/invoices` | computer: Customers in the header (click); phone: Menu (click) → Customers in the drawer (click). Then Remove inactive customers, below the fold (click) → confirm (manual, kept with `tour: keep`) → done (manual) |

Every scenario declares `layouts: [desktop, mobile]`, so its test and its
replay run at 1280x800 and at 390x844; `tours.layouts` in `config.yaml`
matches the app's 768px breakpoint.

## Remote mode

```bash
python fixtures/tour-demo/serve.py --remote-port 8771
python fixtures/tour-demo/serve.py --remote-port 8771 --remote-mode tamper
```

A second origin serves the tours under `/tours/<build-id>/` with CORS, like a
published guide site. The app's host then reads
`manifest="http://127.0.0.1:8771/tours/{buildId}/tours.manifest.json"` with the
bundled copy as `fallback-manifest`. `--remote-mode` makes that origin
misbehave on purpose:

| Mode | What happens | What the tour does |
|---|---|---|
| `ok` | serves the files | loads them; `tour-start` reports `source: "remote"` |
| `fail` | 503 for everything | falls back to the bundled copy |
| `tamper` | one character of each tour file changed | the bundled manifest's SRI hash rejects it; falls back |
| `nocors` | no `Access-Control-Allow-Origin` | the browser blocks it; falls back |

## Test hooks

`serve.py --config file.json` serves `/demo-config.json`, which the app reads at
boot:

| Key | Effect |
|---|---|
| `manifest`, `fallbackManifest`, `buildId`, `targetTimeout` | set on the tour host |
| `testIdOverrides` | `{"invoice-title": "invoice-name"}` renames a test-id in the app — what a renamed test-id in a real app does to a tour |
| `saveDelayMs` | delay before Save opens the detail page (default 150) |
| `focusTrap` | `"reclaim"` pulls focus back into the dialog, like Angular CDK's `cdkTrapFocus` |
| `autoStart` | starts that tour on every page load, as an app might for new users — the generated tests' kill switch keeps it from covering what they click |

`sessionStorage["demo:loads"]` counts full page loads, so a test can tell a
client-side navigation from a reload.
