/**
 * Overlay styles. Theming goes through public ``--steppy-tour-*`` custom
 * properties (the keys of ``theme.ts``), read into private ``--_pw-*`` ones on
 * ``:host`` and never declared themselves — so a page sets them on ``:root``
 * or on the element, and the host's ``theme`` property sets them inline.
 * ``appearance="light"`` (or ``"auto"`` under a light colour scheme) swaps the
 * defaults, never a value the page set. Geometry comes from properties the
 * tracker writes through CSSOM.
 *
 * Defaults meet WCAG AA in both appearances (guard: ``test/tour/small-modules.test.ts``).
 * The bubble inherits the app's font unless ``--steppy-tour-font-family`` (or
 * ``--steppy-font-family``) is set. The bubble's inner layout uses logical
 * properties, so it mirrors under the layer's ``dir`` (an Arabic or Hebrew
 * tour); its placement around the target stays physical.
 */

import { css, type CSSResult } from 'lit';

import { TOUR_DEFAULTS, TOUR_LIGHT, cssValue } from '../ui/tokens.js';
import { TOUR_THEME_KEYS, privateProperty, themeProperty, type TourThemeKey } from './theme.js';

/** Tokens whose default is another token, so they follow its override. */
const DERIVED = {
  link: `var(${privateProperty('bubbleFg')})`,
  strongColor: `var(${privateProperty('bubbleFg')})`,
  emColor: `var(${privateProperty('bubbleFg')})`,
  codeColor: `var(${privateProperty('bubbleFg')})`,
  spotlightRadius: `var(${privateProperty('radius')})`,
} as const;

type Defaults = Record<Exclude<TourThemeKey, keyof typeof DERIVED>, string>;

/** Older or shared properties a token falls back to before its default. */
const ALIASES: Partial<Record<TourThemeKey, string>> = {
  bubbleBg: '--steppy-tooltip-bg',
  bubbleFg: '--steppy-tooltip-fg',
  fontFamily: '--steppy-font-family',
  // The guide's inline-markup properties (textStyles), so one set styles guides and tours alike.
  link: '--steppy-link-color',
  linkDecoration: '--steppy-link-decoration',
  strongWeight: '--steppy-strong-weight',
  strongColor: '--steppy-strong-color',
  emStyle: '--steppy-em-style',
  emColor: '--steppy-em-color',
  codeFont: '--steppy-code-font',
  codeSize: '--steppy-code-size',
  codeColor: '--steppy-code-color',
  codeBg: '--steppy-code-bg',
  codePadding: '--steppy-code-padding',
  codeRadius: '--steppy-code-radius',
};

const DARK: Defaults = TOUR_DEFAULTS;
const LIGHT: Defaults = TOUR_LIGHT;

/**
 * ``--_pw-x: var(--steppy-tour-x, <default>);`` per key. Given ``base``, only
 * the keys whose default differs from it — derived ones never do.
 */
function privateTokens(defaults: Defaults, base?: Defaults): CSSResult {
  const lines: string[] = [];
  for (const key of TOUR_THEME_KEYS) {
    const derived = key in DERIVED;
    if (base && (derived || base[key as keyof Defaults] === defaults[key as keyof Defaults])) continue;
    const value = derived ? DERIVED[key as keyof typeof DERIVED] : defaults[key as keyof Defaults];
    const alias = ALIASES[key];
    const fallback = alias ? `var(${alias}, ${value})` : value;
    lines.push(`${privateProperty(key)}: var(${themeProperty(key)}, ${fallback});`);
  }
  return cssValue(lines.join('\n    '));
}

export const tourStyles = css`
  :host {
    display: contents;
    ${privateTokens(DARK)}
  }
  :host([appearance='light']) {
    ${privateTokens(LIGHT, DARK)}
  }
  @media (prefers-color-scheme: light) {
    :host([appearance='auto']) {
      ${privateTokens(LIGHT, DARK)}
    }
  }
  .layer {
    position: fixed;
    inset: 0;
    width: auto;
    height: auto;
    max-width: none;
    max-height: none;
    margin: 0;
    padding: 0;
    border: 0;
    background: transparent;
    color: inherit;
    overflow: visible;
    pointer-events: none;
    z-index: var(--_pw-z, 2147483000);
  }
  .layer::backdrop {
    pointer-events: none;
    background: transparent;
  }
  .spotlight {
    position: absolute;
    left: var(--_pw-sx, 0);
    top: var(--_pw-sy, 0);
    width: var(--_pw-sw, 0);
    height: var(--_pw-sh, 0);
    border-radius: var(--_pw-spotlight-radius);
    box-shadow:
      0 0 0 var(--_pw-ring-width) var(--_pw-accent),
      0 0 0 max(100vw, 100vh) var(--_pw-scrim);
    pointer-events: none;
    transition:
      left 0.15s ease,
      top 0.15s ease,
      width 0.15s ease,
      height 0.15s ease;
  }
  .bubble {
    position: absolute;
    box-sizing: border-box;
    width: min(var(--_pw-width), calc(100vw - 16px));
    max-height: calc(100vh - 16px);
    overflow: auto;
    padding: var(--_pw-padding);
    border: var(--_pw-border);
    border-radius: var(--_pw-radius);
    background: var(--_pw-bubble-bg);
    color: var(--_pw-bubble-fg);
    box-shadow: var(--_pw-shadow);
    font-family: var(--_pw-font-family);
    font-size: var(--_pw-font-size);
    line-height: var(--_pw-line-height);
    pointer-events: auto;
    visibility: hidden;
    outline: none;
  }
  .bubble[data-placed] {
    visibility: visible;
  }
  .bubble[data-mode='anchored'] {
    left: var(--_pw-x, 0);
    top: var(--_pw-y, 0);
  }
  .bubble[data-mode='centered'] {
    left: 50%;
    top: 50%;
    transform: translate(-50%, -50%);
  }
  .bubble[data-mode='sheet-bottom'],
  .bubble[data-mode='sheet-top'] {
    left: 50%;
    transform: translateX(-50%);
  }
  .bubble[data-mode='sheet-bottom'] {
    bottom: calc(8px + var(--_pw-kb, 0px));
  }
  .bubble[data-mode='sheet-top'] {
    top: 8px;
  }
  .bubble[data-yield] {
    pointer-events: none;
    opacity: 0.15;
  }
  .layer[data-offscreen] .spotlight {
    display: none;
  }
  header {
    display: flex;
    align-items: flex-start;
    gap: 8px;
  }
  h2 {
    flex: 1;
    margin: 0 0 4px;
    font-size: var(--_pw-title-size);
    font-weight: var(--_pw-title-weight);
  }
  .body {
    margin: 0;
    overflow-wrap: anywhere;
  }
  .body a,
  .guide {
    color: var(--_pw-link);
    text-decoration: underline;
  }
  .body a {
    text-decoration: var(--_pw-link-decoration);
  }
  .body strong {
    font-weight: var(--_pw-strong-weight);
    color: var(--_pw-strong-color);
  }
  .body em {
    font-style: var(--_pw-em-style);
    color: var(--_pw-em-color);
  }
  .guide {
    display: inline-block;
    margin-top: 6px;
  }
  .body code {
    font-family: var(--_pw-code-font);
    font-size: var(--_pw-code-size);
    color: var(--_pw-code-color);
    background: var(--_pw-code-bg);
    padding: var(--_pw-code-padding);
    border-radius: var(--_pw-code-radius);
  }
  footer {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 10px;
  }
  .progress {
    margin-inline-end: auto;
    color: var(--_pw-muted);
    font-variant-numeric: tabular-nums;
  }
  button {
    font: inherit;
    font-weight: var(--_pw-button-weight);
    cursor: pointer;
    border-radius: var(--_pw-button-radius);
    padding: var(--_pw-button-padding);
    border: 1px solid transparent;
  }
  button:focus-visible,
  a:focus-visible {
    outline: 2px solid var(--_pw-focus);
    outline-offset: 2px;
  }
  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .primary {
    background: var(--_pw-accent);
    color: var(--_pw-accent-fg);
  }
  .secondary {
    background: transparent;
    color: var(--_pw-bubble-fg);
    border-color: var(--_pw-muted);
  }
  .close {
    margin-inline-start: auto;
    padding: 0 6px;
    background: transparent;
    color: var(--_pw-bubble-fg);
    font-size: 1.25em;
    line-height: 1;
  }
  .pill {
    position: absolute;
    inset-inline-end: 16px;
    bottom: 16px;
    display: flex;
    gap: 4px;
    padding: 4px;
    border-radius: 999px;
    border: var(--_pw-border);
    background: var(--_pw-bubble-bg);
    color: var(--_pw-bubble-fg);
    box-shadow: var(--_pw-shadow);
    font-family: var(--_pw-font-family);
    font-size: var(--_pw-font-size);
    pointer-events: auto;
  }
  .pill .primary {
    border-radius: 999px;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    padding: 0;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
    border: 0;
  }
  @media (prefers-reduced-motion: reduce) {
    .spotlight {
      transition: none;
    }
  }
  @media (forced-colors: active) {
    .spotlight {
      outline: 3px solid Highlight;
      box-shadow: none;
    }
    .bubble,
    .pill {
      border: 1px solid CanvasText;
    }
  }
  @media print {
    .layer {
      display: none;
    }
  }
`;
