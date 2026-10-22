/**
 * Shared chrome fragments composed by the three templates. Pure functions over
 * {@link TemplateContext} — no state, no engine logic. Each template arranges
 * these into its own layout.
 */

import { html, nothing, type TemplateResult } from 'lit';

import { format, type UiStrings } from '../i18n/ui-strings.js';
import type { TemplateContext, TemplateId } from './types.js';

const TEMPLATE_LABEL_KEY: Record<TemplateId, keyof UiStrings> = {
  sidebar: 'templateSidebar',
  overlay: 'templateOverlay',
  inline: 'templateInline',
};

export function renderProgress(ctx: TemplateContext): TemplateResult {
  return html`<span class="steppy-progress" aria-live="polite"
    >${format(ctx.ui.progress, { current: ctx.currentIndex, total: ctx.total })}</span
  >`;
}

/** Compact "i/n" counter (inline + sidebar footers). */
export function renderCounter(ctx: TemplateContext): TemplateResult {
  return html`<span class="steppy-counter" aria-live="polite"
    >${ctx.currentIndex}/${ctx.total}</span
  >`;
}

export function renderPrev(ctx: TemplateContext): TemplateResult {
  return html`<button
    class="steppy-btn steppy-prev"
    @click=${ctx.onPrev}
    ?disabled=${ctx.prevDisabled}
  >
    ${ctx.ui.previous}
  </button>`;
}

export function renderNext(ctx: TemplateContext): TemplateResult {
  return html`<button
    class="steppy-btn steppy-next"
    @click=${ctx.onNext}
    ?disabled=${ctx.nextDisabled}
  >
    ${ctx.ui.next}
  </button>`;
}

export function renderRestart(ctx: TemplateContext): TemplateResult {
  return html`<button class="steppy-btn steppy-restart" @click=${ctx.onRestart}>
    ${ctx.ui.restart}
  </button>`;
}

export function renderLocaleSelect(ctx: TemplateContext): TemplateResult | typeof nothing {
  if (ctx.locales.length <= 1) return nothing;
  return html`<label class="steppy-field">
    ${ctx.ui.localeSwitcher}
    <select @change=${ctx.onLocaleChange} .value=${ctx.activeLocale}>
      ${ctx.locales.map(
        (loc) =>
          html`<option value=${loc} ?selected=${loc === ctx.activeLocale}>${loc}</option>`,
      )}
    </select>
  </label>`;
}

export function renderLayoutSelect(ctx: TemplateContext): TemplateResult | typeof nothing {
  if (ctx.layouts.length <= 1 || ctx.hideLayoutSwitcher) return nothing;
  return html`<label class="steppy-field">
    ${ctx.ui.variantSwitcher}
    <select @change=${ctx.onLayoutChange} .value=${ctx.activeLayout ?? ''}>
      ${ctx.layouts.map(
        (lay) =>
          html`<option value=${lay} ?selected=${lay === ctx.activeLayout}>${lay}</option>`,
      )}
    </select>
  </label>`;
}

/** Segmented [Sidebar | Overlay | Inline] control (active = dark fill). */
export function renderTemplateSwitcher(ctx: TemplateContext): TemplateResult {
  return html`<div class="steppy-seg" role="group" aria-label=${ctx.ui.templateSwitcher}>
    ${ctx.templates.map((t) => {
      const active = t === ctx.activeTemplate;
      return html`<button
        type="button"
        class="steppy-seg-btn"
        aria-current=${active ? 'true' : 'false'}
        @click=${() => ctx.onTemplateChange(t)}
      >
        ${ctx.ui[TEMPLATE_LABEL_KEY[t]]}
      </button>`;
    })}
  </div>`;
}
