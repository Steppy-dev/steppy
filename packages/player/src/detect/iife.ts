/**
 * Reader language and layout detection: the browser half, bundled into
 * ``steppy-detect.js`` (``vite.detect.config.ts``) for the static target.
 *
 * Loaded synchronously from ``<head>`` by ``templates/static/_steppy_detect.html.j2``,
 * which renders a ``<script type="application/json" id="steppy-detect-config">``
 * block just before it. Running in ``<head>`` is the point: a redirect happens
 * before anything paints, and the layout is applied through a stylesheet rule
 * on ``<html data-steppy-layout>`` before the body exists, so a phone reader
 * never sees the desktop screenshots flash. The DOM work (classes, buttons, the
 * cursor's step data, the player's attributes) follows on ``DOMContentLoaded``.
 *
 * Template overrides that still carry the pre-detection inline layout script
 * keep working: that script runs later and toggles ``.steppy-layout-active``,
 * but the ``html[data-steppy-layout]`` rule is more specific, and this file
 * re-applies the resolved layout on ``DOMContentLoaded``, after it.
 */

import {
  carryQueryAndHash,
  isBot,
  layoutForWidth,
  resolveLayout,
  resolveLocale,
  safeStorage,
  type SafeStorage,
} from './core.js';

interface DetectConfig {
  locale: boolean;
  layout: boolean;
  /** The page's own locale; ``null`` on a locale-agnostic page (``interactive.html``). */
  pageLocale: string | null;
  locales: string[];
  defaultLocale: string;
  /** Href of this page in each locale, relative to it. */
  hrefFor: Record<string, string>;
  layouts: string[];
  defaultLayout: string | null;
  breakpoints: Record<string, number> | null;
  storageKeys: { locale: string; layout: string };
}

interface SteppyDetectApi {
  readonly locale: string;
  readonly layout: string | null;
  applyLayout(name: string, options?: { store?: boolean }): void;
}

declare global {
  interface Window {
    SteppyDetect?: SteppyDetectApi;
  }
}

const LAYOUT_ATTR = 'data-steppy-layout';

function readConfig(): DetectConfig | null {
  const el = document.getElementById('steppy-detect-config');
  if (!el || !el.textContent) return null;
  try {
    const cfg = JSON.parse(el.textContent) as DetectConfig;
    return cfg && Array.isArray(cfg.locales) && Array.isArray(cfg.layouts) ? cfg : null;
  } catch {
    return null;
  }
}

function getLocalStorage(): Storage | null {
  return window.localStorage;
}

function browserLanguages(): string[] {
  const nav = window.navigator;
  if (nav.languages && nav.languages.length) return Array.from(nav.languages);
  return nav.language ? [nav.language] : [];
}

function viewportWidth(): number {
  return window.innerWidth || document.documentElement.clientWidth || 0;
}

function cssString(value: string): string {
  return '"' + value.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';
}

/** ``url`` with ``name=value`` set in its query, keeping its fragment. */
function withParam(url: string, name: string, value: string): string {
  try {
    const u = new URL(url, window.location.href);
    u.searchParams.set(name, value);
    return u.href;
  } catch {
    return url;
  }
}

function sameDocumentUrl(a: string, b: string): boolean {
  try {
    const ua = new URL(a, window.location.href);
    const ub = new URL(b, window.location.href);
    return ua.origin === ub.origin && ua.pathname === ub.pathname;
  } catch {
    return false;
  }
}

function run(): void {
  const cfg = readConfig();
  if (!cfg) return;

  const params = new URLSearchParams(window.location.search);
  const localeStore = safeStorage(cfg.storageKeys.locale, getLocalStorage);
  const layoutStore = safeStorage(cfg.storageKeys.layout, getLocalStorage);
  const bot = isBot(window.navigator.userAgent, window.navigator.webdriver);

  // ── Locale ──────────────────────────────────────────────────────────────
  let locale = cfg.pageLocale ?? cfg.defaultLocale;
  if (cfg.locale && !bot && cfg.locales.length > 1) {
    const resolved = resolveLocale({
      param: params.get('lang'),
      stored: localeStore.get(),
      prefs: browserLanguages(),
      locales: cfg.locales,
      defaultLocale: cfg.defaultLocale,
    });
    if (cfg.pageLocale === null) {
      locale = resolved.value;
    } else if (resolved.source !== 'default' && resolved.value !== cfg.pageLocale) {
      // Nothing preferred over this page means it stays: a reader whose browser
      // matches no configured locale keeps whichever page they opened.
      const href = cfg.hrefFor[resolved.value];
      if (href) {
        window.location.replace(carryQueryAndHash(href, window.location.search, window.location.hash));
        return;
      }
    }
  }
  if (cfg.locales.length > 1) bindLocalePicker(cfg, localeStore);

  // ── Layout ──────────────────────────────────────────────────────────────
  const layouts = cfg.layouts;
  const multiLayout = layouts.length > 1;
  let layout: string | null = layouts.length ? (cfg.defaultLayout ?? layouts[0] ?? null) : null;
  let explicit = false;
  let domReady = false;
  const breakpoints = cfg.layout ? cfg.breakpoints : null;

  if (multiLayout) {
    const resolved = resolveLayout({
      param: params.get('layout'),
      stored: layoutStore.get(),
      width: viewportWidth(),
      breakpoints,
      layouts,
      defaultLayout: cfg.defaultLayout,
    });
    layout = resolved.value;
    explicit = resolved.source === 'param' || resolved.source === 'stored';
    document.documentElement.setAttribute(LAYOUT_ATTR, layout);
    const style = document.createElement('style');
    style.setAttribute('data-steppy-detect', '');
    style.textContent =
      `html[${LAYOUT_ATTR}] .steppy-layout{display:none}` +
      layouts
        .map((n) => `html[${LAYOUT_ATTR}=${cssString(n)}] .steppy-layout[data-layout=${cssString(n)}]{display:block}`)
        .join('');
    (document.head || document.documentElement).appendChild(style);
  }

  function applyLayout(name: string, options: { store?: boolean } = {}): void {
    if (!multiLayout || layouts.indexOf(name) === -1) return;
    if (options.store) {
      layoutStore.set(name);
      explicit = true;
    }
    layout = name;
    document.documentElement.setAttribute(LAYOUT_ATTR, name);
    if (!domReady) return;
    if (document.body) document.body.setAttribute('data-active-layout', name);
    document.querySelectorAll<HTMLElement>('.steppy-layout').forEach((el) => {
      el.classList.toggle('steppy-layout-active', el.getAttribute('data-layout') === name);
    });
    document.querySelectorAll<HTMLElement>('.steppy-layout-switch [data-layout]').forEach((b) => {
      if (b.getAttribute('data-layout') === name) b.setAttribute('aria-current', 'true');
      else b.removeAttribute('aria-current');
    });
    if (window.__STEPPY_STEPS_BY_LAYOUT__) {
      window.__STEPPY_STEPS__ = window.__STEPPY_STEPS_BY_LAYOUT__[name] || [];
      window.__STEPPY_ACTIVE_LAYOUT__ = name;
    }
    document.querySelectorAll('steppy-guide').forEach((g) => {
      if (g.getAttribute('variant') !== name) g.setAttribute('variant', name);
    });
    document.dispatchEvent(new CustomEvent('steppy:layout-change', { detail: { layout: name } }));
  }

  window.SteppyDetect = {
    get locale() {
      return locale;
    },
    get layout() {
      return layout;
    },
    applyLayout,
  };

  // A pick in the page's layout switch (static buttons) is the reader's choice.
  document.addEventListener('click', (event) => {
    const target = event.target as Element | null;
    const button = target && target.closest ? target.closest('.steppy-layout-switch [data-layout]') : null;
    const name = button && button.getAttribute('data-layout');
    if (name) applyLayout(name, { store: true });
  });

  // Picks made inside the player carry ``detail.user``; host-driven changes do not.
  document.addEventListener('steppy:variant', (event) => {
    const detail = (event as CustomEvent<{ to?: string; user?: boolean }>).detail;
    if (detail && detail.user && detail.to) applyLayout(detail.to, { store: true });
  });
  document.addEventListener('steppy:locale', (event) => {
    const detail = (event as CustomEvent<{ to?: string; user?: boolean }>).detail;
    if (detail && detail.user && detail.to) {
      localeStore.set(detail.to);
      locale = detail.to;
    }
  });

  const onReady = (): void => {
    domReady = true;
    if (layout && multiLayout) applyLayout(layout);
    if (cfg.pageLocale === null && cfg.locale && !bot) {
      document.querySelectorAll('steppy-guide').forEach((g) => {
        if (g.getAttribute('locale') !== locale) g.setAttribute('locale', locale);
      });
    }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', onReady);
  else onReady();

  // Until the reader picks, follow the viewport (a rotated tablet, a resized window).
  if (multiLayout && breakpoints) {
    let timer: number | undefined;
    window.addEventListener('resize', () => {
      if (explicit) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        if (explicit) return;
        const next = layoutForWidth(viewportWidth(), breakpoints, layouts);
        if (next && next !== layout) applyLayout(next);
      }, 150);
    });
  }
}

/**
 * Store a pick in the locale switch before the link navigates. A link is
 * recognised by ``data-steppy-locale`` or, for template overrides with their
 * own markup, by pointing at this page's sibling in that locale. When storage
 * is unavailable the choice rides along as ``?lang=`` instead, or the next page
 * would detect the browser language again and send the reader straight back.
 */
function bindLocalePicker(cfg: DetectConfig, localeStore: SafeStorage): void {
  document.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
    const target = event.target as Element | null;
    const link = target && target.closest ? (target.closest('a[href]') as HTMLAnchorElement | null) : null;
    if (!link) return;
    let picked = link.getAttribute('data-steppy-locale');
    if (!picked && link.closest('.steppy-locale-switch')) {
      for (const loc of cfg.locales) {
        const href = cfg.hrefFor[loc];
        if (href && sameDocumentUrl(href, link.href)) {
          picked = loc;
          break;
        }
      }
    }
    if (!picked || cfg.locales.indexOf(picked) === -1) return;
    if (!localeStore.set(picked) && cfg.locale) {
      event.preventDefault();
      window.location.href = withParam(link.href, 'lang', picked);
    }
  });
}

run();
