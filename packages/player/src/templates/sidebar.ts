/**
 * Sidebar template — a fixed left rail (guide title, meta line, vertical step
 * timeline, current-step description, prev/next + counter) beside the preview.
 * The current step's full localized description is surfaced in the rail; the
 * in-stage floating tooltip is suppressed (the stage handles that based on the
 * active template). Collapses to a single column on narrow containers.
 *
 * The rail is height-capped and the timeline scrolls inside it, so a 19-step
 * guide occupies the same vertical space as a 3-step one. The component pairs
 * this with ``_scrollActiveRowIntoView`` so advancing never leaves the current
 * step off-screen. Every layout dimension the cap touches — rail width, rail
 * height, sticky offset — is a ``--steppy-*`` custom property, because a host
 * that embeds the player under its own chrome (the Angular target) cannot use
 * the ``100vh`` default.
 */

import { css, html, nothing, type TemplateResult } from 'lit';

import { format } from '../i18n/ui-strings.js';
import { renderInlineMarkdown } from '../ui/markdown.js';
import {
  renderCounter,
  renderLayoutSelect,
  renderLocaleSelect,
  renderNext,
  renderPrev,
  renderTemplateSwitcher,
} from './controls.js';
import type { StepView, TemplateContext } from './types.js';

function renderRow(ctx: TemplateContext, s: StepView): TemplateResult {
  const sub =
    s.status === 'completed'
      ? html`${ctx.ui.stepPassed} <span aria-hidden="true">✓</span>`
      : s.subLabel || nothing;
  return html`
    <li
      class="steppy-step steppy-step--${s.status}"
      aria-current=${s.status === 'current' ? 'step' : nothing}
    >
      <button
        class="steppy-step-row"
        ?disabled=${!s.reachable}
        @click=${() => ctx.onGoTo(s.index)}
      >
        <span class="steppy-node" aria-hidden="true"></span>
        <span class="steppy-step-main">
          <span class="steppy-step-title">${renderInlineMarkdown(s.title)}</span>
          <span class="steppy-step-sub">${sub}</span>
        </span>
      </button>
      ${s.status === 'current' && s.description
        ? html`<p class="steppy-step-desc">${renderInlineMarkdown(s.description)}</p>`
        : nothing}
    </li>
  `;
}

export function render(ctx: TemplateContext): TemplateResult {
  return html`
    <div class="steppy-tpl-sidebar">
      <div class="steppy-sidebar-layout">
        <aside class="steppy-rail">
          <div class="steppy-rail-head">
            <h2 class="steppy-guide-title">${ctx.guideTitle}</h2>
            <p class="steppy-meta">
              ${format(ctx.ui.stepsMeta, {
                count: ctx.total,
                duration: ctx.durationLabel,
              })}
            </p>
            <div class="steppy-rail-switchers">
              ${renderTemplateSwitcher(ctx)} ${renderLocaleSelect(ctx)}
              ${renderLayoutSelect(ctx)}
            </div>
          </div>
          <ol class="steppy-timeline">
            ${ctx.steps.map((s) => renderRow(ctx, s))}
          </ol>
          <div class="steppy-rail-foot">
            ${renderPrev(ctx)} ${renderNext(ctx)} ${renderCounter(ctx)}
          </div>
        </aside>
        <div class="steppy-preview">${ctx.renderStage()}</div>
      </div>
      ${ctx.completed ? html`<div class="steppy-state">${ctx.ui.done}</div>` : nothing}
    </div>
  `;
}

export const styles = css`
  .steppy-tpl-sidebar {
    container-type: inline-size;
  }
  .steppy-sidebar-layout {
    display: grid;
    grid-template-columns: var(--steppy-rail-width, 340px) 1fr;
    gap: 20px;
    align-items: start;
  }
  /* The rail is capped and sticky, never the preview: when the capture is a tall
     mobile screenshot the preview is the taller item, and sticking *it* would pin
     its top at the offset and put its bottom permanently out of reach. Sticking
     the rail instead keeps the nav in view while that image scrolls, and is a
     no-op in the common case where the rail is the taller item. */
  .steppy-rail {
    display: flex;
    flex-direction: column;
    gap: 16px;
    background: var(--steppy-surface);
    border: 1px solid var(--steppy-border);
    border-radius: 10px;
    padding: 18px;
    position: sticky;
    top: var(--steppy-sidebar-offset, 16px);
    /* border-box is required, not cosmetic: max-height sizes the *content* box
       by default, so the 18px padding and 1px border would push the rail 38px
       past its own cap and back off the bottom of the screen. */
    box-sizing: border-box;
    max-height: var(--steppy-rail-max-height, calc(100vh - 32px));
  }
  .steppy-rail-head {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .steppy-guide-title {
    margin: 0;
    font-family: var(--steppy-font-serif);
    font-size: 22px;
    font-weight: 600;
    line-height: 1.2;
    color: var(--steppy-fg);
  }
  .steppy-meta {
    margin: 0;
    font-family: var(--steppy-font-mono);
    font-size: 12px;
    color: var(--steppy-muted);
  }
  .steppy-rail-switchers {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    font-size: 13px;
  }

  /* ── Timeline ──────────────────────────────────────────────────────── */
  /* The timeline is the only part of the rail that scrolls, so the head
     (title/meta/switchers) and the foot (prev/next/counter) stay pinned however
     long the guide is. min-height:0 is load-bearing: a flex item's default
     min-height:auto refuses to shrink below its content, so without it the rail
     grows past its own max-height and nothing ever scrolls. */
  .steppy-timeline {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    scrollbar-gutter: stable;
  }
  .steppy-step {
    position: relative;
    padding-left: 4px;
  }
  /* Connecting line between nodes. */
  .steppy-step:not(:last-child) .steppy-node::after {
    content: '';
    position: absolute;
    left: 50%;
    top: 14px;
    transform: translateX(-50%);
    width: 2px;
    height: calc(100% - 6px);
    background: var(--steppy-border);
  }
  .steppy-step-row {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    width: 100%;
    background: transparent;
    border: 0;
    padding: 8px 6px;
    text-align: left;
    cursor: pointer;
    color: var(--steppy-fg);
    border-radius: 6px;
    font: inherit;
  }
  .steppy-step-row:disabled {
    cursor: default;
  }
  .steppy-step-row:hover:not(:disabled) {
    background: var(--steppy-bg);
  }
  .steppy-node {
    position: relative;
    flex: 0 0 auto;
    width: 14px;
    height: 14px;
    margin-top: 3px;
    border-radius: 50%;
    border: 2px solid var(--steppy-border);
    background: var(--steppy-surface);
    box-sizing: border-box;
    z-index: 1;
  }
  .steppy-step--completed .steppy-node {
    background: var(--steppy-success);
    border-color: var(--steppy-success);
  }
  .steppy-step--current .steppy-node {
    background: var(--steppy-accent);
    border-color: var(--steppy-accent);
    box-shadow: 0 0 0 4px var(--steppy-accent-weak);
  }
  .steppy-step-main {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }
  .steppy-step-title {
    font-size: 14px;
    color: var(--steppy-fg);
  }
  .steppy-step--current .steppy-step-title {
    font-weight: 700;
  }
  .steppy-step--upcoming .steppy-step-title {
    color: var(--steppy-muted);
  }
  .steppy-step-sub {
    font-family: var(--steppy-font-mono);
    font-size: 11px;
    color: var(--steppy-muted);
  }
  .steppy-step-desc {
    margin: 4px 0 8px 26px;
    font-size: 13px;
    line-height: 1.45;
    color: var(--steppy-fg);
  }

  .steppy-rail-foot {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .steppy-rail-foot .steppy-counter {
    margin-left: auto;
    font-family: var(--steppy-font-mono);
    font-size: 13px;
    color: var(--steppy-muted);
  }
  /* align-self overrides the grid's align-items:start for this item alone. A
     landscape capture is much shorter than a long rail, and leaving it
     top-aligned puts all the slack in one block below the image; centring splits
     it and reads as padding. When the preview is the taller item it fills the
     row and this is a no-op. Safe for the overlay coordinate system: the cursor,
     hotspot and tooltip are absolutely positioned inside .steppy-stage and
     scaled from the image's live rect, so they travel with it. */
  .steppy-preview {
    min-width: 0;
    align-self: center;
  }

  /* Collapse to a single column on narrow containers (rail on top). The rail
     keeps a cap — uncapped, rail-on-top reproduces the unbounded-timeline
     problem on a phone — but drops sticky, which would otherwise park most of
     the viewport on the rail and leave no room for the screenshot below it.
     Both the container and media query must carry every rule. */
  @container (max-width: 640px) {
    .steppy-sidebar-layout {
      grid-template-columns: 1fr;
    }
    .steppy-rail {
      position: static;
      max-height: var(--steppy-rail-max-height-narrow, 60vh);
    }
    .steppy-preview {
      align-self: start;
    }
  }
  @media (max-width: 640px) {
    .steppy-sidebar-layout {
      grid-template-columns: 1fr;
    }
    .steppy-rail {
      position: static;
      max-height: var(--steppy-rail-max-height-narrow, 60vh);
    }
    .steppy-preview {
      align-self: start;
    }
  }
`;
