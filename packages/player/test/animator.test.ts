/**
 * Unit tests for the cursor animator's pure-math helpers and the
 * ``CursorAnimator.normalizeTargets`` static. DOM-driven tests live in the
 * component test file.
 */
import { describe, expect, it } from 'vitest';
import {
  CursorAnimator,
  bezierControlPoint,
  easeInOut,
  getDisplayScale,
  rectCenter,
  scaleBbox,
  scaleRect,
  travelDuration,
} from '../src/cursor/animator.js';
import { DEFAULT_TIMINGS } from '../src/cursor/types.js';

describe('easeInOut', () => {
  it('is 0 at t=0 and 1 at t=1', () => {
    expect(easeInOut(0)).toBe(0);
    expect(easeInOut(1)).toBe(1);
  });
  it('is symmetric (smoothstep)', () => {
    // smoothstep(0.5) = 0.5 exactly
    expect(easeInOut(0.5)).toBeCloseTo(0.5, 10);
    // s(t) + s(1-t) = 1
    for (const t of [0.1, 0.2, 0.3, 0.4, 0.7, 0.9]) {
      expect(easeInOut(t) + easeInOut(1 - t)).toBeCloseTo(1, 10);
    }
  });
  it('is monotonically non-decreasing', () => {
    let prev = -Infinity;
    for (let i = 0; i <= 100; i++) {
      const v = easeInOut(i / 100);
      expect(v).toBeGreaterThanOrEqual(prev);
      prev = v;
    }
  });
});

describe('getDisplayScale + scaleRect + rectCenter', () => {
  // happy-dom stubs getBoundingClientRect to return zero rects unless we fake
  // it. We construct a stand-in HTMLElement with a mock rect.
  const makeImg = (w: number, h: number): HTMLElement => {
    const el = document.createElement('img');
    el.getBoundingClientRect = () =>
      ({ x: 0, y: 0, top: 0, left: 0, right: w, bottom: h, width: w, height: h, toJSON() {} } as DOMRect);
    return el;
  };

  it('returns null when viewport is missing or zero', () => {
    const img = makeImg(800, 600);
    expect(getDisplayScale(img, null)).toBeNull();
    expect(getDisplayScale(img, { width: 0, height: 600 })).toBeNull();
    expect(getDisplayScale(img, { width: 800, height: 0 })).toBeNull();
  });

  it('returns null when the image has no laid-out size', () => {
    const img = makeImg(0, 0);
    expect(getDisplayScale(img, { width: 1440, height: 900 })).toBeNull();
  });

  it('computes a fractional scale when image is smaller than capture', () => {
    const img = makeImg(720, 450); // 50% of 1440x900
    const scale = getDisplayScale(img, { width: 1440, height: 900 });
    expect(scale).not.toBeNull();
    expect(scale!.sx).toBeCloseTo(0.5, 10);
    expect(scale!.sy).toBeCloseTo(0.5, 10);
  });

  it('scaleRect projects bbox into display space', () => {
    const r = scaleRect(
      { x: 100, y: 200, width: 50, height: 30 },
      { sx: 0.5, sy: 0.25 },
    );
    expect(r).toEqual({ x: 50, y: 50, width: 25, height: 7.5 });
  });

  it('rectCenter returns the geometric center', () => {
    expect(rectCenter({ x: 10, y: 20, width: 30, height: 40 })).toEqual({
      x: 25,
      y: 40,
    });
  });

  it('scaleBbox composes getDisplayScale + scaleRect + rectCenter', () => {
    const img = makeImg(720, 450); // half size
    const result = scaleBbox(
      { x: 200, y: 100, width: 80, height: 40 },
      img,
      { width: 1440, height: 900 },
    );
    // Capture-space center is (240, 120); halved → (120, 60)
    expect(result).not.toBeNull();
    expect(result!.x).toBeCloseTo(120, 10);
    expect(result!.y).toBeCloseTo(60, 10);
  });

  it('scaleBbox returns null when bbox is null', () => {
    const img = makeImg(720, 450);
    expect(scaleBbox(null, img, { width: 1440, height: 900 })).toBeNull();
    expect(scaleBbox(undefined, img, { width: 1440, height: 900 })).toBeNull();
  });
});

describe('travelDuration', () => {
  it('floors at 150ms for tiny distances', () => {
    expect(travelDuration({ x: 0, y: 0 }, { x: 0, y: 0 }, 1.5)).toBe(150);
    expect(travelDuration({ x: 10, y: 10 }, { x: 10, y: 10 }, 1.5)).toBe(150);
  });

  it('scales distance ÷ speed for longer travels', () => {
    // 1500px / 1.5 px/ms = 1000ms
    expect(travelDuration({ x: 0, y: 0 }, { x: 1500, y: 0 }, 1.5)).toBe(1000);
    // 300px / 3 px/ms = 100ms → floored to 150
    expect(travelDuration({ x: 0, y: 0 }, { x: 300, y: 0 }, 3)).toBe(150);
    // 750px / 1.5 = 500ms
    expect(travelDuration({ x: 0, y: 0 }, { x: 750, y: 0 }, 1.5)).toBe(500);
  });

  it('uses default speed fallback when speed is 0', () => {
    // speed=0 → divides by 1.5 → 600 / 1.5 = 400
    expect(travelDuration({ x: 0, y: 0 }, { x: 600, y: 0 }, 0)).toBe(400);
  });
});

describe('bezierControlPoint', () => {
  it('returns the from-point when from === to (zero-length)', () => {
    expect(bezierControlPoint({ x: 10, y: 20 }, { x: 10, y: 20 })).toEqual({
      x: 10,
      y: 20,
    });
  });

  it('produces an offset perpendicular to the from→to vector', () => {
    const from = { x: 0, y: 0 };
    const to = { x: 100, y: 0 };
    const cp = bezierControlPoint(from, to);
    // Midpoint is (50, 0). Perpendicular offset is 15% of 100 = 15, in +y dir.
    expect(cp.x).toBeCloseTo(50, 10);
    expect(cp.y).toBeCloseTo(15, 10);
  });
});

describe('CursorAnimator.normalizeTargets', () => {
  const bbox = (n: number) => ({ x: n, y: n, width: 10, height: 10 });

  it('returns [] for null cursor', () => {
    expect(CursorAnimator.normalizeTargets(null)).toEqual([]);
  });

  it('returns one click target', () => {
    const out = CursorAnimator.normalizeTargets({
      kind: 'click',
      target: { selector: '#a', bbox: bbox(1) },
    });
    expect(out).toHaveLength(1);
    expect(out[0]!.kind).toBe('click');
    expect(out[0]!.bbox).toEqual(bbox(1));
  });

  it('returns one type target with value', () => {
    const out = CursorAnimator.normalizeTargets({
      kind: 'type',
      target: { bbox: bbox(2) },
      value: 'notebook',
    });
    expect(out).toHaveLength(1);
    expect(out[0]!.kind).toBe('type');
    expect(out[0]!.value).toBe('notebook');
  });

  it('expands type-multi into N type targets', () => {
    const out = CursorAnimator.normalizeTargets({
      kind: 'type-multi',
      targets: [
        { selector: '#first', bbox: bbox(1), value: 'A' },
        { selector: '#last', bbox: bbox(2), value: 'B' },
      ],
    });
    expect(out).toHaveLength(2);
    expect(out.every((t) => t.kind === 'type')).toBe(true);
    expect(out[0]!.value).toBe('A');
    expect(out[1]!.selector).toBe('#last');
  });

  it('emits a hover kind for hover behavior', () => {
    const out = CursorAnimator.normalizeTargets({
      kind: 'hover',
      target: { bbox: bbox(0) },
    });
    expect(out).toHaveLength(1);
    expect(out[0]!.kind).toBe('hover');
  });
});

describe('CursorAnimator defaults', () => {
  it('exposes DEFAULT_TIMINGS that match the historical stepbystep values', () => {
    expect(DEFAULT_TIMINGS).toEqual({
      pre_action_ms: 200,
      action_ms: 300,
      post_action_ms: 150,
      travel_speed_px_per_ms: 1.5,
    });
  });
});
