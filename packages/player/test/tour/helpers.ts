/**
 * Test seams for the tour overlay. happy-dom has no layout, no popover API and
 * a rAF outside fake-timer control, so tests run the overlay on:
 *
 * - ``ManualScheduler`` — frames run on ``flushFrames()``, timers on
 *   ``advance(ms)``; nothing happens on its own.
 * - ``stubDom`` — an element's rect comes from ``data-rect="x,y,w,h"`` (no
 *   attribute: not visible), the viewport and media queries are settable.
 */

import type { DomAdapter, Scheduler, Viewport } from '../../src/tour/dom.js';
import type { Rect } from '../../src/ui/placement.js';

interface Timer {
  at: number;
  cb: () => void;
  every?: number;
}

export class ManualScheduler implements Scheduler {
  t = 0;
  private seq = 0;
  private timers = new Map<number, Timer>();
  private frames = new Map<number, () => void>();

  now = () => this.t;
  raf = (cb: () => void) => {
    const id = ++this.seq;
    this.frames.set(id, cb);
    return id;
  };
  cancelRaf = (id: number) => {
    this.frames.delete(id);
  };
  setTimeout = (cb: () => void, ms: number) => {
    const id = ++this.seq;
    this.timers.set(id, { at: this.t + Math.max(0, ms), cb });
    return id;
  };
  clearTimeout = (id: number) => {
    this.timers.delete(id);
  };
  setInterval = (cb: () => void, ms: number) => {
    const id = ++this.seq;
    this.timers.set(id, { at: this.t + ms, cb, every: ms });
    return id;
  };
  clearInterval = (id: number) => {
    this.timers.delete(id);
  };

  flushFrames(): void {
    for (let round = 0; round < 20 && this.frames.size; round++) {
      const due = [...this.frames.values()];
      this.frames.clear();
      due.forEach((cb) => cb());
    }
  }

  advance(ms: number): void {
    const end = this.t + ms;
    for (;;) {
      let nextId = -1;
      let next: Timer | null = null;
      for (const [id, timer] of this.timers) {
        if (timer.at <= end && (!next || timer.at < next.at)) {
          next = timer;
          nextId = id;
        }
      }
      if (!next) break;
      this.t = next.at;
      if (next.every) next.at += next.every;
      else this.timers.delete(nextId);
      next.cb();
      this.flushFrames();
    }
    this.t = end;
    this.flushFrames();
  }
}

export interface StubDom extends DomAdapter {
  scheduler: ManualScheduler;
  vp: Viewport;
  media: Record<string, boolean>;
  scrolled: Element[];
  popover: boolean;
}

export function rectOf(el: Element): Rect | null {
  const raw = el.getAttribute('data-rect');
  if (!raw || el.hasAttribute('hidden') || !el.isConnected) return null;
  const [x, y, width, height] = raw.split(',').map(Number) as [number, number, number, number];
  return width > 0 && height > 0 ? { x, y, width, height } : null;
}

export function stubDom(): StubDom {
  const dom: StubDom = {
    scheduler: new ManualScheduler(),
    vp: { x: 0, y: 0, width: 1280, height: 800 },
    media: {},
    scrolled: [],
    popover: false,
    rect: (el) => rectOf(el) ?? (el.getAttribute('part')?.includes('bubble') ? { x: 0, y: 0, width: 300, height: 120 } : null),
    isVisible: (el) => rectOf(el) !== null,
    viewport: () => dom.vp,
    matchMedia: (q) => dom.media[q] ?? false,
    reducedMotion: () => false,
    scrollIntoView: (el) => {
      dom.scrolled.push(el);
    },
    supportsPopover: () => dom.popover,
  };
  return dom;
}

/** Let MutationObserver callbacks and promise chains run. */
export async function settle(times = 3): Promise<void> {
  for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0));
}
