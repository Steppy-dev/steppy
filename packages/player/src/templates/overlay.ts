/**
 * Overlay template — the original/default player presentation: a full-bleed
 * screenshot stage with a floating callout pinned to the highlighted target,
 * and a bottom chrome bar. Lifted from the component verbatim (only the new
 * template switcher is added); engine logic is untouched.
 */

import { css, html, nothing, type TemplateResult } from 'lit';

import {
  renderLayoutSelect,
  renderLocaleSelect,
  renderNext,
  renderPrev,
  renderProgress,
  renderRestart,
  renderTemplateSwitcher,
} from './controls.js';
import type { TemplateContext } from './types.js';

export function render(ctx: TemplateContext): TemplateResult {
  return html`
    <div class="steppy-wrapper steppy-tpl-overlay">
      ${ctx.renderStage()}
      <div class="steppy-chrome">
        ${renderProgress(ctx)} ${renderPrev(ctx)} ${renderNext(ctx)} ${renderRestart(ctx)}
        ${renderLocaleSelect(ctx)} ${renderLayoutSelect(ctx)} ${renderTemplateSwitcher(ctx)}
      </div>
      ${ctx.completed ? html`<div class="steppy-state">${ctx.ui.done}</div>` : nothing}
    </div>
  `;
}

export const styles = css`
  .steppy-tpl-overlay .steppy-seg {
    margin-left: auto;
  }
`;
