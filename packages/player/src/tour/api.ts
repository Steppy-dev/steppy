/**
 * The public surface of ``<steppy-tour-host>`` — what an app (or a
 * framework adapter) programs against.
 */

import type { TargetClickMode } from './contract.js';
import type { TourSource } from './loader.js';
import type { TourStringOverrides } from './strings.js';
import type { TourAppearance, TourTheme } from './theme.js';

export type { TargetClickMode } from './contract.js';
export type { TourSource } from './loader.js';
export type { TourStringOverrides } from './strings.js';
export type { TourAppearance, TourTheme } from './theme.js';

export interface TourStartOptions {
  /** Route parameters for the tour's entry (``/invoices/:invoiceId``). */
  params?: Record<string, string | number>;
  /** Start at this step id instead of the first. */
  step?: string;
}

export interface TourPosition {
  tourId: string;
  stepId: string;
  /** The step's target test-id, ``null`` for a centred step. ``stepId`` is not one: read this. */
  testId: string | null;
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
  /** ``--steppy-tour-*`` values set inline on the element; they win over the page's CSS. */
  theme: TourTheme | undefined;
  /** Default palette: ``dark`` (default), ``light``, or ``auto`` (follows the colour scheme). */
  appearance: TourAppearance;
  /** What a click on an information step's target does when its tour file does not say: ``pass`` (default), ``next``, ``block``. */
  infoTargetClick: TargetClickMode;
  /** Start a tour. Resolves true once it started; never rejects. */
  start(tourId: string, options?: TourStartOptions): Promise<boolean>;
  stop(reason?: string): void;
  /** Advance the current step, whatever its kind. */
  next(): void;
  readonly isActive: boolean;
  readonly current: TourPosition | null;
}
