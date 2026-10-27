/**
 * ``@steppy/player/tour`` — the in-app tour overlay.
 *
 * Importing this module registers ``<steppy-tour-host>`` (once, even if two
 * copies of the bundle load). It touches ``HTMLElement`` at load, so a
 * server-rendered app imports it in the browser only.
 */

import { defineTourHost } from './host.js';

export { SteppyTourHost, defineTourHost } from './host.js';
export type {
  TargetClickMode,
  TourHostApi,
  TourNavigate,
  TourNavigateContext,
  TourPosition,
  TourSource,
  TourStartOptions,
  TourStringOverrides,
} from './api.js';
export { TOUR_APPEARANCES, TOUR_THEME_KEYS, themeProperty, type TourAppearance, type TourTheme, type TourThemeKey } from './theme.js';
export type { TourEventDetailMap, TourEventMeta, TourStepPosition } from './events.js';
export type { DismissVia, StepReason, TourErrorDetail, TourSourceKind } from './engine.js';
export {
  ADVANCE_KINDS,
  PLACEMENTS,
  TARGET_CLICK_MODES,
  TOUR_ERROR_REASONS,
  TOUR_EVENTS,
  TOUR_PARTS,
  TOUR_SCHEMA_VERSION,
  type AdvanceKind,
  type Placement,
  type TourErrorReason,
  type TourEventName,
  type TourPart,
} from './contract.js';
export { TOUR_HOST_TAG, TOURS_KILL_SWITCH_GLOBAL } from '../ui/names.js';
export { DEFAULT_TOUR_STRINGS, type TourStrings } from './strings.js';
export { validateManifest, validateTour, type TourFile, type TourManifest, type TourStep } from './validate.js';

defineTourHost();
