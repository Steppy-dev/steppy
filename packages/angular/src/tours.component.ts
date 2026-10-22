import {
  ChangeDetectionStrategy,
  Component,
  DOCUMENT,
  DestroyRef,
  ElementRef,
  NgZone,
  PLATFORM_ID,
  afterNextRender,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
  type OutputEmitterRef,
} from '@angular/core';
import { isPlatformBrowser } from '@angular/common';
import type { TourEventDetailMap, TourSource, TourStringOverrides } from '@steppy/player/tour';

import { createNavigate } from './navigate';
import { OVERLAY_LOADER, TOUR_EVENT_NAMES, applyProps, type HostProps, type TourHostElement } from './overlay';
import { SteppyTours, type SteppyTourEvent } from './tours.service';

const PREFIX = '[steppy-tours]';

/** ``undefined`` stays ``undefined`` (= use the config); anything else is read as a number. */
function optionalNumber(value: unknown): number | undefined {
  return value === undefined || value === null || value === '' ? undefined : Number(value);
}

/** ``undefined`` stays ``undefined``; a bare attribute (``''``) and ``'true'`` are true. */
function optionalBoolean(value: unknown): boolean | undefined {
  if (value === undefined || value === null) return undefined;
  return value === '' || (value !== false && value !== 'false');
}

/**
 * The in-app tour overlay. Place it **once**, in the app shell:
 *
 * ```html
 * <router-outlet />
 * <steppy-tours />
 * ```
 *
 * It renders nothing on the server. In the browser it mounts
 * ``<steppy-tour-host>`` after the first render and loads the overlay
 * module lazily; every input overrides the matching
 * ``provideSteppyTours()`` setting. Start tours with the
 * ``SteppyTours`` service or the ``steppyTourStart`` directive.
 *
 * Destroying it suspends a running tour rather than dismissing it: the overlay
 * keeps its resume record, so a tour survives a shell swap. Call
 * ``SteppyTours.stop()`` first to end it for good.
 */
@Component({
  selector: 'steppy-tours',
  template: '',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { style: 'display: contents' },
})
export class SteppyToursComponent {
  readonly fallbackManifest = input<string>();
  readonly manifest = input<string>();
  readonly buildId = input<string>();
  readonly guideOrigin = input<string>();
  /** ``null``/``undefined``: the configured locale source. ``''``: follow ``<html lang>``. */
  readonly locale = input<string | null>();
  readonly targetTimeout = input<number | undefined, unknown>(undefined, { transform: optionalNumber });
  readonly baseZIndex = input<number | undefined, unknown>(undefined, { transform: optionalNumber });
  readonly disabled = input<boolean | undefined, unknown>(undefined, { transform: optionalBoolean });
  readonly strings = input<TourStringOverrides>();
  readonly source = input<TourSource | null>();

  readonly tourStart = output<TourEventDetailMap['tour-start']>();
  readonly tourStep = output<TourEventDetailMap['tour-step']>();
  readonly tourComplete = output<TourEventDetailMap['tour-complete']>();
  readonly tourDismiss = output<TourEventDetailMap['tour-dismiss']>();
  readonly tourOffpath = output<TourEventDetailMap['tour-offpath']>();
  readonly tourError = output<TourEventDetailMap['tour-error']>();

  private readonly tours = inject(SteppyTours);
  private readonly config = this.tours.ɵconfig;
  private readonly overlay = inject(OVERLAY_LOADER);
  private readonly zone = inject(NgZone);
  private readonly document = inject(DOCUMENT);
  private readonly hostRef = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly browser = isPlatformBrowser(inject(PLATFORM_ID));
  private readonly navigate = createNavigate(this.config.navigate);
  private readonly el = signal<TourHostElement | null>(null);

  private readonly outputs: { [K in keyof TourEventDetailMap]: OutputEmitterRef<TourEventDetailMap[K]> } = {
    'tour-start': this.tourStart,
    'tour-step': this.tourStep,
    'tour-complete': this.tourComplete,
    'tour-dismiss': this.tourDismiss,
    'tour-offpath': this.tourOffpath,
    'tour-error': this.tourError,
  };

  private readonly props = computed<HostProps>(() => {
    const c = this.config;
    const locale = this.locale();
    const source = this.source();
    return {
      fallbackManifest: this.fallbackManifest() ?? c.fallbackManifest ?? '',
      manifest: this.manifest() ?? c.manifest ?? '',
      buildId: this.buildId() ?? c.buildId ?? '',
      guideOrigin: this.guideOrigin() ?? c.guideOrigin ?? '',
      locale: locale ?? this.tours.ɵlocale(),
      disabled: this.disabled() ?? c.disabled ?? false,
      strings: this.strings() ?? c.strings,
      source: source !== undefined ? source : (c.source ?? null),
      targetTimeout: this.targetTimeout() ?? c.targetTimeout,
      baseZIndex: this.baseZIndex() ?? c.baseZIndex,
    };
  });

  private readonly onEvent = (event: Event): void =>
    this.zone.run(() => {
      const record = { type: event.type, detail: (event as CustomEvent).detail } as SteppyTourEvent;
      this.tours.ɵrecord(record);
      (this.outputs[record.type] as OutputEmitterRef<unknown>).emit(record.detail);
    });

  constructor() {
    // afterNextRender runs in the browser only, outside the Angular zone.
    afterNextRender(() => this.mount());
    effect(() => {
      const el = this.el();
      const props = this.props();
      if (el) this.zone.runOutsideAngular(() => applyProps(el, props));
    });
    inject(DestroyRef).onDestroy(() => this.unmount());
  }

  private mount(): void {
    if (!this.browser) return;
    if (!this.tours.ɵclaim(this)) {
      console.warn(`${PREFIX} another <steppy-tours> is already mounted; this one does nothing. Mount it once, in the app shell.`);
      return;
    }
    const props = untracked(this.props);
    if (!props.fallbackManifest && !props.manifest) {
      console.warn(`${PREFIX} no manifest: set fallbackManifest (or manifest) in provideSteppyTours() or on <steppy-tours>`);
    }
    // The element is on the page — with its properties and listeners — before
    // the overlay module defines it: the overlay keeps properties set before
    // its upgrade, a resumed tour's tour-start is not missed, and anything that
    // waits for the element to be defined finds it in the DOM.
    const el = this.document.createElement(this.overlay.tag) as TourHostElement;
    applyProps(el, props);
    el.navigate = this.navigate;
    for (const name of TOUR_EVENT_NAMES) el.addEventListener(name, this.onEvent);
    this.hostRef.nativeElement.appendChild(el);
    this.el.set(el);
    this.overlay
      .load()
      .then(() => customElements.whenDefined(this.overlay.tag))
      .then(
        () => {
          if (this.el() === el) this.tours.ɵready(this, el);
        },
        (err: unknown) => {
          if (this.el() !== el) return;
          console.error(`${PREFIX} the tour overlay failed to load`, err);
          this.tours.ɵready(this, null);
        },
      );
  }

  private unmount(): void {
    const el = untracked(this.el);
    if (el) {
      for (const name of TOUR_EVENT_NAMES) el.removeEventListener(name, this.onEvent);
      el.remove();
      this.el.set(null);
    }
    this.tours.ɵrelease(this);
  }
}
