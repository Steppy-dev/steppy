import { Injectable, NgZone, PLATFORM_ID, computed, inject, isSignal, signal, type Signal } from '@angular/core';
import { isPlatformBrowser } from '@angular/common';
import { Observable, Subject } from 'rxjs';
import type { TourErrorDetail, TourEventDetailMap, TourPosition, TourStartOptions } from '@steppy/player/tour';

import { DEFAULT_READY_TIMEOUT_MS, STEPPY_TOURS_CONFIG, type SteppyLocaleSource, type SteppyToursConfig } from './config';
import type { TourHostElement } from './overlay';

/** One overlay event, as the service and the component outputs see it. */
export type SteppyTourEvent = {
  [K in keyof TourEventDetailMap]: { type: K; detail: TourEventDetailMap[K] };
}[keyof TourEventDetailMap];

const PREFIX = '[steppy-tours]';

/**
 * Start and follow in-app tours from anywhere in the app.
 *
 * The overlay itself lives in ``<steppy-tours>``, placed once in the app
 * shell; ``start()`` waits for it to be mounted and loaded (up to
 * ``readyTimeout``), so a help button can call it during startup. State is
 * built from the overlay's six events and exposed as signals; on the server
 * every call is a no-op.
 */
@Injectable({ providedIn: 'root' })
export class SteppyTours {
  private readonly zone = inject(NgZone);
  private readonly browser = isPlatformBrowser(inject(PLATFORM_ID));

  /** @internal */
  readonly ɵconfig: SteppyToursConfig = inject(STEPPY_TOURS_CONFIG, { optional: true }) ?? {};
  /** @internal The configured locale source, resolved once in the root injection context. */
  readonly ɵlocale: Signal<string> = resolveLocale(this.ɵconfig.locale);

  private readonly _active = signal(false);
  private readonly _current = signal<TourPosition | null>(null);
  private readonly _lastError = signal<TourErrorDetail | null>(null);
  private readonly _events = new Subject<SteppyTourEvent>();

  /** A tour is running (from ``tour-start`` until it completes, is dismissed or fails). */
  readonly active: Signal<boolean> = this._active.asReadonly();
  /** The step shown, or ``null`` before the first step and after the end. */
  readonly current: Signal<TourPosition | null> = this._current.asReadonly();
  /** The last ``tour-error`` since the last start (fatal or not). */
  readonly lastError: Signal<TourErrorDetail | null> = this._lastError.asReadonly();
  /** Every overlay event, in order, delivered inside the Angular zone. */
  readonly events: Observable<SteppyTourEvent> = this._events.asObservable();

  private owner: object | null = null;
  private host: TourHostElement | null = null;
  private failed = false;
  private waiters: ((host: TourHostElement | null) => void)[] = [];
  private seq = 0;

  /**
   * Start a tour. Resolves ``true`` once it started, ``false`` when it could
   * not (a ``tour-error`` says why), when no ``<steppy-tours>`` is mounted
   * in time, when a later ``start()``/``stop()`` superseded this call, or on
   * the server. Never rejects.
   */
  async start(tourId: string, options: TourStartOptions = {}): Promise<boolean> {
    if (!this.browser) return false;
    const seq = ++this.seq;
    const host = await this.whenReady();
    if (seq !== this.seq) return false;
    if (!host) {
      console.warn(
        this.failed
          ? `${PREFIX} start("${tourId}"): the tour overlay failed to load`
          : `${PREFIX} start("${tourId}"): no <steppy-tours> is mounted, or the overlay did not load ` +
              `within ${this.readyTimeout()} ms. Place <steppy-tours /> once in the app shell.`,
      );
      return false;
    }
    // Outside the zone: the overlay's observers, polls and frames must not
    // drive change detection.
    return this.zone.runOutsideAngular(() => host.start(tourId, options));
  }

  /** End the running tour (``reason`` is passed to ``tour-dismiss``); also cancels a waiting ``start()``. */
  stop(reason?: string): void {
    this.seq++;
    this.flush(null);
    const host = this.host;
    if (host) this.zone.runOutsideAngular(() => host.stop(reason));
  }

  /** Advance the current step, whatever its kind. */
  next(): void {
    const host = this.host;
    if (host) this.zone.runOutsideAngular(() => host.next());
  }

  // ── Used by <steppy-tours> ──────────────────────────────────────────────

  /** @internal The first component to mount owns the overlay. */
  ɵclaim(owner: object): boolean {
    if (this.owner !== null && this.owner !== owner) return false;
    this.owner = owner;
    return true;
  }

  /** @internal The overlay is defined (``host``) or failed to load (``null``). */
  ɵready(owner: object, host: TourHostElement | null): void {
    if (this.owner !== owner) return;
    this.host = host;
    this.failed = host === null;
    this.flush(host);
  }

  /** @internal The owning component was destroyed. */
  ɵrelease(owner: object): void {
    if (this.owner !== owner) return;
    this.owner = null;
    this.host = null;
    this.failed = false;
    this._active.set(false);
    this._current.set(null);
  }

  /** @internal One overlay event (already inside the zone). */
  ɵrecord(event: SteppyTourEvent): void {
    switch (event.type) {
      case 'tour-start':
        this._active.set(true);
        this._current.set(null);
        this._lastError.set(null);
        break;
      case 'tour-step': {
        const { tourId, stepId, testId, index, total } = event.detail;
        this._active.set(true);
        this._current.set({ tourId, stepId, testId, index, total });
        break;
      }
      case 'tour-complete':
      case 'tour-dismiss':
        this._active.set(false);
        this._current.set(null);
        break;
      case 'tour-error':
        this._lastError.set(event.detail);
        if (event.detail.fatal) {
          this._active.set(false);
          this._current.set(null);
        }
        break;
      case 'tour-offpath':
        break;
    }
    this._events.next(event);
  }

  // ── Internals ──────────────────────────────────────────────────────────────

  private readyTimeout(): number {
    return this.ɵconfig.readyTimeout ?? DEFAULT_READY_TIMEOUT_MS;
  }

  private whenReady(): Promise<TourHostElement | null> {
    if (this.host) return Promise.resolve(this.host);
    if (this.failed) return Promise.resolve(null);
    return new Promise((resolve) => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      const done = (host: TourHostElement | null) => {
        clearTimeout(timer);
        this.waiters = this.waiters.filter((w) => w !== done);
        resolve(host);
      };
      this.waiters.push(done);
      // Outside the zone: a pending timer would keep a zone.js app from ever
      // becoming stable while it waits.
      timer = this.zone.runOutsideAngular(() => setTimeout(() => done(null), this.readyTimeout()));
    });
  }

  private flush(host: TourHostElement | null): void {
    for (const waiter of [...this.waiters]) waiter(host);
  }
}

function resolveLocale(source: SteppyLocaleSource | undefined): Signal<string> {
  if (source === undefined) return computed(() => '');
  if (typeof source === 'string') return computed(() => source);
  if (isSignal(source)) return computed(() => source() ?? '');
  const made = source();
  return typeof made === 'string' ? computed(() => made) : computed(() => made() ?? '');
}
