import { afterEach, describe, expect, it } from 'vitest';

import type { GuideManifest, ManifestStep } from '../src/manifest/schema.js';
import { baseStyles } from '../src/templates/base-styles.js';
import { evenDown, mountVideoStage } from '../src/video/stage.js';
import {
  CURSOR_FADE_MS,
  PULSE_MS,
  PULSE_SCALE_PEAK,
  RIPPLE_MS,
  RIPPLE_SCALE_FROM,
  RIPPLE_SCALE_TO,
} from '../src/video/timeline.js';
import { makeManifest } from './fixtures.js';

const CLICK = {
  kind: 'click',
  target: { bbox: { x: 480, y: 280, width: 40, height: 40 } },
};

function caps(cursor: unknown = CLICK, shot = 'a.png', size = { width: 1000, height: 600 }) {
  return { fr: { desktop: { frames: [{ screenshot: shot, captureSize: size, cursor: cursor as never }] } } };
}

function manifestWith(steps: Partial<ManifestStep>[]): GuideManifest {
  const base = makeManifest().steps[0]!;
  return makeManifest({
    locales: ['fr'],
    layouts: ['desktop'],
    defaultLocale: 'fr',
    defaultLayout: 'desktop',
    title: { fr: 'Factures' },
    steps: steps.map((s, i) => ({
      ...base,
      id: `step-${i + 1}`,
      index: i + 1,
      text: { fr: `Etape ${i + 1}` },
      captures: caps(),
      ...s,
    })),
  });
}

/**
 * happy-dom neither fetches nor decodes images: no `load`, no `error`, and
 * `naturalWidth` stays 0. Stub the intrinsic size where it matters — every
 * mount here uses a tiny `imageTimeoutMs` so `ready()` falls through the
 * load-budget path rather than waiting on events that never fire.
 */
function stubNatural(host: HTMLElement, width: number, height: number): void {
  for (const img of Array.from(host.shadowRoot!.querySelectorAll('img'))) {
    Object.defineProperty(img, 'naturalWidth', { value: width, configurable: true });
    Object.defineProperty(img, 'naturalHeight', { value: height, configurable: true });
  }
}

let host: HTMLElement | null = null;

function mount(manifest: GuideManifest, options: Record<string, unknown> = {}) {
  host = document.createElement('div');
  document.body.appendChild(host);
  const stage = mountVideoStage(host, manifest, {
    locale: 'fr',
    layout: 'desktop',
    fps: 30,
    width: 1280,
    introMs: 0,
    outroMs: 0,
    imageTimeoutMs: 1,
    ...options,
  } as never);
  return { host: host!, stage };
}

afterEach(() => {
  document.body.innerHTML = '';
  host = null;
});

describe('evenDown', () => {
  it('rounds down to even — libx264 with yuv420p rejects odd dimensions', () => {
    expect(evenDown(1281)).toBe(1280);
    expect(evenDown(1280)).toBe(1280);
    expect(evenDown(721.7)).toBe(720);
  });

  it('never returns a degenerate size', () => {
    expect(evenDown(0)).toBe(2);
    expect(evenDown(-40)).toBe(2);
  });
});

describe('mountVideoStage', () => {
  it('mounts into a shadow root and reuses the player stylesheet verbatim', () => {
    const { host } = mount(manifestWith([{}]));
    const css = host.shadowRoot!.querySelector('style')!.textContent!;
    expect(css).toContain(baseStyles.cssText);
  });

  it('neutralises the wall-clock CSS the timeline replaces', () => {
    const { host } = mount(manifestWith([{}]));
    const css = host.shadowRoot!.querySelector('style')!.textContent!;
    // `animation-delay` cannot scrub a transition, and prefers-reduced-motion
    // would strip the keyframes entirely. Both are disabled and driven inline.
    expect(css).toMatch(/animation:\s*none\s*!important/);
    expect(css).toMatch(/transition:\s*none\s*!important/);
  });

  it('refuses to use the stage before ready()', () => {
    const { stage } = mount(manifestWith([{}]));
    expect(() => stage.seek(0)).toThrow(/ready\(\)/);
  });

  it('mounts one img per distinct capture, never swapping a single src', async () => {
    const m = manifestWith([{ captures: caps(CLICK, 'one.png') }, { captures: caps(CLICK, 'two.png') }]);
    const { host, stage } = mount(m);
    await stage.ready();
    const srcs = Array.from(host.shadowRoot!.querySelectorAll('.steppy-stage img')).map((i) =>
      (i as HTMLImageElement).getAttribute('src'),
    );
    expect(srcs).toEqual(['one.png', 'two.png']);
  });

  it('deduplicates a capture reused across steps', async () => {
    const m = manifestWith([{ captures: caps(CLICK, 'same.png') }, { captures: caps(CLICK, 'same.png') }]);
    const { host, stage } = mount(m);
    await stage.ready();
    expect(host.shadowRoot!.querySelectorAll('.steppy-stage img')).toHaveLength(1);
  });

  it('derives an even canvas from the capture aspect', async () => {
    const { host, stage } = mount(manifestWith([{}]), { width: 1280 });
    stubNatural(host, 1000, 600);
    await stage.ready();
    const { width, height } = stage.size();
    expect(width).toBe(1280);
    expect(width % 2).toBe(0);
    expect(height % 2).toBe(0);
    // 1000x600 capture at 1280 wide -> 768 tall, plus the caption band.
    expect(height).toBeGreaterThan(768);
  });

  it('produces a portrait canvas for a portrait capture — mobile needs no special case', async () => {
    const m = manifestWith([{ captures: caps(CLICK, 'm.png', { width: 390, height: 844 }) }]);
    const { stage } = mount(m, { width: 720 });
    await stage.ready();
    const { width, height } = stage.size();
    expect(height).toBeGreaterThan(width);
  });

  it('shrinks to fit max_height rather than cropping', async () => {
    const { stage } = mount(manifestWith([{}]), { width: 1280, maxHeight: 500 });
    await stage.ready();
    expect(stage.size().height).toBeLessThanOrEqual(500);
  });

  it('omits the caption band when captions are off', async () => {
    const { host, stage } = mount(manifestWith([{}]), { captions: 'off' });
    await stage.ready();
    expect(host.shadowRoot!.querySelector('.steppy-video-band')).toBeNull();
  });

  it('rejects a guide whose captures changed viewport mid-flight', async () => {
    const m = manifestWith([
      { captures: caps(CLICK, 'a.png', { width: 1000, height: 600 }) },
      { captures: caps(CLICK, 'b.png', { width: 800, height: 600 }) },
    ]);
    const { stage } = mount(m);
    await expect(stage.ready()).rejects.toThrow(/recapture/i);
  });

  it('names the missing pair when a locale/layout has no captures', async () => {
    const { stage } = mount(manifestWith([{}]), { locale: 'nl' });
    await expect(stage.ready()).rejects.toThrow(/no captures for locale 'nl'/);
  });

  it('renders on past an unloadable screenshot instead of hanging, and says which', async () => {
    const { stage } = mount(manifestWith([{ captures: caps(CLICK, 'gone.png') }]));
    await stage.ready();
    expect(stage.missingShots()).toEqual(['gone.png']);
    expect(stage.frameCount).toBeGreaterThan(0);
  });

  it('reports nothing missing once an image has decoded', async () => {
    const { host, stage } = mount(manifestWith([{}]));
    for (const img of Array.from(host.shadowRoot!.querySelectorAll('img'))) {
      Object.defineProperty(img, 'complete', { value: true, configurable: true });
      Object.defineProperty(img, 'naturalWidth', { value: 1000, configurable: true });
      Object.defineProperty(img, 'naturalHeight', { value: 600, configurable: true });
    }
    await stage.ready();
    expect(stage.missingShots()).toEqual([]);
  });
});

describe('seek', () => {
  it('reports an unchanged frame so the renderer can skip re-capturing it', async () => {
    const { stage } = mount(manifestWith([{}]), { minStepMs: 9000, fps: 30 });
    await stage.ready();
    const last = stage.frameCount - 1;
    expect(stage.seek(last - 1)).toBe(true); // first paint of the hold
    expect(stage.seek(last)).toBe(false); // identical pixels
  });

  it('paints the caption for the current step', async () => {
    const { host, stage } = mount(manifestWith([{}, {}]));
    await stage.ready();
    stage.seek(0);
    expect(host.shadowRoot!.querySelector('.steppy-video-caption')!.textContent).toBe('Etape 1');
    stage.seek(stage.frameCount - 1);
    expect(host.shadowRoot!.querySelector('.steppy-video-caption')!.textContent).toBe('Etape 2');
  });

  it('toggles the active screenshot by opacity class', async () => {
    const m = manifestWith([{ captures: caps(CLICK, 'one.png') }, { captures: caps(CLICK, 'two.png') }]);
    const { host, stage } = mount(m);
    await stage.ready();
    stage.seek(0);
    const on = () =>
      (host.shadowRoot!.querySelector('.steppy-stage img.on') as HTMLImageElement | null)?.getAttribute('src');
    expect(on()).toBe('one.png');
    stage.seek(stage.frameCount - 1);
    expect(on()).toBe('two.png');
  });

  it('drives the cursor with inline styles, not classes', async () => {
    const { host, stage } = mount(manifestWith([{}]));
    await stage.ready();
    const cursor = host.shadowRoot!.querySelector('.steppy-cursor') as HTMLElement;
    // Part-way through the travel segment.
    stage.seek(2);
    expect(cursor.style.left).toMatch(/px$/);
    expect(cursor.style.transform).toMatch(/^scale\(/);
    expect(cursor.classList.contains('pulse')).toBe(false);
    expect(cursor.classList.contains('visible')).toBe(false);
  });

  it('is order-independent — the same index paints the same DOM', async () => {
    const { host, stage } = mount(manifestWith([{}, {}]));
    await stage.ready();
    const snapshot = (i: number) => {
      stage.seek(i);
      return host.shadowRoot!.innerHTML;
    };
    const target = Math.floor(stage.frameCount / 2);
    const forward = snapshot(target);
    stage.seek(0);
    stage.seek(stage.frameCount - 1);
    expect(snapshot(target)).toBe(forward);
  });

  it('shows the intro card first and the outro card last', async () => {
    const { host, stage } = mount(manifestWith([{}]), { introMs: 1000, outroMs: 1000 });
    await stage.ready();
    const card = host.shadowRoot!.querySelector('.steppy-video-card') as HTMLElement;
    stage.seek(0);
    expect(card.classList.contains('on')).toBe(true);
    expect(card.querySelector('h1')!.textContent).toBe('');
    stage.seek(Math.floor(stage.frameCount / 2));
    expect(card.classList.contains('on')).toBe(false);
    stage.seek(stage.frameCount - 1);
    expect(card.classList.contains('on')).toBe(true);
  });

  it('puts the configured title on the cards', async () => {
    const { host, stage } = mount(manifestWith([{}]), { introMs: 1000, title: 'Invoices' });
    await stage.ready();
    stage.seek(0);
    expect(host.shadowRoot!.querySelector('.steppy-video-card h1')!.textContent).toBe('Invoices');
  });

  it('burns in a provenance stamp when configured', async () => {
    const { host, stage } = mount(manifestWith([{}]), { stamp: 'v2.14 · 2026-08-05' });
    await stage.ready();
    expect(host.shadowRoot!.querySelector('.steppy-video-stamp')!.textContent).toBe('v2.14 · 2026-08-05');
  });

  it('omits the watermark when no logo is configured', async () => {
    const { host, stage } = mount(manifestWith([{}]));
    await stage.ready();
    expect(host.shadowRoot!.querySelector('.steppy-video-watermark')).toBeNull();
  });
});

// ── Visual-parity drift guard ────────────────────────────────────────────────
//
// The video recomputes the cursor pulse and click ripple as inline styles
// instead of letting the stylesheet animate them. That is the only way to make
// a frame deterministic, but it also means the same numbers now live in two
// places. If someone retunes the CSS the video keeps rendering the old motion
// with every suite green — so pin them to each other.

describe('CSS parity', () => {
  const css = baseStyles.cssText;

  function block(name: string): string {
    const i = css.indexOf(`@keyframes ${name}`);
    expect(i, `missing @keyframes ${name}`).toBeGreaterThan(-1);
    return css.slice(i, css.indexOf('}', css.indexOf('}', i) + 1) + 200);
  }

  it('ripple duration matches RIPPLE_MS', () => {
    const m = css.match(/animation:\s*steppy-ripple\s+(\d+)ms/);
    expect(m).toBeTruthy();
    expect(Number(m![1])).toBe(RIPPLE_MS);
  });

  it('ripple scale endpoints match the timeline constants', () => {
    const frames = block('steppy-ripple');
    expect(frames).toContain(`scale(${RIPPLE_SCALE_FROM})`);
    expect(frames).toContain(`scale(${RIPPLE_SCALE_TO})`);
  });

  it('pulse duration matches PULSE_MS', () => {
    const m = css.match(/animation:\s*steppy-pulse\s+(\d+)ms/);
    expect(m).toBeTruthy();
    expect(Number(m![1])).toBe(PULSE_MS);
  });

  it('pulse peak matches PULSE_SCALE_PEAK', () => {
    expect(block('steppy-pulse')).toContain(`scale(${PULSE_SCALE_PEAK})`);
  });

  it('cursor opacity transition matches CURSOR_FADE_MS', () => {
    const m = css.match(/\.steppy-cursor\s*\{[^}]*transition:\s*opacity\s+(\d+)ms/);
    expect(m).toBeTruthy();
    expect(Number(m![1])).toBe(CURSOR_FADE_MS);
  });

  it('the easing functions the video reimplements are still the ones CSS uses', () => {
    expect(css).toMatch(/animation:\s*steppy-ripple\s+\d+ms\s+ease-out/);
    expect(css).toMatch(/animation:\s*steppy-pulse\s+\d+ms\s+ease-in-out/);
  });
});
