/**
 * ``loadTour``: bundled-first, ``{buildId}``, request options per origin,
 * integrity pinning, caps, the HTML sniff, fallbacks and error ranking.
 * ``fetch`` is a stub; the browser-enforced parts (SRI, CORS) are covered by
 * the Chromium integration suite.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it, vi } from 'vitest';

import { loadTour, pickLocale, readCapped, type LoaderConfig } from '../../src/tour/loader.js';

const DIR = resolve(__dirname, '../../../../contracts/fixtures/tours/valid');
const file = (name: string) => readFileSync(resolve(DIR, name), 'utf8');
const ORIGIN = 'https://app.example';
const REMOTE = 'https://guide.example.com/tours/{buildId}/tours.manifest.json';

type Routes = Record<string, string | number | ((init: RequestInit) => Response | Promise<Response>)>;

function stubFetch(routes: Routes) {
  const calls: { url: string; init: RequestInit }[] = [];
  const fetch = vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const r = routes[url];
    if (r === undefined) return new Response('not found', { status: 404 });
    if (typeof r === 'number') return new Response('', { status: r });
    if (typeof r === 'function') return r(init);
    return new Response(r, { status: 200 });
  });
  return { fetch: fetch as unknown as typeof globalThis.fetch, calls };
}

function bundledRoutes(prefix = `${ORIGIN}/tours/`): Routes {
  return Object.fromEntries(
    ['tours.manifest.json', 'create-invoice.en.json', 'create-invoice.fr.json'].map((n) => [prefix + n, file(n)]),
  );
}

function cfg(extra: Partial<LoaderConfig>): LoaderConfig {
  return {
    manifest: '',
    fallbackManifest: '/tours/tours.manifest.json',
    buildId: '',
    locale: 'en',
    guideOrigin: '',
    origin: ORIGIN,
    baseURI: `${ORIGIN}/`,
    ...extra,
  };
}

describe('the bundled manifest', () => {
  it('loads the tour in the requested locale, same-origin options', async () => {
    const { fetch, calls } = stubFetch(bundledRoutes());
    const r = await loadTour(cfg({ fetch, locale: 'fr-BE' }), 'create-invoice');
    expect(r.ok && [r.locale, r.source, r.tour.title]).toEqual(['fr', 'bundled', 'Créer une facture']);
    expect(calls[0]!.init).toMatchObject({ credentials: 'same-origin', mode: 'same-origin', redirect: 'follow', cache: 'no-cache' });
    expect(calls[1]!.init.integrity).toBeUndefined();
  });

  it('must be same-origin', async () => {
    const { fetch } = stubFetch({});
    const r = await loadTour(cfg({ fetch, fallbackManifest: 'https://elsewhere.example/m.json' }), 'create-invoice');
    expect(r.ok === false && r.message).toMatch(/same-origin/);
  });

  it('reports an unknown tour as load-failed', async () => {
    const { fetch } = stubFetch(bundledRoutes());
    const r = await loadTour(cfg({ fetch }), 'nope');
    expect(r).toMatchObject({ ok: false, reason: 'load-failed' });
  });

  it('treats an SPA answering with HTML as load-failed', async () => {
    const routes = bundledRoutes();
    routes[`${ORIGIN}/tours/create-invoice.en.json`] = '<!doctype html><html></html>';
    const { fetch } = stubFetch(routes);
    const r = await loadTour(cfg({ fetch }), 'create-invoice');
    expect(r).toMatchObject({ ok: false, reason: 'load-failed' });
  });
});

describe('host options and the manifest cache', () => {
  it('reduces a guide-origin URL to its origin', async () => {
    const { fetch } = stubFetch(bundledRoutes());
    const r = await loadTour(cfg({ fetch, guideOrigin: 'https://guide.example.com/' }), 'create-invoice');
    expect(r.ok && [r.guideOrigin, r.tour.guideUrl]).toEqual(['https://guide.example.com', 'https://guide.example.com/create-invoice/en.html']);
  });

  it('asks again after a manifest that could not be fetched', async () => {
    const cache = new Map();
    const routes = bundledRoutes();
    const body = routes[`${ORIGIN}/tours/tours.manifest.json`] as string;
    let online = false;
    routes[`${ORIGIN}/tours/tours.manifest.json`] = () => (online ? new Response(body) : Promise.reject(new TypeError('offline')));
    const { fetch } = stubFetch(routes);
    expect(await loadTour(cfg({ fetch, cache }), 'create-invoice')).toMatchObject({ ok: false, reason: 'load-failed' });
    online = true;
    expect((await loadTour(cfg({ fetch, cache }), 'create-invoice')).ok).toBe(true);
  });

  it('keeps a manifest that failed validation: asking again would get the same answer', async () => {
    const cache = new Map();
    const { fetch, calls } = stubFetch({ [`${ORIGIN}/tours/tours.manifest.json`]: '{}' });
    expect(await loadTour(cfg({ fetch, cache }), 'create-invoice')).toMatchObject({ ok: false, reason: 'invalid-data' });
    await loadTour(cfg({ fetch, cache }), 'create-invoice');
    expect(calls).toHaveLength(1);
  });
});

describe('remote mode', () => {
  const remoteRoutes = (): Routes => ({
    ...bundledRoutes(),
    ...bundledRoutes('https://guide.example.com/tours/demo-0001/'),
  });

  it('fills {buildId} from the bundled manifest and pins the remote file to the bundled hash', async () => {
    const { fetch, calls } = stubFetch(remoteRoutes());
    const r = await loadTour(cfg({ fetch, manifest: REMOTE }), 'create-invoice');
    expect(r.ok && r.source).toBe('remote');
    const remote = calls.filter((c) => c.url.startsWith('https://guide.example.com'));
    expect(remote.map((c) => c.url)).toEqual([
      'https://guide.example.com/tours/demo-0001/tours.manifest.json',
      'https://guide.example.com/tours/demo-0001/create-invoice.en.json',
    ]);
    expect(remote[0]!.init).toMatchObject({ credentials: 'omit', mode: 'cors', redirect: 'error', cache: 'no-cache' });
    expect(remote[1]!.init.integrity).toMatch(/^sha384-/);
  });

  it('falls back to the bundled copy when the remote fails', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const routes = remoteRoutes();
    routes['https://guide.example.com/tours/demo-0001/create-invoice.en.json'] = 503;
    const { fetch } = stubFetch(routes);
    const r = await loadTour(cfg({ fetch, manifest: REMOTE }), 'create-invoice');
    expect(r.ok && r.source).toBe('bundled');
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it('rejects a remote manifest for another build', async () => {
    const routes = remoteRoutes();
    routes['https://guide.example.com/tours/x-2/tours.manifest.json'] = file('tours.manifest.json');
    const { fetch } = stubFetch(routes);
    const r = await loadTour(cfg({ fetch, manifest: REMOTE, fallbackManifest: '', buildId: 'x-2' }), 'create-invoice');
    expect(r).toMatchObject({ ok: false, reason: 'invalid-data' });
  });

  it('needs a build id for {buildId}', async () => {
    const { fetch } = stubFetch({});
    const r = await loadTour(cfg({ fetch, manifest: REMOTE, fallbackManifest: '' }), 'create-invoice');
    expect(r).toMatchObject({ ok: false, reason: 'load-failed' });
  });

  describe('pinned by the bundled manifest of the same build', () => {
    const BASE = 'https://guide.example.com/tours/demo-0001/';
    const OTHER_HASH = `sha384-${'A'.repeat(64)}`;
    const bundledHash = (locale: string) => JSON.parse(file('tours.manifest.json')).tours[0].integrity[locale];
    const tampered = (patch: (m: Record<string, any>) => void): Routes => {
      const routes = remoteRoutes();
      const m = JSON.parse(file('tours.manifest.json'));
      patch(m);
      routes[`${BASE}tours.manifest.json`] = JSON.stringify(m);
      return routes;
    };
    const tourCalls = (calls: { url: string; init: RequestInit }[]) => calls.filter((c) => !c.url.endsWith('manifest.json'));

    it("ignores the hash a remote manifest vouches for itself", async () => {
      const { fetch, calls } = stubFetch(tampered((m) => (m.tours[0].integrity.en = OTHER_HASH)));
      const r = await loadTour(cfg({ fetch, manifest: REMOTE }), 'create-invoice');
      expect(r.ok && r.source).toBe('remote');
      expect(tourCalls(calls).map((c) => [c.url, c.init.integrity])).toEqual([[`${BASE}create-invoice.en.json`, bundledHash('en')]]);
    });

    it('never fetches a locale the bundle does not pin', async () => {
      const routes = tampered((m) => {
        m.locales.push('en-US');
        Object.assign(m.tours[0].files, { 'en-US': 'create-invoice.en-US.json' });
        Object.assign(m.tours[0].title, { 'en-US': 'x' });
        Object.assign(m.tours[0].integrity, { 'en-US': OTHER_HASH });
      });
      const { fetch, calls } = stubFetch(routes);
      const r = await loadTour(cfg({ fetch, manifest: REMOTE, locale: 'en-US' }), 'create-invoice');
      expect(r.ok && r.locale).toBe('en');
      expect(tourCalls(calls).map((c) => [c.url, c.init.integrity])).toEqual([[`${BASE}create-invoice.en.json`, bundledHash('en')]]);
    });

    it('never follows a case variant the remote lists first', async () => {
      const routes = tampered((m) => {
        m.tours = [m.tours[0]];
        m.locales = ['en', 'FR'];
        m.tours[0].files = { en: 'create-invoice.en.json', FR: 'create-invoice.FR.json' };
        m.tours[0].title = { en: 'x', FR: 'x' };
        m.tours[0].integrity = { en: OTHER_HASH, FR: OTHER_HASH };
      });
      const { fetch, calls } = stubFetch(routes);
      const r = await loadTour(cfg({ fetch, manifest: REMOTE, locale: 'fr' }), 'create-invoice');
      expect(r.ok && r.locale).toBe('fr');
      expect(tourCalls(calls).map((c) => [c.url, c.init.integrity])).toEqual([[`${BASE}create-invoice.fr.json`, bundledHash('fr')]]);
    });

    it('never fetches a tour the bundle does not list', async () => {
      const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
      const routes = tampered((m) => {
        const extra = JSON.parse(JSON.stringify(m.tours[0]));
        extra.id = 'phish';
        extra.files = { en: 'phish.en.json', fr: 'phish.fr.json' };
        m.tours.push(extra);
      });
      const { fetch, calls } = stubFetch(routes);
      const r = await loadTour(cfg({ fetch, manifest: REMOTE }), 'phish');
      expect(r).toMatchObject({ ok: false, reason: 'load-failed' });
      expect(calls.some((c) => c.url.includes('phish'))).toBe(false);
      expect(warn).toHaveBeenCalled();
      warn.mockRestore();
    });

    it('pins a same-origin proxy too', async () => {
      const { fetch, calls } = stubFetch({ ...bundledRoutes(), ...bundledRoutes(`${ORIGIN}/proxy/`) });
      const r = await loadTour(cfg({ fetch, manifest: '/proxy/tours.manifest.json' }), 'create-invoice');
      expect(r.ok).toBe(true);
      expect(tourCalls(calls).map((c) => [c.url, c.init.integrity])).toEqual([[`${ORIGIN}/proxy/create-invoice.en.json`, bundledHash('en')]]);
    });

    it("without a bundle, a cross-origin file is held to the remote manifest's own hash", async () => {
      const { fetch, calls } = stubFetch(tampered((m) => (m.tours[0].integrity.en = OTHER_HASH)));
      const r = await loadTour(cfg({ fetch, manifest: REMOTE, fallbackManifest: '', buildId: 'demo-0001' }), 'create-invoice');
      expect(r.ok).toBe(true); // the stub does not enforce SRI; the browser would reject the mismatch
      expect(tourCalls(calls).map((c) => c.init.integrity)).toEqual([OTHER_HASH]);
    });
  });

  it('a custom source supplies the tour file', async () => {
    const { fetch } = stubFetch(bundledRoutes());
    const source = vi.fn(async (_id: string, locale: string) => JSON.parse(file(`create-invoice.${locale}.json`)));
    const r = await loadTour(cfg({ fetch, source }), 'create-invoice');
    expect(r.ok && r.source).toBe('custom');
    expect(source).toHaveBeenCalledWith('create-invoice', 'en', { buildId: 'demo-0001', manifestUrl: `${ORIGIN}/tours/tours.manifest.json` });
  });
});

describe('readCapped', () => {
  it('refuses a declared or streamed body over the cap, and invalid UTF-8', async () => {
    await expect(readCapped(new Response('x'.repeat(10), { headers: { 'content-length': '10' } }), 5)).rejects.toThrow(/larger/);
    await expect(readCapped(new Response('x'.repeat(10)), 5)).rejects.toThrow(/larger/);
    await expect(readCapped(new Response(new Uint8Array([0xff, 0xfe])), 5)).rejects.toThrow(/UTF-8/);
    await expect(readCapped(new Response('ok'), 5)).resolves.toBe('ok');
  });
});

describe('pickLocale', () => {
  it('exact, base language, default, then first', () => {
    expect(pickLocale(['en', 'fr'], 'fr', 'en')).toBe('fr');
    expect(pickLocale(['en', 'fr'], 'fr-CA', 'en')).toBe('fr');
    expect(pickLocale(['en', 'fr'], 'de', 'en')).toBe('en');
    expect(pickLocale(['fr'], 'de', 'en')).toBe('fr');
  });
});
