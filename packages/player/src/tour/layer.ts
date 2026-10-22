/**
 * Stacking. Where the popover API exists the layer is a ``popover="manual"``
 * element in the browser's top layer — above any z-index the app uses — and
 * every step re-raises it (hide, then show) so it stays above an app dialog
 * opened after it. Elsewhere the layer falls back to ``z-index: var(--_pw-z)``.
 *
 * Known limit: a native ``<dialog>`` opened with ``showModal()`` makes
 * everything outside it inert, the layer included; re-raising does not lift
 * that. Steps advanced by the app's own events still work, the overlay's
 * buttons do not until the dialog closes.
 */

import type { DomAdapter } from './dom.js';

function deepActive(): Element | null {
  let el = document.activeElement;
  while (el?.shadowRoot?.activeElement) el = el.shadowRoot.activeElement;
  return el;
}

export function isLayerOpen(el: HTMLElement): boolean {
  try {
    return el.matches(':popover-open');
  } catch {
    return false;
  }
}

export function showLayer(el: HTMLElement, dom: DomAdapter): void {
  if (!dom.supportsPopover() || isLayerOpen(el)) return;
  try {
    el.showPopover();
  } catch {
    // Not connected yet or already shown elsewhere: the z-index fallback holds.
  }
}

/** Move the layer to the top of the top layer, keeping the app's focus where it was. */
export function raiseLayer(el: HTMLElement, dom: DomAdapter): void {
  if (!dom.supportsPopover()) return;
  const active = deepActive();
  try {
    if (isLayerOpen(el)) el.hidePopover();
    el.showPopover();
  } catch {
    return;
  }
  if (active instanceof HTMLElement && active.isConnected && deepActive() !== active) active.focus({ preventScroll: true });
}
