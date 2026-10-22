/**
 * Inline template — full-bleed preview with a slim full-width caption bar: the
 * step counter, the current localized description, a mono sub-label, and
 * prev/next. The switchers ride along compactly so the player stays
 * self-contained, but the bar is the only chrome over the preview.
 */

import { css, html, nothing, type TemplateResult } from 'lit';

import {
  renderCounter,
  renderLayoutSelect,
  renderLocaleSelect,
  renderNext,
  renderPrev,
  renderTemplateSwitcher,
} from './controls.js';
import { renderInlineMarkdown } from '../ui/markdown.js';
import type { TemplateContext } from './types.js';

export function render(ctx: TemplateContext): TemplateResult {
  const cur =
    ctx.steps.find((s) => s.status === 'current') ?? ctx.steps[ctx.currentIndex - 1];
  return html`
    <div class="steppy-tpl-inline">
      ${ctx.renderStage()}
      <div class="steppy-inline-bar">
        ${renderCounter(ctx)}
        <span class="steppy-inline-desc">${renderInlineMarkdown(cur?.description ?? '')}</span>
        ${cur?.subLabel
          ? html`<span class="steppy-inline-sub">${cur.subLabel}</span>`
          : nothing}
        <span class="steppy-inline-controls">
          ${renderTemplateSwitcher(ctx)} ${renderLocaleSelect(ctx)} ${renderLayoutSelect(ctx)}
          ${renderPrev(ctx)} ${renderNext(ctx)}
        </span>
      </div>
      ${ctx.completed ? html`<div class="steppy-state">${ctx.ui.done}</div>` : nothing}
    </div>
  `;
}

export const styles = css`
  .steppy-tpl-inline {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .steppy-inline-bar {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    background: var(--steppy-chrome-bg);
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 14px;
  }
  .steppy-inline-bar .steppy-counter {
    font-family: var(--steppy-font-mono);
    font-weight: 600;
    color: var(--steppy-accent);
  }
  .steppy-inline-desc {
    flex: 1 1 240px;
    min-width: 0;
    color: var(--steppy-fg);
  }
  .steppy-inline-sub {
    font-family: var(--steppy-font-mono);
    font-size: 12px;
    color: var(--steppy-muted);
  }
  .steppy-inline-controls {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    margin-left: auto;
    flex-wrap: wrap;
  }
`;
