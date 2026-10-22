/**
 * ``<steppy-tour-host>`` — mount it once in the app shell and call
 * ``start(tourId)``. The element interprets the engine's effects (``engine.ts``)
 * and owns the browser-facing pieces: loader, resolver, tracker, triggers,
 * focus, the live region and the resume record. It renders nothing while no
 * tour runs.
 *
 * Two switches keep it out of the way: the global
 * ``__STEPPY_TOURS_DISABLED__`` (set by generated E2E tests so an app that
 * auto-starts a tour cannot cover what a test clicks; read at call time) and
 * the ``disabled`` property. Either makes ``start()`` a no-op.
 */

import { LitElement, nothing, type PropertyValues } from 'lit';

import { TOUR_ACTIVE_SYMBOL_KEY, TOUR_HOST_TAG, TOURS_KILL_SWITCH_GLOBAL } from '../ui/names.js';
import type { LinkPolicy } from '../ui/markdown.js';
import type { Rect, TooltipSide } from '../ui/placement.js';
import type { TourHostApi, TourNavigate, TourPosition, TourStartOptions } from './api.js';
import { DEFAULT_BASE_Z_INDEX, DEFAULT_TARGET_TIMEOUT_MS } from './contract.js';
import { defaultDom, type DomAdapter } from './dom.js';
import {
  initialState,
  isActive,
  position,
  reduce,
  type Effect,
  type EngineEvent,
  type EngineState,
  type TourErrorDetail,
  type WaitMode,
} from './engine.js';
import { emitTourEvent, type TourEventDetailMap } from './events.js';
import { raiseLayer, showLayer } from './layer.js';
import { loadTour, type LoaderConfig, type LoadOk, type ManifestLoad, type TourSource } from './loader.js';
import { createResolver, type Resolver } from './resolver.js';
import { basePath, buildRoute, linkHref, matchRoute, toAppPath, toDocumentPath } from './routes.js';
import { isComboboxLike, readFieldValue, valueSignature } from './signature.js';
import { clearResume, readResume, writeResume } from './storage.js';
import { resolveTourStrings, textDir, type TourStringOverrides } from './strings.js';
import { tourStyles } from './styles.js';
import { renderLayer, type Handlers } from './templates.js';
import { createTracker, type Tracker } from './tracker.js';
import { APP_DIALOG, attachTriggers, watchChange } from './triggers.js';
import { httpOrigin, type TourStep } from './validate.js';

const ACTIVE = Symbol.for(TOUR_ACTIVE_SYMBOL_KEY);
const KEYBOARD_GRACE_MS = 300;
const TIMEOUT_MIN = 500;
const TIMEOUT_MAX = 120_000;

type GlobalWithActive = typeof globalThis & { [ACTIVE]?: SteppyTourHost | null } & Record<string, unknown>;
const g = globalThis as GlobalWithActive;

const killSwitch = (): boolean => g[TOURS_KILL_SWITCH_GLOBAL] === true;

interface Run {
  tourId: string;
  params: Record<string, string>;
  load: LoadOk;
  entryPath: string | null;
  base: string;
}

export interface SteppyTourHost {
  addEventListener<K extends keyof TourEventDetailMap>(
    type: K,
    listener: (this: SteppyTourHost, ev: CustomEvent<TourEventDetailMap[K]>) => void,
    options?: boolean | AddEventListenerOptions,
  ): void;
  addEventListener(type: string, listener: EventListenerOrEventListenerObject, options?: boolean | AddEventListenerOptions): void;
}

export class SteppyTourHost extends LitElement implements TourHostApi {
  static override styles = tourStyles;
  static override properties = {
    manifest: { type: String },
    fallbackManifest: { type: String, attribute: 'fallback-manifest' },
    buildId: { type: String, attribute: 'build-id' },
    locale: { type: String },
    guideOrigin: { type: String, attribute: 'guide-origin' },
    baseZIndex: { type: Number, attribute: 'base-z-index' },
    targetTimeout: { type: Number, attribute: 'target-timeout' },
    disabled: { type: Boolean, reflect: true },
    source: { attribute: false },
    navigate: { attribute: false },
    strings: { attribute: false },
  };

  /** Test seam: geometry and timing. */
  static dom: DomAdapter = defaultDom;

  manifest = '';
  fallbackManifest = '';
  buildId = '';
  locale = '';
  guideOrigin = '';
  baseZIndex = DEFAULT_BASE_Z_INDEX;
  targetTimeout = DEFAULT_TARGET_TIMEOUT_MS;
  disabled = false;
  source: TourSource | null = null;
  navigate: TourNavigate | null = null;
  strings: TourStringOverrides | undefined = undefined;

  private _s: EngineState = initialState();
  private _queue: EngineEvent[] = [];
  private _dispatching = false;
  private _run: Run | null = null;
  private _cache = new Map<string, Promise<ManifestLoad>>();
  private _resolver: Resolver | null = null;
  private _cancelWait: (() => void) | null = null;
  private _target: Element | null = null;
  private _tracker: Tracker | null = null;
  private _detach: (() => void) | null = null;
  private _change: ReturnType<typeof watchChange> | null = null;
  private _shownKey = '';
  private _shownAt = 0;
  private _pressToken = -1;
  private _focusPending = false;
  private _appFocus: Element | null = null;
  private _swapSeq = 0;
  /** While a tour runs: an app that switches language through ``<html lang>`` alone. */
  private _lang = new MutationObserver(() => {
    if (!this.locale) void this._swapLocale();
  });
  private _policy: LinkPolicy = { autolink: false, link: (url) => this._link(url) };
  private _handlers: Handlers = {
    press: () => {
      this._pressToken = this._s.token;
    },
    next: (e) => this._guarded(e, () => this._dispatch({ type: 'NEXT', token: this._s.token })),
    skip: (e) => this._guarded(e, () => this._dispatch({ type: 'SKIP', token: this._s.token })),
    end: (e) => this._guarded(e, () => this._dispatch({ type: 'DISMISS', via: 'end' })),
    close: (e) => this._guarded(e, () => this._dispatch({ type: 'DISMISS', via: 'close' })),
    resume: (e) => this._guarded(e, () => this._dispatch({ type: 'RESUME', token: this._s.token })),
  };

  private get _dom(): DomAdapter {
    return (this.constructor as typeof SteppyTourHost).dom;
  }

  // ── Public API ──────────────────────────────────────────────────────────

  get isActive(): boolean {
    return isActive(this._s);
  }

  get current(): TourPosition | null {
    if (!isActive(this._s) || !this._s.loaded) return null;
    const step = this._s.loaded.tour.steps[this._s.index]!;
    return { tourId: this._s.loaded.tour.id, stepId: step.id, ...position(this._s) };
  }

  async start(tourId: string, options: TourStartOptions = {}): Promise<boolean> {
    return this._start(tourId, options, false);
  }

  stop(reason?: string): void {
    this._dispatch({ type: 'DISMISS', via: 'api', reason });
  }

  next(): void {
    this._dispatch({ type: 'NEXT' });
  }

  // ── Lifecycle ───────────────────────────────────────────────────────────

  override connectedCallback(): void {
    super.connectedCallback();
    this.style.setProperty('--_pw-z', String(this.baseZIndex));
  }

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    // Deferred: a re-parenting moves the element and reconnects it at once.
    queueMicrotask(() => {
      if (!this.isConnected) this._teardown(false);
    });
  }

  protected override willUpdate(changed: PropertyValues<this>): void {
    if (changed.has('manifest') || changed.has('fallbackManifest') || changed.has('buildId')) this._cache.clear();
    if (changed.has('baseZIndex')) this.style.setProperty('--_pw-z', String(this.baseZIndex));
    if (changed.has('disabled') && this.disabled && this._s.phase !== 'idle') this._dispatch({ type: 'DISMISS', via: 'disabled' });
  }

  protected override updated(changed: PropertyValues<this>): void {
    if (changed.has('locale') && this.isActive) void this._swapLocale();
    if ((changed.has('manifest') || changed.has('fallbackManifest')) && this.hasUpdated) this._tryResume();
    this._afterRender();
  }

  protected override firstUpdated(): void {
    this._tryResume();
  }

  protected override render() {
    const s = this._s;
    if (!this._run || !s.loaded || !isActive(s)) return nothing;
    const step = s.loaded.tour.steps[s.index]!;
    const mode = s.phase === 'showing' || s.phase === 'fallback' ? 'bubble' : s.phase === 'paused' ? 'pill' : 'none';
    const pos = position(s);
    return renderLayer(
      {
        mode,
        lang: s.loaded.tour.locale.replace(/_/g, '-'),
        dir: textDir(s.loaded.tour.locale),
        tourId: s.loaded.tour.id,
        step,
        state: s.phase === 'fallback' ? 'fallback' : 'shown',
        anchored: s.phase === 'showing' && this._target !== null,
        position: pos,
        last: pos.index === pos.total - 1,
        nextEnabled: step.advance !== 'input' || s.fieldNonEmpty,
        guideUrl: s.loaded.tour.guideUrl,
        strings: resolveTourStrings(s.loaded.tour.locale, this.strings),
        policy: this._policy,
        popover: this._dom.supportsPopover(),
      },
      this._handlers,
    );
  }

  // ── Starting ────────────────────────────────────────────────────────────

  private async _start(tourId: string, options: TourStartOptions, resumed: boolean): Promise<boolean> {
    try {
      if (killSwitch() || this.disabled) {
        console.info(`[steppy-tour] "${tourId}" not started: tours are disabled on this page`);
        return false;
      }
      const other = g[ACTIVE];
      if (other && other !== this) other._dispatch({ type: 'DISMISS', via: 'replaced' });
      if (this._s.phase !== 'idle') this._dispatch({ type: 'DISMISS', via: 'replaced' });
      this._dispatch({ type: 'REQUEST' });
      const token = this._s.token;
      const params = Object.fromEntries(Object.entries(options.params ?? {}).map(([k, v]) => [k, String(v)]));
      const locale = this._preferredLocale();
      const load = await loadTour(this._cfg(locale), tourId);
      if (this._s.token !== token) return false;
      if (!load.ok) return this._fail({ reason: load.reason, fatal: true, message: load.message, tourId, issues: load.issues });

      const { tour, manifest } = load;
      const dom = this._dom;
      const layouts = manifest.layouts ?? {};
      const applicable = tour.steps.map((st) => !st.layouts || st.layouts.some((l) => dom.matchMedia(layouts[l]!)));
      const meta = { tourId, buildId: tour.buildId, locale: tour.locale };
      if (!applicable.some(Boolean)) return this._fail({ reason: 'load-failed', fatal: true, message: 'no steps apply to this layout', ...meta });
      let start = applicable.indexOf(true);
      if (options.step !== undefined) {
        const at = tour.steps.findIndex((st) => st.id === options.step);
        if (at >= 0 && applicable[at]) start = at;
        else if (resumed) {
          clearResume();
          return this._fail(null);
        }
      }

      const base = basePath(document.baseURI, location.origin);
      let entryPath: string | null = null;
      let all = params;
      if (tour.entry && !resumed) {
        const route = tour.entry.route;
        const here = matchRoute(route, toAppPath(location.pathname, base)) ?? matchRoute(route, location.pathname);
        all = { ...(here ?? {}), ...params };
        if (!here || Object.entries(params).some(([k, v]) => here[k] !== v)) {
          const built = buildRoute(route, all);
          if ('missing' in built) {
            return this._fail({
              reason: 'entry-params',
              fatal: true,
              message: `route ${route} needs ${built.missing.join(', ')}`,
              missing: built.missing,
              ...meta,
            });
          }
          entryPath = toDocumentPath(built.path, base);
        }
      }
      this._run = { tourId, params: all, load, entryPath, base };
      this._resolver = createResolver({ attr: load.testIdAttribute, host: this, dom });
      g[ACTIVE] = this;
      this._dispatch({
        type: 'LOADED',
        loaded: { tour, source: load.source, applicable, start, resumed, navigate: entryPath !== null },
      });
      this._lang.observe(document.documentElement, { attributeFilter: ['lang'] });
      // The app may have changed language while the files loaded (an async i18n
      // init racing a resumed tour): catch up now that there is a run to swap.
      if (this._preferredLocale() !== locale) void this._swapLocale();
      return true;
    } catch (err) {
      console.error('[steppy-tour] start failed', err);
      this._fail({ reason: 'load-failed', fatal: true, message: String(err), tourId });
      return false;
    }
  }

  private _fail(error: TourErrorDetail | null): false {
    if (error) this._dispatch({ type: 'LOAD_FAILED', error });
    else this._dispatch({ type: 'DISMISS', via: 'api' });
    return false;
  }

  private _tryResume(): void {
    if (this.disabled || killSwitch() || this._s.phase !== 'idle' || g[ACTIVE]) return;
    const key = this._manifestKey();
    const record = key ? readResume(key, Date.now()) : null;
    if (record) void this._start(record.tourId, { params: record.params, step: record.stepId }, true);
  }

  private _manifestKey(): string {
    return [this.manifest, this.fallbackManifest].filter(Boolean).join(' ');
  }

  /** The host's ``locale``, else the page's ``<html lang>``. */
  private _preferredLocale(): string {
    return this.locale || document.documentElement.lang;
  }

  private _cfg(locale: string): LoaderConfig {
    const { manifest, fallbackManifest, buildId, guideOrigin, source } = this;
    return { manifest, fallbackManifest, buildId, locale, guideOrigin, source, cache: this._cache };
  }

  /** Reload the running tour in the preferred locale; the last request wins. */
  private async _swapLocale(): Promise<void> {
    const run = this._run;
    if (!run) return;
    const seq = ++this._swapSeq;
    const locale = this._preferredLocale();
    const load = await loadTour(this._cfg(locale), run.tourId);
    if (seq !== this._swapSeq || this._run !== run) return;
    if (load.ok && load.tour.buildId === run.load.tour.buildId) {
      if (load.tour.locale === run.load.tour.locale) return;
      this._dispatch({ type: 'LOCALE', tour: load.tour });
      if (this._s.loaded?.tour === load.tour) {
        run.load = load;
        this._announce();
        return;
      }
    }
    console.warn(`[steppy-tour] locale "${locale}" not applied: ${load.ok ? 'the tour differs' : load.message}`);
  }

  // ── The engine loop ─────────────────────────────────────────────────────

  private _dispatch(event: EngineEvent): void {
    this._queue.push(event);
    if (this._dispatching) return;
    this._dispatching = true;
    const before = this._s;
    try {
      while (this._queue.length) {
        const t = reduce(this._s, this._queue.shift()!);
        this._s = t.state;
        for (const effect of t.effects) this._effect(effect);
      }
    } finally {
      this._dispatching = false;
    }
    if (this._s !== before) this.requestUpdate();
  }

  private _effect(e: Effect): void {
    const run = this._run;
    switch (e.type) {
      case 'emit':
        emitTourEvent(this, e.name, e.detail as never);
        break;
      case 'persist':
        if (run) this._persist(e.index);
        break;
      case 'clearPersist':
        clearResume();
        break;
      case 'navigate':
        void this._enter(e.token);
        break;
      case 'wait':
        this._wait(e.mode, e.index, e.token);
        break;
      case 'focus':
        this._focusPending = true;
        break;
      case 'teardown':
        this._teardown(true);
        break;
      case 'load':
        break;
    }
  }

  private _persist(index: number): void {
    const run = this._run!;
    const tour = run.load.tour;
    writeResume({
      v: 1,
      tourId: tour.id,
      stepId: tour.steps[index]!.id,
      locale: tour.locale,
      params: run.params,
      buildId: tour.buildId,
      manifestKey: this._manifestKey(),
      savedAt: Date.now(),
    });
  }

  private async _enter(token: number): Promise<void> {
    const run = this._run!;
    const path = run.entryPath!;
    const ctx = { appPath: toAppPath(path, run.base), tourId: run.tourId };
    try {
      const hook = this.navigate ?? ((p: string) => location.assign(p));
      await Promise.race([
        Promise.resolve(hook(path, ctx)),
        new Promise((r) => this._dom.scheduler.setTimeout(() => r(undefined), this._timeout())),
      ]);
      this._dispatch({ type: 'ENTER_DONE', token });
    } catch (err) {
      this._dispatch({ type: 'ENTER_FAILED', token, message: `navigation failed: ${String(err)}` });
    }
  }

  private _timeout(): number {
    const t = Number(this.targetTimeout);
    return Number.isFinite(t) ? Math.min(TIMEOUT_MAX, Math.max(TIMEOUT_MIN, t)) : DEFAULT_TARGET_TIMEOUT_MS;
  }

  private _wait(mode: WaitMode, index: number, token: number): void {
    this._cancelWait?.();
    const step = this._s.loaded!.tour.steps[index]!;
    const resolver = this._resolver!;
    const ids =
      mode === 'resolve' || mode === 'fallback'
        ? [step.advance === 'visible' ? null : step.target, step.waitFor].filter((v): v is string => v !== null)
        : [step.target!];
    const timeoutMs = mode === 'resolve' || mode === 'lost' || mode === 'visible' ? this._timeout() : null;
    this._cancelWait = resolver.wait({
      ids,
      timeoutMs,
      onFound: (found) => {
        this._cancelWait = null;
        // A visible-advance step resolves on its waitFor only: its target may
        // still be missing, and the step then shows centred until it appears.
        this._target = step.target ? (found.get(step.target) ?? resolver.find(step.target)) : null;
        // A pre-filled field enables Next from the start.
        const fieldNonEmpty = step.advance === 'input' && this._target !== null && readFieldValue(this._target).trim() !== '';
        const next: EngineEvent =
          mode === 'resolve' || mode === 'fallback'
            ? { type: 'RESOLVED', token, fieldNonEmpty }
            : mode === 'visible'
              ? { type: 'TRIGGER', token, kind: 'visible' }
              : { type: 'TARGET_BACK', token };
        this._dispatch(next);
      },
      onTimeout: (missing) => {
        this._cancelWait = null;
        if (mode === 'lost') return this._dispatch({ type: 'LOST_TIMEOUT', token });
        const kind = missing === step.waitFor && missing !== step.target ? 'waitFor' : 'target';
        this._dispatch({ type: 'RESOLVE_TIMEOUT', token, testId: missing, kind });
      },
    });
  }

  // ── Showing ─────────────────────────────────────────────────────────────

  private _layer(): HTMLElement | null {
    return this.renderRoot.querySelector<HTMLElement>('[part~="layer"]');
  }

  private _bubble(): HTMLElement | null {
    return this.renderRoot.querySelector<HTMLElement>('[part~="bubble"]');
  }

  private _afterRender(): void {
    const s = this._s;
    const layer = this._layer();
    const shown = s.phase === 'showing' || s.phase === 'fallback' || s.phase === 'paused';
    if (!layer || !shown || !s.loaded) {
      this._tracker?.stop();
      this._change?.stop();
      this._change = null;
      if (!isActive(s)) this._shownKey = '';
      return;
    }
    showLayer(layer, this._dom);
    this._ensureTriggers();
    const step = s.loaded.tour.steps[s.index]!;
    const key = `${s.loaded.tour.id}/${s.index}/${s.phase}`;
    const fresh = key !== this._shownKey;
    if (fresh) {
      this._shownKey = key;
      this._shownAt = this._dom.scheduler.now();
      raiseLayer(layer, this._dom);
      if (s.phase === 'showing') this._onStepShown(step);
      this._announce();
    }
    if (s.phase === 'showing' || s.phase === 'fallback') {
      if (!this._tracker) this._tracker = this._makeTracker();
      this._tracker.start();
      this._tracker.notify();
    } else this._tracker?.stop();
  }

  /** Manual and fallback steps take focus — once placed: a hidden bubble cannot be focused. */
  private _applyPendingFocus(): void {
    const primary = this._focusPending ? this.renderRoot.querySelector<HTMLElement>('[part~="primary"]') : null;
    if (!primary) return;
    this._focusPending = false;
    primary.focus({ preventScroll: true });
  }

  private _onStepShown(step: TourStep): void {
    const target = this._target;
    this._change?.stop();
    this._change = null;
    if (target && step.advance === 'change') {
      const token = this._s.token;
      this._change = watchChange(target, valueSignature, () => this._dispatch({ type: 'TRIGGER', token, kind: 'change' }), this._dom.scheduler);
    }
    if (target) {
      const r = this._dom.rect(target);
      const vp = this._dom.viewport();
      const inside = r && r.x >= vp.x && r.y >= vp.y && r.x + r.width <= vp.x + vp.width && r.y + r.height <= vp.y + vp.height;
      if (!inside) this._dom.scrollIntoView(target, this._dom.reducedMotion());
    }
    // Focus never leaves an app element; it only comes back out of the overlay.
    const active = this._deepActive();
    if (active && this.renderRoot.contains(active) && step.advance !== 'manual') {
      const back = target instanceof HTMLElement ? target : this._appFocus;
      (back as HTMLElement | null)?.focus?.({ preventScroll: true });
    }
  }

  private _announce(): void {
    this._dom.scheduler.raf(() => {
      const live = this.renderRoot.querySelector('[role="status"]');
      const bubble = this._bubble();
      if (!live) return;
      live.textContent = '';
      if (this._s.phase === 'paused') live.textContent = resolveTourStrings(this._s.loaded?.tour.locale ?? 'en', this.strings).paused;
      else if (bubble) {
        const part = (p: string) => bubble.querySelector(`[part~="${p}"]`)?.textContent?.trim() ?? '';
        live.textContent = [part('title'), part('body'), part('progress')].filter(Boolean).join(' ');
      }
    });
  }

  private _makeTracker(): Tracker {
    return createTracker({
      dom: this._dom,
      layer: () => this._layer(),
      bubble: () => this._bubble(),
      target: () => (this._s.phase === 'showing' ? this._target : null),
      refind: () => {
        const id = this._s.loaded?.tour.steps[this._s.index]?.target;
        const found = id && this._resolver ? this._resolver.find(id) : null;
        if (found) this._target = found;
        return found;
      },
      preferred: () => this._preferredSide(),
      avoid: () => this._nextTargetRect(),
      dialogs: () => this._openDialogs(),
      onPlaced: () => this._applyPendingFocus(),
      onLost: () => {
        // Queued behind any click trigger already deferred: a click whose
        // handler removes its own target (a dialog's Save) advances, it does
        // not lose the step. The stale token then drops this report.
        const token = this._s.token;
        this._dom.scheduler.setTimeout(() => this._dispatch({ type: 'TARGET_LOST', token }), 0);
      },
    });
  }

  private _preferredSide(): TooltipSide | 'auto' {
    const step = this._s.loaded?.tour.steps[this._s.index];
    if (step?.placement && step.placement !== 'auto') return step.placement;
    return step?.advance === 'change' && this._target && isComboboxLike(this._target) ? 'top' : 'auto';
  }

  private _nextTargetRect(): Rect | null {
    const s = this._s;
    const steps = s.loaded?.tour.steps ?? [];
    const next = steps.find((st, j) => j > s.index && s.loaded!.applicable[j]);
    const el = next?.target && this._resolver ? this._resolver.find(next.target) : null;
    return el ? this._dom.rect(el) : null;
  }

  /** The app's open dialogs (the overlay's own bubble is in the shadow root, so never one of them). */
  private _openDialogs(): Rect[] {
    const out: Rect[] = [];
    for (const el of document.querySelectorAll(APP_DIALOG)) {
      const r = this._dom.rect(el);
      if (r) out.push(r);
    }
    return out;
  }

  private _ensureTriggers(): void {
    if (this._detach || !this._run) return;
    this._detach = attachTriggers({
      host: this,
      attr: this._run.load.testIdAttribute,
      state: () => this._s,
      dispatch: (e) => this._dispatch(e),
      target: () => this._target,
      persistAhead: (from) => {
        const s = this._s;
        const next = s.loaded!.applicable.findIndex((ok, j) => ok && j > from);
        if (next === -1) clearResume();
        else this._persist(next);
      },
      interacted: () => this._change?.arm(),
      tick: () => this._change?.tick(),
      defer: (cb) => this._dom.scheduler.setTimeout(cb, 0),
    });
    document.addEventListener('focusin', this._onFocusIn, true);
  }

  private _onFocusIn = (e: FocusEvent) => {
    if (!e.composedPath().includes(this)) this._appFocus = e.composedPath()[0] as Element;
  };

  /** Our buttons: a click counts only if it was pressed on this step (or, by keyboard, after a short grace). */
  private _guarded(e: MouseEvent, act: () => void): void {
    e.stopPropagation();
    const keyboard = e.detail === 0;
    if (keyboard ? this._dom.scheduler.now() - this._shownAt < KEYBOARD_GRACE_MS : this._pressToken !== this._s.token) return;
    act();
  }

  private _deepActive(): Element | null {
    let el = document.activeElement;
    while (el?.shadowRoot?.activeElement) el = el.shadowRoot.activeElement;
    return el;
  }

  private _teardown(restoreFocus: boolean): void {
    this._cancelWait?.();
    this._cancelWait = null;
    this._tracker?.stop();
    this._tracker = null;
    this._change?.stop();
    this._change = null;
    this._detach?.();
    this._detach = null;
    document.removeEventListener('focusin', this._onFocusIn, true);
    this._lang.disconnect();
    const active = this._deepActive();
    if (restoreFocus && active && this.renderRoot.contains(active) && this._appFocus instanceof HTMLElement) {
      this._appFocus.focus({ preventScroll: true });
    }
    this._target = null;
    this._run = null;
    this._resolver = null;
    this._shownKey = '';
    if (g[ACTIVE] === this) g[ACTIVE] = null;
    if (!restoreFocus && this._s.phase !== 'idle') this._s = { ...initialState(), token: this._s.token + 1 };
  }

  // ── Links in step text ──────────────────────────────────────────────────

  private _link(url: string): ReturnType<LinkPolicy['link']> {
    const run = this._run;
    const base = run?.base ?? basePath(document.baseURI, location.origin);
    const path = linkHref(url, base, location.origin);
    if (path !== null) {
      return {
        kind: 'internal',
        href: path,
        onActivate: (e: MouseEvent) => {
          if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
          e.preventDefault();
          const hook = this.navigate ?? ((p: string) => location.assign(p));
          void Promise.resolve(hook(path, { appPath: toAppPath(path, base), tourId: run?.tourId ?? '' })).catch(() => undefined);
        },
      };
    }
    const origin = httpOrigin(url);
    const trusted = run?.load.guideOrigin ?? null;
    return origin && trusted && origin === trusted ? { kind: 'external', href: url } : null;
  }
}

/** Register ``<steppy-tour-host>`` once; a second call (or a second copy of the bundle) is a no-op. */
export function defineTourHost(): void {
  const existing = customElements.get(TOUR_HOST_TAG);
  if (!existing) customElements.define(TOUR_HOST_TAG, SteppyTourHost);
  else if (existing !== SteppyTourHost) {
    console.warn(`[steppy-tour] <${TOUR_HOST_TAG}> is already defined by another copy of the overlay; keeping that one`);
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'steppy-tour-host': SteppyTourHost;
  }
}
