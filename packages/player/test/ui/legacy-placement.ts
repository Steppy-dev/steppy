/**
 * Frozen copy of ``placeTooltip`` from ``src/interaction/positioning.ts`` as it
 * was before it moved to ``src/ui/placement.ts``. Test-only oracle: called
 * without options, the shared ``placeTooltip`` must return exactly this.
 */

import type { DisplayRect } from '../../src/cursor/types.js';

export type LegacyTooltipSide = 'top' | 'bottom' | 'left' | 'right';

export function legacyPlaceTooltip(
  hotspot: DisplayRect,
  tooltip: { width: number; height: number },
  stage: { width: number; height: number },
  gap = 8,
): { side: LegacyTooltipSide; x: number; y: number } {
  const above = hotspot.y;
  const below = stage.height - (hotspot.y + hotspot.height);
  const left = hotspot.x;
  const right = stage.width - (hotspot.x + hotspot.width);

  // Prefer vertical placement (above or below). Only fall back to horizontal
  // when neither vertical side has room for the tooltip's height plus gap.
  const verticalNeeded = tooltip.height + gap;
  const horizontalNeeded = tooltip.width + gap;
  const bestVertical: { side: LegacyTooltipSide; room: number } =
    below >= above
      ? { side: 'bottom', room: below }
      : { side: 'top', room: above };
  let pick: LegacyTooltipSide;
  if (bestVertical.room >= verticalNeeded) {
    pick = bestVertical.side;
  } else {
    const bestHorizontal: { side: LegacyTooltipSide; room: number } =
      right >= left
        ? { side: 'right', room: right }
        : { side: 'left', room: left };
    pick = bestHorizontal.room >= horizontalNeeded ? bestHorizontal.side : bestVertical.side;
  }

  const cx = hotspot.x + hotspot.width / 2;
  const cy = hotspot.y + hotspot.height / 2;

  let x = 0;
  let y = 0;
  if (pick === 'bottom') {
    x = clamp(cx - tooltip.width / 2, 0, stage.width - tooltip.width);
    y = Math.min(hotspot.y + hotspot.height + gap, stage.height - tooltip.height);
  } else if (pick === 'top') {
    x = clamp(cx - tooltip.width / 2, 0, stage.width - tooltip.width);
    y = Math.max(0, hotspot.y - tooltip.height - gap);
  } else if (pick === 'right') {
    x = Math.min(hotspot.x + hotspot.width + gap, stage.width - tooltip.width);
    y = clamp(cy - tooltip.height / 2, 0, stage.height - tooltip.height);
  } else {
    x = Math.max(0, hotspot.x - tooltip.width - gap);
    y = clamp(cy - tooltip.height / 2, 0, stage.height - tooltip.height);
  }
  return { side: pick, x, y };
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}
