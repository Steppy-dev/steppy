import { InjectionToken, type Signal } from '@angular/core';
import type { TourAppearance, TourNavigate, TourSource, TourStringOverrides, TourTheme } from '@steppy/player/tour';

/**
 * Where the tours' language comes from. A string or a signal is used as is; a
 * function is called once, in the root injection context, so it may
 * ``inject()`` the app's i18n service and return a signal of its language.
 * ``null``, ``undefined`` and ``''`` all mean "follow ``<html lang>``".
 */
export type SteppyLocaleSource =
  | string
  | Signal<string | null | undefined>
  | (() => string | Signal<string | null | undefined>);

/** How a tour changes route; ``(path, {appPath, tourId}) => …`` for your own. */
export type SteppyNavigate = TourNavigate;

export interface SteppyToursConfig {
  /** The tour manifest bundled with the app (same origin), e.g. ``tours/tours.manifest.json``. */
  fallbackManifest?: string;
  /** A remote manifest URL; may contain ``{buildId}``. */
  manifest?: string;
  /** The app's build id, for ``{buildId}``. Default: the bundled manifest's. */
  buildId?: string;
  /** The origin trusted for guide links. Default: the bundled manifest's. */
  guideOrigin?: string;
  /** Milliseconds to wait for a step's element (500–120000). Default: the overlay's 8000. */
  targetTimeout?: number;
  /** Stacking where the top layer is unavailable. Default: the overlay's. */
  baseZIndex?: number;
  /** Turns tours off; stops a running one. */
  disabled?: boolean;
  /** Chrome string overrides per locale (buttons, hints). */
  strings?: TourStringOverrides;
  /** ``--steppy-tour-*`` values set on the overlay element; they win over your stylesheet. */
  theme?: TourTheme;
  /** The overlay's default palette: ``'dark'`` (the overlay's default), ``'light'`` or ``'auto'``. */
  appearance?: TourAppearance;
  /** Supply tour files yourself; see the overlay's ``source`` property. */
  source?: TourSource | null;
  /** The tours' language. Default: ``<html lang>``. */
  locale?: SteppyLocaleSource;
  /**
   * ``'router'`` (default): the Angular router, inside the zone; a refused or
   * redirected navigation fails the tour at once. ``'document'``: a full page
   * load. Or a function of your own.
   */
  navigate?: 'router' | 'document' | SteppyNavigate;
  /** How long ``start()`` waits for ``<steppy-tours>`` and the overlay to load. Default 10000 ms. */
  readyTimeout?: number;
}

export const STEPPY_TOURS_CONFIG = new InjectionToken<SteppyToursConfig>('STEPPY_TOURS_CONFIG');

export const DEFAULT_READY_TIMEOUT_MS = 10_000;
