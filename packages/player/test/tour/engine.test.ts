/** The pure tour reducer (``src/tour/engine.ts``): every transition, no DOM. */

import { describe, expect, it } from 'vitest';

import { initialState, position, reduce, type Effect, type EngineEvent, type EngineState, type Loaded } from '../../src/tour/engine.js';
import type { TourFile, TourStep } from '../../src/tour/validate.js';

function step(id: string, advance: TourStep['advance'], extra: Partial<TourStep> = {}): TourStep {
  return { id, target: advance === 'manual' ? null : id, advance, waitFor: null, title: null, text: null, placement: null, layouts: null, ...extra };
}

const TOUR: TourFile = {
  id: 'create-invoice',
  buildId: 'b-1',
  locale: 'en',
  title: 'Create',
  guideUrl: null,
  entry: { route: '/invoices' },
  steps: [step('a', 'click'), step('b', 'input'), step('c', 'change'), step('d', 'manual')],
};

function loaded(extra: Partial<Loaded> = {}): Loaded {
  return { tour: TOUR, source: 'bundled', applicable: [true, true, true, true], start: 0, resumed: false, navigate: false, ...extra };
}

function run(events: EngineEvent[], s: EngineState = initialState()): { s: EngineState; effects: Effect[] } {
  const effects: Effect[] = [];
  for (const e of events) {
    const t = reduce(s, typeof (e as { token?: number }).token === 'number' && (e as { token: number }).token < 0 ? { ...e, token: s.token } as EngineEvent : e);
    s = t.state;
    effects.push(...t.effects);
  }
  return { s, effects };
}

/** ``token: -1`` means "the current token" in these scripts. */
const T = -1;
const emitted = (effects: Effect[]) => effects.filter((e) => e.type === 'emit').map((e) => (e as { name: string }).name);
const started = () => run([{ type: 'REQUEST' }, { type: 'LOADED', loaded: loaded() }, { type: 'RESOLVED', token: T }]);

describe('starting', () => {
  it('loads, emits tour-start, persists and resolves the first step', () => {
    const { s, effects } = run([{ type: 'REQUEST' }, { type: 'LOADED', loaded: loaded() }]);
    expect(s.phase).toBe('resolving');
    expect(emitted(effects)).toEqual(['tour-start']);
    expect(effects).toContainEqual({ type: 'persist', index: 0 });
    expect(effects).toContainEqual({ type: 'wait', mode: 'resolve', index: 0, token: s.token });
    const start = effects.find((e) => e.type === 'emit') as { detail: Record<string, unknown> };
    expect(start.detail).toEqual({ tourId: 'create-invoice', buildId: 'b-1', locale: 'en', total: 4, source: 'bundled', resumed: false, entryNavigated: false });
  });

  it('navigates to the entry first when asked', () => {
    const { s, effects } = run([{ type: 'REQUEST' }, { type: 'LOADED', loaded: loaded({ navigate: true }) }]);
    expect(s.phase).toBe('entering');
    expect(effects.map((e) => e.type)).toEqual(['load', 'emit', 'persist', 'navigate']);
    const after = run([{ type: 'ENTER_DONE', token: T }], s);
    expect(after.s.phase).toBe('resolving');
  });

  it('a failed entry navigation ends the tour with entry-params', () => {
    const entering = run([{ type: 'REQUEST' }, { type: 'LOADED', loaded: loaded({ navigate: true }) }]).s;
    const { s, effects } = run([{ type: 'ENTER_FAILED', token: T, message: 'nope' }], entering);
    expect(s.phase).toBe('idle');
    expect(effects[0]).toMatchObject({ type: 'emit', name: 'tour-error', detail: { reason: 'entry-params', fatal: true } });
  });

  it('a load failure emits a fatal tour-error and goes idle', () => {
    const error = { reason: 'load-failed' as const, fatal: true, message: 'x' };
    const { s, effects } = run([{ type: 'REQUEST' }, { type: 'LOAD_FAILED', error }]);
    expect(s.phase).toBe('idle');
    expect(effects).toContainEqual({ type: 'emit', name: 'tour-error', detail: error });
  });

  it('reports a resume as reason "resume"', () => {
    const { effects } = run([{ type: 'REQUEST' }, { type: 'LOADED', loaded: loaded({ resumed: true, start: 1 }) }, { type: 'RESOLVED', token: T }]);
    const st = effects.find((e) => e.type === 'emit' && e.name === 'tour-step') as { detail: Record<string, unknown> };
    expect(st.detail).toMatchObject({ stepId: 'b', index: 1, reason: 'resume' });
  });
});

describe('a shown step', () => {
  it('emits tour-step with position, advance and reason', () => {
    const { s, effects } = started();
    expect(s.phase).toBe('showing');
    const st = effects.find((e) => e.type === 'emit' && e.name === 'tour-step') as { detail: Record<string, unknown> };
    expect(st.detail).toEqual({ tourId: 'create-invoice', buildId: 'b-1', locale: 'en', stepId: 'a', index: 0, total: 4, advance: 'click', reason: 'start' });
  });

  it('advances on its own trigger kind only', () => {
    const { s } = started();
    expect(run([{ type: 'TRIGGER', token: T, kind: 'change' }], s).s.index).toBe(0);
    const next = run([{ type: 'TRIGGER', token: T, kind: 'click' }], s);
    expect(next.s).toMatchObject({ phase: 'resolving', index: 1, reason: 'advance' });
  });

  it('drops events carrying a stale token', () => {
    const { s } = started();
    expect(reduce(s, { type: 'TRIGGER', token: s.token - 1, kind: 'click' }).state).toBe(s);
  });

  it('an input step needs a non-empty field before Next, but the API can always advance', () => {
    let s = run([{ type: 'TRIGGER', token: T, kind: 'click' }, { type: 'RESOLVED', token: T }], started().s).s;
    expect(s.index).toBe(1);
    expect(run([{ type: 'NEXT', token: T }], s).s.index).toBe(1);
    s = run([{ type: 'FIELD', token: T, nonEmpty: true }], s).s;
    expect(run([{ type: 'NEXT', token: T }], s).s.index).toBe(2);
    expect(run([{ type: 'NEXT' }], run([{ type: 'FIELD', token: T, nonEmpty: false }], s).s).s.index).toBe(2);
  });

  it('an input-commit trigger advances an input step', () => {
    const s = run([{ type: 'TRIGGER', token: T, kind: 'click' }, { type: 'RESOLVED', token: T }], started().s).s;
    expect(run([{ type: 'TRIGGER', token: T, kind: 'input-commit' }], s).s.index).toBe(2);
  });

  it('completes after the last applicable step', () => {
    let { s } = started();
    for (const kind of ['click', 'input-commit', 'change'] as const) s = run([{ type: 'TRIGGER', token: T, kind }, { type: 'RESOLVED', token: T }], s).s;
    expect(s.index).toBe(3);
    const done = run([{ type: 'NEXT', token: T }], s);
    expect(done.s.phase).toBe('idle');
    expect(emitted(done.effects)).toEqual(['tour-complete']);
    expect(done.effects.map((e) => e.type)).toEqual(['emit', 'clearPersist', 'teardown']);
  });

  it('focuses the bubble on manual steps', () => {
    const s = run([{ type: 'REQUEST' }, { type: 'LOADED', loaded: loaded({ start: 3 }) }]).s;
    expect(run([{ type: 'RESOLVED', token: T }], s).effects).toContainEqual({ type: 'focus' });
  });
});

describe('layouts', () => {
  it('skips steps that do not apply and counts only those that do', () => {
    const { s, effects } = run([
      { type: 'REQUEST' },
      { type: 'LOADED', loaded: loaded({ applicable: [true, false, true, true] }) },
      { type: 'RESOLVED', token: T },
      { type: 'TRIGGER', token: T, kind: 'click' },
      { type: 'RESOLVED', token: T },
    ]);
    expect(s.index).toBe(2);
    expect(position(s)).toEqual({ index: 1, total: 3 });
    const last = effects.filter((e) => e.type === 'emit' && e.name === 'tour-step').at(-1) as { detail: Record<string, unknown> };
    expect(last.detail).toMatchObject({ stepId: 'c', index: 1, total: 3 });
  });
});

describe('fallback, lost and paused', () => {
  it('a target timeout shows the fallback, reports target-timeout and keeps watching', () => {
    const resolving = run([{ type: 'REQUEST' }, { type: 'LOADED', loaded: loaded() }]).s;
    const { s, effects } = run([{ type: 'RESOLVE_TIMEOUT', token: T, testId: 'a', kind: 'target' }], resolving);
    expect(s.phase).toBe('fallback');
    expect(effects[0]).toMatchObject({ name: 'tour-error', detail: { reason: 'target-timeout', fatal: false, stepId: 'a', testId: 'a', kind: 'target' } });
    expect(effects).toContainEqual({ type: 'wait', mode: 'fallback', index: 0, token: s.token });
    const recovered = run([{ type: 'RESOLVED', token: T }], s);
    expect(recovered.effects[0]).toMatchObject({ name: 'tour-step', detail: { reason: 'recovered' } });
    expect(run([{ type: 'SKIP', token: T }], s).s).toMatchObject({ phase: 'resolving', index: 1, reason: 'skip' });
  });

  it('a lost target pauses after its deadline and auto-resumes when back', () => {
    const lost = run([{ type: 'TARGET_LOST', token: T }], started().s);
    expect(lost.s.phase).toBe('lost');
    expect(run([{ type: 'TARGET_BACK', token: T }], lost.s)).toMatchObject({ s: { phase: 'showing' }, effects: [] });
    const paused = run([{ type: 'LOST_TIMEOUT', token: T }], lost.s);
    expect(paused.s.phase).toBe('paused');
    expect(paused.effects[0]).toMatchObject({ name: 'tour-offpath', detail: { stepId: 'a', reason: 'target-lost' } });
    expect(run([{ type: 'TARGET_BACK', token: T }], paused.s).effects[0]).toMatchObject({ name: 'tour-step', detail: { reason: 'resume' } });
    expect(run([{ type: 'RESUME', token: T }], paused.s).s.phase).toBe('resolving');
  });
});

describe('jumps', () => {
  it('a click on a later click-step target counts as performed', () => {
    const { s } = run([{ type: 'JUMP', token: T, to: 2, performed: true }], started().s);
    expect(s).toMatchObject({ phase: 'resolving', index: 3, reason: 'jump' });
  });
  it('otherwise the tour moves to that step', () => {
    const { s } = run([{ type: 'JUMP', token: T, to: 2, performed: false }], started().s);
    expect(s).toMatchObject({ phase: 'resolving', index: 2, reason: 'jump' });
  });
  it('never jumps backwards or to a step that does not apply', () => {
    const shown = started().s;
    expect(run([{ type: 'JUMP', token: T, to: 0, performed: true }], shown).s).toBe(shown);
  });
});

describe('dismissing', () => {
  it.each(['close', 'escape', 'end', 'api', 'replaced', 'disabled'] as const)('via %s', (via) => {
    const { s, effects } = run([{ type: 'DISMISS', via, reason: via === 'api' ? 'host' : undefined }], started().s);
    expect(s.phase).toBe('idle');
    expect(effects[0]).toMatchObject({ name: 'tour-dismiss', detail: { stepId: 'a', index: 0, total: 4, via } });
    expect(effects.slice(1)).toEqual([{ type: 'clearPersist' }, { type: 'teardown' }]);
  });
  it('while loading, quietly', () => {
    const { effects } = run([{ type: 'REQUEST' }, { type: 'DISMISS', via: 'api' }]);
    expect(emitted(effects)).toEqual([]);
  });
  it('a late load result after a dismiss is ignored', () => {
    const { s } = run([{ type: 'REQUEST' }, { type: 'DISMISS', via: 'api' }, { type: 'LOADED', loaded: loaded() }]);
    expect(s.phase).toBe('idle');
  });
});

describe('locale swap', () => {
  it('swaps a tour with the same steps, refuses one that differs', () => {
    const { s } = started();
    const fr = { ...TOUR, locale: 'fr', title: 'Créer' };
    expect(run([{ type: 'LOCALE', tour: fr }], s).s.loaded!.tour.locale).toBe('fr');
    const other = { ...fr, steps: TOUR.steps.slice(1) };
    expect(run([{ type: 'LOCALE', tour: other }], s).s.loaded!.tour.locale).toBe('en');
  });
});
