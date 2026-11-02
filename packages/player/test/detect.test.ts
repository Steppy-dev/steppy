import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  carryQueryAndHash,
  isBot,
  layoutForWidth,
  matchLocale,
  resolveLayout,
  resolveLocale,
  safeStorage,
} from '../src/detect/core.js';

// ── Pure rules ──────────────────────────────────────────────────────────

describe('matchLocale', () => {
  const locales = ['fr', 'nl', 'en', 'pt-BR'];

  it('prefers an exact match, case- and separator-insensitive', () => {
    expect(matchLocale(['NL'], locales)).toBe('nl');
    expect(matchLocale(['pt_br'], locales)).toBe('pt-BR');
  });

  it('falls back to the primary subtag', () => {
    expect(matchLocale(['nl-BE'], locales)).toBe('nl');
    expect(matchLocale(['pt-PT'], locales)).toBe('pt-BR');
    expect(matchLocale(['fr-BE'], ['fr-BE', 'nl'])).toBe('fr-BE');
    expect(matchLocale(['fr'], ['fr-BE', 'nl'])).toBe('fr-BE');
  });

  it('returns null when nothing matches', () => {
    expect(matchLocale(['de-DE', 'ja'], locales)).toBeNull();
    expect(matchLocale([], locales)).toBeNull();
    expect(matchLocale(['', '  '], locales)).toBeNull();
  });

  it('honours the reader order over exactness further down the list', () => {
    expect(matchLocale(['nl-BE', 'fr'], ['fr', 'nl'])).toBe('nl');
  });
});

describe('resolveLocale precedence', () => {
  const base = { locales: ['fr', 'nl', 'en'], defaultLocale: 'fr' };

  it('parameter > stored > browser > default', () => {
    expect(resolveLocale({ ...base, param: 'en', stored: 'nl', prefs: ['nl'] })).toEqual({ value: 'en', source: 'param' });
    expect(resolveLocale({ ...base, stored: 'nl', prefs: ['en'] })).toEqual({ value: 'nl', source: 'stored' });
    expect(resolveLocale({ ...base, prefs: ['nl-BE'] })).toEqual({ value: 'nl', source: 'browser' });
    expect(resolveLocale({ ...base, prefs: ['de'] })).toEqual({ value: 'fr', source: 'default' });
  });

  it('skips an unknown parameter or stored value', () => {
    expect(resolveLocale({ ...base, param: 'xx', stored: 'yy', prefs: ['nl'] })).toEqual({ value: 'nl', source: 'browser' });
  });
});

describe('layout from the viewport', () => {
  const layouts = ['desktop', 'mobile'];
  const breakpoints = { mobile: 0, desktop: 835 };

  it('picks the layout whose minimum width the viewport reaches', () => {
    expect(layoutForWidth(390, breakpoints, layouts)).toBe('mobile');
    expect(layoutForWidth(834, breakpoints, layouts)).toBe('mobile');
    expect(layoutForWidth(835, breakpoints, layouts)).toBe('desktop');
    expect(layoutForWidth(1440, breakpoints, layouts)).toBe('desktop');
  });

  it('honours explicit breakpoints', () => {
    expect(layoutForWidth(700, { mobile: 0, desktop: 600 }, layouts)).toBe('desktop');
  });

  it('has nothing to go on without breakpoints or a width', () => {
    expect(layoutForWidth(390, null, layouts)).toBeNull();
    expect(layoutForWidth(0, breakpoints, layouts)).toBeNull();
  });

  it('parameter > stored > viewport > default', () => {
    const input = { layouts, breakpoints, defaultLayout: 'desktop' };
    expect(resolveLayout({ ...input, param: 'desktop', stored: 'mobile', width: 390 })).toEqual({
      value: 'desktop',
      source: 'param',
    });
    expect(resolveLayout({ ...input, stored: 'desktop', width: 390 })).toEqual({ value: 'desktop', source: 'stored' });
    expect(resolveLayout({ ...input, width: 390 })).toEqual({ value: 'mobile', source: 'viewport' });
    expect(resolveLayout({ ...input, width: 390, breakpoints: null })).toEqual({ value: 'desktop', source: 'default' });
    expect(resolveLayout({ ...input, param: 'tablet', width: 1440 })).toEqual({ value: 'desktop', source: 'viewport' });
  });
});

describe('isBot', () => {
  it.each([
    'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
    'Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
    'Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)',
    'DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)',
    'facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/120.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0) Chrome-Lighthouse',
  ])('recognises %s', (ua) => {
    expect(isBot(ua)).toBe(true);
  });

  it('lets browsers through', () => {
    expect(isBot('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1')).toBe(false);
    expect(isBot('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36')).toBe(false);
    expect(isBot('')).toBe(false);
  });

  it('treats automation as a bot', () => {
    expect(isBot('Mozilla/5.0 Chrome/120.0', true)).toBe(true);
  });
});

describe('safeStorage', () => {
  it('reads and writes', () => {
    const store = new Map<string, string>();
    const fake = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
    } as unknown as Storage;
    const s = safeStorage('steppy:locale', () => fake);
    expect(s.get()).toBeNull();
    expect(s.set('nl')).toBe(true);
    expect(s.get()).toBe('nl');
  });

  it('survives storage that throws on access, read or write', () => {
    const blocked = safeStorage('k', () => {
      throw new DOMException('denied', 'SecurityError');
    });
    expect(blocked.get()).toBeNull();
    expect(blocked.set('x')).toBe(false);
    const full = safeStorage('k', () => ({
      getItem: () => {
        throw new Error('nope');
      },
      setItem: () => {
        throw new DOMException('full', 'QuotaExceededError');
      },
    }) as unknown as Storage);
    expect(full.get()).toBeNull();
    expect(full.set('x')).toBe(false);
    expect(safeStorage('k', () => null).set('x')).toBe(false);
  });
});

describe('carryQueryAndHash', () => {
  it('keeps the visit query and fragment on the target', () => {
    expect(carryQueryAndHash('./nl.html', '?onboarding=1', '#step-3')).toBe('./nl.html?onboarding=1#step-3');
    expect(carryQueryAndHash('./index.nl.html', '', '')).toBe('./index.nl.html');
  });
});

// ── Browser glue (src/detect/iife.ts) ───────────────────────────────────

interface PageOptions {
  pageLocale?: string | null;
  locale?: boolean;
  layout?: boolean;
  url?: string;
  languages?: string[];
  userAgent?: string;
  width?: number;
  stored?: Record<string, string>;
  blockStorage?: boolean;
  body?: string;
}

const STEPS = {
  desktop: [{ index: 1, frames: [] }],
  mobile: [{ index: 1, frames: [] }],
};

const restorers: (() => void)[] = [];

function memoryStorage(initial: Record<string, string>): Storage {
  const data = new Map(Object.entries(initial));
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (k: string) => data.get(k) ?? null,
    key: (i: number) => Array.from(data.keys())[i] ?? null,
    removeItem: (k: string) => void data.delete(k),
    setItem: (k: string, v: string) => void data.set(k, String(v)),
  };
}

function defineOn(target: object, key: string, value: unknown): void {
  const own = Object.getOwnPropertyDescriptor(target, key);
  Object.defineProperty(target, key, { configurable: true, get: () => value });
  restorers.push(() => {
    if (own) Object.defineProperty(target, key, own);
    else delete (target as Record<string, unknown>)[key];
  });
}

async function runPage(opts: PageOptions = {}): Promise<{ replace: ReturnType<typeof vi.fn> }> {
  const config = {
    locale: opts.locale ?? true,
    layout: opts.layout ?? true,
    pageLocale: opts.pageLocale === undefined ? 'fr' : opts.pageLocale,
    locales: ['fr', 'nl', 'en'],
    defaultLocale: 'fr',
    hrefFor: { fr: './fr.html', nl: './nl.html', en: './en.html' },
    layouts: ['desktop', 'mobile'],
    defaultLayout: 'desktop',
    breakpoints: { mobile: 0, desktop: 835 },
    storageKeys: { locale: 'oce-lang', layout: 'steppy:layout' },
  };
  window.history.replaceState(null, '', opts.url ?? '/guide/fr.html');
  document.head.innerHTML = `<script type="application/json" id="steppy-detect-config">${JSON.stringify(config)}</script>`;
  document.body.innerHTML =
    opts.body ??
    `<nav class="steppy-locale-switch"><a href="./nl.html" data-steppy-locale="nl">NL</a></nav>
     <nav class="steppy-layout-switch"><button data-layout="desktop" aria-current="true">D</button><button data-layout="mobile">M</button></nav>
     <div class="steppy-layout steppy-layout-active" data-layout="desktop"></div>
     <div class="steppy-layout" data-layout="mobile"></div>
     <steppy-guide locale="fr" variant="desktop"></steppy-guide>`;
  if (opts.blockStorage) {
    defineOn(window, 'localStorage', undefined);
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new DOMException('blocked', 'SecurityError');
      },
    });
  } else {
    defineOn(window, 'localStorage', memoryStorage(opts.stored ?? {}));
  }
  defineOn(window.navigator, 'languages', opts.languages ?? ['fr-FR']);
  defineOn(window.navigator, 'userAgent', opts.userAgent ?? 'Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36');
  defineOn(window.navigator, 'webdriver', false);
  defineOn(window, 'innerWidth', opts.width ?? 1280);
  window.__STEPPY_STEPS_BY_LAYOUT__ = STEPS as never;
  const replace = vi.fn();
  defineOn(window, 'location', new Proxy(window.location, {
    get(target, key) {
      if (key === 'replace') return replace;
      const value = Reflect.get(target, key);
      return typeof value === 'function' ? value.bind(target) : value;
    },
  }));
  vi.resetModules();
  await import('../src/detect/iife.js');
  return { replace };
}

/** Cancel the link's navigation after the script's own document listener ran. */
function stopNavigation(): void {
  const stop = (e: Event): void => e.preventDefault();
  document.addEventListener('click', stop);
  restorers.push(() => document.removeEventListener('click', stop));
}

describe('steppy-detect.js on a page', () => {
  beforeEach(() => {
    document.documentElement.removeAttribute('data-steppy-layout');
  });
  afterEach(() => {
    while (restorers.length) restorers.pop()!();
    delete window.SteppyDetect;
    delete window.__STEPPY_STEPS_BY_LAYOUT__;
  });

  it('sends an nl-BE reader to the nl page, keeping query and hash', async () => {
    const { replace } = await runPage({ languages: ['nl-BE', 'nl'], url: '/guide/fr.html?onboarding=1#s2' });
    expect(replace).toHaveBeenCalledWith('./nl.html?onboarding=1#s2');
  });

  it('never redirects a crawler', async () => {
    const { replace } = await runPage({
      languages: ['nl-BE'],
      userAgent: 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
    });
    expect(replace).not.toHaveBeenCalled();
  });

  it('keeps the page when the stored pick is this page', async () => {
    const { replace } = await runPage({ languages: ['nl-BE'], stored: { 'oce-lang': 'fr' } });
    expect(replace).not.toHaveBeenCalled();
  });

  it('follows a stored pick over the browser', async () => {
    const { replace } = await runPage({ languages: ['fr'], stored: { 'oce-lang': 'en' } });
    expect(replace).toHaveBeenCalledWith('./en.html');
  });

  it('follows ?lang= over a stored pick', async () => {
    const { replace } = await runPage({ url: '/guide/fr.html?lang=nl', stored: { 'oce-lang': 'en' } });
    expect(replace).toHaveBeenCalledWith('./nl.html?lang=nl');
  });

  it('leaves a reader whose browser matches nothing on the page they opened', async () => {
    const { replace } = await runPage({ pageLocale: 'nl', url: '/guide/nl.html', languages: ['de-DE'] });
    expect(replace).not.toHaveBeenCalled();
  });

  it('does nothing about the language when locale detection is off', async () => {
    const { replace } = await runPage({ locale: false, languages: ['nl-BE'] });
    expect(replace).not.toHaveBeenCalled();
  });

  it('still detects when storage is blocked', async () => {
    const { replace } = await runPage({ languages: ['nl'], blockStorage: true });
    expect(replace).toHaveBeenCalledWith('./nl.html');
  });

  it('shows the mobile layout on a 390px viewport and opens the player on it', async () => {
    await runPage({ width: 390 });
    expect(document.documentElement.getAttribute('data-steppy-layout')).toBe('mobile');
    expect(document.querySelector('style[data-steppy-detect]')!.textContent).toContain(
      'html[data-steppy-layout="mobile"] .steppy-layout[data-layout="mobile"]{display:block}',
    );
    expect(document.querySelector('.steppy-layout[data-layout="mobile"]')!.classList.contains('steppy-layout-active')).toBe(true);
    expect(document.querySelector('steppy-guide')!.getAttribute('variant')).toBe('mobile');
    expect(window.__STEPPY_ACTIVE_LAYOUT__).toBe('mobile');
    expect(window.SteppyDetect!.layout).toBe('mobile');
  });

  it('keeps the default layout on a desktop viewport', async () => {
    await runPage({ width: 1440 });
    expect(document.documentElement.getAttribute('data-steppy-layout')).toBe('desktop');
  });

  it('ignores the viewport when layout detection is off, but keeps a stored pick', async () => {
    await runPage({ width: 390, layout: false });
    expect(window.SteppyDetect!.layout).toBe('desktop');
    await runPage({ width: 1440, layout: false, stored: { 'steppy:layout': 'mobile' } });
    expect(window.SteppyDetect!.layout).toBe('mobile');
  });

  it('remembers a pick in the layout switch, and the pick beats the viewport', async () => {
    await runPage({ width: 390 });
    (document.querySelector('button[data-layout="desktop"]') as HTMLButtonElement).click();
    expect(window.localStorage.getItem('steppy:layout')).toBe('desktop');
    expect(document.querySelector('button[data-layout="desktop"]')!.getAttribute('aria-current')).toBe('true');
    await runPage({ width: 390, stored: { 'steppy:layout': 'desktop' } });
    expect(window.SteppyDetect!.layout).toBe('desktop');
  });

  it('remembers a pick in the language switch', async () => {
    await runPage({ languages: ['fr'] });
    const link = document.querySelector('a[data-steppy-locale="nl"]') as HTMLAnchorElement;
    stopNavigation();
    link.click();
    expect(window.localStorage.getItem('oce-lang')).toBe('nl');
  });

  it('recognises an override\'s own language links by their target', async () => {
    await runPage({
      languages: ['fr'],
      body: '<nav class="steppy-locale-switch"><a href="./en.html">EN</a></nav>',
    });
    const link = document.querySelector('a') as HTMLAnchorElement;
    stopNavigation();
    link.click();
    expect(window.localStorage.getItem('oce-lang')).toBe('en');
  });

  it('sets the player language on the locale-agnostic interactive page', async () => {
    const { replace } = await runPage({ pageLocale: null, url: '/guide/interactive.html', languages: ['nl-BE'] });
    expect(replace).not.toHaveBeenCalled();
    expect(document.querySelector('steppy-guide')!.getAttribute('locale')).toBe('nl');
  });

  it('stores a pick made inside the player, not a host-driven change', async () => {
    await runPage({ pageLocale: null, url: '/guide/interactive.html' });
    const guide = document.querySelector('steppy-guide')!;
    guide.dispatchEvent(new CustomEvent('steppy:variant', { detail: { from: 'desktop', to: 'mobile' }, bubbles: true }));
    expect(window.localStorage.getItem('steppy:layout')).toBeNull();
    guide.dispatchEvent(
      new CustomEvent('steppy:locale', { detail: { from: 'fr', to: 'en', user: true }, bubbles: true }),
    );
    guide.dispatchEvent(
      new CustomEvent('steppy:variant', { detail: { from: 'desktop', to: 'mobile', user: true }, bubbles: true }),
    );
    expect(window.localStorage.getItem('oce-lang')).toBe('en');
    expect(window.localStorage.getItem('steppy:layout')).toBe('mobile');
  });
});
