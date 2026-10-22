import { describe, expect, it } from 'vitest';
import { isInsideRect, placeTooltip } from '../src/interaction/positioning.js';

describe('placeTooltip', () => {
  const stage = { width: 800, height: 600 };
  const tooltip = { width: 200, height: 80 };

  it('places below when the hotspot is near the top with room below', () => {
    const hotspot = { x: 100, y: 30, width: 50, height: 20 };
    const p = placeTooltip(hotspot, tooltip, stage);
    expect(p.side).toBe('bottom');
    expect(p.y).toBeGreaterThan(hotspot.y + hotspot.height);
  });

  it('places above when there is no room below', () => {
    const hotspot = { x: 100, y: 500, width: 50, height: 80 };
    const p = placeTooltip(hotspot, tooltip, stage);
    expect(p.side).toBe('top');
    expect(p.y).toBeLessThan(hotspot.y);
  });

  it('falls back to right when above/below are tight', () => {
    const hotspot = { x: 50, y: 250, width: 50, height: 100 };
    const tinyVertical = { width: 30, height: 600 };
    const p = placeTooltip(hotspot, tinyVertical, stage);
    expect(p.side === 'right' || p.side === 'left').toBe(true);
  });

  it('clamps the tooltip inside the stage', () => {
    // Hotspot near the right edge — tooltip wants to go right but should clamp.
    const hotspot = { x: 750, y: 100, width: 30, height: 20 };
    const p = placeTooltip(hotspot, tooltip, stage);
    expect(p.x).toBeGreaterThanOrEqual(0);
    expect(p.x + tooltip.width).toBeLessThanOrEqual(stage.width);
    expect(p.y).toBeGreaterThanOrEqual(0);
    expect(p.y + tooltip.height).toBeLessThanOrEqual(stage.height);
  });
});

describe('isInsideRect', () => {
  const rect = { x: 10, y: 10, width: 100, height: 50 };

  it('returns true for points inside', () => {
    expect(isInsideRect(rect, { x: 50, y: 25 })).toBe(true);
  });

  it('returns true on the borders (inclusive)', () => {
    expect(isInsideRect(rect, { x: 10, y: 10 })).toBe(true);
    expect(isInsideRect(rect, { x: 110, y: 60 })).toBe(true);
  });

  it('returns false for points outside', () => {
    expect(isInsideRect(rect, { x: 9, y: 25 })).toBe(false);
    expect(isInsideRect(rect, { x: 111, y: 25 })).toBe(false);
    expect(isInsideRect(rect, { x: 50, y: 9 })).toBe(false);
    expect(isInsideRect(rect, { x: 50, y: 61 })).toBe(false);
  });
});
