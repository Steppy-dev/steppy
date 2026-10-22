/**
 * The string mechanism both players share: placeholder formatting, locale
 * lookup with a base-language rung, and the chrome vocabulary they have in
 * common (so "Next" reads the same in the guide and in the tour).
 */

/** Replace ``{name}`` placeholders; unknown names stay as written. */
export function format(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (_, key) => (key in vars ? String(vars[key]) : `{${key}}`));
}

/**
 * Bare language subtag, lowercased (``fr-BE`` -> ``fr``). Mirrors Python's
 * ``steppy.locales.base_language``: lenient, never validates.
 */
export function baseLanguage(tag: string): string {
  return tag.replace(/_/g, '-').split('-', 1)[0]!.toLowerCase();
}

/**
 * The key of ``keys`` to use for ``requested``: an exact match (case- and
 * ``_``/``-``-insensitive) first, then the requested tag's base language. The
 * query is only ever truncated, never widened — ``fr`` does not match ``fr-BE``.
 */
export function lookupLocale(keys: Iterable<string>, requested: string | null | undefined): string | null {
  if (!requested) return null;
  const norm = (tag: string) => tag.replace(/_/g, '-').toLowerCase();
  const list = [...keys];
  const wanted = norm(requested);
  const exact = list.find((key) => norm(key) === wanted);
  if (exact !== undefined) return exact;
  const base = baseLanguage(requested);
  return list.find((key) => norm(key) === base) ?? null;
}

/**
 * Bundled ``defaults`` for ``locale`` (else ``fallback``), with the host's
 * sparse per-locale ``overrides`` merged on top. Without an override the
 * bundled object itself is returned.
 */
export function resolveStrings<T extends object>(
  defaults: Record<string, T>,
  locale: string,
  overrides?: Partial<Record<string, Partial<T>>>,
  fallback = 'en',
): T {
  const key = lookupLocale(Object.keys(defaults), locale);
  const base = (key !== null ? defaults[key] : undefined) ?? defaults[fallback]!;
  const overrideKey = overrides ? lookupLocale(Object.keys(overrides), locale) : null;
  const override = overrideKey !== null ? overrides![overrideKey] : undefined;
  return override ? { ...base, ...override } : base;
}

export interface ChromeStrings {
  next: string;
  /** Placeholders: ``{current}`` and ``{total}``. */
  progress: string;
}

/** Chrome words the guide and the tour share, per bundled locale. */
export const CHROME_STRINGS: Record<'en' | 'fr' | 'nl' | 'de', ChromeStrings> = {
  en: { next: 'Next', progress: 'Step {current} of {total}' },
  fr: { next: 'Suivant', progress: 'Étape {current} sur {total}' },
  nl: { next: 'Volgende', progress: 'Stap {current} van {total}' },
  de: { next: 'Weiter', progress: 'Schritt {current} von {total}' },
};
