import type { CaptureContent, GuideManifest, ManifestStep } from './schema.js';

/**
 * Resolve a step's guide text for one (locale, layout).
 *
 * Two levels, no partials: a `Capture` either carries a full `content`
 * override — the desktop/mobile twin-branch case, where the same logical step
 * is authored once per layout — or it inherits the step-level value.
 *
 * The caller must pass the layout key it also read the *frame* from. Content
 * and frame coming from the same `Capture` is the invariant that keeps a
 * viewer from ever seeing one layout's instructions above another layout's
 * screenshot; see `captureLayoutKey`.
 */
export function resolveStepContent(step: ManifestStep, locale: string, layoutKey: string): CaptureContent {
  const override = step.captures[locale]?.[layoutKey]?.content;
  if (override) return override;
  return {
    title: step.title?.[locale] ?? null,
    text: step.text[locale] ?? '',
    subtitle: step.subtitle?.[locale] ?? null,
  };
}

/**
 * The layout key a render should read `step.captures[locale]` under.
 *
 * Prefers the requested key, then the manifest default, then whatever the bag
 * actually holds. The schema guarantees the full (locale × layout)
 * cross-product, so the first branch is the live path; the rest only matter
 * when a host sets an unrecognised `variant`, and they exist so the fallback
 * lands on the manifest's own default rather than on object insertion order.
 */
export function captureLayoutKey(
  step: ManifestStep,
  locale: string,
  requested: string | null,
  manifest: Pick<GuideManifest, 'layouts' | 'defaultLayout'>,
): string | null {
  const bag = step.captures[locale];
  if (!bag) return null;
  if (requested && requested in bag) return requested;
  const fallback = manifest.defaultLayout ?? manifest.layouts[0] ?? '_';
  if (fallback in bag) return fallback;
  return Object.keys(bag)[0] ?? null;
}
