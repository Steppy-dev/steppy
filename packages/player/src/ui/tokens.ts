/**
 * Default look values shared by the guide player and the in-app tour overlay.
 *
 * Constants only, never data: they are interpolated into the ``css`` tagged
 * templates with ``cssValue`` (Lit's ``unsafeCSS``), which concatenates the
 * value verbatim — ``baseStyles.cssText`` is byte-identical to the literal
 * stylesheet it replaced (guard: ``test/ui/guide-styles-stable.test.ts``).
 */

import { unsafeCSS, type CSSResult } from 'lit';

/** The guide's palette (``--steppy-*`` defaults in ``templates/base-styles.ts``). */
export const PALETTE = {
  bg: '#f4f1ea',
  surface: '#fbf9f4',
  chromeBg: '#efece3',
  fg: '#1f1d1a',
  muted: '#8a857c',
  border: '#e1ddd1',
  accent: '#d24f2c',
  accentWeak: 'rgba(210, 79, 44, 0.35)',
  success: '#3a7d44',
} as const;

/** System font stacks only: offline, no CDN. */
export const FONTS = {
  serif: "ui-serif, Georgia, 'Times New Roman', serif",
  sans: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  mono: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
} as const;

/** The dark bubble both players draw next to a target (guide tooltip, tour popover). */
export const BUBBLE_LOOK = {
  padding: '8px 12px',
  radius: '6px',
  maxWidth: '280px',
  fontSize: '14px',
  lineHeight: '1.4',
  shadow: '0 4px 12px rgba(0, 0, 0, 0.2)',
} as const;

export const BUTTON_LOOK = {
  radius: '4px',
  padding: '4px 10px',
} as const;

/**
 * Tour overlay defaults. The accent differs from the guide's on purpose:
 * ``#d24f2c`` measures 4.29:1 against white text, below WCAG AA for the
 * overlay's buttons; ``#a8410e`` measures 6.13:1 (guard: ``test/tour/small-modules.test.ts``).
 */
export const TOUR_DEFAULTS = {
  bubbleBg: PALETTE.fg,
  bubbleFg: PALETTE.surface,
  muted: '#c9c5bd',
  accent: '#a8410e',
  accentFg: '#ffffff',
  focus: '#f09a6a',
  scrim: 'rgba(31, 29, 26, 0.45)',
  radius: BUBBLE_LOOK.radius,
} as const;

/** A token value inside a ``css`` template (inserted verbatim, never data). */
export const cssValue = (value: string | number): CSSResult => unsafeCSS(value);
