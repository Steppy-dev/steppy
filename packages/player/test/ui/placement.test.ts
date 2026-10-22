/**
 * ``ui/placement.ts``: without options it is the guide's historical rule (a
 * random grid against the frozen copy); with options it honours a preferred
 * side, keeps margins, avoids rects and never covers the hotspot when a side
 * fits.
 */

import { describe, expect, it } from 'vitest';

import { inflate, intersect, intersects, placeTooltip, union, type Rect } from '../../src/ui/placement.js';
import { legacyPlaceTooltip } from './legacy-placement.js';

/** Small deterministic PRNG (mulberry32), so a failure reproduces. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function cases(n: number, seed: number) {
  const r = rng(seed);
  const int = (lo: number, hi: number) => Math.floor(lo + r() * (hi - lo + 1));
  return Array.from({ length: n }, () => {
    const stage = { width: int(200, 1600), height: int(200, 1000) };
    const hotspot = { x: int(-20, stage.width), y: int(-20, stage.height), width: int(0, 400), height: int(0, 200) };
    const tooltip = { width: int(40, 420), height: int(20, 260) };
    return { stage, hotspot, tooltip, gap: int(0, 16) };
  });
}

const box = (p: { x: number; y: number }, size: { width: number; height: number }): Rect => ({ ...p, ...size });

describe('placeTooltip without options', () => {
  it('matches the pre-extraction rule on 2,000 random cases', () => {
    for (const c of cases(2000, 1)) {
      expect(placeTooltip(c.hotspot, c.tooltip, c.stage, c.gap)).toStrictEqual(
        legacyPlaceTooltip(c.hotspot, c.tooltip, c.stage, c.gap),
      );
    }
  });

  it('adds no overlaps key', () => {
    expect(placeTooltip({ x: 10, y: 10, width: 10, height: 10 }, { width: 50, height: 20 }, { width: 400, height: 300 })).not.toHaveProperty(
      'overlaps',
    );
  });
});

describe('placeTooltip with options', () => {
  const stage = { width: 1000, height: 800 };
  const tooltip = { width: 200, height: 100 };
  const middle = { x: 450, y: 350, width: 100, height: 40 };

  it('honours the preferred side when it fits', () => {
    for (const side of ['top', 'bottom', 'left', 'right'] as const) {
      expect(placeTooltip(middle, tooltip, stage, 10, { preferred: side, margin: 8 }).side).toBe(side);
    }
  });

  it('flips to the opposite side when the preferred one has no room', () => {
    const nearTop = { x: 450, y: 20, width: 100, height: 40 };
    const p = placeTooltip(nearTop, tooltip, stage, 10, { preferred: 'top', margin: 8 });
    expect(p).toMatchObject({ side: 'bottom', y: 70, overlaps: false });
  });

  it('auto keeps the historical bias: roomiest vertical side first, bottom on ties', () => {
    expect(placeTooltip(middle, tooltip, { width: 1000, height: 740 }, 10, {}).side).toBe('bottom');
    expect(placeTooltip({ ...middle, y: 600 }, tooltip, stage, 10, {}).side).toBe('top');
    const wide = { x: 100, y: 10, width: 300, height: 780 };
    expect(placeTooltip(wide, tooltip, stage, 10, {}).side).toBe('right');
  });

  it('keeps the margin from every stage edge on the cross axis', () => {
    const corner = { x: 0, y: 300, width: 20, height: 20 };
    const p = placeTooltip(corner, tooltip, stage, 10, { preferred: 'bottom', margin: 8 });
    expect(p.x).toBe(8);
  });

  it('never covers the hotspot when any side fits (random grid)', () => {
    for (const c of cases(2000, 2)) {
      const p = placeTooltip(c.hotspot, c.tooltip, c.stage, c.gap, { margin: 8 });
      if (p.overlaps === false) expect(intersects(box(p, c.tooltip), c.hotspot)).toBe(false);
      else expect(p.overlaps).toBe(intersects(box(p, c.tooltip), c.hotspot));
      expect(p.x).toBeGreaterThanOrEqual(Math.min(8, c.stage.width - c.tooltip.width - 8));
    }
  });

  it('moves away from a rect to avoid when another side fits', () => {
    const below = { x: 400, y: 400, width: 200, height: 100 };
    const p = placeTooltip(middle, tooltip, stage, 10, { preferred: 'bottom', margin: 8, avoid: [below] });
    expect(p.side).toBe('top');
    expect(intersects(box(p, tooltip), below)).toBe(false);
  });

  it('still uses a fitting side that hits an avoided rect when nothing else fits', () => {
    const tall = { width: 200, height: 400 };
    const target = { x: 450, y: 10, width: 100, height: 40 };
    const p = placeTooltip(target, tall, { width: 1000, height: 600 }, 10, {
      preferred: 'bottom',
      margin: 8,
      avoid: [{ x: 0, y: 0, width: 1000, height: 600 }],
    });
    expect(p).toMatchObject({ side: 'bottom', overlaps: false });
  });

  it('reports an unavoidable overlap', () => {
    const huge = { width: 900, height: 700 };
    const p = placeTooltip(middle, huge, stage, 10, { margin: 8 });
    expect(p.overlaps).toBe(true);
  });
});

describe('rect helpers', () => {
  const a = { x: 0, y: 0, width: 10, height: 10 };
  it('intersect, union, inflate', () => {
    expect(intersect(a, { x: 5, y: 5, width: 10, height: 10 })).toEqual({ x: 5, y: 5, width: 5, height: 5 });
    expect(intersect(a, { x: 10, y: 0, width: 5, height: 5 })).toBeNull();
    expect(intersects(a, { x: 10, y: 0, width: 5, height: 5 })).toBe(false);
    expect(union(a, { x: 20, y: -5, width: 5, height: 5 })).toEqual({ x: 0, y: -5, width: 25, height: 15 });
    expect(inflate(a, 6)).toEqual({ x: -6, y: -6, width: 22, height: 22 });
  });
});
