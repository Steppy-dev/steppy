/**
 * Shared player styles: design tokens, the screenshot stage + cursor/ripple/
 * kbd/hotspot/tooltip, status/error blocks, and the shared chrome control
 * primitives (buttons, selects, segmented control) every template composes.
 *
 * Design language: warm cream surfaces, near-black text, rust-orange accent,
 * green for completed steps. Serif titles, sans body, mono meta/counters. All
 * theming flows through CSS custom properties on ``:host`` so the player stays
 * overridable; fonts use system stacks only (offline, no CDN).
 *
 * The historical engine variable names (``--steppy-hotspot-color`` etc.) are
 * preserved and merely re-pointed at the new palette so existing host overrides
 * keep working.
 */

import { css } from 'lit';

import { BUBBLE_LOOK, BUTTON_LOOK, FONTS, PALETTE, cssValue } from '../ui/tokens.js';

export const baseStyles = css`
  :host {
    display: block;
    position: relative;
    /* palette */
    --steppy-bg: ${cssValue(PALETTE.bg)};
    --steppy-surface: ${cssValue(PALETTE.surface)};
    --steppy-chrome-bg: ${cssValue(PALETTE.chromeBg)};
    --steppy-fg: ${cssValue(PALETTE.fg)};
    --steppy-muted: ${cssValue(PALETTE.muted)};
    --steppy-border: ${cssValue(PALETTE.border)};
    --steppy-accent: ${cssValue(PALETTE.accent)};
    --steppy-accent-weak: ${cssValue(PALETTE.accentWeak)};
    --steppy-success: ${cssValue(PALETTE.success)};
    /* typography */
    --steppy-font-serif: ${cssValue(FONTS.serif)};
    --steppy-font-sans: ${cssValue(FONTS.sans)};
    --steppy-font-mono: ${cssValue(FONTS.mono)};
    --steppy-font-family: var(--steppy-font-sans);
    /* engine vars (names preserved, re-pointed at the palette) */
    --steppy-cursor-size: 24px;
    --steppy-cursor-color: var(--steppy-fg);
    --steppy-ripple-color: rgba(31, 29, 26, 0.25);
    --steppy-hotspot-color: var(--steppy-accent);
    --steppy-hotspot-glow: var(--steppy-accent-weak);
    --steppy-tooltip-bg: var(--steppy-fg);
    --steppy-tooltip-fg: var(--steppy-surface);

    font-family: var(--steppy-font-family);
    color: var(--steppy-fg);
  }

  .steppy-wrapper {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  /* ── Stage ─────────────────────────────────────────────────────────── */
  .steppy-stage {
    position: relative;
    width: 100%;
    background: var(--steppy-chrome-bg);
    overflow: hidden;
    border-radius: 8px;
    user-select: none;
  }
  .steppy-stage img {
    display: block;
    /* width:auto caps display at the screenshot's intrinsic pixel width (never
       upscales), max-width:100% fills/shrinks within the stage. A dense capture
       (mobile device_scale_factor=3) downscales to fill the stage crisply; a
       low-res capture renders at native size instead of blurred. The img stays
       left-aligned (no centering) so the cursor/hotspot overlay — positioned in
       img-display-space with the stage top-left as origin — stays aligned. */
    width: auto;
    max-width: 100%;
    height: auto;
    pointer-events: none;
  }
  .steppy-cursor {
    position: absolute;
    top: 0;
    left: 0;
    width: var(--steppy-cursor-size);
    height: var(--steppy-cursor-size);
    pointer-events: none;
    opacity: 0;
    transition: opacity 200ms ease-in-out;
    background: var(--steppy-cursor-color);
    clip-path: polygon(0 0, 0 75%, 25% 55%, 45% 95%, 60% 85%, 40% 50%, 75% 40%);
    filter: drop-shadow(0 2px 6px rgba(0, 0, 0, 0.3));
    transform-origin: 0 0;
    z-index: 4;
  }
  .steppy-cursor.visible {
    opacity: 1;
  }
  .steppy-cursor.pulse {
    animation: steppy-pulse 200ms ease-in-out;
  }
  @keyframes steppy-pulse {
    0%,
    100% {
      transform: scale(1);
    }
    50% {
      transform: scale(1.25);
    }
  }
  .steppy-ripple {
    position: absolute;
    top: 0;
    left: 0;
    width: 12px;
    height: 12px;
    margin: -6px;
    border-radius: 50%;
    background: transparent;
    border: 2px solid var(--steppy-ripple-color);
    pointer-events: none;
    opacity: 0;
    z-index: 3;
  }
  .steppy-ripple.fire {
    animation: steppy-ripple 400ms ease-out forwards;
  }
  @keyframes steppy-ripple {
    0% {
      transform: scale(0.6);
      opacity: 1;
    }
    100% {
      transform: scale(3);
      opacity: 0;
    }
  }
  .steppy-kbd {
    position: absolute;
    top: 0;
    left: 0;
    background: var(--steppy-fg);
    color: var(--steppy-surface);
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 12px;
    font-family: var(--steppy-font-mono);
    opacity: 0;
    transition: opacity 120ms;
    pointer-events: none;
    z-index: 5;
  }
  .steppy-kbd.visible {
    opacity: 1;
  }
  .steppy-hotspot {
    position: absolute;
    box-sizing: border-box;
    background: transparent;
    border: 2px solid var(--steppy-hotspot-color);
    border-radius: 4px;
    cursor: pointer;
    padding: 0;
    z-index: 2;
    transition: outline-offset 120ms;
    animation: steppy-hotspot-pulse 1500ms ease-in-out infinite;
    box-shadow: 0 0 0 4px var(--steppy-hotspot-glow);
  }
  .steppy-hotspot:focus-visible {
    outline: 3px solid var(--steppy-hotspot-color);
    outline-offset: 4px;
  }
  .steppy-hotspot.miss {
    animation: steppy-hotspot-shake 400ms;
  }
  @keyframes steppy-hotspot-pulse {
    0%,
    100% {
      box-shadow: 0 0 0 4px var(--steppy-hotspot-glow);
    }
    50% {
      box-shadow: 0 0 0 10px transparent;
    }
  }
  @keyframes steppy-hotspot-shake {
    0%,
    100% {
      transform: translateX(0);
    }
    25% {
      transform: translateX(-6px);
    }
    75% {
      transform: translateX(6px);
    }
  }
  .steppy-tooltip {
    position: absolute;
    background: var(--steppy-tooltip-bg);
    color: var(--steppy-tooltip-fg);
    padding: ${cssValue(BUBBLE_LOOK.padding)};
    border-radius: ${cssValue(BUBBLE_LOOK.radius)};
    max-width: ${cssValue(BUBBLE_LOOK.maxWidth)};
    font-size: ${cssValue(BUBBLE_LOOK.fontSize)};
    line-height: ${cssValue(BUBBLE_LOOK.lineHeight)};
    z-index: 6;
    pointer-events: none;
    box-shadow: ${cssValue(BUBBLE_LOOK.shadow)};
  }
  .steppy-miss-toast {
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    bottom: 16px;
    background: var(--steppy-fg);
    color: var(--steppy-surface);
    padding: 6px 14px;
    border-radius: 999px;
    font-size: 13px;
    z-index: 7;
    pointer-events: none;
    animation: steppy-toast 1400ms ease-out forwards;
  }
  @keyframes steppy-toast {
    0%,
    80% {
      opacity: 1;
    }
    100% {
      opacity: 0;
    }
  }
  .steppy-stage-click-catcher {
    position: absolute;
    inset: 0;
    z-index: 1;
    cursor: default;
  }

  /* ── Status / error ────────────────────────────────────────────────── */
  .steppy-state {
    padding: 24px;
    text-align: center;
    color: var(--steppy-muted);
  }
  .steppy-state.error {
    color: #b3402a;
    background: #fbeae6;
    border-radius: 6px;
  }
  .steppy-state .steppy-state-detail {
    font-size: 12px;
    margin-top: 8px;
    font-family: var(--steppy-font-mono);
    white-space: pre-wrap;
    text-align: left;
    max-height: 200px;
    overflow: auto;
  }

  /* ── Shared chrome controls ────────────────────────────────────────── */
  .steppy-chrome {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    background: var(--steppy-chrome-bg);
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 14px;
  }
  .steppy-progress {
    margin-right: auto;
    font-family: var(--steppy-font-mono);
    font-variant-numeric: tabular-nums;
    color: var(--steppy-muted);
  }
  .steppy-btn {
    font: inherit;
    background: var(--steppy-surface);
    border: 1px solid var(--steppy-border);
    border-radius: ${cssValue(BUTTON_LOOK.radius)};
    padding: ${cssValue(BUTTON_LOOK.padding)};
    cursor: pointer;
    color: var(--steppy-fg);
  }
  .steppy-btn:hover:not(:disabled) {
    border-color: var(--steppy-muted);
  }
  .steppy-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .steppy-field {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--steppy-muted);
  }
  .steppy-field select {
    font: inherit;
    background: var(--steppy-surface);
    border: 1px solid var(--steppy-border);
    border-radius: 4px;
    padding: 4px 8px;
    cursor: pointer;
    color: var(--steppy-fg);
  }
  /* Segmented control (pill container; active = dark fill + light text). */
  .steppy-seg {
    display: inline-flex;
    background: var(--steppy-bg);
    border: 1px solid var(--steppy-border);
    border-radius: 999px;
    padding: 2px;
    gap: 2px;
  }
  .steppy-seg-btn {
    font: inherit;
    font-size: 13px;
    border: 0;
    background: transparent;
    color: var(--steppy-muted);
    padding: 3px 12px;
    border-radius: 999px;
    cursor: pointer;
  }
  .steppy-seg-btn[aria-current='true'] {
    background: var(--steppy-fg);
    color: var(--steppy-surface);
  }
  .steppy-seg-btn:focus-visible {
    outline: 2px solid var(--steppy-accent);
    outline-offset: 1px;
  }

  @media (prefers-reduced-motion: reduce) {
    .steppy-cursor,
    .steppy-cursor.pulse,
    .steppy-ripple.fire,
    .steppy-hotspot,
    .steppy-hotspot.miss,
    .steppy-miss-toast {
      animation: none !important;
      transition: none !important;
    }
  }
`;
