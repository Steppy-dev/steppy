/**
 * The tour state machine, as a pure reducer: ``reduce(state, event)`` returns
 * the next state and the effects the host must carry out (load, navigate, wait
 * for an element, emit an event, persist the resume record, ...). No DOM, no
 * timers — every transition is a unit test.
 *
 * Async results come back as events carrying the ``token`` they were started
 * under; ``token`` changes whenever a step or phase change invalidates pending
 * work, so a late answer from an abandoned wait is dropped instead of moving
 * the tour.
 *
 * Phases: ``idle`` → ``loading`` → (``entering``) → ``resolving`` →
 * ``showing``; a target that does not appear in time puts the step in
 * ``fallback`` (the "can't find this element" popover, with Skip / End); a
 * target that disappears while shown goes ``lost``, then ``paused`` (the pill)
 * when it stays away.
 */

import type { AdvanceKind, TourErrorReason } from './contract.js';
import type { Issue, TourFile } from './validate.js';

export type Phase = 'idle' | 'loading' | 'entering' | 'resolving' | 'showing' | 'lost' | 'fallback' | 'paused';
export type StepReason = 'start' | 'advance' | 'jump' | 'skip' | 'resume' | 'recovered';
export type DismissVia = 'close' | 'escape' | 'end' | 'api' | 'replaced' | 'disabled';
export type TourSourceKind = 'bundled' | 'remote' | 'custom';
export type TriggerKind = 'click' | 'input-commit' | 'change' | 'visible';
export type WaitMode = 'resolve' | 'fallback' | 'lost' | 'paused' | 'visible';

export interface Loaded {
  tour: TourFile;
  source: TourSourceKind;
  /** Per step: does it apply to the current layout (``tours.layouts``)? */
  applicable: readonly boolean[];
  start: number;
  resumed: boolean;
  /** The entry route must be navigated to before the first step. */
  navigate: boolean;
}

export interface TourErrorDetail {
  reason: TourErrorReason;
  fatal: boolean;
  message: string;
  tourId?: string;
  buildId?: string;
  locale?: string;
  stepId?: string;
  index?: number;
  testId?: string;
  kind?: 'target' | 'waitFor';
  missing?: string[];
  issues?: Issue[];
}

export interface EngineState {
  readonly phase: Phase;
  readonly token: number;
  readonly loaded: Loaded | null;
  readonly index: number;
  /** The ``tour-step`` reason to report when the pending step shows. */
  readonly reason: StepReason;
  readonly fieldNonEmpty: boolean;
}

export type EngineEvent =
  | { type: 'REQUEST' }
  | { type: 'LOADED'; loaded: Loaded }
  | { type: 'LOAD_FAILED'; error: TourErrorDetail }
  | { type: 'ENTER_DONE'; token: number }
  | { type: 'ENTER_FAILED'; token: number; message: string }
  | { type: 'RESOLVED'; token: number; fieldNonEmpty?: boolean }
  | { type: 'RESOLVE_TIMEOUT'; token: number; testId: string | null; kind: 'target' | 'waitFor' }
  | { type: 'TRIGGER'; token: number; kind: TriggerKind }
  | { type: 'NEXT'; token?: number }
  | { type: 'SKIP'; token: number }
  | { type: 'FIELD'; token: number; nonEmpty: boolean }
  | { type: 'JUMP'; token: number; to: number; performed: boolean }
  | { type: 'TARGET_LOST'; token: number }
  | { type: 'TARGET_BACK'; token: number }
  | { type: 'LOST_TIMEOUT'; token: number }
  | { type: 'RESUME'; token: number }
  | { type: 'DISMISS'; via: DismissVia; reason?: string }
  | { type: 'LOCALE'; tour: TourFile };

export type Effect =
  | { type: 'load' }
  | { type: 'emit'; name: 'tour-start' | 'tour-step' | 'tour-complete' | 'tour-dismiss' | 'tour-offpath' | 'tour-error'; detail: object }
  | { type: 'persist'; index: number }
  | { type: 'clearPersist' }
  | { type: 'navigate'; token: number }
  | { type: 'wait'; mode: WaitMode; index: number; token: number }
  | { type: 'focus' }
  | { type: 'teardown' };

export interface Transition {
  state: EngineState;
  effects: Effect[];
}

export function initialState(): EngineState {
  return { phase: 'idle', token: 0, loaded: null, index: 0, reason: 'start', fieldNonEmpty: false };
}

export const isActive = (s: EngineState): boolean => s.phase !== 'idle' && s.phase !== 'loading';

/** Position among applicable steps (0-based) and their count — what progress shows. */
export function position(s: EngineState, index = s.index): { index: number; total: number } {
  const applicable = s.loaded?.applicable ?? [];
  return { index: applicable.slice(0, index).filter(Boolean).length, total: applicable.filter(Boolean).length };
}

function meta(s: EngineState) {
  const tour = s.loaded!.tour;
  return { tourId: tour.id, buildId: tour.buildId, locale: tour.locale };
}

function stepDetail(s: EngineState) {
  const step = s.loaded!.tour.steps[s.index]!;
  return { ...meta(s), stepId: step.id, ...position(s) };
}

const emit = (name: Extract<Effect, { type: 'emit' }>['name'], detail: object): Effect => ({ type: 'emit', name, detail });

/** Enter ``index`` in ``resolving`` (a fresh token: every pending wait is void). */
function resolveStep(s: EngineState, index: number, reason: StepReason): Transition {
  const state: EngineState = { ...s, phase: 'resolving', index, reason, token: s.token + 1, fieldNonEmpty: false };
  return { state, effects: [{ type: 'persist', index }, { type: 'wait', mode: 'resolve', index, token: state.token }] };
}

/** Move past ``from`` to the next applicable step, or complete the tour. */
function advanceFrom(s: EngineState, from: number, reason: StepReason): Transition {
  const applicable = s.loaded!.applicable;
  const next = applicable.findIndex((ok, j) => ok && j > from);
  if (next === -1) {
    const done = { ...initialState(), token: s.token + 1 };
    return {
      state: done,
      effects: [emit('tour-complete', { ...meta(s), total: position(s).total }), { type: 'clearPersist' }, { type: 'teardown' }],
    };
  }
  return resolveStep(s, next, reason);
}

function step(s: EngineState) {
  return s.loaded!.tour.steps[s.index]!;
}

const TRIGGER_FOR: Record<TriggerKind, AdvanceKind> = {
  click: 'click',
  'input-commit': 'input',
  change: 'change',
  visible: 'visible',
};

export function reduce(s: EngineState, e: EngineEvent): Transition {
  const none: Transition = { state: s, effects: [] };
  if ('token' in e && e.token !== undefined && e.token !== s.token) return none;

  switch (e.type) {
    case 'REQUEST':
      return { state: { ...initialState(), phase: 'loading', token: s.token + 1 }, effects: [{ type: 'load' }] };

    case 'LOAD_FAILED':
      if (s.phase !== 'loading') return none;
      return {
        state: { ...initialState(), token: s.token + 1 },
        effects: [emit('tour-error', e.error), { type: 'clearPersist' }, { type: 'teardown' }],
      };

    case 'LOADED': {
      if (s.phase !== 'loading') return none;
      const loaded = e.loaded;
      const base: EngineState = { ...s, loaded, index: loaded.start };
      const start = emit('tour-start', {
        ...meta(base),
        total: position(base).total,
        source: loaded.source,
        resumed: loaded.resumed,
        entryNavigated: loaded.navigate,
      });
      const reason: StepReason = loaded.resumed ? 'resume' : 'start';
      if (loaded.navigate) {
        const state: EngineState = { ...base, phase: 'entering', reason, token: s.token + 1 };
        return { state, effects: [start, { type: 'persist', index: loaded.start }, { type: 'navigate', token: state.token }] };
      }
      const t = resolveStep(base, loaded.start, reason);
      return { state: t.state, effects: [start, ...t.effects] };
    }

    case 'ENTER_DONE':
      return s.phase === 'entering' ? resolveStep(s, s.index, s.reason) : none;

    case 'ENTER_FAILED':
      if (s.phase !== 'entering') return none;
      return {
        state: { ...initialState(), token: s.token + 1 },
        effects: [
          emit('tour-error', { reason: 'entry-params', fatal: true, message: e.message, ...meta(s) }),
          { type: 'clearPersist' },
          { type: 'teardown' },
        ],
      };

    case 'RESOLVED': {
      if (s.phase !== 'resolving' && s.phase !== 'fallback') return none;
      const reason: StepReason = s.phase === 'fallback' ? 'recovered' : s.reason;
      const state: EngineState = { ...s, phase: 'showing', reason, token: s.token + 1, fieldNonEmpty: e.fieldNonEmpty ?? false };
      const st = step(state);
      const effects: Effect[] = [emit('tour-step', { ...stepDetail(state), advance: st.advance, reason })];
      if (st.advance === 'manual') effects.push({ type: 'focus' });
      if (st.advance === 'visible') effects.push({ type: 'wait', mode: 'visible', index: s.index, token: state.token });
      return { state, effects };
    }

    case 'RESOLVE_TIMEOUT': {
      const visibleWait = s.phase === 'showing' && step(s).advance === 'visible';
      if (s.phase !== 'resolving' && !visibleWait) return none;
      const state: EngineState = { ...s, phase: 'fallback', token: s.token + 1 };
      const detail = { ...stepDetail(state), reason: 'target-timeout', fatal: false, message: 'the target did not appear in time' };
      return {
        state,
        effects: [
          emit('tour-error', { ...detail, ...(e.testId ? { testId: e.testId } : {}), kind: e.kind }),
          { type: 'wait', mode: 'fallback', index: s.index, token: state.token },
          { type: 'focus' },
        ],
      };
    }

    case 'TRIGGER':
      if (s.phase !== 'showing' || step(s).advance !== TRIGGER_FOR[e.kind]) return none;
      return advanceFrom(s, s.index, 'advance');

    case 'NEXT': {
      const api = e.token === undefined;
      if (s.phase === 'fallback' && api) return advanceFrom(s, s.index, 'skip');
      if (s.phase !== 'showing') return none;
      if (!api && step(s).advance === 'input' && !s.fieldNonEmpty) return none;
      return advanceFrom(s, s.index, 'advance');
    }

    case 'SKIP':
      return s.phase === 'fallback' ? advanceFrom(s, s.index, 'skip') : none;

    case 'FIELD':
      return s.phase === 'showing' && s.fieldNonEmpty !== e.nonEmpty ? { state: { ...s, fieldNonEmpty: e.nonEmpty }, effects: [] } : none;

    case 'JUMP': {
      const active = s.phase === 'showing' || s.phase === 'lost' || s.phase === 'paused' || s.phase === 'fallback';
      if (!active || e.to <= s.index || !s.loaded!.applicable[e.to]) return none;
      return e.performed ? advanceFrom(s, e.to, 'jump') : resolveStep(s, e.to, 'jump');
    }

    case 'TARGET_LOST':
      if (s.phase !== 'showing') return none;
      return lost(s);

    case 'TARGET_BACK': {
      if (s.phase !== 'lost' && s.phase !== 'paused') return none;
      const state: EngineState = { ...s, phase: 'showing', token: s.token + 1 };
      if (s.phase === 'lost') return { state, effects: [] }; // a silent re-show
      return {
        state: { ...state, reason: 'resume' },
        effects: [emit('tour-step', { ...stepDetail(state), advance: step(state).advance, reason: 'resume' })],
      };
    }

    case 'LOST_TIMEOUT': {
      if (s.phase !== 'lost') return none;
      const state: EngineState = { ...s, phase: 'paused', token: s.token + 1 };
      return {
        state,
        effects: [
          emit('tour-offpath', { ...stepDetail(state), reason: 'target-lost' }),
          { type: 'wait', mode: 'paused', index: s.index, token: state.token },
        ],
      };
    }

    case 'RESUME':
      return s.phase === 'paused' ? resolveStep(s, s.index, 'resume') : none;

    case 'DISMISS': {
      if (s.phase === 'idle') return none;
      const effects: Effect[] = [];
      if (s.loaded && s.phase !== 'loading') {
        effects.push(emit('tour-dismiss', { ...stepDetail(s), via: e.via, ...(e.reason ? { reason: e.reason } : {}) }));
      }
      effects.push({ type: 'clearPersist' }, { type: 'teardown' });
      return { state: { ...initialState(), token: s.token + 1 }, effects };
    }

    case 'LOCALE': {
      if (!isActive(s)) return none;
      const old = s.loaded!.tour.steps;
      const same =
        e.tour.steps.length === old.length &&
        e.tour.steps.every((st, i) => st.id === old[i]!.id && st.target === old[i]!.target && st.advance === old[i]!.advance);
      return same ? { state: { ...s, loaded: { ...s.loaded!, tour: e.tour } }, effects: [] } : none;
    }
  }
}

function lost(s: EngineState): Transition {
  const state: EngineState = { ...s, phase: 'lost', token: s.token + 1 };
  return { state, effects: [{ type: 'wait', mode: 'lost', index: s.index, token: state.token }] };
}
