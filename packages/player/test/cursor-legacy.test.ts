import { afterEach, describe, expect, it, vi } from 'vitest';

/**
 * The static step-by-step page embeds every layout's steps and shows one. The
 * legacy cursor must drive the visible layout's sections with that layout's
 * frame data, and move when the layout changes — otherwise a phone reader's
 * mobile screenshots get the desktop cursor coordinates.
 */

const observed: Element[][] = [];
const disconnects = vi.fn();

class FakeObserver {
  private readonly targets: Element[] = [];
  constructor() {
    observed.push(this.targets);
  }
  observe(el: Element): void {
    this.targets.push(el);
  }
  unobserve(): void {}
  disconnect(): void {
    disconnects();
  }
}

function page(active: string | null): void {
  if (active) document.documentElement.setAttribute('data-steppy-layout', active);
  else document.documentElement.removeAttribute('data-steppy-layout');
  document.body.innerHTML = ['desktop', 'mobile']
    .map(
      (name) =>
        `<div class="steppy-layout" data-layout="${name}"><section class="steppy-step" data-step-index="1" id="${name}-1"></section></div>`,
    )
    .join('');
  const step = (w: number) => [{ index: 1, frames: [{ screenshot: 'x.png', viewport: { width: w, height: 800 }, cursor: null }] }];
  const byLayout = { desktop: step(1280), mobile: step(390) };
  window.__STEPPY_STEPS_BY_LAYOUT__ = byLayout as never;
  window.__STEPPY_STEPS__ = byLayout.desktop as never;
  window.__STEPPY_ACTIVE_LAYOUT__ = 'desktop';
}

// Each import is a fresh module that listens on the one shared document; drop
// those listeners between tests so an earlier page cannot answer the event.
const listeners: [string, EventListenerOrEventListenerObject][] = [];

async function load(): Promise<void> {
  vi.stubGlobal('IntersectionObserver', FakeObserver);
  const add = document.addEventListener.bind(document);
  vi.spyOn(document, 'addEventListener').mockImplementation((type, listener, options) => {
    listeners.push([type, listener]);
    add(type, listener, options);
  });
  vi.resetModules();
  await import('../src/cursor/legacy-iife.js');
  vi.mocked(document.addEventListener).mockRestore();
}

describe('legacy cursor on a multi-layout page', () => {
  afterEach(() => {
    for (const [type, listener] of listeners.splice(0)) document.removeEventListener(type, listener);
    observed.length = 0;
    disconnects.mockClear();
    vi.unstubAllGlobals();
    delete window.__STEPPY_STEPS_BY_LAYOUT__;
    delete window.__STEPPY_STEPS__;
    delete window.__STEPPY_ACTIVE_LAYOUT__;
  });

  it('mounts on the layout steppy-detect.js picked', async () => {
    page('mobile');
    await load();
    expect(observed.at(-1)!.map((el) => el.id)).toEqual(['mobile-1']);
  });

  it('falls back to the page global without the detection script', async () => {
    page(null);
    await load();
    expect(observed.at(-1)!.map((el) => el.id)).toEqual(['desktop-1']);
  });

  it('remounts when the layout changes, and only then', async () => {
    page('desktop');
    await load();
    const mounts = observed.length;
    document.dispatchEvent(new CustomEvent('steppy:layout-change', { detail: { layout: 'desktop' } }));
    expect(observed.length).toBe(mounts);
    document.dispatchEvent(new CustomEvent('steppy:layout-change', { detail: { layout: 'mobile' } }));
    expect(disconnects).toHaveBeenCalled();
    expect(observed.at(-1)!.map((el) => el.id)).toEqual(['mobile-1']);
  });
});
