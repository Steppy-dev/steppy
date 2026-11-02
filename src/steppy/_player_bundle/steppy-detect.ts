/**
 * Reader language and layout detection: the pure half.
 *
 * Every rule lives here and nothing touches the DOM, ``window`` or storage at
 * import time, so the same file serves two consumers:
 *
 * - ``iife.ts``, bundled into ``steppy-detect.js`` for the static target's pages;
 * - the Angular target, which copies this file verbatim into the host app as
 *   ``steppy-detect.ts`` (it is vendored next to the bundles and pinned equal to
 *   this source by ``tests/test_player_bundle_files.py``). Keep it free of
 *   imports for that reason.
 *
 * Precedence, for both dimensions: an explicit choice (a URL parameter, then a
 * pick stored by a picker) → what the browser says → the default.
 */

/** Where a resolved value came from. ``param`` and ``stored`` are the reader's own choice. */
export type DetectSource = 'param' | 'stored' | 'browser' | 'viewport' | 'default';

export interface Resolved {
  value: string;
  source: DetectSource;
}

function canonical(tag: string): string {
  return tag.trim().replace(/_/g, '-').toLowerCase();
}

function primarySubtag(tag: string): string {
  return canonical(tag).split('-')[0] ?? '';
}

/**
 * The configured locale that best serves one of the reader's preferences, or
 * ``null``. Preferences are tried in the reader's order; for each, an exact
 * match (case- and ``_``/``-``-insensitive) wins, then the configured locale
 * that *is* its primary subtag (``nl-BE`` → ``nl``), then any configured locale
 * sharing it (``nl-BE`` → ``nl-NL``).
 */
export function matchLocale(prefs: readonly string[], locales: readonly string[]): string | null {
  for (const pref of prefs) {
    if (typeof pref !== 'string' || !pref.trim()) continue;
    const want = canonical(pref);
    const exact = locales.find((l) => canonical(l) === want);
    if (exact) return exact;
    const base = primarySubtag(pref);
    const bare = locales.find((l) => canonical(l) === base);
    if (bare) return bare;
    const sibling = locales.find((l) => primarySubtag(l) === base);
    if (sibling) return sibling;
  }
  return null;
}

export interface LocaleInput {
  param?: string | null;
  stored?: string | null;
  /** ``navigator.languages``; pass ``[]`` to turn browser detection off. */
  prefs?: readonly string[];
  locales: readonly string[];
  defaultLocale: string;
}

export function resolveLocale(input: LocaleInput): Resolved {
  const { locales } = input;
  const param = input.param ? matchLocale([input.param], locales) : null;
  if (param) return { value: param, source: 'param' };
  const stored = input.stored ? matchLocale([input.stored], locales) : null;
  if (stored) return { value: stored, source: 'stored' };
  const browser = matchLocale(input.prefs ?? [], locales);
  if (browser) return { value: browser, source: 'browser' };
  return { value: input.defaultLocale, source: 'default' };
}

/**
 * The layout for a viewport ``width``: the one with the largest minimum width
 * that the viewport reaches. ``null`` when there is nothing to go on.
 */
export function layoutForWidth(
  width: number | null | undefined,
  breakpoints: Readonly<Record<string, number>> | null | undefined,
  layouts: readonly string[],
): string | null {
  if (!breakpoints || typeof width !== 'number' || !isFinite(width) || width <= 0) return null;
  let best: string | null = null;
  let bestMin = -1;
  for (const name of layouts) {
    const min = breakpoints[name];
    if (typeof min !== 'number') continue;
    if (width >= min && min > bestMin) {
      best = name;
      bestMin = min;
    }
  }
  return best;
}

export interface LayoutInput {
  param?: string | null;
  stored?: string | null;
  width?: number | null;
  /** Minimum viewport width per layout; ``null`` turns viewport detection off. */
  breakpoints?: Readonly<Record<string, number>> | null;
  layouts: readonly string[];
  defaultLayout?: string | null;
}

export function resolveLayout(input: LayoutInput): Resolved {
  const { layouts } = input;
  if (input.param && layouts.includes(input.param)) return { value: input.param, source: 'param' };
  if (input.stored && layouts.includes(input.stored)) return { value: input.stored, source: 'stored' };
  const fromWidth = layoutForWidth(input.width, input.breakpoints, layouts);
  if (fromWidth) return { value: fromWidth, source: 'viewport' };
  const fallback = input.defaultLayout && layouts.includes(input.defaultLayout) ? input.defaultLayout : layouts[0];
  return { value: fallback ?? '', source: 'default' };
}

/** Search engines, link unfurlers, auditors and automation: never redirected. */
const BOT_UA =
  /bot\b|bot\/|crawl|spider|slurp|mediapartners|adsbot|apis-google|feedfetcher|facebookexternalhit|facebookcatalog|embedly|quora link preview|outbrain|pinterest|vkshare|w3c_validator|whatsapp|telegram|skypeuripreview|bingpreview|yandex|baiduspider|duckduckgo|applebot|petalbot|semrush|ahrefs|mj12|ia_archiver|headlesschrome|lighthouse|page ?speed|pingdom|gtmetrix|prerender|phantomjs/i;

export function isBot(userAgent: string | null | undefined, webdriver?: boolean | null): boolean {
  if (webdriver) return true;
  if (!userAgent) return false;
  return BOT_UA.test(userAgent);
}

export interface SafeStorage {
  get(): string | null;
  /** ``false`` when the write did not happen (private mode, blocked storage, quota). */
  set(value: string): boolean;
}

/**
 * One key in ``localStorage``, every access in try/catch: merely *reading*
 * ``window.localStorage`` throws in some browsers when site data is blocked.
 */
export function safeStorage(key: string, getStore: () => Storage | null | undefined): SafeStorage {
  return {
    get() {
      try {
        const store = getStore();
        return store ? store.getItem(key) : null;
      } catch {
        return null;
      }
    },
    set(value: string) {
      try {
        const store = getStore();
        if (!store) return false;
        store.setItem(key, value);
        return true;
      } catch {
        return false;
      }
    },
  };
}

/** ``href`` with the current visit's query and fragment carried over. */
export function carryQueryAndHash(href: string, search: string, hash: string): string {
  const cut = href.search(/[?#]/);
  const base = cut === -1 ? href : href.slice(0, cut);
  return base + (search || '') + (hash || '');
}
