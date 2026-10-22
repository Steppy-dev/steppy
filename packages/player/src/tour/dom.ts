/**
 * Everything the overlay asks the browser about geometry and timing, behind
 * one interface. ``defaultDom`` is the real thing; tests swap
 * ``SteppyTourHost.dom`` for a stub with settable rects and a manual
 * scheduler (happy-dom has no layout, and its rAF escapes fake timers).
 */

import { union, type Rect } from '../ui/placement.js';

export interface Scheduler {
  now(): number;
  raf(cb: () => void): number;
  cancelRaf(id: number): void;
  setTimeout(cb: () => void, ms: number): number;
  clearTimeout(id: number): void;
  setInterval(cb: () => void, ms: number): number;
  clearInterval(id: number): void;
}

export interface Viewport {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface DomAdapter {
  scheduler: Scheduler;
  /** The element's effective rect (see ``effectiveRect``), or null when it has none. */
  rect(el: Element): Rect | null;
  isVisible(el: Element): boolean;
  /** The visual viewport, in layout-viewport coordinates. */
  viewport(): Viewport;
  matchMedia(query: string): boolean;
  reducedMotion(): boolean;
  scrollIntoView(el: Element, instant: boolean): void;
  supportsPopover(): boolean;
}

const FOCUSABLE = 'button,input,select,textarea,a[href],[role=button],[role=combobox],[tabindex]:not([tabindex="-1"])';

function ownRect(el: Element): Rect | null {
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 ? { x: r.left, y: r.top, width: r.width, height: r.height } : null;
}

/**
 * Playwright's notion of a visible box, extended for wrapper hosts: an element
 * with ``display: contents`` measures as the union of its children, and an
 * empty 0x0 host (a custom element wrapping a native control) as its first
 * visible focusable descendant.
 */
export function effectiveRect(el: Element, depth = 0): Rect | null {
  const style = getComputedStyle(el);
  if (style.visibility === 'hidden') return null;
  if (style.display === 'contents' && depth < 3) {
    let acc: Rect | null = null;
    for (const child of el.children) {
      const r = effectiveRect(child, depth + 1);
      if (r) acc = acc ? union(acc, r) : r;
    }
    return acc;
  }
  const own = ownRect(el);
  if (own || depth >= 3) return own;
  for (const inner of el.querySelectorAll(FOCUSABLE)) {
    const r = ownRect(inner);
    if (r && getComputedStyle(inner).visibility !== 'hidden') return r;
  }
  return null;
}

export const defaultScheduler: Scheduler = {
  now: () => performance.now(),
  raf: (cb) => requestAnimationFrame(cb),
  cancelRaf: (id) => cancelAnimationFrame(id),
  setTimeout: (cb, ms) => window.setTimeout(cb, ms),
  clearTimeout: (id) => window.clearTimeout(id),
  setInterval: (cb, ms) => window.setInterval(cb, ms),
  clearInterval: (id) => window.clearInterval(id),
};

export const defaultDom: DomAdapter = {
  scheduler: defaultScheduler,
  rect: (el) => effectiveRect(el),
  isVisible: (el) => effectiveRect(el) !== null,
  viewport: () => {
    const vv = window.visualViewport;
    return vv
      ? { x: vv.offsetLeft, y: vv.offsetTop, width: vv.width, height: vv.height }
      : { x: 0, y: 0, width: window.innerWidth, height: window.innerHeight };
  },
  matchMedia: (q) => {
    try {
      return window.matchMedia(q).matches;
    } catch {
      return false;
    }
  },
  reducedMotion: () => defaultDom.matchMedia('(prefers-reduced-motion: reduce)'),
  scrollIntoView: (el, instant) =>
    el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: instant ? 'instant' : 'smooth' } as ScrollIntoViewOptions),
  supportsPopover: () => typeof HTMLElement.prototype.showPopover === 'function',
};
