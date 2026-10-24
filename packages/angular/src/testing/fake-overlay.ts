/**
 * A stand-in for ``<steppy-tour-host>``: each call gets a tag of its own
 * (the custom-element registry is shared by every spec), a load the test
 * resolves when it chooses, and an element class that — like the real Lit
 * element — keeps properties set on it before its upgrade. Every property
 * write and method call is recorded with whether it ran inside the Angular
 * zone.
 */

import { NgZone, type Provider } from '@angular/core';
import type { TourEventDetailMap, TourStartOptions } from '@steppy/player/tour';

import { OVERLAY_LOADER, type OverlayLoader, type TourHostElement } from '../overlay';

export const HOST_PROPS = [
  'fallbackManifest',
  'manifest',
  'buildId',
  'locale',
  'guideOrigin',
  'baseZIndex',
  'targetTimeout',
  'disabled',
  'source',
  'navigate',
  'strings',
  'theme',
  'appearance',
] as const;

export interface Recorded {
  kind: 'set' | 'call';
  name: string;
  value: unknown;
  inZone: boolean;
}

export interface FakeOverlay extends OverlayLoader {
  readonly calls: Recorded[];
  /** What ``start()`` resolves to. */
  startResult: boolean;
  /** How many times ``load()`` was called. */
  loads: number;
  /** Define the element (as the real module does on import) and settle ``load()``. */
  resolveLoad(): void;
  rejectLoad(error?: unknown): void;
  /** The mounted element, if any. */
  element(): TourHostElement | null;
  provider(): Provider;
}

let counter = 0;

export function fakeOverlay(): FakeOverlay {
  const tag = `steppy-tour-host-t${++counter}`;
  const calls: Recorded[] = [];
  const record = (kind: Recorded['kind'], name: string, value: unknown) =>
    calls.push({ kind, name, value, inZone: NgZone.isInAngularZone() });
  let resolve!: () => void;
  let reject!: (e: unknown) => void;
  const loaded = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });

  class Host extends HTMLElement {
    private readonly values: Record<string, unknown> = {};
    constructor() {
      super();
      // Lit's handling of properties set before the upgrade: keep, delete the
      // own property, re-apply through the accessor.
      const saved: [string, unknown][] = [];
      for (const prop of HOST_PROPS) {
        if (Object.prototype.hasOwnProperty.call(this, prop)) {
          saved.push([prop, (this as unknown as Record<string, unknown>)[prop]]);
          delete (this as unknown as Record<string, unknown>)[prop];
        }
      }
      for (const [prop, value] of saved) (this as unknown as Record<string, unknown>)[prop] = value;
    }
    start(tourId: string, options?: TourStartOptions): Promise<boolean> {
      record('call', 'start', [tourId, options]);
      return Promise.resolve(fake.startResult);
    }
    stop(reason?: string): void {
      record('call', 'stop', reason);
    }
    next(): void {
      record('call', 'next', undefined);
    }
    emit<K extends keyof TourEventDetailMap>(name: K, detail: TourEventDetailMap[K]): void {
      this.dispatchEvent(new CustomEvent(name, { detail, bubbles: true, composed: true }));
    }
  }
  for (const prop of HOST_PROPS) {
    Object.defineProperty(Host.prototype, prop, {
      configurable: true,
      get(this: Host) {
        return (this as unknown as { values: Record<string, unknown> }).values[prop];
      },
      set(this: Host, value: unknown) {
        (this as unknown as { values: Record<string, unknown> }).values[prop] = value;
        record('set', prop, value);
      },
    });
  }

  const fake: FakeOverlay = {
    tag,
    calls,
    startResult: true,
    loads: 0,
    load: () => {
      fake.loads++;
      return loaded;
    },
    resolveLoad: () => {
      customElements.define(tag, Host);
      resolve();
    },
    rejectLoad: (error = new Error('chunk failed')) => reject(error),
    element: () => document.querySelector<TourHostElement>(tag),
    provider: () => ({ provide: OVERLAY_LOADER, useValue: fake }),
  };
  return fake;
}

/** Dispatch an overlay event on the mounted fake element. */
export function emit<K extends keyof TourEventDetailMap>(overlay: FakeOverlay, name: K, detail: TourEventDetailMap[K]): void {
  (overlay.element() as unknown as { emit: (n: K, d: TourEventDetailMap[K]) => void }).emit(name, detail);
}

/** Let promises and ``afterNextRender`` callbacks run. */
export async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
}

export const META = { tourId: 'create-invoice', buildId: 'b-1', locale: 'en' } as const;
