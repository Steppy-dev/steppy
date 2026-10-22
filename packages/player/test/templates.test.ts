/**
 * Tests for the presentation-template layer: runtime switching, resolution
 * precedence (attr → persisted → manifest default → overlay), state
 * preservation across a switch, and per-template chrome.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SteppyGuide, type AnimatorLike } from '../src/steppy-guide.js';
import type { GuideManifest } from '../src/manifest/schema.js';
import { ALL_TEMPLATES, TEMPLATES, isTemplateId, templateStyles } from '../src/templates/index.js';
import { makeManifest } from './fixtures.js';

// ── Animator stub (mirrors component.test.ts) ────────────────────────────

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
    ({ x, y, top: y, left: x, right: x + w, bottom: y + h, width: w, height: h, toJSON() {} }) as DOMRect;
}

function stubImageReady(img: HTMLImageElement, w: number, h: number): void {
  Object.defineProperty(img, 'complete', { value: true, configurable: true });
  Object.defineProperty(img, 'naturalWidth', { value: w, configurable: true });
  Object.defineProperty(img, 'naturalHeight', { value: h, configurable: true });
  stubBoundingRect(img, w, h);
}

function mockFetchOnce(manifest: GuideManifest): void {
  vi.spyOn(globalThis, 'fetch').mockImplementationOnce(
    async () =>
      new Response(JSON.stringify(manifest), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
  );
}

// ── Manifest builders ────────────────────────────────────────────────────

const BASE_STEP = makeManifest().steps[0]!;

function clickStep(i: number): GuideManifest['steps'][number] {
  const captures: GuideManifest['steps'][number]['captures'] = {};
  for (const loc of ['fr', 'nl']) {
    captures[loc] = {};
    for (const lay of ['desktop', 'mobile']) {
      const f = BASE_STEP.captures[loc]![lay]!.frames[0]!;
      captures[loc]![lay] = {
        frames: [{ ...f, screenshot: `./s/${loc}/${lay}/step-0${i}.png` }],
      };
    }
  }
  return {
    ...BASE_STEP,
    id: `step-${i}`,
    index: i,
    title: { fr: `Titre ${i}`, nl: `Titel ${i}` },
    text: { fr: `Description ${i}`, nl: `Beschrijving ${i}` },
    subtitle: { fr: `champ ${i}`, nl: `veld ${i}` },
    captures,
  };
}

function manifestWith(
  steps: number,
  overrides: Partial<GuideManifest> = {},
): GuideManifest {
  return makeManifest({
    steps: Array.from({ length: steps }, (_, i) => clickStep(i + 1)),
    ...overrides,
  });
}

// ── Mount helper ─────────────────────────────────────────────────────────

function root(el: SteppyGuide): ShadowRoot {
  return el.shadowRoot as ShadowRoot;
}
function getStage(el: SteppyGuide): HTMLElement | null {
  return root(el).querySelector('.steppy-stage');
}
function getImg(el: SteppyGuide): HTMLImageElement | null {
  return root(el).querySelector('img');
}
function layoutPlayer(el: SteppyGuide): void {
  const stage = getStage(el);
  const img = getImg(el);
  if (stage) stubBoundingRect(stage, 800, 500);
  if (img) stubImageReady(img, 800, 500);
}

async function mount(manifest: GuideManifest): Promise<{
  el: SteppyGuide;
  events: { name: string; detail: unknown }[];
}> {
  mockFetchOnce(manifest);
  const container = document.createElement('div');
  document.body.appendChild(container);
  const el = document.createElement('steppy-guide') as SteppyGuide;
  el.locale = manifest.defaultLocale;
  el.variant = manifest.defaultLayout;
  el.src = 'https://test.local/guide.json';
  container.appendChild(el);

  const events: { name: string; detail: unknown }[] = [];
  for (const name of ['steppy:ready', 'steppy:step', 'steppy:complete', 'steppy:variant']) {
    el.addEventListener(name, (e) => events.push({ name, detail: (e as CustomEvent).detail }));
  }

  await el.updateComplete;
  await Promise.resolve();
  await Promise.resolve();
  await el.updateComplete;
  layoutPlayer(el);
  await vi.advanceTimersByTimeAsync(50);
  await el.updateComplete;
  return { el, events };
}

/** Re-stub the (possibly recreated) stage/img after a template switch and
 *  re-fire the img load so the hotspot recomputes — happy-dom fires neither the
 *  ResizeObserver nor the load event on its own. */
async function relayoutAfterSwitch(el: SteppyGuide): Promise<void> {
  layoutPlayer(el);
  getImg(el)?.dispatchEvent(new Event('load'));
  await el.updateComplete;
}

let originalCreateAnimator: typeof SteppyGuide.createAnimator;
let originalLocalStorage: PropertyDescriptor | undefined;

/** happy-dom does not provide a functional localStorage here; install a simple
 *  in-memory stub so the persistence path (which the component try/catches) is
 *  actually exercised. */
function installLocalStorage(): void {
  const store = new Map<string, string>();
  const ls = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size;
    },
  };
  originalLocalStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', {
    value: ls,
    configurable: true,
    writable: true,
  });
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  originalCreateAnimator = SteppyGuide.createAnimator;
  SteppyGuide.createAnimator = () => makeStubAnimator();
  installLocalStorage();
  localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  SteppyGuide.createAnimator = originalCreateAnimator;
  document.body.innerHTML = '';
  localStorage.clear();
  if (originalLocalStorage) {
    Object.defineProperty(globalThis, 'localStorage', originalLocalStorage);
  } else {
    delete (globalThis as { localStorage?: unknown }).localStorage;
  }
});

// ── Resolution precedence ────────────────────────────────────────────────

describe('template resolution', () => {
  it('defaults to overlay (no attr / persisted / manifest default)', async () => {
    const { el } = await mount(manifestWith(2));
    expect(root(el).querySelector('.steppy-tpl-overlay')).toBeTruthy();
    expect(root(el).querySelector('.steppy-chrome')).toBeTruthy();
  });

  it('honours manifest defaultTemplate', async () => {
    const { el } = await mount(manifestWith(2, { defaultTemplate: 'sidebar' }));
    expect(root(el).querySelector('.steppy-tpl-sidebar')).toBeTruthy();
  });

  it('persisted value beats the manifest default', async () => {
    localStorage.setItem('steppy:active-template', 'inline');
    const { el } = await mount(manifestWith(2, { defaultTemplate: 'sidebar' }));
    expect(root(el).querySelector('.steppy-tpl-inline')).toBeTruthy();
  });

  it('explicit attribute beats persisted value', async () => {
    localStorage.setItem('steppy:active-template', 'inline');
    mockFetchOnce(manifestWith(2, { defaultTemplate: 'sidebar' }));
    const container = document.createElement('div');
    document.body.appendChild(container);
    const el = document.createElement('steppy-guide') as SteppyGuide;
    el.setAttribute('template', 'overlay');
    el.src = 'https://test.local/guide.json';
    container.appendChild(el);
    await el.updateComplete;
    await Promise.resolve();
    await el.updateComplete;
    expect(root(el).querySelector('.steppy-tpl-overlay')).toBeTruthy();
  });

  it('switching persists the new template to localStorage', async () => {
    const { el } = await mount(manifestWith(2));
    el.template = 'inline';
    await el.updateComplete;
    expect(localStorage.getItem('steppy:active-template')).toBe('inline');
  });
});

// ── Switching preserves engine state ─────────────────────────────────────

describe('template switch preserves state', () => {
  it('switches the DOM without resetting the current step', async () => {
    const { el, events } = await mount(manifestWith(3));
    // Advance to step 2 by clicking the highlighted hotspot.
    const hotspot = root(el).querySelector('.steppy-hotspot') as HTMLButtonElement;
    expect(hotspot).toBeTruthy();
    hotspot.click();
    await vi.advanceTimersByTimeAsync(1000);
    await el.updateComplete;

    const stepsBefore = events.filter((e) => e.name === 'steppy:step').length;

    el.template = 'sidebar';
    await el.updateComplete;
    await relayoutAfterSwitch(el);

    // No new steppy:step fired — the switch is a pure re-render.
    expect(events.filter((e) => e.name === 'steppy:step').length).toBe(stepsBefore);
    expect(root(el).querySelector('.steppy-tpl-sidebar')).toBeTruthy();
    // The current step is still step 2 (its row is the active one).
    const current = root(el).querySelector('.steppy-step--current .steppy-step-title');
    expect(current?.textContent?.trim()).toBe('Titre 2');
    // Stage survived (same screenshot for the current step).
    expect(getImg(el)?.getAttribute('src')).toContain('step-02');
  });

  it('keeps the hotspot interactive across a switch', async () => {
    const { el, events } = await mount(manifestWith(2));
    expect(root(el).querySelector('.steppy-hotspot')).toBeTruthy();

    el.template = 'inline';
    await el.updateComplete;
    await relayoutAfterSwitch(el);

    const hotspot = root(el).querySelector('.steppy-hotspot') as HTMLButtonElement;
    expect(hotspot).toBeTruthy();
    hotspot.click();
    await vi.advanceTimersByTimeAsync(1000);
    await el.updateComplete;
    expect(events.some((e) => e.name === 'steppy:step' && (e.detail as { index: number }).index === 2)).toBe(true);
  });

  it('switching template does not change the layout variant', async () => {
    const { el } = await mount(manifestWith(2));
    expect(el.variant).toBe('desktop');
    el.template = 'sidebar';
    await el.updateComplete;
    expect(el.variant).toBe('desktop');
  });
});

// ── Per-template chrome ──────────────────────────────────────────────────

describe('per-template chrome', () => {
  it('sidebar renders the timeline and the current description', async () => {
    const { el } = await mount(manifestWith(3, { defaultTemplate: 'sidebar' }));
    const rows = root(el).querySelectorAll('.steppy-timeline .steppy-step');
    expect(rows.length).toBe(3);
    const desc = root(el).querySelector('.steppy-step-desc');
    expect(desc?.textContent?.trim()).toBe('Description 1');
  });

  it('inline renders the current description in the bottom bar', async () => {
    const { el } = await mount(manifestWith(2, { defaultTemplate: 'inline' }));
    const desc = root(el).querySelector('.steppy-inline-desc');
    expect(desc?.textContent?.trim()).toBe('Description 1');
  });

  it('sidebar scrolls the active timeline row into view on advance', async () => {
    const scrollIntoView = vi.fn();
    vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(scrollIntoView);
    const { el } = await mount(manifestWith(12, { defaultTemplate: 'sidebar' }));
    scrollIntoView.mockClear();  // ignore whatever the initial render did

    el.next();
    await vi.advanceTimersByTimeAsync(1000);
    await el.updateComplete;

    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
    // The row it scrolled to is the new current step, not the one we left.
    const current = root(el).querySelector('.steppy-step--current .steppy-step-title');
    expect(current?.textContent?.trim()).toBe('Titre 2');
  });

  it('does not scroll a timeline row in templates that have none', async () => {
    const scrollIntoView = vi.fn();
    vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(scrollIntoView);
    const { el } = await mount(manifestWith(12, { defaultTemplate: 'overlay' }));
    scrollIntoView.mockClear();

    el.next();
    await vi.advanceTimersByTimeAsync(1000);
    await el.updateComplete;

    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it('Next advances on an interactive step in every template', async () => {
    for (const tpl of ['overlay', 'sidebar', 'inline'] as const) {
      const { el, events } = await mount(manifestWith(2, { defaultTemplate: tpl }));
      // Still waiting for the hotspot click; Next should advance anyway.
      el.next();
      await vi.advanceTimersByTimeAsync(1000);
      await el.updateComplete;
      expect(
        events.some((e) => e.name === 'steppy:step' && (e.detail as { index: number }).index === 2),
      ).toBe(true);
      document.body.innerHTML = '';
    }
  });
});

// ── Inline-markdown emphasis in body text ────────────────────────────────

describe('markdown emphasis in body text', () => {
  /** A manifest whose first step carries **bold** markdown in title + text. */
  function boldManifest(defaultTemplate: GuideManifest['defaultTemplate']): GuideManifest {
    const m = manifestWith(2, { defaultTemplate });
    m.steps[0] = {
      ...m.steps[0]!,
      title: { fr: 'Titre **un**', nl: 'Titel **een**' },
      text: { fr: 'Cliquez sur **Créer**', nl: 'Klik op **Maak**' },
    };
    return m;
  }

  it('overlay tooltip renders bold as <strong>, not literal asterisks', async () => {
    const { el } = await mount(boldManifest('overlay'));
    const tooltip = root(el).querySelector('.steppy-tooltip');
    expect(tooltip).toBeTruthy();
    expect(tooltip!.querySelector('strong')?.textContent).toBe('Créer');
    expect(tooltip!.textContent).not.toContain('**');
  });

  it('sidebar renders bold in the description and title', async () => {
    const { el } = await mount(boldManifest('sidebar'));
    const desc = root(el).querySelector('.steppy-step-desc');
    expect(desc!.querySelector('strong')?.textContent).toBe('Créer');
    expect(desc!.textContent).not.toContain('**');
    const title = root(el).querySelector('.steppy-step--current .steppy-step-title');
    expect(title!.querySelector('strong')?.textContent).toBe('un');
  });

  it('inline renders bold in the bottom-bar description', async () => {
    const { el } = await mount(boldManifest('inline'));
    const desc = root(el).querySelector('.steppy-inline-desc');
    expect(desc!.querySelector('strong')?.textContent).toBe('Créer');
    expect(desc!.textContent).not.toContain('**');
  });
});

// ── Registry consistency ─────────────────────────────────────────────────
//
// index.ts derives isTemplateId and templateStyles from TEMPLATES, but
// ALL_TEMPLATES stays an explicit list because the switcher's display order is
// deliberately not the registry's declaration order. These guard that the one
// remaining hand-maintained list can't silently drift: a template registered
// but missing from ALL_TEMPLATES would render fine yet be unreachable from the
// switcher, with no other test failing.

describe('template registry', () => {
  it('ALL_TEMPLATES lists exactly the registered templates', () => {
    expect([...ALL_TEMPLATES].sort()).toEqual(Object.keys(TEMPLATES).sort());
  });

  it('templateStyles carries base styles plus one entry per template', () => {
    expect(templateStyles).toHaveLength(Object.keys(TEMPLATES).length + 1);
  });

  // happy-dom computes no CSS, so nothing else in this suite can tell that the
  // sidebar rail is capped and its timeline scrolls — a refactor could drop
  // either rule and every assertion above would still pass. This checks the
  // declarations themselves, which is the most a DOM-only environment can do.
  it('sidebar keeps its rail cap, internal scroll and layout tokens', () => {
    const css = TEMPLATES.sidebar.styles.cssText;
    for (const decl of [
      '--steppy-rail-width',
      '--steppy-rail-max-height',
      '--steppy-sidebar-offset',
      'overflow-y: auto',
      'min-height: 0',
    ]) {
      expect(css).toContain(decl);
    }
  });

  it('isTemplateId accepts every registered template and rejects others', () => {
    for (const id of Object.keys(TEMPLATES)) {
      expect(isTemplateId(id)).toBe(true);
    }
    for (const bogus of ['', 'Overlay', 'carousel', null, undefined, 42]) {
      expect(isTemplateId(bogus)).toBe(false);
    }
  });
});
