/**
 * In-app tour data contract v1, the TypeScript twin of ``src/steppy/tour_spec.py``.
 *
 * The overlay validates every manifest and tour file against these values
 * (``validate.ts``); the compiler never emits anything they reject.
 * ``tests/test_tour_contract_sync.py`` keeps both sides equal, so every value
 * stays a plain literal: string patterns carry no backslashes, and arrays are
 * written on one line.
 */

export const TOUR_SCHEMA_VERSION = 1;

export const ADVANCE_KINDS = ['click', 'input', 'change', 'visible', 'manual'] as const;
export const TARGETED_ADVANCE_KINDS = ['click', 'input', 'change', 'visible'] as const;
export const PLACEMENTS = ['top', 'bottom', 'left', 'right', 'auto'] as const;
export const TARGET_CLICK_MODES = ['pass', 'next', 'block'] as const;
export const TOUR_EVENTS = ['tour-start', 'tour-step', 'tour-complete', 'tour-dismiss', 'tour-offpath', 'tour-error'] as const;
export const TOUR_ERROR_REASONS = ['target-timeout', 'entry-params', 'load-failed', 'schema-unsupported', 'invalid-data'] as const;
export const TOUR_PARTS = ['layer', 'spotlight', 'bubble', 'title', 'body', 'progress', 'button', 'primary', 'secondary', 'next', 'close', 'skip', 'end', 'resume', 'pill', 'guide-link'] as const;
export const ATTR_TOUR_ID = 'data-tour-id';
export const ATTR_STEP_ID = 'data-step-id';
export const ATTR_STATE = 'data-state';
export const ATTR_ADVANCE = 'data-advance';
export const BUBBLE_STATES = ['shown', 'fallback'] as const;
/** Languages ``strings.ts`` ships chrome strings for; others need the host's ``strings``. */
export const CHROME_STRING_LOCALES = ['en', 'fr', 'nl', 'de'] as const;

export const TEST_ID_PATTERN = '^[A-Za-z0-9_.:/-]{1,128}$';
export const STEP_ID_PATTERN = '^[A-Za-z0-9_.:/-]{1,128}$';
export const TEST_ID_ATTRIBUTE_PATTERN = '^data-[a-z0-9-]+$';
export const TOUR_ID_PATTERN = '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$';
export const LAYOUT_NAME_PATTERN = '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$';
export const ROLE_PATTERN = '^[A-Za-z0-9_-]{1,64}$';
export const LOCALE_PATTERN = '^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{1,8}){0,5}$';
export const SRI_PATTERN = '^sha384-[A-Za-z0-9+/]{64}$';
export const TOUR_FILE_NAME_PATTERN = '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}[.][A-Za-z0-9_-]{1,64}[.]json$';
export const ROUTE_PATTERN = '^/$|^(?:/(?::[A-Za-z_][A-Za-z0-9_]{0,63}|[A-Za-z0-9._~%!$&()*+,;=@-]{1,128})){1,32}$';
export const MEDIA_QUERY_PATTERN = '^[A-Za-z0-9 :().,<>=/_-]{1,256}$';
export const BUILD_ID_PATTERN = '^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?$';

export const MAX_TOURS = 500;
export const MAX_STEPS = 100;
export const MAX_LOCALES = 32;
export const MAX_LAYOUTS = 16;
export const MAX_TITLE_CHARS = 200;
export const MAX_TEXT_CHARS = 2000;
export const MAX_ROUTE_CHARS = 512;
export const MAX_URL_CHARS = 2048;
export const MAX_MEDIA_QUERY_CHARS = 256;
export const MAX_TOUR_FILE_BYTES = 262144;
export const MAX_MANIFEST_BYTES = 1048576;

/** Player-only defaults (no Python twin). */
export const DEFAULT_TARGET_TIMEOUT_MS = 8000;
export const DEFAULT_BASE_Z_INDEX = 2147483000;

export type AdvanceKind = (typeof ADVANCE_KINDS)[number];
export type Placement = (typeof PLACEMENTS)[number];
/** What a click on an information step's target does: reach the element, advance, or nothing. */
export type TargetClickMode = (typeof TARGET_CLICK_MODES)[number];
export type TourEventName = (typeof TOUR_EVENTS)[number];
export type TourErrorReason = (typeof TOUR_ERROR_REASONS)[number];
export type TourPart = (typeof TOUR_PARTS)[number];
