import { Injectable, Pipe, inject, signal, type PipeTransform } from '@angular/core';

/**
 * ``/demo-config.json`` (served by ``fixtures/tour-demo/serve.py``) tunes the
 * demo for tests, with the same keys as the plain demo:
 *   manifest, fallbackManifest, buildId, targetTimeout  -> <steppy-tours>
 *   testIdOverrides {"name": "renamed"}                   -> simulate a renamed test-id
 *   saveDelayMs (150)                                     -> delay before the detail page
 *   autoStart "<tour id>"                                -> start a tour on every page load
 *   blockDetail true                                      -> a guard refuses /invoices/:id
 */
export interface DemoSettings {
  manifest?: string;
  fallbackManifest?: string;
  buildId?: string;
  targetTimeout?: number;
  testIdOverrides?: Record<string, string>;
  saveDelayMs?: number;
  autoStart?: string;
  blockDetail?: boolean;
}

@Injectable({ providedIn: 'root' })
export class DemoConfig {
  readonly value = signal<DemoSettings>({});

  async load(): Promise<void> {
    try {
      const res = await fetch('/demo-config.json', { cache: 'no-store' });
      if (res.ok) this.value.set(await res.json());
    } catch {
      // No config: the defaults.
    }
  }

  tid(name: string): string {
    return this.value().testIdOverrides?.[name] ?? name;
  }
}

/** ``'invoice-title' | tid`` — the test-id, or its override for a "renamed in the app" test. */
@Pipe({ name: 'tid' })
export class TidPipe implements PipeTransform {
  private readonly config = inject(DemoConfig);

  transform(name: string): string {
    return this.config.tid(name);
  }
}
