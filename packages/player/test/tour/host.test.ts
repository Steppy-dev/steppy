/**
 * ``<steppy-tour-host>`` end to end in happy-dom: the real loader over the
 * golden tour files, the stub DOM for geometry and time. Real hit-testing,
 * the top layer and focus traps are covered by the Chromium suite.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../src/tour/index.js';
import { SteppyTourHost, defineTourHost } from '../../src/tour/host.js';
import { TOUR_EVENTS } from '../../src/tour/contract.js';
import { TOUR_HOST_TAG, TOUR_RESUME_STORAGE_KEY, TOURS_KILL_SWITCH_GLOBAL } from '../../src/ui/names.js';
import { settle, stubDom, type StubDom } from './helpers.js';

const DIR = resolve(__dirname, '../../../../contracts/fixtures/tours/valid');
const FILES = ['tours.manifest.json', 'create-invoice.en.json', 'create-invoice.fr.json', 'send-invoice.en.json', 'send-invoice.fr.json'];
const APP = `
  <div data-testid="invoice-list" data-rect="0,0,800,400">
    <button data-testid="new-invoice-btn" data-rect="20,20,120,32">New invoice</button>
  </div>
  <input data-testid="invoice-title" data-rect="20,100,300,32">
  <div data-testid="customer-select" role="combobox" data-rect="20,160,300,32"><span class="label">Choose</span></div>
  <button data-testid="save-invoice-btn" data-rect="20,220,100,32">Save</button>
  <section data-testid="invoice-detail" data-rect="0,300,800,200">Draft</section>`;

let dom: StubDom;
let host: SteppyTourHost;
let events: { type: string; detail: Record<string, unknown> }[];
let unlisten: (() => void)[] = [];

function serve(): void {
  const body = Object.fromEntries(FILES.map((f) => [`${location.origin}/tours/${f}`, readFileSync(resolve(DIR, f), 'utf8')]));
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => (body[url] ? new Response(body[url]) : new Response('missing', { status: 404 }))),
  );
}

async function mount(setup: (h: SteppyTourHost) => void = () => {}): Promise<SteppyTourHost> {
  const el = document.createElement(TOUR_HOST_TAG);
  el.fallbackManifest = '/tours/tours.manifest.json';
  setup(el);
  document.body.append(el);
  await el.updateComplete;
  return el;
}

/** Let promises, mutation observers, frames and renders run. */
async function tick(ms = 0): Promise<void> {
  await settle();
  dom.scheduler.advance(ms);
  await host.updateComplete;
  await settle();
  dom.scheduler.flushFrames();
  await host.updateComplete;
}

const root = () => host.shadowRoot!;
const bubble = () => root().querySelector<HTMLElement>('[part~="bubble"]');
const part = (name: string) => root().querySelector<HTMLElement>(`[part~="${name}"]`);
const app = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)!;
const of = (type: string) => events.filter((e) => e.type === type);

function press(el: HTMLElement): void {
  el.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, composed: true }));
  el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, composed: true }));
  el.dispatchEvent(new MouseEvent('click', { bubbles: true, composed: true, detail: 1 }));
}

beforeEach(async () => {
  dom = stubDom();
  SteppyTourHost.dom = dom;
  serve();
  sessionStorage.clear();
  delete (globalThis as Record<string, unknown>)[TOURS_KILL_SWITCH_GLOBAL];
  history.replaceState(null, '', '/invoices');
  document.documentElement.lang = '';
  document.body.innerHTML = APP;
  events = [];
  // Removed by hand: happy-dom ignores addEventListener's ``signal`` option.
  for (const name of TOUR_EVENTS) {
    const record = (e: Event) => events.push({ type: name, detail: (e as CustomEvent).detail });
    document.addEventListener(name, record);
    unlisten.push(() => document.removeEventListener(name, record));
  }
  host = await mount();
});

afterEach(() => {
  host.stop();
  unlisten.forEach((off) => off());
  unlisten = [];
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

async function begin(id = 'create-invoice', options = {}): Promise<boolean> {
  const ok = await host.start(id, options);
  await tick();
  return ok;
}

describe('idle', () => {
  it('renders nothing and reports no position', () => {
    expect(root().querySelector('[part~="layer"]')).toBeNull();
    expect(host.isActive).toBe(false);
    expect(host.current).toBeNull();
  });

  it('defines the element once', () => {
    expect(customElements.get(TOUR_HOST_TAG)).toBe(SteppyTourHost);
    expect(() => defineTourHost()).not.toThrow();
  });

  it('carries the stacking order through CSSOM', async () => {
    host.baseZIndex = 1234;
    await host.updateComplete;
    expect(host.style.getPropertyValue('--_pw-z')).toBe('1234');
  });
});

describe('a tour, followed', () => {
  it('starts and shows the first step with the DOM contract', async () => {
    expect(await begin()).toBe(true);
    expect(of('tour-start')[0]!.detail).toMatchObject({ tourId: 'create-invoice', buildId: 'demo-0001', locale: 'en', total: 5, source: 'bundled', resumed: false, entryNavigated: false });
    expect(of('tour-step')[0]!.detail).toMatchObject({ stepId: 'new-invoice-btn', index: 0, total: 5, advance: 'click', reason: 'start' });
    const b = bubble()!;
    expect(b.getAttribute('role')).toBe('dialog');
    expect([b.dataset.tourId, b.dataset.stepId, b.dataset.state, b.dataset.advance]).toEqual(['create-invoice', 'new-invoice-btn', 'shown', 'click']);
    expect(part('title')!.textContent).toBe('New invoice');
    expect(part('body')!.querySelector('strong')!.textContent).toBe('New invoice');
    expect(part('progress')!.textContent).toBe('Step 1 of 5');
    expect(part('next')).toBeNull();
    expect(part('spotlight')).not.toBeNull();
    expect(b.hasAttribute('data-testid')).toBe(false);
    expect(host.current).toEqual({ tourId: 'create-invoice', stepId: 'new-invoice-btn', testId: 'new-invoice-btn', index: 0, total: 5 });
    expect(of('tour-step')[0]!.detail.testId).toBe('new-invoice-btn');
  });

  it('advances click → input (Next gated on a value) → change → click → manual, then completes', async () => {
    await begin();
    app('new-invoice-btn').click();
    await tick(1);
    expect(host.current!.stepId).toBe('invoice-title');
    const next = () => part('next') as HTMLButtonElement;
    expect(next().getAttribute('part')).toBe('button next primary');
    expect(next().disabled).toBe(true);

    const input = app('invoice-title') as HTMLInputElement;
    input.value = 'Maintenance';
    input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    await tick();
    expect(next().disabled).toBe(false);
    press(next());
    await tick();
    expect(host.current!.stepId).toBe('customer-select');
    expect(part('next')!.getAttribute('part')).toBe('button next secondary');

    const combo = app('customer-select');
    combo.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, composed: true }));
    combo.querySelector('.label')!.textContent = 'Acme Corp';
    await tick();
    expect(host.current!.stepId).toBe('save-invoice-btn');

    app('save-invoice-btn').click();
    await tick(1);
    expect(host.current!.stepId).toBe('invoice-detail');
    expect(part('next')!.textContent!.trim()).toBe('Done');
    expect(part('guide-link')!.getAttribute('href')).toBe('https://guide.example.com/create-invoice/en.html');
    expect(sessionStorage.getItem(TOUR_RESUME_STORAGE_KEY)).toContain('invoice-detail');

    press(part('next')!);
    await tick();
    expect(of('tour-complete')).toHaveLength(1);
    expect(of('tour-step').map((e) => e.detail.stepId)).toEqual(['new-invoice-btn', 'invoice-title', 'customer-select', 'save-invoice-btn', 'invoice-detail']);
    expect(root().querySelector('[part~="layer"]')).toBeNull();
    expect(sessionStorage.getItem(TOUR_RESUME_STORAGE_KEY)).toBeNull();
  });

  it('ignores a click on Next that was not pressed on this step, accepts the keyboard after a grace', async () => {
    await begin();
    app('new-invoice-btn').click();
    await tick(1);
    const input = app('invoice-title') as HTMLInputElement;
    input.value = 'x';
    input.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    await tick();
    part('next')!.dispatchEvent(new MouseEvent('click', { bubbles: true, composed: true, detail: 1 }));
    await tick();
    expect(host.current!.stepId).toBe('invoice-title');
    dom.scheduler.advance(400);
    part('next')!.dispatchEvent(new MouseEvent('click', { bubbles: true, composed: true, detail: 0 }));
    await tick();
    expect(host.current!.stepId).toBe('customer-select');
  });

  it('a change event with a value commits an input step', async () => {
    await begin('create-invoice', { step: 'invoice-title' });
    const input = app('invoice-title') as HTMLInputElement;
    input.value = 'Hello';
    input.dispatchEvent(new Event('change', { bubbles: true }));
    await tick(1);
    expect(host.current!.stepId).toBe('customer-select');
  });

  it('a click whose handler removes its own target advances instead of losing the step', async () => {
    await begin('create-invoice', { step: 'save-invoice-btn' });
    const save = app('save-invoice-btn');
    save.addEventListener('click', () => save.remove());
    save.click();
    // A frame lands between the click and its deferred trigger: the tracker
    // sees the target gone first.
    document.dispatchEvent(new Event('scroll'));
    dom.scheduler.flushFrames();
    await tick(1);
    expect(host.current!.stepId).toBe('invoice-detail');
    expect(of('tour-offpath')).toHaveLength(0);
  });

  it('a click on a later click step jumps past it', async () => {
    await begin('create-invoice', { step: 'invoice-title' });
    app('save-invoice-btn').click();
    await tick(1);
    expect(host.current!.stepId).toBe('invoice-detail');
    expect(of('tour-step').at(-1)!.detail.reason).toBe('jump');
  });
});

describe('when the app does not cooperate', () => {
  it('a missing target shows the fallback and reports target-timeout; Skip moves on', async () => {
    app('new-invoice-btn').remove();
    await begin();
    expect(bubble()).toBeNull();
    await tick(8000);
    expect(bubble()!.dataset.state).toBe('fallback');
    expect(part('spotlight')).toBeNull();
    expect(of('tour-error')[0]!.detail).toMatchObject({ reason: 'target-timeout', fatal: false, stepId: 'new-invoice-btn', testId: 'new-invoice-btn', kind: 'target' });
    expect(part('title')!.textContent).toBe("Can't find this element");
    dom.scheduler.advance(400);
    press(part('skip')!);
    await tick();
    expect(host.current!.stepId).toBe('invoice-title');
    expect(of('tour-step').at(-1)!.detail.reason).toBe('skip');
  });

  it('names the waitFor gate when that is what is missing', async () => {
    app('invoice-list').removeAttribute('data-testid');
    await begin();
    await tick(8000);
    expect(of('tour-error')[0]!.detail).toMatchObject({ testId: 'invoice-list', kind: 'waitFor' });
  });

  it('a target that disappears pauses the tour, and its return resumes it', async () => {
    await begin('create-invoice', { step: 'invoice-title' });
    const input = app('invoice-title');
    input.remove();
    dom.scheduler.advance(300);
    await tick();
    expect(bubble()).toBeNull();
    await tick(8000);
    expect(part('pill')).not.toBeNull();
    expect(of('tour-offpath')[0]!.detail).toMatchObject({ stepId: 'invoice-title', reason: 'target-lost' });
    document.body.append(input);
    await tick(300);
    expect(bubble()!.dataset.stepId).toBe('invoice-title');
    expect(of('tour-step').at(-1)!.detail.reason).toBe('resume');
  });

  it('a load failure is a fatal tour-error and nothing renders', async () => {
    host.fallbackManifest = '/tours/missing.json';
    await host.updateComplete;
    expect(await begin()).toBe(false);
    expect(of('tour-error')[0]!.detail).toMatchObject({ reason: 'load-failed', fatal: true });
    expect(root().querySelector('[part~="layer"]')).toBeNull();
  });
});

describe('dismissing', () => {
  it('Escape ends the tour', async () => {
    await begin();
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await tick();
    expect(of('tour-dismiss')[0]!.detail).toMatchObject({ via: 'escape', stepId: 'new-invoice-btn' });
    expect(host.isActive).toBe(false);
  });

  it('Escape inside an open popup or an app dialog is the app\'s', async () => {
    await begin();
    const popup = document.createElement('div');
    popup.setAttribute('aria-expanded', 'true');
    document.body.append(popup);
    popup.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    const prevented = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    prevented.preventDefault();
    window.dispatchEvent(prevented);
    await tick();
    expect(of('tour-dismiss')).toHaveLength(0);
  });

  it('the close button, stop() and disabled', async () => {
    await begin();
    press(part('close')!);
    await tick();
    expect(of('tour-dismiss')[0]!.detail.via).toBe('close');
    await begin();
    host.stop('host-closed');
    expect(of('tour-dismiss')[1]!.detail).toMatchObject({ via: 'api', reason: 'host-closed' });
    await begin();
    host.disabled = true;
    await tick();
    expect(of('tour-dismiss')[2]!.detail.via).toBe('disabled');
    expect(await host.start('create-invoice')).toBe(false);
  });
});

describe('switches', () => {
  it('the kill switch makes start() a silent no-op', async () => {
    (globalThis as Record<string, unknown>)[TOURS_KILL_SWITCH_GLOBAL] = true;
    const info = vi.spyOn(console, 'info').mockImplementation(() => {});
    expect(await begin()).toBe(false);
    expect(events).toEqual([]);
    info.mockRestore();
  });
});

describe('entry routes', () => {
  it('navigates through the hook when the app is elsewhere', async () => {
    history.replaceState(null, '', '/');
    const navigate = vi.fn((path: string) => history.pushState(null, '', path));
    host.navigate = navigate;
    expect(await begin()).toBe(true);
    await tick();
    expect(navigate).toHaveBeenCalledWith('/invoices', { appPath: '/invoices', tourId: 'create-invoice' });
    expect(of('tour-start')[0]!.detail.entryNavigated).toBe(true);
    expect(bubble()!.dataset.stepId).toBe('new-invoice-btn');
  });

  it('fills route parameters from start() and reports the ones missing', async () => {
    history.replaceState(null, '', '/');
    const navigate = vi.fn();
    host.navigate = navigate;
    expect(await begin('send-invoice')).toBe(false);
    expect(of('tour-error')[0]!.detail).toMatchObject({ reason: 'entry-params', missing: ['invoiceId'] });
    expect(await begin('send-invoice', { params: { invoiceId: 1001 } })).toBe(true);
    expect(navigate).toHaveBeenCalledWith('/invoices/1001', { appPath: '/invoices/1001', tourId: 'send-invoice' });
  });

  it('stays put when the current route already matches', async () => {
    history.replaceState(null, '', '/invoices/1001');
    const navigate = vi.fn();
    host.navigate = navigate;
    expect(await begin('send-invoice')).toBe(true);
    expect(navigate).not.toHaveBeenCalled();
  });
});

describe('resume and locale', () => {
  it('a connected host resumes a recorded tour without navigating', async () => {
    sessionStorage.setItem(
      TOUR_RESUME_STORAGE_KEY,
      JSON.stringify({
        v: 1,
        tourId: 'create-invoice',
        stepId: 'customer-select',
        locale: 'en',
        params: {},
        buildId: 'demo-0001',
        manifestKey: '/tours/tours.manifest.json',
        savedAt: Date.now(),
      }),
    );
    host.remove();
    history.replaceState(null, '', '/elsewhere');
    host = await mount();
    await tick();
    await tick();
    expect(of('tour-start').at(-1)!.detail).toMatchObject({ resumed: true, entryNavigated: false });
    expect(host.current!.stepId).toBe('customer-select');
  });

  it('chrome strings follow the tour file\'s language', async () => {
    host.locale = 'fr';
    await host.updateComplete;
    await begin('create-invoice', { step: 'invoice-title' });
    expect(part('progress')!.textContent).toBe('Étape 2 sur 5');
    expect(part('next')!.textContent!.trim()).toBe('Suivant');
    expect(bubble()!.getAttribute('aria-labelledby')).toBe('pw-title');
  });
});

describe('links in step text', () => {
  it('route links go through the navigate hook; other origins stay text', async () => {
    const tour = JSON.parse(readFileSync(resolve(DIR, 'create-invoice.en.json'), 'utf8'));
    tour.steps[0].text = 'See [the list](/invoices) or [evil](https://evil.example/x) or [guide](https://guide.example.com/x).';
    host.source = async () => tour;
    const navigate = vi.fn();
    host.navigate = navigate;
    await begin();
    const links = [...part('body')!.querySelectorAll('a')];
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['/invoices', 'https://guide.example.com/x']);
    expect(links[1]!.getAttribute('target')).toBe('_blank');
    links[0]!.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }));
    expect(navigate).toHaveBeenCalledWith('/invoices', { appPath: '/invoices', tourId: 'create-invoice' });
    expect(part('body')!.textContent).toContain('[evil](https://evil.example/x)');
  });
});

describe('links that resolve off-site', () => {
  it('render as text: dot segments collapse into a protocol-relative //host', async () => {
    const tour = JSON.parse(readFileSync(resolve(DIR, 'create-invoice.en.json'), 'utf8'));
    tour.steps[0].text = '[a](/.//evil.example/x) [b](/a/..//evil.example) [c](/%2e%2e//evil.example)';
    host.source = async () => tour;
    await begin();
    expect(part('body')!.querySelectorAll('a')).toHaveLength(0);
    expect(part('body')!.textContent).toContain('[a](/.//evil.example/x)');
  });
});

/** Like ``serve``; the ``gated`` files wait for ``release(name)``, ``overrides`` replace bodies (null: 404). */
function serveGated(gated: string[] = [], overrides: Record<string, string | null> = {}) {
  const body: Record<string, string | null> = {
    ...Object.fromEntries(FILES.map((f) => [f, readFileSync(resolve(DIR, f), 'utf8')])),
    ...overrides,
  };
  const open = new Map<string, () => void>();
  const gates = new Map(gated.map((n) => [n, new Promise<void>((r) => open.set(n, r))]));
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const name = url.slice(`${location.origin}/tours/`.length);
      await gates.get(name);
      const b = body[name];
      return typeof b === 'string' ? new Response(b) : new Response('missing', { status: 404 });
    }),
  );
  return { release: (name: string) => open.get(name)?.() };
}

const titleText = () => part('title')!.textContent!.trim();

describe('language', () => {
  it('marks the layer with the tour file\'s language and direction', async () => {
    await begin();
    expect([part('layer')!.getAttribute('lang'), part('layer')!.getAttribute('dir')]).toEqual(['en', 'ltr']);
  });

  it('switches mid-tour when the host locale changes, and re-announces the step', async () => {
    await begin();
    expect(titleText()).toBe('New invoice');
    host.locale = 'fr';
    await tick();
    await tick();
    expect(titleText()).toBe('Nouvelle facture');
    expect(part('progress')!.textContent).toBe('Étape 1 sur 5');
    expect(part('layer')!.getAttribute('lang')).toBe('fr');
    expect(root().querySelector('[role="status"]')!.textContent).toContain('Nouvelle facture');
    expect(host.current!.stepId).toBe('new-invoice-btn');
  });

  it('switches after the host locale was set to undefined', async () => {
    await begin();
    (host as unknown as { locale: string | undefined }).locale = undefined;
    await tick();
    await tick();
    host.locale = 'fr';
    await tick();
    await tick();
    expect(titleText()).toBe('Nouvelle facture');
  });

  it('follows <html lang> while no host locale is set, and ignores it otherwise', async () => {
    await begin();
    document.documentElement.lang = 'fr';
    await tick();
    await tick();
    expect(titleText()).toBe('Nouvelle facture');
    host.locale = 'fr';
    await tick();
    document.documentElement.lang = 'en';
    await tick();
    await tick();
    expect(titleText()).toBe('Nouvelle facture');
  });

  it.each(['property', 'html lang'])('catches up with a %s change made while the tour loads', async (via) => {
    const { release } = serveGated(['create-invoice.en.json']);
    const started = host.start('create-invoice');
    await settle();
    if (via === 'property') host.locale = 'fr';
    else document.documentElement.lang = 'fr';
    await host.updateComplete;
    release('create-invoice.en.json');
    expect(await started).toBe(true);
    await tick();
    await tick();
    expect(titleText()).toBe('Nouvelle facture');
  });

  it('applies the last requested locale when reloads finish out of order', async () => {
    await begin();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { release } = serveGated(['create-invoice.fr.json']);
    host.locale = 'fr';
    await tick();
    host.locale = 'en';
    await tick();
    release('create-invoice.fr.json');
    await tick();
    await tick();
    expect(titleText()).toBe('New invoice');
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it('keeps the current language and warns when the reload fails', async () => {
    await begin();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    serveGated([], { 'create-invoice.fr.json': null });
    host.locale = 'fr';
    await tick();
    await tick();
    expect(titleText()).toBe('New invoice');
    expect(warn.mock.calls.flat().join(' ')).toMatch(/locale "fr" not applied/);
    warn.mockRestore();
  });

  it('lays a right-to-left tour out as one, with English chrome unless the host supplies strings', async () => {
    const manifest = JSON.parse(readFileSync(resolve(DIR, 'tours.manifest.json'), 'utf8'));
    manifest.locales.push('ar');
    Object.assign(manifest.tours[0].files, { ar: 'create-invoice.ar.json' });
    Object.assign(manifest.tours[0].title, { ar: 'إنشاء فاتورة' });
    Object.assign(manifest.tours[0].integrity, { ar: manifest.tours[0].integrity.en });
    const ar = { ...JSON.parse(readFileSync(resolve(DIR, 'create-invoice.en.json'), 'utf8')), locale: 'ar' };
    serveGated([], { 'tours.manifest.json': JSON.stringify(manifest), 'create-invoice.ar.json': JSON.stringify(ar) });
    host.locale = 'ar';
    await host.updateComplete;
    await begin();
    expect([part('layer')!.getAttribute('lang'), part('layer')!.getAttribute('dir')]).toEqual(['ar', 'rtl']);
    expect(part('progress')!.textContent).toBe('Step 1 of 5');
    host.strings = { ar: { progress: 'الخطوة {current} من {total}' } };
    await tick();
    expect(part('progress')!.textContent).toBe('الخطوة 1 من 5');
  });
});

describe("a click on an information step's target", () => {
  // ``invoice-detail`` is the last step: targeted, advancing on Next.
  function counted(id: string): { clicks: number; presses: number } {
    const n = { clicks: 0, presses: 0 };
    const el = app(id);
    el.addEventListener('click', () => n.clicks++);
    el.addEventListener('mousedown', () => n.presses++);
    return n;
  }
  async function atInfo(mode?: 'pass' | 'next' | 'block'): Promise<void> {
    if (mode) host.infoTargetClick = mode;
    await begin('create-invoice', { step: 'invoice-detail' });
    expect(host.current!.stepId).toBe('invoice-detail');
  }

  it('reaches the element by default (pass)', async () => {
    const n = counted('invoice-detail');
    await atInfo();
    press(app('invoice-detail'));
    await tick();
    expect(n).toEqual({ clicks: 1, presses: 1 });
    expect(host.current!.stepId).toBe('invoice-detail');
  });

  it('next: never reaches the element, advances as Next would', async () => {
    const n = counted('invoice-detail');
    await atInfo('next');
    const md = new MouseEvent('mousedown', { bubbles: true, composed: true, cancelable: true });
    app('invoice-detail').dispatchEvent(md);
    expect(md.defaultPrevented).toBe(true);
    app('invoice-detail').dispatchEvent(new MouseEvent('click', { bubbles: true, composed: true, detail: 1 }));
    await tick();
    expect(n).toEqual({ clicks: 0, presses: 0 });
    expect(of('tour-complete')).toHaveLength(1);
  });

  it('next: a double click advances one step and never leaks to the app', async () => {
    const n = counted('invoice-detail');
    await atInfo('next');
    press(app('invoice-detail'));
    press(app('invoice-detail'));
    app('invoice-detail').dispatchEvent(new MouseEvent('dblclick', { bubbles: true, composed: true }));
    await tick();
    expect(of('tour-complete')).toHaveLength(1);
    expect(n).toEqual({ clicks: 0, presses: 0 });
    // Once the hold ran out, the element is the app's again.
    dom.scheduler.advance(600);
    press(app('invoice-detail'));
    expect(n.clicks).toBe(1);
  });

  it('block: never reaches the element, and nothing happens', async () => {
    const n = counted('invoice-detail');
    await atInfo('block');
    press(app('invoice-detail'));
    await tick();
    expect(n).toEqual({ clicks: 0, presses: 0 });
    expect(host.current!.stepId).toBe('invoice-detail');
    press(part('next')!);
    await tick();
    expect(of('tour-complete')).toHaveLength(1);
  });

  it('leaves clicks elsewhere and on action steps alone', async () => {
    host.infoTargetClick = 'block';
    const list = counted('invoice-list');
    await begin();
    app('new-invoice-btn').click();
    await tick(1);
    expect(host.current!.stepId).toBe('invoice-title');
    app('invoice-list').click();
    expect(list.clicks).toBeGreaterThan(0);
  });
});
