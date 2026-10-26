/**
 * Keeps the spotlight on the target and the bubble next to it while a step is
 * shown. Geometry is written through CSSOM custom properties only (no
 * ``style=`` attributes, which a strict CSP blocks).
 *
 * Driven by capture-phase scroll (which catches scrolling ancestors), resize,
 * the visual viewport, ResizeObserver and a slow poll; ``notify`` coalesces
 * them into one placement per frame. A target that got detached or hidden is
 * re-found by id first; only when that fails is the step reported lost.
 *
 * Invariant: the bubble never covers the spotlight when a side has room for
 * it; otherwise it becomes a sheet at the top or bottom of the viewport, away
 * from the target's centre (where a click lands). A step's hold zone is
 * stricter: the bubble never covers it, even to sit beside the target, because
 * the bubble is the overlay's one element that takes the pointer, and a hover
 * menu the pointer leaves closes. A step with nothing to point
 * at is centred, unless that would cover an open app dialog: such a step is
 * usually about the dialog (a confirmation), so it becomes a sheet instead.
 */

import { inflate, intersect, intersects, placeTooltip, type Rect, type TooltipSide } from '../ui/placement.js';
import type { DomAdapter, Viewport } from './dom.js';
import { isExpanded } from './signature.js';

export const SPOT_PAD = 6;
export const GAP = 10;
export const MARGIN = 8;
export const SHEET_BELOW = 640;
const POLL_MS = 250;

export interface TrackerOptions {
  dom: DomAdapter;
  layer: () => HTMLElement | null;
  bubble: () => HTMLElement | null;
  /** The current target, or null for a centred (targetless) step. */
  target: () => Element | null;
  /** Re-find the target by id after it was detached or hidden. */
  refind: () => Element | null;
  preferred: () => TooltipSide | 'auto';
  /** A rect the bubble should not cover when it has a choice (the next step's target). */
  avoid: () => Rect | null;
  /** The step's hold zone, which the bubble must never cover (``hold`` in the tour file). */
  hold?: () => Element | null;
  /** The app's open dialogs, which a centred bubble moves off. */
  dialogs?: () => Rect[];
  onLost: () => void;
  /** The bubble was placed (it is visible from now on, so it can take focus). */
  onPlaced?: () => void;
}

export interface Tracker {
  start(): void;
  stop(): void;
  notify(): void;
}

const CLIPS = /^(?:hidden|auto|scroll|clip)$/;

/** Ancestors that clip the target. ``body`` and ``html`` never do: their overflow belongs to the viewport. */
function clippingAncestors(el: Element): Element[] {
  const out: Element[] = [];
  for (let p = el.parentElement; p && p !== document.body && p !== document.documentElement; p = p.parentElement) {
    const cs = getComputedStyle(p);
    if (CLIPS.test(cs.overflowX) || CLIPS.test(cs.overflowY)) out.push(p);
  }
  return out;
}

export function createTracker(o: TrackerOptions): Tracker {
  const s = o.dom.scheduler;
  let running = false;
  let frame = 0;
  let poll = 0;
  let clips: Element[] = [];
  let clipsFor: Element | null = null;
  let holdFor: Element | null = null;
  let observer: ResizeObserver | null = null;

  const px = (n: number) => `${Math.round(n * 100) / 100}px`;

  function tick(): void {
    frame = 0;
    if (!running) return;
    const layer = o.layer();
    const bubble = o.bubble();
    if (!layer || !bubble) return;
    const vp = o.dom.viewport();
    let target = o.target();
    const anchoredStep = target !== null;
    if (target && (!target.isConnected || !o.dom.isVisible(target))) target = o.refind();
    if (anchoredStep && !target) {
      running = false;
      o.onLost();
      return;
    }

    bubble.removeAttribute('data-offscreen');
    layer.removeAttribute('data-offscreen');
    layer.style.setProperty('--_pw-kb', px(Math.max(0, window.innerHeight - (vp.y + vp.height))));
    if (!target) return centre(bubble, vp);
    if (clipsFor !== target) {
      clips = clippingAncestors(target);
      clipsFor = target;
      observer?.disconnect();
      observer?.observe(target);
      observer?.observe(bubble);
      holdFor = null;
    }
    const zone = o.hold?.() ?? null;
    if (zone !== holdFor) {
      if (holdFor) observer?.unobserve(holdFor);
      // A hover menu grows when the pointer reaches it: placed beside the collapsed rail, the bubble would end up inside.
      if (zone) observer?.observe(zone);
      holdFor = zone;
    }
    const held = zone ? o.dom.rect(zone) : null;
    let spot: Rect | null = inflate(o.dom.rect(target) ?? { x: 0, y: 0, width: 0, height: 0 }, SPOT_PAD);
    for (const clip of clips) {
      const r = o.dom.rect(clip);
      if (r && spot) spot = intersect(spot, inflate(r, SPOT_PAD));
    }
    spot = spot && intersect(spot, { x: vp.x, y: vp.y, width: vp.width, height: vp.height });
    if (!spot) {
      bubble.setAttribute('data-offscreen', '');
      layer.setAttribute('data-offscreen', '');
      return centre(bubble, vp);
    }
    layer.style.setProperty('--_pw-sx', px(spot.x));
    layer.style.setProperty('--_pw-sy', px(spot.y));
    layer.style.setProperty('--_pw-sw', px(spot.width));
    layer.style.setProperty('--_pw-sh', px(spot.height));
    bubble.toggleAttribute('data-yield', isExpanded(target));

    const away = held ?? spot;
    const centreY = away.y + away.height / 2;
    const sheet = (): void => setMode(bubble, centreY > vp.y + vp.height / 2 ? 'sheet-top' : 'sheet-bottom', null);
    if (vp.width <= SHEET_BELOW) return sheet();
    const size = o.dom.rect(bubble) ?? { x: 0, y: 0, width: 320, height: 120 };
    const avoid = o.avoid();
    const rel = (r: Rect): Rect => ({ x: r.x - vp.x, y: r.y - vp.y, width: r.width, height: r.height });
    const place = (rects: Rect[]) =>
      placeTooltip(rel(spot), size, vp, GAP, { preferred: o.preferred(), margin: MARGIN, avoid: rects.map(rel) });
    const covers = (q: { x: number; y: number }) =>
      held !== null && intersects({ x: q.x, y: q.y, width: size.width, height: size.height }, rel(held));
    let p = place([held, avoid].filter((r): r is Rect => r !== null));
    // No side clear of both: the hold zone wins over the next target, and a sheet over both.
    if (!p.overlaps && covers(p) && avoid) p = place([held!]);
    if (p.overlaps || covers(p)) return sheet();
    layer.style.setProperty('--_pw-x', px(p.x + vp.x));
    layer.style.setProperty('--_pw-y', px(p.y + vp.y));
    setMode(bubble, 'anchored', p.side);
  }

  /** Centred, or a sheet on the roomier side of the open app dialogs a centred bubble would cover. */
  function centre(bubble: HTMLElement, vp: Viewport): void {
    const size = o.dom.rect(bubble) ?? { x: 0, y: 0, width: 320, height: 120 };
    const centred = {
      x: vp.x + (vp.width - size.width) / 2,
      y: vp.y + (vp.height - size.height) / 2,
      width: size.width,
      height: size.height,
    };
    const covered = (o.dialogs?.() ?? []).filter((r) => intersects(r, centred));
    if (!covered.length) return setMode(bubble, 'centered', null);
    const above = Math.min(...covered.map((r) => r.y)) - vp.y;
    const below = vp.y + vp.height - Math.max(...covered.map((r) => r.y + r.height));
    setMode(bubble, above >= below ? 'sheet-top' : 'sheet-bottom', null);
  }

  function setMode(bubble: HTMLElement, mode: string, side: TooltipSide | null): void {
    bubble.setAttribute('data-mode', mode);
    if (side) bubble.setAttribute('data-side', side);
    else bubble.removeAttribute('data-side');
    bubble.setAttribute('data-placed', '');
    o.onPlaced?.();
  }

  function notify(): void {
    if (running && !frame) frame = s.raf(tick);
  }

  const onScroll = () => notify();
  return {
    start() {
      if (running) return notify();
      running = true;
      clipsFor = null;
      document.addEventListener('scroll', onScroll, { capture: true, passive: true });
      window.addEventListener('resize', onScroll, { passive: true });
      window.visualViewport?.addEventListener('resize', onScroll, { passive: true });
      window.visualViewport?.addEventListener('scroll', onScroll, { passive: true });
      observer = typeof ResizeObserver === 'function' ? new ResizeObserver(onScroll) : null;
      poll = s.setInterval(notify, POLL_MS);
      notify();
    },
    stop() {
      running = false;
      if (frame) s.cancelRaf(frame);
      frame = 0;
      s.clearInterval(poll);
      observer?.disconnect();
      observer = null;
      clipsFor = null;
      holdFor = null;
      document.removeEventListener('scroll', onScroll, { capture: true });
      window.removeEventListener('resize', onScroll);
      window.visualViewport?.removeEventListener('resize', onScroll);
      window.visualViewport?.removeEventListener('scroll', onScroll);
    },
    notify,
  };
}
