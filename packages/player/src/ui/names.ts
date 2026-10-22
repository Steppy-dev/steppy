/**
 * Product-derived names, the TypeScript twin of ``src/steppy/naming.py``.
 *
 * One literal per line on purpose: ``tests/test_tour_contract_sync.py`` reads
 * this file with regular expressions and fails when a value drifts from the
 * Python side. A future rename edits both files.
 */

export const PRODUCT = 'steppy';
export const TOUR_HOST_TAG = 'steppy-tour-host';
export const TOURS_MANIFEST_KIND = 'steppy-tours-manifest';
export const TOUR_FILE_KIND = 'steppy-tour';
export const TOURS_MANIFEST_FILENAME = 'tours.manifest.json';
export const TOURS_KILL_SWITCH_GLOBAL = '__STEPPY_TOURS_DISABLED__';
export const TOUR_RESUME_STORAGE_KEY = 'steppy:tour';
/** ``globalThis[Symbol.for(...)]`` marks the host instance that owns the running tour. */
export const TOUR_ACTIVE_SYMBOL_KEY = 'steppy.tour.active';
/** Prefix of every public CSS custom property (``--steppy-accent`` ...). */
export const CSS_TOKEN_PREFIX = '--steppy-';
