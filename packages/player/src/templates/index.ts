/**
 * Template registry. The component dispatches ``render()`` to one of these by
 * the resolved ``template`` id; ``static styles`` aggregates every template's
 * CSS (unrendered templates simply have no matching DOM).
 */

import { baseStyles } from './base-styles.js';
import * as inline from './inline.js';
import * as overlay from './overlay.js';
import * as sidebar from './sidebar.js';
import type { TemplateId, TemplateModule } from './types.js';

/**
 * The registry is the single source of truth: registering a template here is
 * the only edit needed inside this file. `ALL_TEMPLATES`, `isTemplateId` and
 * `templateStyles` are all derived from it below, so a new template can never
 * be renderable-but-unstyled or renderable-but-unlistable.
 */
export const TEMPLATES: Record<TemplateId, TemplateModule> = {
  overlay,
  sidebar,
  inline,
};

/**
 * Display/selection order for the segmented switcher — deliberately not the
 * registry's declaration order, which is why this one list stays explicit.
 * `templates.test.ts` asserts it covers every registered template.
 */
export const ALL_TEMPLATES: readonly TemplateId[] = ['sidebar', 'overlay', 'inline'];

// Derived from the registry rather than from ALL_TEMPLATES: identity must
// follow what can actually be rendered, not what the switcher chooses to show.
const TEMPLATE_SET: ReadonlySet<string> = new Set(Object.keys(TEMPLATES));

export function isTemplateId(value: unknown): value is TemplateId {
  return typeof value === 'string' && TEMPLATE_SET.has(value);
}

/** Aggregated styles for the component's ``static styles``. */
export const templateStyles = [baseStyles, ...Object.values(TEMPLATES).map((m) => m.styles)];

export type { TemplateContext, TemplateId, StepView, TemplateModule } from './types.js';
