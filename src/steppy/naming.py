"""Product-derived names, in one place.

Every string the outside world sees that carries the product name — the in-app
tour element, the tour file ``kind`` markers, the kill-switch global, the
browser storage key — is built from :data:`PRODUCT` here, so a future rename is
one line plus the TypeScript mirror (``packages/player/src/ui/names.ts``, kept
equal by ``tests/test_tour_contract_sync.py``).

A stdlib-only leaf: ``models.py`` and the generated-test helpers may import it
on the eager CLI path.
"""

PRODUCT = "steppy"

#: The custom element the app mounts once in its shell.
TOUR_HOST_TAG = f"{PRODUCT}-tour-host"

#: ``kind`` markers that make a tours manifest / tour file self-identifying —
#: the prune step refuses to touch a directory whose manifest lacks it.
TOURS_MANIFEST_KIND = f"{PRODUCT}-tours-manifest"
TOUR_FILE_KIND = f"{PRODUCT}-tour"

TOURS_MANIFEST_FILENAME = "tours.manifest.json"

#: Sub-directory of a static guide site that holds ``<build-id>/`` tour trees.
GUIDE_SITE_TOURS_DIR = "tours"

#: ``globalThis[...] === true`` makes the overlay's ``start()`` a no-op. The
#: generated E2E contexts set it so an app's own auto-started tour cannot
#: intercept clicks in unrelated tests; the tour replay lifts it.
TOURS_KILL_SWITCH_GLOBAL = f"__{PRODUCT.upper()}_TOURS_DISABLED__"

#: ``sessionStorage`` key of the resume record (survives a full reload).
TOUR_RESUME_STORAGE_KEY = f"{PRODUCT}:tour"

#: Name of the Playwright binding the replay helper exposes to collect events.
TOUR_PROBE_BINDING = f"__{PRODUCT}_tour_event"
