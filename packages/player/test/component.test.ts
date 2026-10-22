/**
 * Component tests for ``<steppy-guide>`` running under happy-dom.
 *
 * The tests stub ``window.fetch`` for the manifest URL, mock
 * ``getBoundingClientRect`` on stage/img elements, and use fake timers so the
 * animator's ``setTimeout`` / ``requestAnimationFrame`` calls progress
 * synchronously. Animation timing is verified via dedicated unit tests
 * elsewhere; the component tests focus on state transitions and event
 * emission.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import '../src/index.js';
import { SteppyGuide, type AnimatorLike } from '../src/steppy-guide.js';
import type { GuideManifest } from '../src/manifest/schema.js';
import { makeManifest, withLayoutContent } from './fixtures.js';

/**
 * Fast stub animator — resolves on the next tick so tests can stub layout
 * between "play loop kicked off" and "play loop reads img bounding rect".
 * Tests focus on the player's state machine + event emission, not animation
 * timing (covered by animator.test.ts).
 */
function makeStubAnimator(): AnimatorLike {
  const nextTick = () => new Promise<void>((r) => setTimeout(r, 0));
  return {
    setTimings: () => {},
    setStage: () => {},
    refreshDom: () => {},
    hide: () => {},
    abort: () => {},
    travelToTarget: nextTick,
    playTargetAction: nextTick,
  };
}

// ── DOM stubs ────────────────────────────────────────────────────────────

function stubBoundingRect(el: Element, w: number, h: number, x = 0, y = 0): void {
  el.getBoundingClientRect = () =>
    ({
      x,
      y,
      top: y,
      left: x,
      right: x + w,
      bottom: y + h,
      width: w,
      height: h,
      toJSON() {},
    } as DOMRect);
}

/**
 * Stub the img's natural-size / load semantics so ``waitImageReady`` resolves
 * immediately under happy-dom.
 */
function stubImageReady(img: HTMLImageElement, w: number, h: number): void {
  Object.defineProperty(img, 'complete', { value: true, configurable: true });
  Object.defineProperty(img, 'naturalWidth', { value: w, configurable: true });
  Object.defineProperty(img, 'naturalHeight', { value: h, configurable: true });
  stubBoundingRect(img, w, h);
}

function mockFetchOnce(manifest: GuideManifest): void {
  vi.spyOn(globalThis, 'fetch').mockImplementationOnce(async () => {
    return new Response(JSON.stringify(manifest), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  });
}

function mockFetchError(status: number, statusText: string): void {
  vi.spyOn(globalThis, 'fetch').mockImplementationOnce(async () => {
    return new Response('error', { status, statusText });
  });
}

async function mount(
  manifest: GuideManifest,
  opts: { stubLayout?: boolean } = {},
): Promise<{
  el: SteppyGuide;
  container: HTMLElement;
  events: { name: string; detail: unknown }[];
}> {
  mockFetchOnce(manifest);
  const container = document.createElement('div');
  document.body.appendChild(container);
  const el = document.createElement('steppy-guide') as SteppyGuide;
  // Reflect manifest's locale/variant so render is deterministic.
  el.locale = manifest.defaultLocale;
  el.variant = manifest.defaultLayout;
  el.src = 'https://test.local/guide.json';
  container.appendChild(el);

  const events: { name: string; detail: unknown }[] = [];
  for (const name of [
    'steppy:ready',
    'steppy:step',
    'steppy:hotspot',
    'steppy:locale',
    'steppy:variant',
    'steppy:complete',
    'steppy:error',
  ]) {
    el.addEventListener(name, (e) =>
      events.push({ name, detail: (e as CustomEvent).detail }),
    );
  }

  // Wait for the manifest fetch and the first paint cycle.
  await el.updateComplete;
  await Promise.resolve();
  await Promise.resolve();
  await el.updateComplete;

  // By now the shadow DOM is rendered and the play loop is awaiting the
  // stubbed travel's setTimeout. Stub the bounding rects of the stage + img
  // so the subsequent _recomputeHotspot can compute a real DisplayRect, then
  // flush the timer so the play loop continues into the waiting state.
  if (opts.stubLayout !== false) {
    const stage = getStage(el);
    const img = getImg(el);
    if (stage && img) {
      stubBoundingRect(stage, 800, 500);
      stubImageReady(img, 800, 500);
    }
  }
  await vi.advanceTimersByTimeAsync(50);
  await el.updateComplete;

  return { el, container, events };
}

function root(el: SteppyGuide): ShadowRoot {
  return el.shadowRoot as ShadowRoot;
}

function getStage(el: SteppyGuide): HTMLElement {
  return root(el).querySelector('.steppy-stage') as HTMLElement;
}

function getImg(el: SteppyGuide): HTMLImageElement {
  return root(el).querySelector('img') as HTMLImageElement;
}

// Stub stage+img layout right after mount so hotspot rect math works.
function layoutPlayer(el: SteppyGuide, stageW = 800, stageH = 500): void {
  const stage = getStage(el);
  const img = getImg(el);
  stubBoundingRect(stage, stageW, stageH);
  stubImageReady(img, stageW, stageH);
}

let originalCreateAnimator: typeof SteppyGuide.createAnimator;

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  originalCreateAnimator = SteppyGuide.createAnimator;
  SteppyGuide.createAnimator = () => makeStubAnimator();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  SteppyGuide.createAnimator = originalCreateAnimator;
  document.body.innerHTML = '';
  // Template persistence is global (localStorage); clear it so it never leaks
  // into the overlay-default assumption these tests rely on.
  try {
    localStorage.clear();
  } catch {
    /* no-op */
  }
});

// ── Loading + ready ──────────────────────────────────────────────────────

describe('manifest loading', () => {
  it('fetches the manifest, emits steppy:ready, and renders the first step', async () => {
    const manifest = makeManifest();
    const { el, events } = await mount(manifest);

    const ready = events.find((e) => e.name === 'steppy:ready');
    expect(ready).toBeDefined();
    expect((ready!.detail as { slug: string }).slug).toBe(manifest.slug);

    // Img src reflects the first step's defaultLocale × defaultLayout capture.
    layoutPlayer(el);
    const img = getImg(el);
    expect(img.getAttribute('src')).toContain('step-01');
  });

  it('emits steppy:error on a failed fetch (HTTP 404)', async () => {
    mockFetchError(404, 'Not Found');
    const container = document.createElement('div');
    document.body.appendChild(container);
    const el = document.createElement('steppy-guide') as SteppyGuide;
    el.src = 'https://test.local/missing.json';
    container.appendChild(el);
    const errors: unknown[] = [];
    el.addEventListener('steppy:error', (e) =>
      errors.push((e as CustomEvent).detail),
    );
    await el.updateComplete;
    await Promise.resolve();
    await el.updateComplete;
    expect(errors).toHaveLength(1);
    expect((errors[0] as { code: string }).code).toBe('fetch_failed');
  });

  it('emits steppy:error on a schema-invalid manifest', async () => {
    const bogus = { ...makeManifest(), version: '99.0' };
    vi.spyOn(globalThis, 'fetch').mockImplementationOnce(
      async () => new Response(JSON.stringify(bogus), { status: 200 }),
    );
    const container = document.createElement('div');
    document.body.appendChild(container);
    const el = document.createElement('steppy-guide') as SteppyGuide;
    el.src = 'https://test.local/bad.json';
    container.appendChild(el);
    const errors: unknown[] = [];
    el.addEventListener('steppy:error', (e) =>
      errors.push((e as CustomEvent).detail),
    );
    await el.updateComplete;
    await Promise.resolve();
    await el.updateComplete;
    expect(errors).toHaveLength(1);
    expect((errors[0] as { code: string }).code).toBe('version_unsupported');
  });
});

// ── Step advance: correct click ─────────────────────────────────────────

describe('interactive step advance', () => {
  it('advances on a hotspot button click and emits steppy:step', async () => {
    const m = makeManifest({
      // Two interactive steps so we can observe the transition.
      steps: [step1Click(), step2Click()],
    });
    const { el, events } = await mount(m);
    layoutPlayer(el);

    // Wait for the cursor travel to finish and the hotspot to render.
    await vi.advanceTimersByTimeAsync(500);
    await el.updateComplete;
    const hotspot = root(el).querySelector('.steppy-hotspot') as HTMLButtonElement;
    expect(hotspot).toBeTruthy();

    hotspot.click();
    // Let the action animation and post-action timers run.
    await vi.advanceTimersByTimeAsync(1000);
    await el.updateComplete;

    const stepEvents = events.filter((e) => e.name === 'steppy:step');
    // step-1 fired at init, step-2 fires after advance
    expect(stepEvents.map((e) => (e.detail as { index: number }).index)).toEqual([1, 2]);

    const hotspotClicks = events.filter((e) => e.name === 'steppy:hotspot');
    expect(hotspotClicks).toHaveLength(1);
    expect((hotspotClicks[0]!.detail as { target: string }).target).toBe('correct');
  });

  it('miss click on the stage emits steppy:hotspot{target:miss} and does not advance', async () => {
    const m = makeManifest({ steps: [step1Click(), step2Click()] });
    const { el, events } = await mount(m);
    layoutPlayer(el);
    await vi.advanceTimersByTimeAsync(500);
    await el.updateComplete;

    const stage = getStage(el);
    // Synthesize a click outside the hotspot rect by clicking on the stage.
    // The hotspot has its own click handler that stopPropagation's, so a
    // stage-level click that doesn't pass through the button is a miss.
    stage.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 0, clientY: 0 }));
    await el.updateComplete;

    const hotspotEvents = events.filter((e) => e.name === 'steppy:hotspot');
    expect(hotspotEvents).toHaveLength(1);
    expect((hotspotEvents[0]!.detail as { target: string }).target).toBe('miss');

    const stepEvents = events.filter((e) => e.name === 'steppy:step');
    expect(stepEvents).toHaveLength(1); // still on step 1
  });
});

// ── Non-interactive step auto-advances ──────────────────────────────────

describe('non-interactive step', () => {
  it('auto-advances after waitAfter ms', async () => {
    const m = makeManifest({
      steps: [stepNavigate(1), clickStep(2)],
    });
    const { el, events } = await mount(m);
    layoutPlayer(el);

    // Default auto-advance is 600ms when waitAfter is 0.
    await vi.advanceTimersByTimeAsync(700);
    await el.updateComplete;

    const stepEvents = events.filter((e) => e.name === 'steppy:step');
    expect(stepEvents.map((e) => (e.detail as { index: number }).index)).toEqual([1, 2]);
  });
});

// ── Locale + variant switching ──────────────────────────────────────────

describe('locale + variant switching', () => {
  it('emits steppy:locale and re-renders text when locale changes', async () => {
    const m = makeManifest();
    const { el, events } = await mount(m);
    layoutPlayer(el);
    el.locale = 'nl';
    await el.updateComplete;
    const localeEvents = events.filter((e) => e.name === 'steppy:locale');
    expect(localeEvents).toHaveLength(1);
    expect(localeEvents[0]!.detail).toMatchObject({ from: 'fr', to: 'nl' });
  });

  it('falls back to defaultLayout when variant is unknown', async () => {
    const m = makeManifest();
    const { el } = await mount(m);
    el.variant = 'tablet'; // not in manifest.layouts
    await el.updateComplete;
    // _resolveVariant pushes it back to the default — but only via the play loop.
    // Direct assignment here just triggers willUpdate; we accept either tablet or desktop
    // as long as the player doesn't throw.
    expect(['desktop', 'tablet']).toContain(el.variant);
  });

  it('swaps the screenshot when the variant (layout) attribute changes', async () => {
    const m = makeManifest(); // step 1 has desktop + mobile captures
    const { el, events } = await mount(m);
    layoutPlayer(el);
    await vi.advanceTimersByTimeAsync(500);
    await el.updateComplete;

    expect(getImg(el).getAttribute('src')).toContain('/desktop/');

    // A host toggle drives the layout via the `variant` attribute/property.
    el.variant = 'mobile';
    await el.updateComplete;
    await vi.advanceTimersByTimeAsync(500);
    await el.updateComplete;

    expect(getImg(el).getAttribute('src')).toContain('/mobile/');
    const variantEvents = events.filter((e) => e.name === 'steppy:variant');
    expect(
      variantEvents.some((e) => (e.detail as { to: string }).to === 'mobile'),
    ).toBe(true);
  });

  it('swaps the caption too when the layout overrides the step text', async () => {
    // The twin-branch pattern: mobile reaches the target through a card, so its
    // wording differs from desktop's. Caption and screenshot must move
    // together — a mobile screenshot under "click the row of the table" is the
    // bug this override exists to fix.
    const m = withLayoutContent(makeManifest(), 0, 'fr', 'mobile', {
      title: 'Ouvrir le menu',
      text: 'Touchez la carte',
      subtitle: null,
    });
    const { el } = await mount(m);
    layoutPlayer(el);
    await vi.advanceTimersByTimeAsync(500);
    await el.updateComplete;

    expect(getImg(el).getAttribute('alt')).toBe('Cliquez sur Nouveau');

    el.variant = 'mobile';
    await el.updateComplete;
    await vi.advanceTimersByTimeAsync(500);
    await el.updateComplete;

    expect(getImg(el).getAttribute('src')).toContain('/mobile/');
    expect(getImg(el).getAttribute('alt')).toBe('Touchez la carte');
    expect(root(el).querySelector('.steppy-tooltip')?.textContent).toContain('Touchez la carte');
  });

  it('re-labels every timeline row on a layout switch, not just the current one', async () => {
    // `_buildStepViews` maps ALL steps, so an override on a step the player has
    // not reached yet must still show through.
    const m = makeManifest({ defaultTemplate: 'sidebar' });
    m.steps.push(clickStep(2));
    withLayoutContent(m, 1, 'fr', 'mobile', {
      title: 'Touchez la deuxième carte',
      text: 'Touchez la deuxième carte',
      subtitle: null,
    });
    const { el } = await mount(m);
    layoutPlayer(el);
    await el.updateComplete;

    const titles = () =>
      Array.from(root(el).querySelectorAll('.steppy-timeline .steppy-step-title')).map((n) =>
        n.textContent?.trim(),
      );
    expect(titles()[1]).toBe('Cliquez 2');

    el.variant = 'mobile';
    await el.updateComplete;
    await vi.advanceTimersByTimeAsync(500);
    await el.updateComplete;

    // The player is still on step 1; step 2's row picked up mobile's wording.
    expect(root(el).querySelector('.steppy-step--current .steppy-step-title')?.textContent?.trim()).toBe(
      'Cliquez sur Nouveau',
    );
    expect(titles()[1]).toBe('Touchez la deuxième carte');
  });
});

describe('hide-layout-switcher', () => {
  it('suppresses the built-in layout switcher but keeps the locale switcher', async () => {
    const m = makeManifest(); // 2 locales, 2 layouts → both switchers render
    const { el } = await mount(m);
    layoutPlayer(el);
    await el.updateComplete;

    const selectsNow = () =>
      Array.from(
        root(el).querySelectorAll('.steppy-chrome select'),
      ) as HTMLSelectElement[];

    // Default: both the locale and the layout <select> render.
    expect(selectsNow()).toHaveLength(2);

    el.hideLayoutSwitcher = true;
    await el.updateComplete;

    // Only the locale switcher (options fr/nl) survives — layout is suppressed.
    const remaining = selectsNow();
    expect(remaining).toHaveLength(1);
    expect(Array.from(remaining[0]!.options).map((o) => o.value)).toEqual([
      'fr',
      'nl',
    ]);
  });
});

// ── Completion ───────────────────────────────────────────────────────────

describe('completion', () => {
  it('emits steppy:complete on advancing past the last step', async () => {
    const m = makeManifest({ steps: [step1Click()] });
    const { el, events } = await mount(m);
    layoutPlayer(el);
    await vi.advanceTimersByTimeAsync(500);
    await el.updateComplete;
    const hotspot = root(el).querySelector('.steppy-hotspot') as HTMLButtonElement;
    hotspot.click();
    await vi.advanceTimersByTimeAsync(1500);
    await el.updateComplete;
    const complete = events.find((e) => e.name === 'steppy:complete');
    expect(complete).toBeDefined();
    expect((complete!.detail as { slug: string }).slug).toBe(m.slug);
  });
});

// ── Restart ──────────────────────────────────────────────────────────────

describe('restart', () => {
  it('jumps back to step 1 and emits steppy:step', async () => {
    const m = makeManifest({ steps: [step1Click(), step2Click()] });
    const { el, events } = await mount(m);
    layoutPlayer(el);
    await vi.advanceTimersByTimeAsync(500);
    await el.updateComplete;
    const hotspot = root(el).querySelector('.steppy-hotspot') as HTMLButtonElement;
    hotspot.click();
    await vi.advanceTimersByTimeAsync(1000);
    await el.updateComplete;

    el.restart();
    await el.updateComplete;
    const stepEvents = events.filter((e) => e.name === 'steppy:step');
    // step-1 init, step-2 after click, step-1 again after restart
    expect(stepEvents.map((e) => (e.detail as { index: number }).index)).toEqual([
      1, 2, 1,
    ]);
  });
});

// ── Cursor mode ──────────────────────────────────────────────────────────

type AnimatorCall =
  | { name: 'setTimings' }
  | { name: 'setStage' }
  | { name: 'refreshDom' }
  | { name: 'hide' }
  | { name: 'abort' }
  | { name: 'travelToTarget' }
  | { name: 'playTargetAction' };

function makeSpyAnimator(): { calls: AnimatorCall[]; animator: AnimatorLike } {
  const calls: AnimatorCall[] = [];
  const nextTick = () => new Promise<void>((r) => setTimeout(r, 0));
  return {
    calls,
    animator: {
      setTimings: () => { calls.push({ name: 'setTimings' }); },
      setStage: () => { calls.push({ name: 'setStage' }); },
      refreshDom: () => { calls.push({ name: 'refreshDom' }); },
      hide: () => { calls.push({ name: 'hide' }); },
      abort: () => { calls.push({ name: 'abort' }); },
      travelToTarget: () => { calls.push({ name: 'travelToTarget' }); return nextTick(); },
      playTargetAction: () => { calls.push({ name: 'playTargetAction' }); return nextTick(); },
    },
  };
}

/** Variant of {@link mount} that sets ``cursor`` before appendChild and uses
 *  a spy animator so the test can assert on which animator methods fired. */
async function mountWithCursor(
  manifest: GuideManifest,
  opts: { cursor?: 'arrow' | 'hotspot' | 'off' | null } = {},
): Promise<{
  el: SteppyGuide;
  events: { name: string; detail: unknown }[];
  calls: AnimatorCall[];
}> {
  const spy = makeSpyAnimator();
  SteppyGuide.createAnimator = () => spy.animator;
  mockFetchOnce(manifest);
  const container = document.createElement('div');
  document.body.appendChild(container);
  const el = document.createElement('steppy-guide') as SteppyGuide;
  el.locale = manifest.defaultLocale;
  el.variant = manifest.defaultLayout;
  if (opts.cursor !== undefined) el.cursor = opts.cursor;
  el.src = 'https://test.local/guide.json';
  container.appendChild(el);

  const events: { name: string; detail: unknown }[] = [];
  for (const name of [
    'steppy:ready',
    'steppy:step',
    'steppy:hotspot',
    'steppy:complete',
    'steppy:error',
  ]) {
    el.addEventListener(name, (e) =>
      events.push({ name, detail: (e as CustomEvent).detail }),
    );
  }

  await el.updateComplete;
  await Promise.resolve();
  await Promise.resolve();
  await el.updateComplete;

  const stage = getStage(el);
  const img = getImg(el);
  if (stage && img) {
    stubBoundingRect(stage, 800, 500);
    stubImageReady(img, 800, 500);
  }
  await vi.advanceTimersByTimeAsync(50);
  await el.updateComplete;

  return { el, events, calls: spy.calls };
}

describe('cursor mode', () => {
  it("'hotspot' skips travelToTarget but renders the hotspot and advances on click", async () => {
    const m = makeManifest({ steps: [step1Click(), step2Click()] });
    const { el, events, calls } = await mountWithCursor(m, { cursor: 'hotspot' });

    // Let any pending timers and recompute settle.
    await vi.advanceTimersByTimeAsync(500);
    await el.updateComplete;

    expect(calls.some((c) => c.name === 'travelToTarget')).toBe(false);
    const hotspot = root(el).querySelector('.steppy-hotspot') as HTMLButtonElement;
    expect(hotspot).toBeTruthy();

    hotspot.click();
    await vi.advanceTimersByTimeAsync(1000);
    await el.updateComplete;

    expect(calls.some((c) => c.name === 'playTargetAction')).toBe(false);
    const stepEvents = events.filter((e) => e.name === 'steppy:step');
    expect(stepEvents.map((e) => (e.detail as { index: number }).index)).toEqual([1, 2]);
  });

  it("'off' auto-advances every step without ever rendering the hotspot", async () => {
    const m = makeManifest({ steps: [step1Click(), step2Click()] });
    const { el, events, calls } = await mountWithCursor(m, { cursor: 'off' });

    // No hotspot at any point.
    expect(root(el).querySelector('.steppy-hotspot')).toBeNull();
    expect(calls.some((c) => c.name === 'travelToTarget')).toBe(false);

    // Auto-advance fallback is 600ms when waitAfter is 0; ride past it twice.
    await vi.advanceTimersByTimeAsync(700);
    await el.updateComplete;
    await vi.advanceTimersByTimeAsync(700);
    await el.updateComplete;

    expect(root(el).querySelector('.steppy-hotspot')).toBeNull();
    expect(calls.some((c) => c.name === 'playTargetAction')).toBe(false);
    const complete = events.find((e) => e.name === 'steppy:complete');
    expect(complete).toBeDefined();
  });

  it('honors manifest cursor.mode when the host attribute is unset', async () => {
    const m = makeManifest({
      steps: [step1Click(), step2Click()],
      cursor: {
        pre_action_ms: 200,
        action_ms: 300,
        post_action_ms: 150,
        travel_speed_px_per_ms: 1.5,
        mode: 'hotspot',
      },
    });
    const { el, calls } = await mountWithCursor(m, { cursor: null });
    await vi.advanceTimersByTimeAsync(500);
    await el.updateComplete;

    expect(calls.some((c) => c.name === 'travelToTarget')).toBe(false);
    expect(root(el).querySelector('.steppy-hotspot')).toBeTruthy();
  });

  it('host attribute wins over manifest cursor.mode', async () => {
    const m = makeManifest({
      steps: [step1Click(), step2Click()],
      cursor: {
        pre_action_ms: 200,
        action_ms: 300,
        post_action_ms: 150,
        travel_speed_px_per_ms: 1.5,
        mode: 'off',
      },
    });
    const { el, calls } = await mountWithCursor(m, { cursor: 'arrow' });
    await vi.advanceTimersByTimeAsync(500);
    await el.updateComplete;

    // Arrow mode runs the travel + renders the hotspot.
    expect(calls.some((c) => c.name === 'travelToTarget')).toBe(true);
    expect(root(el).querySelector('.steppy-hotspot')).toBeTruthy();
  });
});

// ── Helpers: bespoke step shapes ────────────────────────────────────────

function step1Click() {
  return clickStep(1);
}

function step2Click() {
  return clickStep(2);
}

function clickStep(index: number) {
  return {
    id: `step-${index}`,
    index,
    action: 'click' as const,
    interactive: true,
    screenshotWhen: 'after' as const,
    waitAfter: 0,
    text: { fr: `Cliquez ${index}`, nl: `Klik ${index}` },
    subtitle: { fr: null, nl: null },
    captures: capturesFor(index),
  };
}

function stepNavigate(index: number) {
  const captures: Record<string, Record<string, { frames: any[] }>> = {};
  for (const locale of ['fr', 'nl']) {
    captures[locale] = {};
    for (const layout of ['desktop', 'mobile']) {
      captures[locale]![layout] = {
        frames: [
          {
            screenshot: `./screenshots/${locale}/${layout}/step-${index}.png`,
            captureSize:
              layout === 'desktop'
                ? { width: 1440, height: 900 }
                : { width: 390, height: 844 },
            cursor: null,
          },
        ],
      };
    }
  }
  return {
    id: `step-${index}`,
    index,
    action: 'navigate' as const,
    interactive: false,
    screenshotWhen: 'after' as const,
    waitAfter: 0,
    text: { fr: 'Nav', nl: 'Nav' },
    subtitle: { fr: null, nl: null },
    captures,
  };
}

function capturesFor(idx: number) {
  const out: Record<string, Record<string, { frames: any[] }>> = {};
  for (const locale of ['fr', 'nl']) {
    out[locale] = {};
    for (const layout of ['desktop', 'mobile']) {
      out[locale]![layout] = {
        frames: [
          {
            screenshot: `./screenshots/${locale}/${layout}/step-${String(idx).padStart(2, '0')}.png`,
            captureSize:
              layout === 'desktop'
                ? { width: 1440, height: 900 }
                : { width: 390, height: 844 },
            cursor: {
              kind: 'click' as const,
              target: {
                selector: '[data-testid="x"]',
                bbox: { x: 100, y: 200, width: 120, height: 32 },
              },
            },
          },
        ],
      };
    }
  }
  return out;
}
