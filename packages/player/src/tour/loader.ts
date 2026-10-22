/**
 * Loads a tour: the manifest(s), then the tour file for the best locale.
 *
 * Two manifests can be configured. ``fallbackManifest`` is the copy bundled
 * with the app (same origin only). ``manifest`` may point at a remote guide
 * site and may contain ``{buildId}``, filled with the app's build id — the
 * host's ``buildId`` property, else the bundled manifest's — so remote mode
 * only ever fetches the tours of the app's own build. The remote copy is
 * tried first and the bundled copy is the fallback; a remote failure is only
 * logged.
 *
 * Cross-origin requests send no credentials, refuse redirects, and pass the
 * bundled manifest's SRI hash when both copies are the same build, so a
 * tampered remote file is rejected by the browser. Every body is read with a
 * byte cap and an HTML sniff (an SPA answering a missing JSON file with its
 * index page is a load failure, not bad data). Nothing a cross-origin
 * manifest says widens what the bundled one allows: when the bundled manifest
 * is for the same build, any other manifest (remote, or a same-origin proxy)
 * only locates files — the tour, the locale and the SRI hash all come from the
 * bundle, so a tour, locale or case variant the bundle does not pin is never
 * fetched from it.
 */

import { lookupLocale } from '../ui/strings.js';
import { MAX_MANIFEST_BYTES, MAX_TOUR_FILE_BYTES } from './contract.js';
import type { TourSourceKind } from './engine.js';
import {
  httpOrigin,
  parseJson,
  validateManifest,
  validateTour,
  type FailReason,
  type Issue,
  type ManifestTour,
  type Result,
  type TourFile,
  type TourManifest,
} from './validate.js';

export type TourSource = (
  tourId: string,
  locale: string,
  ctx: { buildId: string; manifestUrl: string },
) => Promise<string | Response | object> | string | Response | object;

export interface LoaderConfig {
  manifest: string;
  fallbackManifest: string;
  buildId: string;
  /** The locale asked for (host ``locale``, else the page's ``lang``). */
  locale: string;
  /** The host's ``guide-origin``: the only origin trusted without a manifest's say-so. */
  guideOrigin: string;
  source?: TourSource | null;
  fetch?: typeof fetch;
  origin?: string;
  baseURI?: string;
  timeoutMs?: number;
  /** Manifests per resolved URL, kept by the host for the page's lifetime. */
  cache?: Map<string, Promise<ManifestLoad>>;
}

export interface LoadOk {
  ok: true;
  manifest: TourManifest;
  manifestUrl: string;
  tour: TourFile;
  locale: string;
  source: TourSourceKind;
  testIdAttribute: string;
  guideOrigin: string | null;
}

export interface LoadErr {
  ok: false;
  reason: FailReason;
  message: string;
  issues?: Issue[];
}

export type ManifestLoad = (Result<TourManifest> & { url: string }) | (LoadErr & { url: string });

const RANK: Record<FailReason, number> = { 'load-failed': 0, 'invalid-data': 1, 'schema-unsupported': 2 };

class LoadError extends Error {
  constructor(
    readonly reason: FailReason,
    message: string,
  ) {
    super(message);
  }
}

/** Exact tag, then its base language, then the manifest's default, then the first file. */
export function pickLocale(available: readonly string[], requested: string, fallback: string): string {
  return lookupLocale(available, requested) ?? (available.includes(fallback) ? fallback : available[0]!);
}

/** The body as text, refusing more than ``cap`` bytes and invalid UTF-8. */
export async function readCapped(res: Response, cap: number): Promise<string> {
  if (!res.ok) throw new LoadError('load-failed', `HTTP ${res.status}`);
  const declared = Number(res.headers.get('content-length') ?? NaN);
  if (declared > cap) throw new LoadError('invalid-data', `the file is larger than ${cap} bytes`);
  const decoder = new TextDecoder('utf-8', { fatal: true });
  const decode = (bytes: Uint8Array) => {
    try {
      return decoder.decode(bytes);
    } catch {
      throw new LoadError('invalid-data', 'the file is not valid UTF-8');
    }
  };
  if (!res.body) {
    const bytes = new Uint8Array(await res.arrayBuffer());
    if (bytes.byteLength > cap) throw new LoadError('invalid-data', `the file is larger than ${cap} bytes`);
    return decode(bytes);
  }
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > cap) {
      void reader.cancel();
      throw new LoadError('invalid-data', `the file is larger than ${cap} bytes`);
    }
    chunks.push(value);
  }
  const all = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    all.set(c, offset);
    offset += c.byteLength;
  }
  return decode(all);
}

export async function loadTour(cfg: LoaderConfig, tourId: string): Promise<LoadOk | LoadErr> {
  const origin = cfg.origin ?? location.origin;
  const baseURI = cfg.baseURI ?? document.baseURI;
  const doFetch = cfg.fetch ?? globalThis.fetch.bind(globalThis);
  const cache = cfg.cache ?? new Map<string, Promise<ManifestLoad>>();
  const abs = (u: string) => new URL(u, baseURI);
  const cross = (u: string) => abs(u).origin !== origin;
  const timeoutMs = cfg.timeoutMs ?? 10_000;

  async function get(url: string, kind: 'manifest' | 'tour', integrity?: string): Promise<string> {
    const isCross = cross(url);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const init: RequestInit = {
        credentials: isCross ? 'omit' : 'same-origin',
        mode: isCross ? 'cors' : 'same-origin',
        redirect: isCross ? 'error' : 'follow',
        cache: kind === 'manifest' || !isCross ? 'no-cache' : 'default',
        signal: controller.signal,
      };
      if (integrity) init.integrity = integrity;
      let res: Response;
      try {
        res = await doFetch(abs(url).href, init);
      } catch (err) {
        throw new LoadError('load-failed', `could not fetch ${url}: ${String(err)}`);
      }
      return await readCapped(res, kind === 'manifest' ? MAX_MANIFEST_BYTES : MAX_TOUR_FILE_BYTES);
    } finally {
      clearTimeout(timer);
    }
  }

  function getManifest(url: string): Promise<ManifestLoad> {
    const key = abs(url).href;
    let pending = cache.get(key);
    if (!pending) {
      pending = (async (): Promise<ManifestLoad> => {
        try {
          const parsed = parseJson(await get(url, 'manifest'));
          const r = parsed.ok ? validateManifest(parsed.value) : parsed;
          return { ...r, url: key } as ManifestLoad;
        } catch (err) {
          const e = err instanceof LoadError ? err : new LoadError('load-failed', String(err));
          // A network blip is not the manifest's answer: the next start() asks again.
          if (e.reason === 'load-failed') cache.delete(key);
          return { ok: false, reason: e.reason, message: e.message, url: key };
        }
      })();
      cache.set(key, pending);
    }
    return pending;
  }

  const errors: LoadErr[] = [];
  const failed = (r: { ok: false; reason: FailReason; issues?: Issue[]; message?: string }, what: string) =>
    errors.push({ ok: false, reason: r.reason, message: r.message ?? `${what}: ${r.issues?.[0]?.path ?? ''} ${r.issues?.[0]?.message ?? ''}`.trim(), issues: r.issues });

  let bundled: (TourManifest & { url: string }) | null = null;
  if (cfg.fallbackManifest) {
    if (cross(cfg.fallbackManifest)) {
      errors.push({ ok: false, reason: 'load-failed', message: 'fallbackManifest must be same-origin' });
    } else {
      const r = await getManifest(cfg.fallbackManifest);
      if (r.ok) bundled = { ...r.value, url: r.url };
      else failed(r, 'bundled manifest');
    }
  }

  const expected = cfg.buildId || bundled?.buildId || '';
  let primary: (TourManifest & { url: string }) | null = null;
  if (cfg.manifest) {
    if (cfg.manifest.includes('{buildId}') && !expected) {
      errors.push({ ok: false, reason: 'load-failed', message: 'the manifest URL needs a build id ({buildId}) and none is known' });
    } else {
      const r = await getManifest(cfg.manifest.split('{buildId}').join(encodeURIComponent(expected)));
      if (!r.ok) failed(r, 'manifest');
      else if (expected && r.value.buildId !== expected) {
        errors.push({ ok: false, reason: 'invalid-data', message: `the manifest is for build "${r.value.buildId}", expected "${expected}"` });
      } else primary = { ...r.value, url: r.url };
      if (!r.ok && bundled) console.warn(`[steppy-tour] remote manifest unavailable, using the bundled copy: ${errors.at(-1)?.message}`);
    }
  }
  if (!cfg.manifest && !cfg.fallbackManifest) {
    return { ok: false, reason: 'load-failed', message: 'no manifest is configured (set manifest or fallback-manifest)' };
  }

  const remoteOrigin = primary ? new URL(primary.url).origin : null;
  const guideOrigin =
    (cfg.guideOrigin && (httpOrigin(cfg.guideOrigin) ?? cfg.guideOrigin)) ||
    bundled?.guideOrigin ||
    (primary && (!cross(primary.url) || primary.guideOrigin === remoteOrigin) ? primary.guideOrigin : null);

  for (const m of [primary, bundled]) {
    if (!m) continue;
    // A manifest that is not the bundled one but is for its build only says
    // where the files are: the bundle picks the tour, the locale and the hash.
    // Keyed on "not the bundled manifest" rather than "cross-origin", so a
    // same-origin proxy in front of a guide site is pinned too.
    const ref = m !== bundled && bundled?.buildId === m.buildId ? bundled : m;
    const entry: ManifestTour | undefined = ref.tours.find((t) => t.id === tourId);
    if (!entry) {
      errors.push({ ok: false, reason: 'load-failed', message: `no tour "${tourId}" in ${ref.url}` });
    } else {
      const locale = pickLocale(Object.keys(entry.files), cfg.locale, ref.defaultLocale);
      const isCross = cross(m.url);
      const integrity = ref !== m || isCross ? entry.integrity[locale] : undefined;
      try {
        let raw: unknown;
        if (cfg.source) {
          const got = await cfg.source(tourId, locale, { buildId: m.buildId, manifestUrl: m.url });
          if (typeof got === 'string' || got instanceof Response) {
            const text = typeof got === 'string' ? got : await readCapped(got, MAX_TOUR_FILE_BYTES);
            if (new TextEncoder().encode(text).byteLength > MAX_TOUR_FILE_BYTES) throw new LoadError('invalid-data', 'the tour file is too large');
            const parsed = parseJson(text);
            if (!parsed.ok) throw new LoadError(parsed.reason, parsed.issues[0]?.message ?? '');
            raw = parsed.value;
          } else raw = got;
        } else {
          const parsed = parseJson(await get(new URL(entry.files[locale]!, m.url).href, 'tour', integrity));
          if (!parsed.ok) throw new LoadError(parsed.reason, parsed.issues[0]?.message ?? '');
          raw = parsed.value;
        }
        const v = validateTour(raw, { manifest: ref, entry, locale, guideOrigin });
        if (v.ok) {
          return {
            ok: true,
            manifest: ref,
            manifestUrl: m.url,
            tour: v.value,
            locale,
            source: cfg.source ? 'custom' : m === primary && isCross ? 'remote' : 'bundled',
            testIdAttribute: bundled?.testIdAttribute ?? m.testIdAttribute,
            guideOrigin,
          };
        }
        failed(v, `tour ${tourId} (${locale})`);
      } catch (err) {
        const e = err instanceof LoadError ? err : new LoadError('load-failed', String(err));
        errors.push({ ok: false, reason: e.reason, message: e.message });
      }
    }
    if (m === primary && bundled) console.warn(`[steppy-tour] remote tour rejected, trying the bundled copy: ${errors.at(-1)?.message}`);
  }
  return errors.reduce((best, e) => (RANK[e.reason] > RANK[best.reason] ? e : best), errors[0] ?? {
    ok: false,
    reason: 'load-failed',
    message: `tour "${tourId}" could not be loaded`,
  });
}
