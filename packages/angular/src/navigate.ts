import { inject, NgZone } from '@angular/core';
import { Router, ROUTES } from '@angular/router';
import type { TourNavigate } from '@steppy/player/tour';

import type { SteppyToursConfig } from './config';

/**
 * The ``navigate`` hook the overlay calls to reach a tour's entry route (or a
 * route linked from step text). Call it in an injection context.
 *
 * The overlay calls it from outside the Angular zone; the router — and the
 * routed view it renders — must run inside it, or a zone.js app never renders
 * the page the tour waits on. Returns ``null`` for ``'document'``, and when the
 * app has no routes, so the overlay falls back to a full page load.
 */
export function createNavigate(option: SteppyToursConfig['navigate'] = 'router'): TourNavigate | null {
  if (option === 'document') return null;
  const zone = inject(NgZone);
  let go: TourNavigate;
  if (option === 'router') {
    if (!inject(ROUTES, { optional: true })) return null;
    go = routerNavigate(inject(Router));
  } else {
    go = option;
  }
  // ``async``: a synchronous throw becomes a rejection, which the overlay reports.
  return (path, ctx) => zone.run(async () => void (await go(path, ctx)));
}

/**
 * Navigate with the router and settle only once the router is where the tour
 * wants to be. ``navigateByUrl`` resolves ``false`` both for a refused
 * navigation and for one to the current URL, and ``true`` for one a guard
 * redirected elsewhere — so the outcome is read from ``router.url``. A
 * rejection makes the overlay fail the start at once (``entry-params``)
 * instead of waiting for a target that will never render.
 */
export function routerNavigate(router: Router): TourNavigate {
  const pathOf = (url: string) => router.serializeUrl(router.parseUrl(url)).split(/[?#]/, 1)[0];
  return async (_path, { appPath }) => {
    const navigated = await router.navigateByUrl(appPath);
    if (pathOf(router.url) === pathOf(appPath)) return;
    throw new Error(
      navigated
        ? `the router redirected ${appPath} to ${router.url}`
        : `the router did not navigate to ${appPath} (a guard or another navigation cancelled it)`,
    );
  };
}
