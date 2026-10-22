/**
 * The public surface of ``<steppy-tour-host>`` — what an app (or a
 * framework adapter) programs against.
 */

import type { TourSource } from './loader.js';
import type { TourStringOverrides } from './strings.js';

export type { TourSource } from './loader.js';
export type { TourStringOverrides } from './strings.js';

export interface TourStartOptions {
  /** Route parameters for the tour's entry (``/invoices/:invoiceId``). */
  params?: Record<string, string | number>;
  /** Start at this step id instead of the first. */
  step?: string;
}

export interface TourPosition {
  tourId: string;
  stepId: string;
  /** 0-based among the steps that apply to the current layout. */
  index: number;
  total: number;
}

export interface TourNavigateContext {
  /** The same path relative to ``<base href>`` — what a client-side router wants. */
  appPath: string;
  tourId: string;
}

/**
 * Navigate the app to a tour's entry (or a route linked from step text).
 * ``path`` is the document path, base included. Default: ``location.assign``.
 * A returned promise is awaited (its value is ignored); a rejection fails the
 * start with ``entry-params``. Router calls fit as they are:
 * ``(_, { appPath }) => router.navigateByUrl(appPath)``.
 */
export type TourNavigate = (path: string, ctx: TourNavigateContext) => void | PromiseLike<unknown>;

export interface TourHostApi {
  manifest: string;
  fallbackManifest: string;
  buildId: string;
  locale: string;
  guideOrigin: string;
  baseZIndex: number;
  targetTimeout: number;
  disabled: boolean;
  source: TourSource | null;
  navigate: TourNavigate | null;
  strings: TourStringOverrides | undefined;
  /** Start a tour. Resolves true once it started; never rejects. */
  start(tourId: string, options?: TourStartOptions): Promise<boolean>;
  stop(reason?: string): void;
  /** Advance the current step, whatever its kind. */
  next(): void;
  readonly isActive: boolean;
  readonly current: TourPosition | null;
}
