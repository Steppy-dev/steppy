/** Resolver (test-id lookup, waiting) and tracker (placement modes, yield, loss) on the stub DOM. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createResolver } from '../../src/tour/resolver.js';
import { createTracker } from '../../src/tour/tracker.js';
import { settle, stubDom, type StubDom } from './helpers.js';

let dom: StubDom;
let host: HTMLElement;

beforeEach(() => {
  dom = stubDom();
  document.body.innerHTML = '';
  host = document.createElement('div');
  host.setAttribute('data-testid', 'save');
  document.body.append(host);
});

afterEach(() => {
  document.body.innerHTML = '';
});

function add(testId: string, rect?: string, tag = 'button'): HTMLElement {
  const el = document.createElement(tag);
  el.setAttribute('data-testid', testId);
  if (rect) el.setAttribute('data-rect', rect);
  document.body.append(el);
  return el;
}

describe('resolver.find', () => {
  it('returns the first visible match, skipping the overlay host', () => {
    add('save');
    const visible = add('save', '10,10,50,20');
    add('save', '90,10,50,20');
    const r = createResolver({ attr: 'data-testid', host, dom });
    expect(r.find('save')).toBe(visible);
  });

  it('escapes the id into a single attribute selector', () => {
    const el = add('invoice:list/new.btn', '1,1,5,5');
    add('invoice', '1,1,5,5');
    expect(createResolver({ attr: 'data-testid', host, dom }).find('invoice:list/new.btn')).toBe(el);
  });
});

describe('resolver.wait', () => {
  it('resolves at once when everything is there', () => {
    add('a', '1,1,5,5');
    const onFound = vi.fn();
    createResolver({ attr: 'data-testid', host, dom }).wait({ ids: ['a'], timeoutMs: 1000, onFound, onTimeout: vi.fn() });
    expect(onFound).toHaveBeenCalledTimes(1);
  });

  it('waits for a mutation, checking once per frame', async () => {
    const onFound = vi.fn();
    createResolver({ attr: 'data-testid', host, dom }).wait({ ids: ['a', 'b'], timeoutMs: 5000, onFound, onTimeout: vi.fn() });
    add('a', '1,1,5,5');
    add('b', '1,1,5,5');
    await settle();
    expect(onFound).not.toHaveBeenCalled();
    dom.scheduler.flushFrames();
    expect(onFound).toHaveBeenCalledTimes(1);
    expect([...onFound.mock.calls[0]![0].keys()]).toEqual(['a', 'b']);
  });

  it('times out with the first id still missing, and can be cancelled', () => {
    add('a', '1,1,5,5');
    const onTimeout = vi.fn();
    createResolver({ attr: 'data-testid', host, dom }).wait({ ids: ['a', 'b'], timeoutMs: 1000, onFound: vi.fn(), onTimeout });
    dom.scheduler.advance(1250);
    expect(onTimeout).toHaveBeenCalledWith('b');
    const late = vi.fn();
    const cancel = createResolver({ attr: 'data-testid', host, dom }).wait({ ids: ['c'], timeoutMs: 500, onFound: late, onTimeout: late });
    cancel();
    dom.scheduler.advance(2000);
    expect(late).not.toHaveBeenCalled();
  });

  it('never times out without a deadline', () => {
    const onTimeout = vi.fn();
    createResolver({ attr: 'data-testid', host, dom }).wait({ ids: ['z'], timeoutMs: null, onFound: vi.fn(), onTimeout });
    dom.scheduler.advance(60_000);
    expect(onTimeout).not.toHaveBeenCalled();
  });
});

describe('tracker', () => {
  function setup(
    targetRect: string | null,
    opts: { preferred?: 'auto' | 'top' | 'bottom'; dialogs?: string[]; hold?: string; avoid?: string } = {},
  ) {
    const layer = document.createElement('div');
    const bubble = document.createElement('section');
    bubble.setAttribute('data-rect', '0,0,300,120');
    layer.append(bubble);
    document.body.append(layer);
    const target = targetRect ? add('t', targetRect) : null;
    const zone = opts.hold ? add('zone', opts.hold, 'aside') : null;
    const onLost = vi.fn();
    let current: Element | null = target;
    const tracker = createTracker({
      dom,
      layer: () => layer,
      bubble: () => bubble,
      target: () => current,
      refind: () => null,
      preferred: () => opts.preferred ?? 'auto',
      avoid: () => {
        if (!opts.avoid) return null;
        const [x, y, width, height] = opts.avoid.split(',').map(Number) as [number, number, number, number];
        return { x, y, width, height };
      },
      hold: () => zone,
      dialogs: () =>
        (opts.dialogs ?? []).map((r) => {
          const [x, y, width, height] = r.split(',').map(Number) as [number, number, number, number];
          return { x, y, width, height };
        }),
      onLost,
    });
    tracker.start();
    dom.scheduler.flushFrames();
    return { layer, bubble, target, zone, tracker, onLost, setCurrent: (el: Element | null) => (current = el) };
  }

  it('anchors next to the target and draws the spotlight around it', () => {
    const { layer, bubble } = setup('500,300,100,40');
    expect(bubble.getAttribute('data-mode')).toBe('anchored');
    expect(bubble.getAttribute('data-side')).toBe('bottom');
    expect(bubble.hasAttribute('data-placed')).toBe(true);
    expect(layer.style.getPropertyValue('--_pw-sx')).toBe('494px');
    expect(layer.style.getPropertyValue('--_pw-sw')).toBe('112px');
    expect(parseFloat(layer.style.getPropertyValue('--_pw-y'))).toBeGreaterThanOrEqual(346);
  });

  it('honours a preferred side', () => {
    expect(setup('500,300,100,40', { preferred: 'top' }).bubble.getAttribute('data-side')).toBe('top');
  });

  it('becomes a sheet on a narrow viewport, away from the target', () => {
    dom.vp = { x: 0, y: 0, width: 390, height: 844 };
    expect(setup('20,700,100,40').bubble.getAttribute('data-mode')).toBe('sheet-top');
    document.body.innerHTML = '';
    expect(setup('20,100,100,40').bubble.getAttribute('data-mode')).toBe('sheet-bottom');
  });

  it('never covers the hold zone, even on the preferred side', () => {
    // A link in a 240px sidebar: below it would be inside the sidebar.
    const { bubble, layer } = setup('0,300,240,40', { preferred: 'bottom', hold: '0,0,240,800' });
    expect(bubble.getAttribute('data-mode')).toBe('anchored');
    expect(bubble.getAttribute('data-side')).toBe('right');
    expect(parseFloat(layer.style.getPropertyValue('--_pw-x'))).toBeGreaterThanOrEqual(240);
    // The spotlight stays on the target, not the zone.
    expect(layer.style.getPropertyValue('--_pw-sh')).toBe('52px');
  });

  it('prefers the hold zone over the next target when no side clears both', () => {
    const { bubble } = setup('0,300,240,40', { hold: '0,0,240,800', avoid: '250,200,1030,300' });
    expect(bubble.getAttribute('data-side')).toBe('right');
  });

  it('re-places when the hold zone grows', () => {
    // A rail that expands on hover, the link inside it widening with it.
    const { bubble, target, zone, layer, tracker } = setup('0,300,56,40', { hold: '0,0,56,800' });
    expect(parseFloat(layer.style.getPropertyValue('--_pw-x'))).toBeLessThan(240);
    zone!.setAttribute('data-rect', '0,0,240,800');
    target!.setAttribute('data-rect', '0,300,240,40');
    tracker.notify();
    dom.scheduler.flushFrames();
    expect(bubble.getAttribute('data-mode')).toBe('anchored');
    expect(parseFloat(layer.style.getPropertyValue('--_pw-x'))).toBeGreaterThanOrEqual(240);
  });

  it('turns into a sheet when no side clears the hold zone', () => {
    const { bubble } = setup('500,300,100,40', { hold: '0,0,1280,800' });
    expect(bubble.getAttribute('data-mode')).toMatch(/^sheet-/);
  });

  it('centres a targetless step', () => {
    expect(setup(null).bubble.getAttribute('data-mode')).toBe('centered');
  });

  it('moves a centred step off an open app dialog, to the side with more room', () => {
    // The 300x120 bubble would sit at 490,340 in the 1280x800 viewport.
    expect(setup(null, { dialogs: ['440,250,400,200'] }).bubble.getAttribute('data-mode')).toBe('sheet-bottom');
    document.body.innerHTML = '';
    expect(setup(null, { dialogs: ['440,350,400,200'] }).bubble.getAttribute('data-mode')).toBe('sheet-top');
    document.body.innerHTML = '';
    expect(setup(null, { dialogs: ['0,0,300,200'] }).bubble.getAttribute('data-mode')).toBe('centered');
  });

  it('moves off a dialog when the target is scrolled out of view too', () => {
    const { bubble, target, tracker } = setup('500,300,100,40', { dialogs: ['440,250,400,200'] });
    target!.setAttribute('data-rect', '500,2000,100,40');
    tracker.notify();
    dom.scheduler.flushFrames();
    expect(bubble.hasAttribute('data-offscreen')).toBe(true);
    expect(bubble.getAttribute('data-mode')).toBe('sheet-bottom');
  });

  it('yields to an open popup inside the target', () => {
    const { bubble, target, tracker } = setup('500,300,100,40');
    target!.setAttribute('aria-expanded', 'true');
    tracker.notify();
    dom.scheduler.flushFrames();
    expect(bubble.hasAttribute('data-yield')).toBe(true);
  });

  it('marks a target scrolled out of view as offscreen', () => {
    const { bubble, layer, target, tracker } = setup('500,300,100,40');
    target!.setAttribute('data-rect', '500,2000,100,40');
    tracker.notify();
    dom.scheduler.flushFrames();
    expect(bubble.hasAttribute('data-offscreen') && layer.hasAttribute('data-offscreen')).toBe(true);
  });

  it('reports a target that disappeared and cannot be found again', () => {
    const { target, tracker, onLost } = setup('500,300,100,40');
    target!.remove();
    tracker.notify();
    dom.scheduler.flushFrames();
    expect(onLost).toHaveBeenCalledTimes(1);
  });
});
