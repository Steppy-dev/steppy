/**
 * Video timeline — the deterministic core of `modes: ['video']`.
 *
 * A guide video is a pure function of (manifest, locale, layout, display scale,
 * narration durations, fps). This module computes that function: it lays every
 * step's cursor choreography onto an absolute millisecond timeline, and
 * ``frameAt`` returns the exact render state at any instant. Nothing here
 * touches the DOM or the clock, so the whole thing unit-tests without a browser
 * — and a frame renders identically on a laptop and on a throttled CI runner,
 * which is the property the whole feature rests on.
 *
 * Three things are deliberately *not* derivable and must be passed in:
 *
 * * **``scale``** — the capture-space → display-space ratio. The stage renders
 *   its screenshot with ``width:auto; max-width:100%``, so the displayed width
 *   is ``min(intrinsicPngWidth, stageWidth)`` and ``intrinsicPngWidth`` is
 *   ``captureSize.width × device_scale_factor`` — a factor the manifest does not
 *   carry. ``stage.ts`` measures it with the animator's own ``getDisplayScale``
 *   and hands it here. (Travel duration is a function of *display* distance —
 *   see ``travelDuration`` — so this also means total video duration varies with
 *   the configured output width.)
 * * **``narrationMs``** — clip lengths come from the TTS provider, which runs
 *   before the browser opens. A step is held at least as long as its voiceover.
 * * **``fps``** — sampling rate; the timeline itself is continuous.
 *
 * Phase durations come from the manifest's ``cursor`` block, matching
 * ``CursorAnimator``'s sequence (travel → pre-action → action → post-action).
 * The click ripple is the one exception: its CSS animation is a hardcoded 400ms
 * (``base-styles.ts``) independent of ``action_ms``, so it is modelled as an
 * overlay that may outlive the action phase rather than as the phase itself.
 */

import { CursorAnimator, bezierControlPoint, easeInOut, rectCenter, scaleRect, travelDuration } from '../cursor/animator.js';
import type { NormalizedTarget, TargetKind } from '../cursor/animator.js';
import { DEFAULT_TIMINGS } from '../cursor/types.js';
import type { CursorTimings, DisplayScale } from '../cursor/types.js';
import { resolveStepContent } from '../manifest/content.js';
import type { Frame, GuideManifest, ManifestStep } from '../manifest/schema.js';
import { clamp01, easeInOutCss, easeOutCss, lerp } from './easing.js';

/** Layout key used by the build for guides with no layout dimension. */
export const LAYOUTLESS_KEY = '_';

/** Where the cursor sits before its first travel, matching ``animateFrame``. */
export const CURSOR_ORIGIN: Point = { x: 8, y: 8 };

/** ``.steppy-ripple.fire`` — ``animation: steppy-ripple 400ms ease-out forwards``. */
export const RIPPLE_MS = 400;
export const RIPPLE_SCALE_FROM = 0.6;
export const RIPPLE_SCALE_TO = 3;

/** ``.steppy-cursor.pulse`` — ``animation: steppy-pulse 200ms ease-in-out``. */
export const PULSE_MS = 200;
export const PULSE_SCALE_PEAK = 1.25;

/** ``.steppy-cursor`` — ``transition: opacity 200ms ease-in-out``. */
export const CURSOR_FADE_MS = 200;

/** Trailing beat ``typeValue`` sleeps after the last character. */
export const TYPE_TAIL_MS = 80;

/** Offset of the keycap chip from the cursor point, per ``typeValue``. */
export const KBD_OFFSET = 16;

export interface Point {
  x: number;
  y: number;
}

export type Phase = 'intro' | 'travel' | 'pre-action' | 'action' | 'post-action' | 'hold' | 'outro';

export interface Segment {
  phase: Phase;
  startMs: number;
  endMs: number;
  /** Index into ``manifest.steps``; -1 for the intro/outro cards. */
  stepIndex: number;
  /** Index into the step's frames; -1 for the intro/outro cards. */
  frameIndex: number;
  /** Index into the frame's normalized targets; -1 when the phase has no target. */
  targetIndex: number;
  kind?: TargetKind;
  /** Display-space geometry, precomputed so ``frameAt`` only interpolates. */
  from?: Point;
  to?: Point;
  control?: Point;
  value?: string;
}

export interface Cue {
  stepId: string;
  stepIndex: number;
  startMs: number;
  endMs: number;
  text: string;
  title: string;
}

/** A click ripple, tracked separately because it can outlive its action phase. */
export interface RippleEvent {
  atMs: number;
  at: Point;
}

/** A cursor show/hide transition, so the 200ms opacity fade stays computable. */
export interface VisibilityChange {
  atMs: number;
  visible: boolean;
}

export interface Timeline {
  durationMs: number;
  frameCount: number;
  fps: number;
  segments: Segment[];
  cues: Cue[];
  ripples: RippleEvent[];
  visibility: VisibilityChange[];
  /** Screenshot URL per (stepIndex, frameIndex), flattened for ``frameAt``. */
  screenshots: string[][];
  stepCount: number;
  locale: string;
  layoutKey: string;
}

export interface VideoTimelineOptions {
  locale: string;
  /** Layout name, or null/undefined for a guide with no layout dimension. */
  layout?: string | null;
  /** Measured capture-space → display-space ratio. See the module docstring. */
  scale: DisplayScale;
  fps: number;
  /** Per-step voiceover length, keyed by ``ManifestStep.id``. */
  narrationMs?: Record<string, number>;
  /** Settle beat after a step's animation finishes. */
  holdMs?: number;
  /** Floor on any step's on-screen time, so a fast step is still readable. */
  minStepMs?: number;
  /** Breathing room added after a step's voiceover before advancing. */
  narrationPadMs?: number;
  introMs?: number;
  outroMs?: number;
}

export interface CursorState {
  x: number;
  y: number;
  opacity: number;
  scale: number;
}

export interface RippleState {
  x: number;
  y: number;
  scale: number;
  opacity: number;
}

export interface KbdState {
  x: number;
  y: number;
  text: string;
}

export interface FrameState {
  timeMs: number;
  /** -1 while an intro/outro card is showing. */
  stepIndex: number;
  frameIndex: number;
  screenshot: string;
  cursor: CursorState | null;
  ripple: RippleState | null;
  kbd: KbdState | null;
  caption: string;
  captionTitle: string;
  /** 0..1 across the whole video, for the progress bar. */
  progress: number;
  card: 'intro' | 'outro' | null;
}

/**
 * Every segment boundary is a whole millisecond.
 *
 * Travel duration is a distance/speed quotient, so accumulating it raw drifts
 * (2000 becomes 2000.0000000000005) and — worse — makes two frames of an
 * otherwise motionless hold compare unequal, which would defeat the dedup the
 * render budget depends on. Frames are sampled ~33ms apart; sub-millisecond
 * precision cannot reach the screen.
 */
const ms = Math.round;

const DEFAULTS = {
  holdMs: 700,
  minStepMs: 1500,
  narrationPadMs: 400,
  introMs: 1200,
  outroMs: 1500,
};

function targetCenter(target: NormalizedTarget, scale: DisplayScale): Point {
  return rectCenter(scaleRect(target.bbox, scale));
}

/**
 * How long the action phase lasts for one target.
 *
 * ``typeValue`` appends one character every ``action_ms / n`` and then sleeps a
 * further 80ms, so a typed target runs ``action_ms + 80`` — except for an empty
 * value, where the loop never runs and only the trailing beat remains.
 */
export function actionDuration(target: NormalizedTarget, timings: CursorTimings): number {
  if (target.kind !== 'type') return timings.action_ms;
  const chars = (target.value ?? '').length;
  return chars ? timings.action_ms + TYPE_TAIL_MS : TYPE_TAIL_MS;
}

/** Frames of a step for one (locale, layout), or `[]` when the pair is absent. */
export function framesFor(step: ManifestStep, locale: string, layoutKey: string): Frame[] {
  return step.captures?.[locale]?.[layoutKey]?.frames ?? [];
}

export function resolveLayoutKey(manifest: GuideManifest, layout?: string | null): string {
  if (layout) return layout;
  return manifest.layouts.length > 0 ? manifest.layouts[0]! : LAYOUTLESS_KEY;
}

/**
 * Lay the whole guide out on an absolute millisecond timeline.
 *
 * Steps run in manifest order; within a step, every frame runs in order and
 * every target within a frame runs travel → pre-action → action → post-action,
 * exactly as ``CursorAnimator.animateFrame`` does. Two deliberate divergences
 * from the live player, both because a video is watched rather than driven:
 *
 * * the cursor keeps its position across frames *and* steps (``animateFrame``
 *   resets to the corner each frame, which reads as a glitch between the
 *   progressive frames of a tall ``fill-form``);
 * * ``cursor.mode`` (``hotspot``/``off``) is ignored — those describe click
 *   affordances that do not exist here.
 */
export function buildTimeline(manifest: GuideManifest, options: VideoTimelineOptions): Timeline {
  const { locale, scale, fps } = options;
  const holdMs = options.holdMs ?? DEFAULTS.holdMs;
  const minStepMs = options.minStepMs ?? DEFAULTS.minStepMs;
  const narrationPadMs = options.narrationPadMs ?? DEFAULTS.narrationPadMs;
  const introMs = options.introMs ?? DEFAULTS.introMs;
  const outroMs = options.outroMs ?? DEFAULTS.outroMs;
  const narrationMs = options.narrationMs ?? {};

  const timings: CursorTimings = { ...DEFAULT_TIMINGS, ...manifest.cursor };
  const layoutKey = resolveLayoutKey(manifest, options.layout);

  const segments: Segment[] = [];
  const cues: Cue[] = [];
  const ripples: RippleEvent[] = [];
  const visibility: VisibilityChange[] = [];
  const screenshots: string[][] = [];

  let cursorAt: Point = { ...CURSOR_ORIGIN };
  let cursorVisible = false;
  let t = 0;

  if (introMs > 0) {
    segments.push({ phase: 'intro', startMs: 0, endMs: introMs, stepIndex: -1, frameIndex: -1, targetIndex: -1 });
    t = introMs;
  }

  manifest.steps.forEach((step, stepIndex) => {
    const frames = framesFor(step, locale, layoutKey);
    screenshots.push(frames.map((f) => f.screenshot));

    const stepStart = t;
    const stepHasCursor = frames.some((f) => f.cursor !== null);

    if (cursorVisible !== stepHasCursor) {
      visibility.push({ atMs: stepStart, visible: stepHasCursor });
      cursorVisible = stepHasCursor;
    }

    frames.forEach((frame, frameIndex) => {
      const targets = CursorAnimator.normalizeTargets(frame.cursor);
      if (targets.length === 0) {
        // A frame with nothing to animate still has to be on screen long enough
        // to be seen — otherwise a multi-frame step flashes past its own captures.
        segments.push({ phase: 'hold', startMs: t, endMs: t + holdMs, stepIndex, frameIndex, targetIndex: -1 });
        t += holdMs;
        return;
      }

      targets.forEach((target, targetIndex) => {
        const to = targetCenter(target, scale);
        const from = cursorAt;

        const travelMs = ms(travelDuration(from, to, timings.travel_speed_px_per_ms));
        segments.push({
          phase: 'travel',
          startMs: t,
          endMs: t + travelMs,
          stepIndex,
          frameIndex,
          targetIndex,
          kind: target.kind,
          from,
          to,
          control: bezierControlPoint(from, to),
        });
        t += travelMs;
        cursorAt = to;

        segments.push({
          phase: 'pre-action',
          startMs: t,
          endMs: t + timings.pre_action_ms,
          stepIndex,
          frameIndex,
          targetIndex,
          kind: target.kind,
          to,
        });
        t += timings.pre_action_ms;

        const actionMs = actionDuration(target, timings);
        if (target.kind === 'click') ripples.push({ atMs: t, at: to });
        segments.push({
          phase: 'action',
          startMs: t,
          endMs: t + actionMs,
          stepIndex,
          frameIndex,
          targetIndex,
          kind: target.kind,
          to,
          value: target.value,
        });
        t += actionMs;

        segments.push({
          phase: 'post-action',
          startMs: t,
          endMs: t + timings.post_action_ms,
          stepIndex,
          frameIndex,
          targetIndex,
          kind: target.kind,
          to,
        });
        t += timings.post_action_ms;
      });
    });

    // Settle beat, then stretch the step to satisfy every floor: the author's
    // `wait-after`, the readability minimum, and — the one that actually bites —
    // the voiceover, which must never be cut off mid-sentence.
    const animatedMs = t - stepStart;
    const narration = narrationMs[step.id] ?? 0;
    const wanted = ms(
      Math.max(
        animatedMs + Math.max(holdMs, step.waitAfter),
        minStepMs,
        narration > 0 ? narration + narrationPadMs : 0,
      ),
    );
    const lastFrame = Math.max(0, frames.length - 1);
    segments.push({
      phase: 'hold',
      startMs: t,
      endMs: stepStart + wanted,
      stepIndex,
      frameIndex: lastFrame,
      targetIndex: -1,
    });
    t = stepStart + wanted;

    // Same layout key the frames came from — a step whose wording is
    // overridden per layout must be captioned with the layout being rendered.
    const content = resolveStepContent(step, locale, layoutKey);
    cues.push({
      stepId: step.id,
      stepIndex,
      startMs: stepStart,
      endMs: t,
      text: content.text,
      title: content.title || content.text,
    });
  });

  if (cursorVisible) {
    visibility.push({ atMs: t, visible: false });
    cursorVisible = false;
  }

  if (outroMs > 0) {
    segments.push({ phase: 'outro', startMs: t, endMs: t + outroMs, stepIndex: -1, frameIndex: -1, targetIndex: -1 });
    t += outroMs;
  }

  const durationMs = t;
  return {
    durationMs,
    frameCount: Math.max(1, Math.ceil((durationMs * fps) / 1000)),
    fps,
    segments,
    cues,
    ripples,
    visibility,
    screenshots,
    stepCount: manifest.steps.length,
    locale,
    layoutKey,
  };
}

/** Timestamp sampled for frame ``index``. */
export function timeAtFrame(timeline: Timeline, index: number): number {
  return (index * 1000) / timeline.fps;
}

function segmentAt(segments: Segment[], timeMs: number): Segment | null {
  if (segments.length === 0) return null;
  let lo = 0;
  let hi = segments.length - 1;
  let found = segments[0]!;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const seg = segments[mid]!;
    if (seg.startMs <= timeMs) {
      found = seg;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

function cursorOpacityAt(timeline: Timeline, timeMs: number): number {
  let visible = false;
  let changedAt = -Infinity;
  for (const change of timeline.visibility) {
    if (change.atMs > timeMs) break;
    visible = change.visible;
    changedAt = change.atMs;
  }
  const target = visible ? 1 : 0;
  const from = visible ? 0 : 1;
  const elapsed = timeMs - changedAt;
  if (!Number.isFinite(changedAt) || elapsed >= CURSOR_FADE_MS) return target;
  return lerp(from, target, easeInOutCss(elapsed / CURSOR_FADE_MS));
}

/**
 * Pulse scale at ``elapsed`` into the pre-action phase.
 *
 * The keyframes are 0%/100% ``scale(1)`` with a ``scale(1.25)`` peak at 50%, and
 * CSS applies the timing function *between* each keyframe pair. The animation is
 * a fixed 200ms regardless of ``pre_action_ms``, and has no ``forwards`` fill, so
 * it sits back at 1 once it is over.
 */
export function pulseScaleAt(elapsed: number): number {
  if (elapsed < 0 || elapsed >= PULSE_MS) return 1;
  const half = PULSE_MS / 2;
  if (elapsed < half) return lerp(1, PULSE_SCALE_PEAK, easeInOutCss(elapsed / half));
  return lerp(PULSE_SCALE_PEAK, 1, easeInOutCss((elapsed - half) / half));
}

function rippleAt(timeline: Timeline, timeMs: number): RippleState | null {
  let active: RippleEvent | null = null;
  for (const event of timeline.ripples) {
    if (event.atMs > timeMs) break;
    active = event;
  }
  if (!active) return null;
  const elapsed = timeMs - active.atMs;
  if (elapsed >= RIPPLE_MS) return null;
  const p = easeOutCss(elapsed / RIPPLE_MS);
  return {
    x: active.at.x,
    y: active.at.y,
    scale: lerp(RIPPLE_SCALE_FROM, RIPPLE_SCALE_TO, p),
    opacity: 1 - p,
  };
}

/** Characters visible at ``elapsed`` into a ``type`` action, per ``typeValue``. */
export function typedTextAt(value: string, elapsed: number, actionMs: number): string {
  if (!value) return '';
  const per = actionMs / value.length;
  if (per <= 0) return value;
  const shown = Math.min(value.length, Math.floor(elapsed / per) + 1);
  return value.slice(0, Math.max(0, shown));
}

function pointOnCurve(from: Point, control: Point, to: Point, t: number): Point {
  const u = 1 - t;
  return {
    x: u * u * from.x + 2 * u * t * control.x + t * t * to.x,
    y: u * u * from.y + 2 * u * t * control.y + t * t * to.y,
  };
}

/**
 * The complete render state at ``timeMs`` — everything ``stage.ts`` needs to
 * paint one frame, and nothing it has to decide for itself.
 */
export function stateAt(timeline: Timeline, timeMs: number, timings: CursorTimings): FrameState {
  const seg = segmentAt(timeline.segments, timeMs);

  if (!seg || seg.phase === 'intro' || seg.phase === 'outro') {
    const outro = seg?.phase === 'outro';
    return {
      timeMs,
      stepIndex: -1,
      frameIndex: -1,
      screenshot: '',
      cursor: null,
      ripple: null,
      kbd: null,
      caption: '',
      captionTitle: '',
      progress: outro ? 1 : 0,
      card: outro ? 'outro' : 'intro',
    };
  }

  // Progress advances per *step*, not per millisecond. A bar that creeps every
  // frame would make no two frames identical, which would defeat the dedup that
  // keeps a render affordable — and on a video with a native scrubber, "step 3
  // of 7" is the more useful signal anyway.
  const progress = timeline.stepCount > 0 ? clamp01((seg.stepIndex + 1) / timeline.stepCount) : 0;

  const cue = timeline.cues[seg.stepIndex];
  const screenshot = timeline.screenshots[seg.stepIndex]?.[Math.max(0, seg.frameIndex)] ?? '';
  const elapsed = timeMs - seg.startMs;

  let cursor: CursorState | null = null;
  const opacity = cursorOpacityAt(timeline, timeMs);
  if (opacity > 0) {
    let point: Point;
    let scale = 1;
    if (seg.phase === 'travel' && seg.from && seg.to && seg.control) {
      const span = seg.endMs - seg.startMs;
      point = pointOnCurve(seg.from, seg.control, seg.to, easeInOut(clamp01(span > 0 ? elapsed / span : 1)));
    } else if (seg.to) {
      point = seg.to;
      if (seg.phase === 'pre-action') scale = pulseScaleAt(elapsed);
    } else {
      // A hold after the last target: the cursor stays where it landed.
      point = lastKnownPoint(timeline, timeMs);
    }
    cursor = { x: point.x, y: point.y, opacity, scale };
  }

  let kbd: KbdState | null = null;
  if (seg.phase === 'action' && seg.kind === 'type' && seg.to) {
    const text = typedTextAt(seg.value ?? '', elapsed, timings.action_ms);
    if (text) kbd = { x: seg.to.x + KBD_OFFSET, y: seg.to.y + KBD_OFFSET, text };
  }

  return {
    timeMs,
    stepIndex: seg.stepIndex,
    frameIndex: Math.max(0, seg.frameIndex),
    screenshot,
    cursor,
    ripple: rippleAt(timeline, timeMs),
    kbd,
    caption: cue?.text ?? '',
    captionTitle: cue?.title ?? '',
    progress,
    card: null,
  };
}

function lastKnownPoint(timeline: Timeline, timeMs: number): Point {
  for (let i = timeline.segments.length - 1; i >= 0; i--) {
    const seg = timeline.segments[i]!;
    if (seg.startMs <= timeMs && seg.to) return seg.to;
  }
  return { ...CURSOR_ORIGIN };
}

/** Convenience: the state for a frame index. */
export function frameAt(timeline: Timeline, index: number, timings: CursorTimings): FrameState {
  return stateAt(timeline, timeAtFrame(timeline, index), timings);
}

function samePoint(a: { x: number; y: number } | null, b: { x: number; y: number } | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  // Sub-pixel differences cannot change the rendered frame, and treating them as
  // changes would defeat the static-frame dedup that makes rendering affordable.
  return Math.round(a.x) === Math.round(b.x) && Math.round(a.y) === Math.round(b.y);
}

function sameAlpha(a: number, b: number): boolean {
  return Math.round(a * 100) === Math.round(b * 100);
}

/**
 * Would these two states paint the same pixels?
 *
 * Drives the renderer's dedup: holds and voiceover pauses dominate a narrated
 * video, and re-screenshotting an unchanged frame is the single largest
 * avoidable cost in the pipeline.
 */
export function frameStateEquals(a: FrameState, b: FrameState): boolean {
  if (a.card !== b.card) return false;
  if (a.screenshot !== b.screenshot) return false;
  if (a.caption !== b.caption || a.captionTitle !== b.captionTitle) return false;
  if (Math.round(a.progress * 1000) !== Math.round(b.progress * 1000)) return false;

  if (!!a.cursor !== !!b.cursor) return false;
  if (a.cursor && b.cursor) {
    if (!samePoint(a.cursor, b.cursor)) return false;
    if (!sameAlpha(a.cursor.opacity, b.cursor.opacity)) return false;
    if (Math.round(a.cursor.scale * 100) !== Math.round(b.cursor.scale * 100)) return false;
  }

  if (!!a.ripple !== !!b.ripple) return false;
  if (a.ripple && b.ripple) {
    if (!samePoint(a.ripple, b.ripple)) return false;
    if (!sameAlpha(a.ripple.opacity, b.ripple.opacity)) return false;
    if (Math.round(a.ripple.scale * 100) !== Math.round(b.ripple.scale * 100)) return false;
  }

  if (!!a.kbd !== !!b.kbd) return false;
  if (a.kbd && b.kbd) {
    if (a.kbd.text !== b.kbd.text) return false;
    if (!samePoint(a.kbd, b.kbd)) return false;
  }

  return true;
}
