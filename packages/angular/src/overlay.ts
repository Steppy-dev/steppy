/**
 * The seam between the adapter and the tour overlay (``@steppy/player/tour``).
 *
 * Everything imported from the overlay at the top of this package is a *type*:
 * the overlay touches ``HTMLElement`` when its module loads, so its only
 * runtime reference is the dynamic ``import()`` in ``OVERLAY_LOADER``, which
 * runs in the browser, after the first render. Tests swap the loader for a fake
 * element on a tag of their own.
 *
 * One literal per line: ``tests/test_angular_adapter_sync.py`` reads the tag and
 * the event names with regular expressions and compares them with the overlay.
 */

import { InjectionToken } from '@angular/core';
import type { TourEventName, TourHostApi, TourSource, TourStringOverrides } from '@steppy/player/tour';

export const TOUR_HOST_TAG = 'steppy-tour-host';
export const TOUR_EVENT_NAMES = ['tour-start', 'tour-step', 'tour-complete', 'tour-dismiss', 'tour-offpath', 'tour-error'] as const;

// Fails to compile when the overlay gains an event this list does not name.
type AssertNever<T extends never> = T;
export type ɵAllTourEventsListed = AssertNever<Exclude<TourEventName, (typeof TOUR_EVENT_NAMES)[number]>>;

/** ``<steppy-tour-host>`` as the adapter drives it. */
export type TourHostElement = HTMLElement & TourHostApi;

export interface OverlayLoader {
  /** The custom element tag the loader defines. */
  readonly tag: string;
  /** Load (and so define) the element. */
  load(): Promise<unknown>;
}

export const OVERLAY_LOADER = new InjectionToken<OverlayLoader>('steppy tour overlay', {
  providedIn: 'root',
  factory: () => ({ tag: TOUR_HOST_TAG, load: () => import('@steppy/player/tour') }),
});

/** What the adapter writes onto the element (``navigate`` is set once, at mount). */
export interface HostProps {
  fallbackManifest: string;
  manifest: string;
  buildId: string;
  guideOrigin: string;
  locale: string;
  disabled: boolean;
  strings: TourStringOverrides | undefined;
  source: TourSource | null;
  targetTimeout?: number;
  baseZIndex?: number;
}

/**
 * Write the props onto the element — before it is defined as well: the
 * overlay keeps properties set on the element before its upgrade.
 */
export function applyProps(el: TourHostElement, p: HostProps): void {
  el.fallbackManifest = p.fallbackManifest;
  el.manifest = p.manifest;
  el.buildId = p.buildId;
  el.guideOrigin = p.guideOrigin;
  el.locale = p.locale;
  el.disabled = p.disabled;
  el.strings = p.strings;
  el.source = p.source;
  if (p.targetTimeout !== undefined) el.targetTimeout = p.targetTimeout;
  if (p.baseZIndex !== undefined) el.baseZIndex = p.baseZIndex;
}
