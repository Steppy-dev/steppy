/**
 * Overlay styles. Theming goes through public ``--steppy-*`` custom
 * properties, read into private ``--_pw-*`` ones on ``:host`` and never
 * declared themselves — so a page sets them on ``:root`` or on the element.
 * Geometry comes from properties the tracker writes through CSSOM.
 *
 * Defaults meet WCAG AA (guard: ``test/tour/small-modules.test.ts``). The bubble
 * inherits the app's font unless ``--steppy-font-family`` is set. The
 * bubble's inner layout uses logical properties, so it mirrors under the
 * layer's ``dir`` (an Arabic or Hebrew tour); its placement around the target
 * stays physical.
 */

import { css } from 'lit';

import { BUBBLE_LOOK, BUTTON_LOOK, TOUR_DEFAULTS, cssValue } from '../ui/tokens.js';

export const tourStyles = css`
  :host {
    display: contents;
    --_pw-bg: var(--steppy-tour-bubble-bg, var(--steppy-tooltip-bg, ${cssValue(TOUR_DEFAULTS.bubbleBg)}));
    --_pw-fg: var(--steppy-tour-bubble-fg, var(--steppy-tooltip-fg, ${cssValue(TOUR_DEFAULTS.bubbleFg)}));
    --_pw-muted: var(--steppy-tour-muted, ${cssValue(TOUR_DEFAULTS.muted)});
    --_pw-accent: var(--steppy-tour-accent, ${cssValue(TOUR_DEFAULTS.accent)});
    --_pw-accent-fg: var(--steppy-tour-accent-fg, ${cssValue(TOUR_DEFAULTS.accentFg)});
    --_pw-focus: var(--steppy-tour-focus, ${cssValue(TOUR_DEFAULTS.focus)});
    --_pw-scrim: var(--steppy-tour-scrim, ${cssValue(TOUR_DEFAULTS.scrim)});
    --_pw-radius: var(--steppy-tour-radius, ${cssValue(TOUR_DEFAULTS.radius)});
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
    border-radius: var(--_pw-radius);
    box-shadow:
      0 0 0 2px var(--_pw-accent),
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
    width: min(320px, calc(100vw - 16px));
    max-height: calc(100vh - 16px);
    overflow: auto;
    padding: ${cssValue(BUBBLE_LOOK.padding)};
    border-radius: var(--_pw-radius);
    background: var(--_pw-bg);
    color: var(--_pw-fg);
    box-shadow: ${cssValue(BUBBLE_LOOK.shadow)};
    font-family: var(--steppy-font-family, inherit);
    font-size: ${cssValue(BUBBLE_LOOK.fontSize)};
    line-height: ${cssValue(BUBBLE_LOOK.lineHeight)};
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
    font-size: 1em;
    font-weight: 600;
  }
  .body {
    margin: 0;
    overflow-wrap: anywhere;
  }
  .body a,
  .guide {
    color: var(--_pw-fg);
    text-decoration: underline;
  }
  .guide {
    display: inline-block;
    margin-top: 6px;
  }
  code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.92em;
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
    cursor: pointer;
    border-radius: ${cssValue(BUTTON_LOOK.radius)};
    padding: ${cssValue(BUTTON_LOOK.padding)};
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
    color: var(--_pw-fg);
    border-color: var(--_pw-muted);
  }
  .close {
    margin-inline-start: auto;
    padding: 0 6px;
    background: transparent;
    color: var(--_pw-fg);
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
    background: var(--_pw-bg);
    color: var(--_pw-fg);
    box-shadow: ${cssValue(BUBBLE_LOOK.shadow)};
    font-family: var(--steppy-font-family, inherit);
    font-size: ${cssValue(BUBBLE_LOOK.fontSize)};
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
