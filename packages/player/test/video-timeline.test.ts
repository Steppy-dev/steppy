import { describe, expect, it } from 'vitest';

import { DEFAULT_TIMINGS } from '../src/cursor/types.js';
import type { CursorTimings, DisplayScale } from '../src/cursor/types.js';
import type { GuideManifest, ManifestStep } from '../src/manifest/schema.js';
import { easeOutCss, easeInOutCss, cubicBezier } from '../src/video/easing.js';
import {
  CURSOR_ORIGIN,
  PULSE_MS,
  RIPPLE_MS,
  TYPE_TAIL_MS,
  actionDuration,
  buildTimeline,
  frameAt,
  frameStateEquals,
  pulseScaleAt,
  stateAt,
  timeAtFrame,
  typedTextAt,
} from '../src/video/timeline.js';
import { makeManifest } from './fixtures.js';

const SCALE: DisplayScale = { sx: 1, sy: 1 };
const TIMINGS: CursorTimings = DEFAULT_TIMINGS;

/** No intro/outro card, so segment maths reads directly off the steps. */
const BARE = { locale: 'fr', layout: 'desktop', scale: SCALE, fps: 30, introMs: 0, outroMs: 0 } as const;

function build(manifest: GuideManifest, over: Partial<Parameters<typeof buildTimeline>[1]> = {}) {
  return buildTimeline(manifest, { ...BARE, ...over });
}

function step(over: Partial<ManifestStep> = {}): ManifestStep {
  const base = makeManifest().steps[0]!;
  return { ...base, ...over };
}

/** One step, one frame, one target, at a known bbox. */
function oneTarget(cursor: unknown, over: Partial<ManifestStep> = {}): GuideManifest {
  const s = step(over);
  return makeManifest({
    locales: ['fr'],
    layouts: ['desktop'],
    defaultLocale: 'fr',
    defaultLayout: 'desktop',
    title: { fr: 'T' },
    steps: [
      {
        ...s,
        text: { fr: 'Texte' },
        captures: {
          fr: {
            desktop: {
              frames: [
                {
                  screenshot: 'a.png',
                  captureSize: { width: 1000, height: 600 },
                  cursor: cursor as never,
                },
              ],
            },
          },
        },
      },
    ],
  });
}

const CLICK_AT_500_300 = {
  kind: 'click',
  target: { bbox: { x: 480, y: 280, width: 40, height: 40 } },
};

describe('easing', () => {
  it('cubic-bezier endpoints are exact', () => {
    expect(easeOutCss(0)).toBe(0);
    expect(easeOutCss(1)).toBe(1);
    expect(easeInOutCss(0)).toBe(0);
    expect(easeInOutCss(1)).toBe(1);
  });

  it('ease-in-out is symmetric about its midpoint', () => {
    expect(easeInOutCss(0.5)).toBeCloseTo(0.5, 5);
    expect(easeInOutCss(0.25) + easeInOutCss(0.75)).toBeCloseTo(1, 4);
  });

  it('ease-out front-loads progress', () => {
    expect(easeOutCss(0.25)).toBeGreaterThan(0.25);
    expect(easeOutCss(0.5)).toBeGreaterThan(0.5);
  });

  it('is monotonic', () => {
    let prev = -1;
    for (let p = 0; p <= 1.0001; p += 0.01) {
      const v = easeOutCss(p);
      expect(v).toBeGreaterThanOrEqual(prev);
      prev = v;
    }
  });

  it('clamps out-of-range input', () => {
    expect(easeOutCss(-5)).toBe(0);
    expect(easeOutCss(5)).toBe(1);
  });

  it('short-circuits the linear curve', () => {
    const linear = cubicBezier(0.3, 0.3, 0.7, 0.7);
    expect(linear(0.42)).toBeCloseTo(0.42, 6);
  });
});

describe('actionDuration', () => {
  it('click and hover run for action_ms', () => {
    expect(actionDuration({ kind: 'click', bbox: { x: 0, y: 0, width: 1, height: 1 } }, TIMINGS)).toBe(300);
    expect(actionDuration({ kind: 'hover', bbox: { x: 0, y: 0, width: 1, height: 1 } }, TIMINGS)).toBe(300);
  });

  it('type adds the trailing beat typeValue sleeps', () => {
    const t = { kind: 'type' as const, value: 'abc', bbox: { x: 0, y: 0, width: 1, height: 1 } };
    expect(actionDuration(t, TIMINGS)).toBe(300 + TYPE_TAIL_MS);
  });

  it('an empty typed value is only the trailing beat (the loop never runs)', () => {
    const t = { kind: 'type' as const, value: '', bbox: { x: 0, y: 0, width: 1, height: 1 } };
    expect(actionDuration(t, TIMINGS)).toBe(TYPE_TAIL_MS);
  });
});

describe('buildTimeline', () => {
  it('lays a click out as travel → pre-action → action → post-action → hold', () => {
    const tl = build(oneTarget(CLICK_AT_500_300));
    expect(tl.segments.map((s) => s.phase)).toEqual([
      'travel',
      'pre-action',
      'action',
      'post-action',
      'hold',
    ]);
  });

  it('segments are contiguous and start at zero', () => {
    const tl = build(oneTarget(CLICK_AT_500_300));
    expect(tl.segments[0]!.startMs).toBe(0);
    for (let i = 1; i < tl.segments.length; i++) {
      expect(tl.segments[i]!.startMs).toBe(tl.segments[i - 1]!.endMs);
    }
    expect(tl.segments.at(-1)!.endMs).toBe(tl.durationMs);
  });

  it('travel honours the 150ms floor for a near-origin target', () => {
    const tl = build(oneTarget({ kind: 'click', target: { bbox: { x: 6, y: 6, width: 4, height: 4 } } }));
    const travel = tl.segments.find((s) => s.phase === 'travel')!;
    expect(travel.endMs - travel.startMs).toBe(150);
  });

  it('travel duration scales with display distance', () => {
    const near = build(oneTarget({ kind: 'click', target: { bbox: { x: 100, y: 0, width: 10, height: 10 } } }));
    const far = build(oneTarget({ kind: 'click', target: { bbox: { x: 900, y: 500, width: 10, height: 10 } } }));
    const dur = (t: ReturnType<typeof build>) => {
      const s = t.segments.find((x) => x.phase === 'travel')!;
      return s.endMs - s.startMs;
    };
    expect(dur(far)).toBeGreaterThan(dur(near));
  });

  it('the measured scale changes the timeline — display distance, not capture distance', () => {
    const m = oneTarget(CLICK_AT_500_300);
    const full = build(m);
    const half = build(m, { scale: { sx: 0.5, sy: 0.5 } });
    const travel = (t: ReturnType<typeof build>) => {
      const s = t.segments.find((x) => x.phase === 'travel')!;
      return s.endMs - s.startMs;
    };
    expect(travel(half)).toBeLessThan(travel(full));
  });

  it('the cursor starts at the animator origin', () => {
    const tl = build(oneTarget(CLICK_AT_500_300));
    expect(tl.segments.find((s) => s.phase === 'travel')!.from).toEqual(CURSOR_ORIGIN);
  });

  it('records a ripple for a click but not for a hover', () => {
    expect(build(oneTarget(CLICK_AT_500_300)).ripples).toHaveLength(1);
    const hover = oneTarget({ kind: 'hover', target: { bbox: { x: 10, y: 10, width: 5, height: 5 } } });
    expect(build(hover).ripples).toHaveLength(0);
  });

  it('expands type-multi into one target per field', () => {
    const multi = oneTarget({
      kind: 'type-multi',
      targets: [
        { bbox: { x: 10, y: 10, width: 5, height: 5 }, value: 'a' },
        { bbox: { x: 200, y: 300, width: 5, height: 5 }, value: 'bb' },
        { bbox: { x: 400, y: 100, width: 5, height: 5 }, value: 'ccc' },
      ],
    });
    const tl = build(multi);
    expect(tl.segments.filter((s) => s.phase === 'travel')).toHaveLength(3);
    expect(tl.segments.filter((s) => s.phase === 'action').map((s) => s.value)).toEqual(['a', 'bb', 'ccc']);
  });

  it('holds a cursor-less step for the readability floor', () => {
    const tl = build(oneTarget(null), { minStepMs: 2500 });
    expect(tl.durationMs).toBe(2500);
    expect(tl.segments.every((s) => s.phase === 'hold')).toBe(true);
  });

  it('honours waitAfter as the settle beat when it is longer than holdMs', () => {
    const short = build(oneTarget(CLICK_AT_500_300, { waitAfter: 0 }), { minStepMs: 0, holdMs: 100 });
    const long = build(oneTarget(CLICK_AT_500_300, { waitAfter: 3000 }), { minStepMs: 0, holdMs: 100 });
    expect(long.durationMs - short.durationMs).toBe(2900);
  });

  it('stretches a step so the voiceover is never cut off', () => {
    const m = oneTarget(CLICK_AT_500_300);
    const silent = build(m, { minStepMs: 0 });
    const narrated = build(m, { minStepMs: 0, narrationMs: { 'step-1': 9000 }, narrationPadMs: 500 });
    expect(narrated.durationMs).toBe(9500);
    expect(narrated.durationMs).toBeGreaterThan(silent.durationMs);
  });

  it('never shortens a step below its animation, however brief the narration', () => {
    const m = oneTarget(CLICK_AT_500_300);
    const silent = build(m, { minStepMs: 0 });
    const narrated = build(m, { minStepMs: 0, narrationMs: { 'step-1': 1 }, narrationPadMs: 0 });
    expect(narrated.durationMs).toBe(silent.durationMs);
  });

  it('gives every frame of a multi-frame step its own on-screen time', () => {
    const s = step();
    const m = makeManifest({
      locales: ['fr'],
      layouts: ['desktop'],
      defaultLocale: 'fr',
      defaultLayout: 'desktop',
      title: { fr: 'T' },
      steps: [
        {
          ...s,
          text: { fr: 'Texte' },
          captures: {
            fr: {
              desktop: {
                frames: [
                  { screenshot: 'a.png', captureSize: { width: 1000, height: 600 }, cursor: null },
                  {
                    screenshot: 'b.png',
                    captureSize: { width: 1000, height: 600 },
                    cursor: CLICK_AT_500_300 as never,
                  },
                ],
              },
            },
          },
        },
      ],
    });
    const tl = build(m, { minStepMs: 0, holdMs: 500 });
    expect(tl.screenshots[0]).toEqual(['a.png', 'b.png']);
    // The cursor-less first frame gets a hold of its own before frame 2 animates.
    const first = tl.segments[0]!;
    expect(first.phase).toBe('hold');
    expect(first.frameIndex).toBe(0);
    expect(first.endMs - first.startMs).toBe(500);
    expect(tl.segments[1]!.phase).toBe('travel');
    expect(tl.segments[1]!.frameIndex).toBe(1);
  });

  it('uses the layout-less "_" key when the guide has no layout dimension', () => {
    const s = step();
    const m = makeManifest({
      locales: ['fr'],
      layouts: [],
      defaultLayout: null,
      defaultLocale: 'fr',
      title: { fr: 'T' },
      steps: [
        {
          ...s,
          text: { fr: 'Texte' },
          captures: {
            fr: { _: { frames: [{ screenshot: 'x.png', captureSize: { width: 800, height: 600 }, cursor: null }] } },
          },
        },
      ],
    });
    const tl = buildTimeline(m, { locale: 'fr', layout: null, scale: SCALE, fps: 30, introMs: 0, outroMs: 0 });
    expect(tl.layoutKey).toBe('_');
    expect(tl.screenshots[0]).toEqual(['x.png']);
  });

  it('carries intro and outro cards', () => {
    const tl = buildTimeline(oneTarget(CLICK_AT_500_300), {
      locale: 'fr',
      layout: 'desktop',
      scale: SCALE,
      fps: 30,
      introMs: 1000,
      outroMs: 2000,
    });
    expect(tl.segments[0]!.phase).toBe('intro');
    expect(tl.segments.at(-1)!.phase).toBe('outro');
    expect(tl.segments.at(-1)!.endMs - tl.segments.at(-1)!.startMs).toBe(2000);
  });

  it('emits one cue per step, contiguous and in order', () => {
    const s = step();
    const two = makeManifest({
      locales: ['fr'],
      layouts: ['desktop'],
      defaultLocale: 'fr',
      defaultLayout: 'desktop',
      title: { fr: 'T' },
      steps: [
        { ...s, id: 'a', index: 1, text: { fr: 'Un' }, captures: caps() },
        { ...s, id: 'b', index: 2, text: { fr: 'Deux' }, captures: caps() },
      ],
    });
    const tl = build(two);
    expect(tl.cues.map((c) => c.stepId)).toEqual(['a', 'b']);
    expect(tl.cues.map((c) => c.text)).toEqual(['Un', 'Deux']);
    expect(tl.cues[0]!.endMs).toBe(tl.cues[1]!.startMs);
    expect(tl.cues.at(-1)!.endMs).toBe(tl.durationMs);
  });

  it('falls back to the step text when no per-step title exists', () => {
    const tl = build(oneTarget(CLICK_AT_500_300));
    expect(tl.cues[0]!.title).toBe('Texte');
  });

  it('captions from the rendered layout when the step overrides its text', () => {
    // Same manifest, two layouts, one of which carries its own wording. The
    // cue must follow the layout the video is being rendered for — anything
    // else narrates desktop over mobile footage.
    const s = step();
    const twoLayouts = makeManifest({
      locales: ['fr'],
      layouts: ['desktop', 'mobile'],
      defaultLocale: 'fr',
      defaultLayout: 'desktop',
      title: { fr: 'T' },
      steps: [
        {
          ...s,
          id: 'a',
          index: 1,
          text: { fr: 'Cliquez sur la ligne' },
          captures: {
            fr: {
              desktop: caps().fr.desktop,
              mobile: {
                ...caps().fr.desktop,
                content: { title: null, text: 'Touchez la carte', subtitle: null },
              },
            },
          },
        },
      ],
    });

    expect(build(twoLayouts, { layout: 'desktop' }).cues[0]!.text).toBe('Cliquez sur la ligne');
    const mobile = build(twoLayouts, { layout: 'mobile' });
    expect(mobile.cues[0]!.text).toBe('Touchez la carte');
    // No per-capture title, so the title falls back to the overridden text —
    // not to the step-level one.
    expect(mobile.cues[0]!.title).toBe('Touchez la carte');
  });

  it('cursor visibility toggles around cursor-less steps', () => {
    const s = step();
    const m = makeManifest({
      locales: ['fr'],
      layouts: ['desktop'],
      defaultLocale: 'fr',
      defaultLayout: 'desktop',
      title: { fr: 'T' },
      steps: [
        { ...s, id: 'a', index: 1, text: { fr: 'Un' }, captures: caps(CLICK_AT_500_300) },
        { ...s, id: 'b', index: 2, text: { fr: 'Deux' }, captures: caps(null) },
      ],
    });
    const tl = build(m);
    expect(tl.visibility.map((v) => v.visible)).toEqual([true, false]);
    expect(tl.visibility[1]!.atMs).toBe(tl.cues[1]!.startMs);
  });

  it('frameCount covers the whole duration at the sampled rate', () => {
    const tl = build(oneTarget(CLICK_AT_500_300), { fps: 24 });
    expect(tl.frameCount).toBe(Math.ceil((tl.durationMs * 24) / 1000));
    expect(timeAtFrame(tl, tl.frameCount - 1)).toBeLessThan(tl.durationMs);
  });
});

function caps(cursor: unknown = CLICK_AT_500_300) {
  return {
    fr: {
      desktop: {
        frames: [{ screenshot: 'a.png', captureSize: { width: 1000, height: 600 }, cursor: cursor as never }],
      },
    },
  };
}

describe('pulseScaleAt', () => {
  it('is 1 outside the fixed 200ms animation', () => {
    expect(pulseScaleAt(-1)).toBe(1);
    expect(pulseScaleAt(0)).toBe(1);
    expect(pulseScaleAt(PULSE_MS)).toBe(1);
    expect(pulseScaleAt(PULSE_MS + 5000)).toBe(1);
  });

  it('peaks at the midpoint keyframe', () => {
    expect(pulseScaleAt(PULSE_MS / 2)).toBeCloseTo(1.25, 5);
  });

  it('rises then falls', () => {
    expect(pulseScaleAt(50)).toBeGreaterThan(1);
    expect(pulseScaleAt(50)).toBeLessThan(1.25);
    expect(pulseScaleAt(150)).toBeGreaterThan(1);
    expect(pulseScaleAt(150)).toBeLessThan(1.25);
  });
});

describe('typedTextAt', () => {
  it('reveals one character per action_ms/n slice', () => {
    expect(typedTextAt('abcd', 0, 400)).toBe('a');
    expect(typedTextAt('abcd', 100, 400)).toBe('ab');
    expect(typedTextAt('abcd', 250, 400)).toBe('abc');
    expect(typedTextAt('abcd', 399, 400)).toBe('abcd');
  });

  it('never overruns the value', () => {
    expect(typedTextAt('ab', 99999, 300)).toBe('ab');
  });

  it('handles an empty value', () => {
    expect(typedTextAt('', 10, 300)).toBe('');
  });
});

describe('stateAt', () => {
  it('shows the intro card before the first step', () => {
    const tl = buildTimeline(oneTarget(CLICK_AT_500_300), {
      locale: 'fr',
      layout: 'desktop',
      scale: SCALE,
      fps: 30,
      introMs: 1000,
      outroMs: 0,
    });
    const s = stateAt(tl, 500, TIMINGS);
    expect(s.card).toBe('intro');
    expect(s.stepIndex).toBe(-1);
    expect(s.screenshot).toBe('');
  });

  it('moves the cursor along the travel curve and lands on the target', () => {
    const tl = build(oneTarget(CLICK_AT_500_300));
    const travel = tl.segments[0]!;
    const mid = stateAt(tl, (travel.startMs + travel.endMs) / 2, TIMINGS);
    const landed = stateAt(tl, travel.endMs, TIMINGS);
    expect(landed.cursor!.x).toBeCloseTo(500, 5);
    expect(landed.cursor!.y).toBeCloseTo(300, 5);
    expect(mid.cursor!.x).not.toBeCloseTo(landed.cursor!.x, 1);
  });

  it('fades the cursor in rather than popping it', () => {
    const tl = build(oneTarget(CLICK_AT_500_300));
    expect(stateAt(tl, 0, TIMINGS).cursor).toBeNull();
    expect(stateAt(tl, 100, TIMINGS).cursor!.opacity).toBeGreaterThan(0);
    expect(stateAt(tl, 100, TIMINGS).cursor!.opacity).toBeLessThan(1);
    expect(stateAt(tl, 250, TIMINGS).cursor!.opacity).toBe(1);
  });

  it('runs the ripple for its own 400ms, independently of action_ms', () => {
    const tl = build(oneTarget(CLICK_AT_500_300));
    const action = tl.segments.find((s) => s.phase === 'action')!;
    expect(action.endMs - action.startMs).toBe(300);

    expect(stateAt(tl, action.startMs - 1, TIMINGS).ripple).toBeNull();
    const early = stateAt(tl, action.startMs + 10, TIMINGS).ripple!;
    expect(early.opacity).toBeGreaterThan(0.5);
    // Still visible past the end of the action phase — the reason it is modelled
    // as an overlay rather than as the phase itself.
    expect(stateAt(tl, action.startMs + 350, TIMINGS).ripple).not.toBeNull();
    expect(stateAt(tl, action.startMs + RIPPLE_MS, TIMINGS).ripple).toBeNull();
  });

  it('grows and fades the ripple', () => {
    const tl = build(oneTarget(CLICK_AT_500_300));
    const at = tl.ripples[0]!.atMs;
    const a = stateAt(tl, at + 20, TIMINGS).ripple!;
    const b = stateAt(tl, at + 300, TIMINGS).ripple!;
    expect(b.scale).toBeGreaterThan(a.scale);
    expect(b.opacity).toBeLessThan(a.opacity);
    expect(a.x).toBeCloseTo(500, 5);
  });

  it('shows the keycap chip only while typing', () => {
    const typed = oneTarget({
      kind: 'type',
      target: { bbox: { x: 480, y: 280, width: 40, height: 40 } },
      value: 'hi',
    });
    const tl = build(typed);
    const action = tl.segments.find((s) => s.phase === 'action')!;
    expect(stateAt(tl, action.startMs - 5, TIMINGS).kbd).toBeNull();
    const mid = stateAt(tl, action.startMs + 10, TIMINGS).kbd!;
    expect(mid.text).toBe('h');
    expect(mid.x).toBeCloseTo(516, 5);
    expect(stateAt(tl, action.startMs + 200, TIMINGS).kbd!.text).toBe('hi');
    expect(stateAt(tl, action.endMs, TIMINGS).kbd).toBeNull();
  });

  it('keeps the cursor where it landed during the trailing hold', () => {
    const tl = build(oneTarget(CLICK_AT_500_300), { minStepMs: 8000 });
    const s = stateAt(tl, 7000, TIMINGS);
    expect(s.cursor!.x).toBeCloseTo(500, 5);
    expect(s.cursor!.y).toBeCloseTo(300, 5);
  });

  it('carries the caption', () => {
    const tl = build(oneTarget(CLICK_AT_500_300));
    expect(stateAt(tl, tl.durationMs / 2, TIMINGS).caption).toBe('Texte');
  });

  it('advances progress per step, not per millisecond', () => {
    const s = step();
    const four = makeManifest({
      locales: ['fr'],
      layouts: ['desktop'],
      defaultLocale: 'fr',
      defaultLayout: 'desktop',
      title: { fr: 'T' },
      steps: [1, 2, 3, 4].map((n) => ({
        ...s,
        id: `s${n}`,
        index: n,
        text: { fr: `Etape ${n}` },
        captures: caps(),
      })),
    });
    const tl = build(four);
    expect(tl.cues.map((c) => stateAt(tl, c.startMs, TIMINGS).progress)).toEqual([0.25, 0.5, 0.75, 1]);
    // Constant across a step — this is what keeps a hold dedupable.
    const c0 = tl.cues[0]!;
    expect(stateAt(tl, c0.startMs, TIMINGS).progress).toBe(stateAt(tl, c0.endMs - 1, TIMINGS).progress);
  });

  it('is a pure function of time — sampling order cannot matter', () => {
    const tl = build(oneTarget(CLICK_AT_500_300));
    const forward = Array.from({ length: tl.frameCount }, (_, i) => frameAt(tl, i, TIMINGS));
    const backward = Array.from({ length: tl.frameCount }, (_, i) => tl.frameCount - 1 - i).map((i) =>
      frameAt(tl, i, TIMINGS),
    );
    backward.reverse();
    expect(backward).toEqual(forward);
  });
});

describe('frameStateEquals', () => {
  it('reports a hold as unchanged, so the renderer can skip re-capturing it', () => {
    const tl = build(oneTarget(CLICK_AT_500_300), { minStepMs: 8000, fps: 30 });
    const hold = tl.segments.at(-1)!;
    const a = stateAt(tl, hold.startMs + 1000, TIMINGS);
    const b = stateAt(tl, hold.startMs + 2000, TIMINGS);
    expect(frameStateEquals(a, b)).toBe(true);
  });

  it('reports cursor movement as changed', () => {
    const tl = build(oneTarget(CLICK_AT_500_300));
    const travel = tl.segments[0]!;
    const a = stateAt(tl, travel.startMs + 20, TIMINGS);
    const b = stateAt(tl, travel.startMs + 120, TIMINGS);
    expect(frameStateEquals(a, b)).toBe(false);
  });

  it('reports a screenshot swap as changed', () => {
    const tl = build(oneTarget(CLICK_AT_500_300));
    const a = stateAt(tl, tl.durationMs / 2, TIMINGS);
    expect(frameStateEquals(a, { ...a, screenshot: 'other.png' })).toBe(false);
  });

  it('dedups a static stretch across a real render pass', () => {
    const tl = build(oneTarget(CLICK_AT_500_300), { minStepMs: 6000, fps: 30 });
    let dirty = 0;
    let prev = null as ReturnType<typeof stateAt> | null;
    for (let i = 0; i < tl.frameCount; i++) {
      const s = frameAt(tl, i, TIMINGS);
      if (!prev || !frameStateEquals(prev, s)) dirty++;
      prev = s;
    }
    expect(dirty).toBeGreaterThan(0);
    expect(dirty).toBeLessThan(tl.frameCount);
  });
});
