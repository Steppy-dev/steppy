/**
 * Cursor animation — canonical TS port of the inline JS that previously lived
 * in ``templates/static/cursor.js.j2`` and ``templates/guide.html.j2``. Both
 * the new ``<steppy-guide>`` Lit element and the legacy stepbystep templates
 * consume this module (the latter via ``legacy-iife.ts``).
 *
 * Two animation modes are supported off the same primitives:
 *
 * - **Full frame** (stepbystep): ``animateFrame(frame)`` walks every target in
 *   the frame and runs travel → pre-action → action → post-action without
 *   pause. Matches the historical behavior of the inline JS.
 * - **Interactive** (the new player): ``travelToTarget`` /
 *   ``playTargetAction`` split the sequence so the player can wait for the
 *   user to click a hotspot between phases.
 *
 * The animator owns the cursor / ripple / kbd DOM elements (it does not
 * create them — the host provides them via ``opts.stage`` and the elements
 * inside that stage). Coordinates are scaled at animation time from the
 * frame's capture-space viewport to the displayed image size; this is the
 * single point where capture-space → display-space conversion happens.
 */

import {
  AnimatorFrame,
  Bbox,
  CursorBehavior,
  CursorTimings,
  DEFAULT_TIMINGS,
  DisplayRect,
  DisplayScale,
  MultiTarget,
  SingleTarget,
  Viewport,
} from './types.js';

export type TargetKind = 'click' | 'hover' | 'type';

export interface NormalizedTarget {
  bbox: Bbox;
  value?: string;
  selector?: string;
  kind: TargetKind;
}

const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, Math.max(0, ms)));

export function easeInOut(t: number): number {
  // Smoothstep — symmetric S-curve, matches the legacy inline JS.
  return t * t * (3 - 2 * t);
}

export function getDisplayScale(
  img: HTMLElement,
  viewport: Viewport | null | undefined,
): DisplayScale | null {
  if (!viewport || !viewport.width || !viewport.height) return null;
  const rect = img.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  return { sx: rect.width / viewport.width, sy: rect.height / viewport.height };
}

export function scaleRect(bbox: Bbox, scale: DisplayScale): DisplayRect {
  return {
    x: bbox.x * scale.sx,
    y: bbox.y * scale.sy,
    width: bbox.width * scale.sx,
    height: bbox.height * scale.sy,
  };
}

export function rectCenter(rect: DisplayRect): { x: number; y: number } {
  return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
}

/**
 * Legacy entry point preserved verbatim for the IIFE bundle: capture-space
 * bbox + image + viewport → display-space center point.
 */
export function scaleBbox(
  bbox: Bbox | null | undefined,
  img: HTMLElement,
  viewport: Viewport | null | undefined,
): { x: number; y: number } | null {
  if (!bbox) return null;
  const scale = getDisplayScale(img, viewport);
  if (!scale) return null;
  return rectCenter(scaleRect(bbox, scale));
}

/**
 * Quadratic-Bezier travel duration. Pulled out so it's unit-testable
 * without DOM. ``speed`` is in capture-space px per ms.
 */
export function travelDuration(
  from: { x: number; y: number },
  to: { x: number; y: number },
  speed: number,
): number {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const dist = Math.hypot(dx, dy);
  return Math.max(150, dist / (speed || 1.5));
}

/**
 * Compute a quadratic-Bezier control point offset 15% perpendicular to the
 * straight line between ``from`` and ``to``. Same formula the inline JS uses.
 */
export function bezierControlPoint(
  from: { x: number; y: number },
  to: { x: number; y: number },
): { x: number; y: number } {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const dist = Math.hypot(dx, dy);
  if (!dist) return { x: from.x, y: from.y };
  const mx = (from.x + to.x) / 2;
  const my = (from.y + to.y) / 2;
  const nx = -dy / dist;
  const ny = dx / dist;
  return { x: mx + nx * dist * 0.15, y: my + ny * dist * 0.15 };
}

export interface AnimatorOptions {
  /** The stage element. Must contain ``.steppy-cursor``, ``.steppy-ripple``,
   * ``.steppy-kbd`` descendants and a single ``img`` (the screenshot). */
  stage: HTMLElement;
  timings?: Partial<CursorTimings>;
  /** Hook called when the cursor moves to or fires on a target. Used by the
   * Lit player to broadcast progress events. */
  onPhaseChange?: (phase: 'travel' | 'pre-action' | 'action' | 'post-action' | 'idle', target?: NormalizedTarget) => void;
}

export class CursorAnimator {
  private stage: HTMLElement;
  private timings: CursorTimings;
  private onPhaseChange: AnimatorOptions['onPhaseChange'];
  private cursorEl: HTMLElement | null = null;
  private rippleEl: HTMLElement | null = null;
  private kbdEl: HTMLElement | null = null;
  private imgEl: HTMLElement | null = null;
  private currentPos: { x: number; y: number } = { x: 8, y: 8 };
  private aborted = false;

  constructor(opts: AnimatorOptions) {
    this.stage = opts.stage;
    this.timings = { ...DEFAULT_TIMINGS, ...(opts.timings ?? {}) };
    this.onPhaseChange = opts.onPhaseChange;
    this.refreshDom();
  }

  setTimings(t: Partial<CursorTimings>): void {
    this.timings = { ...this.timings, ...t };
  }

  /** Re-point the animator at a (possibly relocated) stage element and re-read
   *  its children. Needed when a host re-mounts the stage subtree — e.g. the
   *  Lit player switching presentation templates moves ``.steppy-stage`` to a
   *  new DOM position, and the cached ``stage`` ref would otherwise go stale. */
  setStage(stage: HTMLElement): void {
    this.stage = stage;
    this.refreshDom();
  }

  abort(): void {
    this.aborted = true;
  }

  hide(): void {
    if (this.cursorEl) this.cursorEl.classList.remove('visible');
    if (this.kbdEl) this.kbdEl.classList.remove('visible');
    this.onPhaseChange?.('idle');
  }

  /** Re-read stage children. Call after the stage's DOM has been re-rendered. */
  refreshDom(): void {
    this.cursorEl = this.stage.querySelector<HTMLElement>('.steppy-cursor');
    this.rippleEl = this.stage.querySelector<HTMLElement>('.steppy-ripple');
    this.kbdEl = this.stage.querySelector<HTMLElement>('.steppy-kbd');
    this.imgEl = this.stage.querySelector<HTMLImageElement>('img');
  }

  /**
   * Normalize a frame's cursor behavior into an ordered list of targets.
   * ``type-multi`` expands into N targets of kind ``type``; single-target
   * variants produce a single entry. Returns ``[]`` when there's nothing to
   * animate (cursor is null or no targets).
   */
  static normalizeTargets(behavior: CursorBehavior | null): NormalizedTarget[] {
    if (!behavior) return [];
    if (behavior.kind === 'type-multi') {
      return behavior.targets.map(
        (t: MultiTarget): NormalizedTarget => ({
          bbox: t.bbox,
          value: t.value,
          selector: t.selector,
          kind: 'type',
        }),
      );
    }
    const tgt = behavior.target as SingleTarget;
    const value = behavior.kind === 'type' ? behavior.value : undefined;
    return [
      {
        bbox: tgt.bbox,
        value,
        selector: tgt.selector,
        kind: behavior.kind === 'click' ? 'click' : behavior.kind === 'hover' ? 'hover' : 'type',
      },
    ];
  }

  /** Wait for the screenshot ``<img>`` to be ready (load or error). */
  async waitImageReady(): Promise<void> {
    const img = this.imgEl as HTMLImageElement | null;
    if (!img) return;
    if (img.complete && img.naturalWidth) return;
    await new Promise<void>((resolve) => {
      const done = () => resolve();
      img.addEventListener('load', done, { once: true });
      img.addEventListener('error', done, { once: true });
    });
  }

  /**
   * Full sequence over every target in ``frame.cursor`` — historical
   * stepbystep behavior. No user interaction.
   */
  async animateFrame(frame: AnimatorFrame): Promise<void> {
    this.aborted = false;
    this.refreshDom();
    if (!this.cursorEl) return;
    if (!frame.cursor) {
      this.hide();
      return;
    }
    await this.waitImageReady();
    const targets = CursorAnimator.normalizeTargets(frame.cursor);
    this.currentPos = { x: 8, y: 8 };
    this.cursorEl.classList.add('visible');
    for (let i = 0; i < targets.length; i++) {
      if (this.aborted) return;
      await this.travelToTargetIndex(frame, targets, i);
      if (this.aborted) return;
      await this.playTargetActionIndex(frame, targets, i);
      if (this.aborted) return;
      await sleep(this.timings.post_action_ms);
    }
  }

  /**
   * Interactive mode: move the cursor to ``targetIndex`` and pulse it. Caller
   * waits for user input before invoking ``playTargetAction``.
   */
  async travelToTarget(frame: AnimatorFrame, targetIndex: number): Promise<void> {
    this.aborted = false;
    this.refreshDom();
    if (!this.cursorEl) return;
    if (!frame.cursor) {
      this.hide();
      return;
    }
    await this.waitImageReady();
    const targets = CursorAnimator.normalizeTargets(frame.cursor);
    this.cursorEl.classList.add('visible');
    await this.travelToTargetIndex(frame, targets, targetIndex);
  }

  /**
   * Interactive mode: run the action animation for ``targetIndex`` and the
   * post-action settling beat.
   */
  async playTargetAction(frame: AnimatorFrame, targetIndex: number): Promise<void> {
    this.aborted = false;
    this.refreshDom();
    if (!frame.cursor) return;
    const targets = CursorAnimator.normalizeTargets(frame.cursor);
    await this.playTargetActionIndex(frame, targets, targetIndex);
    if (this.aborted) return;
    await sleep(this.timings.post_action_ms);
  }

  /**
   * Pure helper: project a capture-space bbox into the displayed coordinate
   * space. Returns ``null`` when the image hasn't laid out yet. Exposed so
   * the Lit player can place hotspots without duplicating the math.
   */
  computeDisplayRect(bbox: Bbox | null | undefined, viewport: Viewport | null | undefined): DisplayRect | null {
    const img = this.imgEl;
    if (!img || !bbox) return null;
    const scale = getDisplayScale(img, viewport);
    if (!scale) return null;
    return scaleRect(bbox, scale);
  }

  // ── Internal building blocks ───────────────────────────────────────────

  private async travelToTargetIndex(
    frame: AnimatorFrame,
    targets: NormalizedTarget[],
    index: number,
  ): Promise<void> {
    const tgt = targets[index];
    const cursor = this.cursorEl;
    if (!tgt || !cursor) return;
    const center = scaleBbox(tgt.bbox, this.imgEl!, frame.viewport);
    if (!center) return;

    this.onPhaseChange?.('travel', tgt);
    await this.travelTo(cursor, this.currentPos, center);
    this.currentPos = center;

    this.onPhaseChange?.('pre-action', tgt);
    // Pulse: drop the class, force reflow to restart the animation, re-add.
    cursor.classList.remove('pulse');
    void cursor.offsetWidth;
    cursor.classList.add('pulse');
    await sleep(this.timings.pre_action_ms);
    cursor.classList.remove('pulse');
  }

  private async playTargetActionIndex(
    frame: AnimatorFrame,
    targets: NormalizedTarget[],
    index: number,
  ): Promise<void> {
    const tgt = targets[index];
    if (!tgt) return;
    const center = scaleBbox(tgt.bbox, this.imgEl!, frame.viewport) || this.currentPos;
    this.onPhaseChange?.('action', tgt);
    if (tgt.kind === 'click') {
      if (this.rippleEl) this.fireClick(this.rippleEl, center);
      await sleep(this.timings.action_ms);
    } else if (tgt.kind === 'type') {
      if (this.kbdEl) await this.typeValue(this.kbdEl, center, tgt.value);
    } else {
      // hover: linger
      await sleep(this.timings.action_ms);
    }
    this.onPhaseChange?.('post-action', tgt);
  }

  private async travelTo(
    cursor: HTMLElement,
    from: { x: number; y: number },
    to: { x: number; y: number },
  ): Promise<void> {
    const duration = travelDuration(from, to, this.timings.travel_speed_px_per_ms);
    const cp = bezierControlPoint(from, to);
    return new Promise<void>((resolve) => {
      const start = performance.now();
      const frame = (now: number) => {
        if (this.aborted) {
          resolve();
          return;
        }
        const raw = Math.min(1, (now - start) / duration);
        const t = easeInOut(raw);
        const x = (1 - t) * (1 - t) * from.x + 2 * (1 - t) * t * cp.x + t * t * to.x;
        const y = (1 - t) * (1 - t) * from.y + 2 * (1 - t) * t * cp.y + t * t * to.y;
        cursor.style.left = `${x}px`;
        cursor.style.top = `${y}px`;
        if (raw < 1) requestAnimationFrame(frame);
        else resolve();
      };
      requestAnimationFrame(frame);
    });
  }

  private fireClick(ripple: HTMLElement, to: { x: number; y: number }): void {
    ripple.style.left = `${to.x}px`;
    ripple.style.top = `${to.y}px`;
    ripple.classList.remove('fire');
    void ripple.offsetWidth;
    ripple.classList.add('fire');
  }

  private async typeValue(
    kbd: HTMLElement,
    to: { x: number; y: number },
    value: string | undefined,
  ): Promise<void> {
    kbd.style.left = `${to.x + 16}px`;
    kbd.style.top = `${to.y + 16}px`;
    kbd.textContent = '';
    kbd.classList.add('visible');
    const chars = (value ?? '').split('');
    const per = chars.length ? this.timings.action_ms / chars.length : this.timings.action_ms;
    for (const ch of chars) {
      if (this.aborted) break;
      kbd.textContent = (kbd.textContent ?? '') + ch;
      await sleep(per);
    }
    await sleep(80);
    kbd.classList.remove('visible');
  }
}
