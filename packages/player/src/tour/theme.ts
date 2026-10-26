/**
 * The overlay's theme surface: the public ``--steppy-tour-*`` custom
 * properties, and the ``theme`` object that writes them from code.
 *
 * One key list drives the stylesheet (``styles.ts``), the host's ``theme``
 * property and the docs (guard: ``test/tour/theme.test.ts``). Values come from
 * the app only — its stylesheet or the ``theme`` property — never from a tour
 * file, so tour data still cannot reach a style.
 */

export const TOUR_THEME_KEYS = [
  // Colour
  'bubbleBg',
  'bubbleFg',
  'muted',
  'accent',
  'accentFg',
  'focus',
  'scrim',
  'link',
  'border',
  // Inline markup in step text (each falls back to the guide's --steppy-* property)
  'strongWeight',
  'strongColor',
  'emStyle',
  'emColor',
  'codeFont',
  'codeSize',
  'codeColor',
  'codeBg',
  'codePadding',
  'codeRadius',
  'linkDecoration',
  // Shape and type
  'radius',
  'width',
  'padding',
  'shadow',
  'fontFamily',
  'fontSize',
  'lineHeight',
  'titleSize',
  'titleWeight',
  'buttonRadius',
  'buttonPadding',
  'buttonWeight',
  // Spotlight
  'ringWidth',
  'spotlightRadius',
] as const;

export type TourThemeKey = (typeof TOUR_THEME_KEYS)[number];

/** Any subset of the keys, as CSS values: ``{ accent: '#0b5cad', width: '360px' }``. */
export type TourTheme = Partial<Record<TourThemeKey, string>>;

/** ``dark`` is the default; ``auto`` follows ``prefers-color-scheme``. */
export const TOUR_APPEARANCES = ['dark', 'light', 'auto'] as const;

export type TourAppearance = (typeof TOUR_APPEARANCES)[number];

const KEYS: ReadonlySet<string> = new Set(TOUR_THEME_KEYS);

export const kebab = (key: string): string => key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);

/** ``accentFg`` → ``--steppy-tour-accent-fg``. */
export const themeProperty = (key: TourThemeKey): string => `--steppy-tour-${kebab(key)}`;

/** The private property the stylesheet reads (``--_pw-accent-fg``). */
export const privateProperty = (key: TourThemeKey): string => `--_pw-${kebab(key)}`;

/** Custom-property declarations for a theme; unknown keys and empty values are dropped. */
export function themeDeclarations(theme: TourTheme | null | undefined): Map<string, string> {
  const out = new Map<string, string>();
  if (!theme || typeof theme !== 'object') return out;
  for (const [key, value] of Object.entries(theme)) {
    if (!KEYS.has(key) || typeof value !== 'string' || value.trim() === '') continue;
    out.set(themeProperty(key as TourThemeKey), value.trim());
  }
  return out;
}
