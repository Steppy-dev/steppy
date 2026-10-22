/**
 * Test-id resolution: ``[data-testid="<id>"]`` (the manifest's attribute)
 * against the document — light DOM only — skipping the overlay itself, first
 * *visible* match wins.
 *
 * Waiting is event-driven and only runs while the tour needs an element: a
 * document MutationObserver (filtered to attributes that can change
 * visibility, coalesced to one check per frame) plus a slow poll for changes
 * no mutation reports (a stylesheet swap, a CSS transition finishing). Time
 * spent in a background tab does not count against the deadline.
 */

import type { DomAdapter } from './dom.js';

const POLL_MS = 250;
const MAX_MATCHES = 500;

export interface WaitRequest {
  ids: readonly string[];
  /** Milliseconds to wait, or null for no deadline. */
  timeoutMs: number | null;
  onFound(found: Map<string, Element>): void;
  /** Called with the first id still missing. */
  onTimeout(missing: string): void;
}

export interface Resolver {
  find(id: string): Element | null;
  /** Start waiting; returns the cancel function. */
  wait(req: WaitRequest): () => void;
}

export function createResolver(opts: { attr: string; host: Element; dom: DomAdapter }): Resolver {
  const { attr, host, dom } = opts;
  const s = dom.scheduler;

  function find(id: string): Element | null {
    const selector = `[${attr}="${CSS.escape(id)}"]`;
    const all = document.querySelectorAll(selector);
    for (let i = 0; i < all.length && i < MAX_MATCHES; i++) {
      const el = all[i]!;
      if (el === host || host.contains(el)) continue;
      if (dom.isVisible(el)) return el;
    }
    return null;
  }

  function wait(req: WaitRequest): () => void {
    let done = false;
    let frame = 0;
    let hiddenSince: number | null = document.hidden ? s.now() : null;
    let hiddenTotal = 0;
    const started = s.now();
    const attributeFilter = [attr, 'class', 'style', 'hidden', 'open', 'aria-hidden', 'aria-expanded'];

    const check = (): boolean => {
      frame = 0;
      if (done) return true;
      const found = new Map<string, Element>();
      let missing: string | null = null;
      for (const id of req.ids) {
        const el = find(id);
        if (el) found.set(id, el);
        else if (missing === null) missing = id;
      }
      if (missing === null) {
        stop();
        req.onFound(found);
        return true;
      }
      if (req.timeoutMs !== null) {
        const hidden = hiddenSince !== null ? s.now() - hiddenSince : 0;
        if (hiddenSince === null && s.now() - started - hiddenTotal - hidden >= req.timeoutMs) {
          stop();
          req.onTimeout(missing);
          return true;
        }
      }
      return false;
    };
    const schedule = () => {
      if (!frame && !done) frame = s.raf(check);
    };
    const onVisibility = () => {
      if (document.hidden) hiddenSince ??= s.now();
      else if (hiddenSince !== null) {
        hiddenTotal += s.now() - hiddenSince;
        hiddenSince = null;
      }
    };
    const observer = new MutationObserver(schedule);
    const poll = s.setInterval(() => check(), POLL_MS);
    function stop() {
      done = true;
      observer.disconnect();
      s.clearInterval(poll);
      if (frame) s.cancelRaf(frame);
      document.removeEventListener('visibilitychange', onVisibility);
    }

    if (!check()) {
      observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter });
      document.addEventListener('visibilitychange', onVisibility);
    }
    return stop;
  }

  return { find, wait };
}
