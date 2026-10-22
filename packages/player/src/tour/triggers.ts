/**
 * Turns what the user does *in the app* into engine events: document-level
 * capture listeners that never stop or alter the app's own events.
 *
 * - A click whose path holds the current step's test-id advances a ``click``
 *   step; a click on a *later* step's target jumps there (performed when that
 *   step is itself a click step). The resume record is written synchronously,
 *   before any navigation the click may cause; the engine is told on the next
 *   task so the app's handlers run first.
 * - ``input`` / ``change`` on an ``input`` step's target enable Next / commit.
 * - Any interaction outside the overlay arms a ``change`` step's watcher.
 * - Escape dismisses the tour unless the app is using it (a dialog, an open
 *   popup, ``preventDefault``, an IME composition).
 *
 * Events whose path goes through the overlay are its own UI and are ignored.
 * Untrusted (scripted) events count like trusted ones: the CI replay clicks
 * with synthetic events.
 */

import type { EngineEvent, EngineState } from './engine.js';
import type { TourStep } from './validate.js';
import { isExpanded, readFieldValue } from './signature.js';

export interface TriggerContext {
  host: HTMLElement;
  attr: string;
  state(): EngineState;
  dispatch(e: EngineEvent): void;
  /** The current step's resolved target. */
  target(): Element | null;
  /** Called synchronously when a click advances or jumps: persist the next step. */
  persistAhead(fromIndex: number): void;
  /** Any interaction outside the overlay (arms ``change`` watchers). */
  interacted(): void;
  /** A ``change`` event or a document click: re-check a ``change`` step. */
  tick(): void;
  defer(cb: () => void): void;
}

/** An app dialog: Escape inside one is the app's, and a centred bubble moves off an open one. */
export const APP_DIALOG = 'dialog,[role=dialog],[role=alertdialog],[aria-modal="true"]';

function appPath(e: Event, host: HTMLElement): Element[] | null {
  const path = e.composedPath().slice(0, 64);
  if (path.includes(host)) return null;
  return path.filter((n): n is Element => n instanceof Element);
}

export function attachTriggers(ctx: TriggerContext): () => void {
  const { host, attr } = ctx;
  const current = (): { s: EngineState; step: TourStep } | null => {
    const s = ctx.state();
    if (!s.loaded || s.phase === 'idle' || s.phase === 'loading' || s.phase === 'entering') return null;
    return { s, step: s.loaded.tour.steps[s.index]! };
  };

  const onClick = (e: Event) => {
    const path = appPath(e, host);
    if (!path) return;
    ctx.interacted();
    ctx.tick();
    const cur = current();
    if (!cur) return;
    const { s, step } = cur;
    const ids = new Set(path.map((el) => el.getAttribute(attr)).filter((v): v is string => v !== null));
    const token = s.token;
    if (s.phase === 'showing' && step.advance === 'click' && step.target !== null && ids.has(step.target)) {
      ctx.persistAhead(s.index);
      ctx.defer(() => ctx.dispatch({ type: 'TRIGGER', token, kind: 'click' }));
      return;
    }
    const steps = s.loaded!.tour.steps;
    const to = steps.findIndex((st, j) => j > s.index && s.loaded!.applicable[j] && st.target !== null && ids.has(st.target));
    if (to === -1) return;
    const performed = steps[to]!.advance === 'click';
    if (performed) ctx.persistAhead(to);
    ctx.defer(() => ctx.dispatch({ type: 'JUMP', token, to, performed }));
  };

  const inTarget = (e: Event): boolean => {
    const t = ctx.target();
    return t !== null && e.composedPath().includes(t);
  };

  const onInput = (e: Event) => {
    if (!appPath(e, host)) return;
    ctx.interacted();
    const cur = current();
    if (cur?.s.phase === 'showing' && cur.step.advance === 'input' && inTarget(e)) {
      ctx.dispatch({ type: 'FIELD', token: cur.s.token, nonEmpty: readFieldValue(ctx.target()!).trim() !== '' });
    }
  };

  const onChange = (e: Event) => {
    if (!appPath(e, host)) return;
    ctx.interacted();
    ctx.tick();
    const cur = current();
    if (cur?.s.phase === 'showing' && cur.step.advance === 'input' && inTarget(e)) {
      const token = cur.s.token;
      if (readFieldValue(ctx.target()!).trim() !== '') ctx.defer(() => ctx.dispatch({ type: 'TRIGGER', token, kind: 'input-commit' }));
    }
  };

  const onPress = (e: Event) => {
    if (appPath(e, host)) ctx.interacted();
  };

  const onEscape = (e: Event) => {
    const k = e as KeyboardEvent;
    if (k.key !== 'Escape' || k.defaultPrevented || k.isComposing) return;
    const cur = current();
    if (!cur) return;
    const path = k.composedPath();
    const ours = path.indexOf(host);
    const appElements = (ours === -1 ? path : path.slice(ours + 1)).filter((n): n is Element => n instanceof Element);
    if (appElements.some((el) => el.matches(`${APP_DIALOG},[aria-expanded="true"]`))) return;
    // Only a target still in the page counts: one inside a dialog the app has
    // since closed is detached, and Escape is the user's again.
    const target = ctx.target();
    if (target?.isConnected && (target.closest(APP_DIALOG) || isExpanded(target))) return;
    ctx.dispatch({ type: 'DISMISS', via: 'escape' });
  };

  const opts = { capture: true };
  document.addEventListener('click', onClick, opts);
  document.addEventListener('input', onInput, opts);
  document.addEventListener('change', onChange, opts);
  document.addEventListener('pointerdown', onPress, opts);
  document.addEventListener('mousedown', onPress, opts);
  document.addEventListener('keydown', onPress, opts);
  window.addEventListener('keydown', onEscape);
  return () => {
    document.removeEventListener('click', onClick, opts);
    document.removeEventListener('input', onInput, opts);
    document.removeEventListener('change', onChange, opts);
    document.removeEventListener('pointerdown', onPress, opts);
    document.removeEventListener('mousedown', onPress, opts);
    document.removeEventListener('keydown', onPress, opts);
    window.removeEventListener('keydown', onEscape);
  };
}

/**
 * Watches a ``change`` step: the value signature is sampled until the user
 * first interacts (so a label the app fills in asynchronously is not taken
 * for a change), then any difference — ignored while a popup is open, since
 * the signature of an open dropdown is not a choice — advances the step.
 */
export function watchChange(
  target: Element,
  signature: (el: Element) => string,
  onChange: () => void,
  schedule: { raf(cb: () => void): number; setInterval(cb: () => void, ms: number): number; clearInterval(id: number): void },
): { arm(): void; tick(): void; stop(): void } {
  let base = signature(target);
  let armed = false;
  let stopped = false;
  let pending = false;
  const tick = () => {
    pending = false;
    if (stopped || isExpanded(target)) return;
    const sig = signature(target);
    if (!armed) {
      base = sig;
      return;
    }
    if (sig !== base) {
      stopped = true;
      cleanup();
      onChange();
    }
  };
  const soon = () => {
    if (!pending && !stopped) {
      pending = true;
      schedule.raf(tick);
    }
  };
  const observer = new MutationObserver(soon);
  observer.observe(target, { attributes: true, characterData: true, childList: true, subtree: true });
  const poll = schedule.setInterval(tick, 300);
  const cleanup = () => {
    observer.disconnect();
    schedule.clearInterval(poll);
  };
  return {
    arm() {
      armed = true;
    },
    tick: soon,
    stop() {
      stopped = true;
      cleanup();
    },
  };
}
