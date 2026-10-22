import type { GuideManifest } from '../src/manifest/schema.js';

/**
 * Minimal valid manifest factory. Tests override fields via the partial arg
 * so the assertions read clearly.
 */
export function makeManifest(overrides: Partial<GuideManifest> = {}): GuideManifest {
  const base: GuideManifest = {
    version: '1.0',
    slug: 'invoices',
    mode: 'interactive',
    role: 'editor',
    title: { fr: 'Factures', nl: 'Facturen' },
    locales: ['fr', 'nl'],
    defaultLocale: 'fr',
    layouts: ['desktop', 'mobile'],
    defaultLayout: 'desktop',
    cursor: {
      pre_action_ms: 200,
      action_ms: 300,
      post_action_ms: 150,
      travel_speed_px_per_ms: 1.5,
    },
    steps: [
      {
        id: 'step-1',
        index: 1,
        action: 'click',
        interactive: true,
        screenshotWhen: 'after',
        waitAfter: 0,
        text: { fr: 'Cliquez sur Nouveau', nl: 'Klik op Nieuw' },
        subtitle: { fr: null, nl: null },
        captures: localesByLayouts(['fr', 'nl'], ['desktop', 'mobile'], (locale, layout) => ({
          frames: [
            {
              screenshot: `./screenshots/${locale}/${layout}/step-01.png`,
              captureSize:
                layout === 'desktop'
                  ? { width: 1440, height: 900 }
                  : { width: 390, height: 844 },
              cursor: {
                kind: 'click',
                target: {
                  selector: '[data-testid="new-rx-btn"]',
                  bbox: { x: 100, y: 200, width: 120, height: 32 },
                },
              },
            },
          ],
        })),
      },
    ],
  };
  return { ...base, ...overrides };
}

/**
 * Attach a per-layout content override to one step, in place.
 *
 * Opt-in on purpose: `makeManifest()` stays override-free so every existing
 * assertion keeps testing the inherit path.
 */
export function withLayoutContent(
  manifest: GuideManifest,
  stepIndex: number,
  locale: string,
  layout: string,
  content: { title: string | null; text: string; subtitle: string | null },
): GuideManifest {
  manifest.steps[stepIndex]!.captures[locale]![layout]!.content = content;
  return manifest;
}

function localesByLayouts<T>(
  locales: string[],
  layouts: string[],
  build: (locale: string, layout: string) => T,
): Record<string, Record<string, T>> {
  const out: Record<string, Record<string, T>> = {};
  for (const locale of locales) {
    out[locale] = {};
    for (const layout of layouts) {
      out[locale]![layout] = build(locale, layout);
    }
  }
  return out;
}
