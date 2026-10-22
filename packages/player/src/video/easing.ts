/**
 * CSS timing-function equivalents, for the video renderer.
 *
 * The live player animates the cursor pulse and the click ripple with CSS
 * ``@keyframes`` (``base-styles.ts``), which are driven by the wall clock. The
 * video renderer steps frame by frame instead, so it recomputes those styles
 * itself — and to look identical it has to apply the *same* easing CSS would.
 *
 * ``.steppy-cursor.pulse`` uses ``ease-in-out`` and ``.steppy-ripple.fire`` uses
 * ``ease-out``; both are cubic Béziers, not the smoothstep in ``cursor/animator.ts``
 * (which eases cursor *travel* and is reused verbatim from there — do not
 * confuse the two).
 *
 * Pure and DOM-free so it unit-tests without a browser.
 */

const NEWTON_ITERATIONS = 6;
const SUBDIVISION_EPSILON = 1e-7;

function bezierAt(t: number, a1: number, a2: number): number {
  // Coefficients of the cubic with P0 = 0 and P3 = 1.
  const c = 3 * a1;
  const b = 3 * (a2 - a1) - c;
  const a = 1 - c - b;
  return ((a * t + b) * t + c) * t;
}

function bezierSlopeAt(t: number, a1: number, a2: number): number {
  const c = 3 * a1;
  const b = 3 * (a2 - a1) - c;
  const a = 1 - c - b;
  return (3 * a * t + 2 * b) * t + c;
}

/**
 * Build a CSS ``cubic-bezier(x1, y1, x2, y2)`` timing function.
 *
 * Solves x(t) = progress by Newton-Raphson, falling back to bisection when the
 * curve is locally flat (zero slope), then evaluates y at that t — the same
 * approach browsers use.
 */
export function cubicBezier(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
): (progress: number) => number {
  // A linear curve needs no solving, and skipping it avoids the flat-slope path.
  if (x1 === y1 && x2 === y2) return (p) => clamp01(p);

  return (progress: number): number => {
    const p = clamp01(progress);
    if (p === 0 || p === 1) return p;

    let t = p;
    for (let i = 0; i < NEWTON_ITERATIONS; i++) {
      const slope = bezierSlopeAt(t, x1, x2);
      if (slope === 0) break;
      t -= (bezierAt(t, x1, x2) - p) / slope;
    }

    // Newton can wander outside [0,1] on flat curves; bisect back into range.
    if (!(t >= 0 && t <= 1)) {
      let lo = 0;
      let hi = 1;
      t = p;
      while (hi - lo > SUBDIVISION_EPSILON) {
        if (bezierAt(t, x1, x2) < p) lo = t;
        else hi = t;
        t = (lo + hi) / 2;
      }
    }

    return bezierAt(t, y1, y2);
  };
}

export function clamp01(value: number): number {
  if (!(value > 0)) return 0; // also catches NaN
  return value < 1 ? value : 1;
}

/** CSS ``ease-in-out`` — the cursor pulse keyframes. */
export const easeInOutCss = cubicBezier(0.42, 0, 0.58, 1);

/** CSS ``ease-out`` — the click ripple keyframes. */
export const easeOutCss = cubicBezier(0, 0, 0.58, 1);

/** Linear interpolation, clamped at both ends. */
export function lerp(from: number, to: number, progress: number): number {
  return from + (to - from) * clamp01(progress);
}
