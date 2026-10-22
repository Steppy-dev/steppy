/**
 * Pure positioning helpers — hotspot rect derivation (and, re-exported from
 * ``ui/placement.ts``, tooltip placement).
 * No DOM dependencies in the math; one of the functions reads
 * ``getBoundingClientRect`` from a passed element, but the placement math is
 * separate and unit-testable.
 */
import type { Bbox, DisplayRect, Viewport } from '../cursor/types.js';
import { getDisplayScale, scaleRect } from '../cursor/animator.js';

// Tooltip placement is shared with the in-app tour overlay, which must not
// pull the cursor animator in; it lives in ui/placement.ts.
export { placeTooltip, type TooltipPlacement, type TooltipSide } from '../ui/placement.js';

/**
 * Project a capture-space bbox into the displayed rect over an ``img``.
 * Returns ``null`` when the image hasn't laid out or the viewport is unknown.
 */
export function computeHotspotRect(
  img: HTMLElement,
  viewport: Viewport | null | undefined,
  bbox: Bbox | null | undefined,
): DisplayRect | null {
  if (!bbox) return null;
  const scale = getDisplayScale(img, viewport);
  if (!scale) return null;
  return scaleRect(bbox, scale);
}

/**
 * Returns true when ``(x, y)`` (display-space) falls inside the hotspot rect.
 * Used to classify clicks as ``correct`` or ``miss``.
 */
export function isInsideRect(
  rect: DisplayRect,
  point: { x: number; y: number },
): boolean {
  return (
    point.x >= rect.x &&
    point.x <= rect.x + rect.width &&
    point.y >= rect.y &&
    point.y <= rect.y + rect.height
  );
}
