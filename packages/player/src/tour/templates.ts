/**
 * The overlay's DOM. Data is only ever bound as text or attribute values
 * (never ``style=``, never HTML), and the UI never carries the app's test-id
 * attribute, so app locators cannot match it.
 *
 * DOM contract (the CI replay and the docs rely on it): the bubble is
 * ``section[part~="bubble"][role="dialog"]`` with ``data-tour-id``,
 * ``data-step-id``, ``data-state`` (``shown`` | ``fallback``) and
 * ``data-advance``; Next is ``part="button next primary"`` on input and manual
 * steps and ``"button next secondary"`` on change steps, absent on click and
 * visible steps; the fallback has ``skip`` and ``end``; the pill holds
 * ``resume`` and ``close``.
 */

import { html, nothing, type TemplateResult } from 'lit';

import { renderInlineMarkdown, type LinkPolicy } from '../ui/markdown.js';
import { format } from '../ui/strings.js';
import type { TourStrings } from './strings.js';
import { instructionFor } from './strings.js';
import type { TourStep } from './validate.js';

export interface ViewModel {
  mode: 'bubble' | 'pill' | 'none';
  /** The tour file's language and direction, set on the layer (the page's may differ). */
  lang: string;
  dir: 'ltr' | 'rtl';
  tourId: string;
  step: TourStep;
  state: 'shown' | 'fallback';
  /** Spotlight drawn (an anchored step that is showing). */
  anchored: boolean;
  position: { index: number; total: number };
  last: boolean;
  nextEnabled: boolean;
  guideUrl: string | null;
  strings: TourStrings;
  policy: LinkPolicy;
  popover: boolean;
}

export interface Handlers {
  press(e: Event): void;
  next(e: MouseEvent): void;
  skip(e: MouseEvent): void;
  end(e: MouseEvent): void;
  close(e: MouseEvent): void;
  resume(e: MouseEvent): void;
}

const stop = (e: Event) => e.stopPropagation();

function nextButton(v: ViewModel, h: Handlers): TemplateResult | typeof nothing {
  const kind = v.step.advance;
  if (kind === 'click' || kind === 'visible') return nothing;
  const label = v.last && kind === 'manual' ? v.strings.done : v.strings.next;
  const style = kind === 'change' ? 'secondary' : 'primary';
  return html`<button
    part="button next ${style}"
    class=${style}
    ?disabled=${!v.nextEnabled}
    @pointerdown=${h.press}
    @mousedown=${h.press}
    @click=${h.next}
  >
    ${label}
  </button>`;
}

function bubble(v: ViewModel, h: Handlers): TemplateResult {
  const s = v.strings;
  const fallback = v.state === 'fallback';
  const title = fallback ? s.notFoundTitle : v.step.title;
  const body = fallback ? s.notFoundBody : (v.step.text ?? instructionFor(v.step.advance, s));
  const showGuide = v.guideUrl !== null && (fallback || v.last);
  return html`<section
    part="bubble"
    class="bubble"
    role="dialog"
    aria-modal="false"
    aria-labelledby=${title ? 'pw-title' : nothing}
    aria-label=${title ? nothing : s.dialogLabel}
    aria-describedby="pw-body"
    tabindex="-1"
    data-tour-id=${v.tourId}
    data-step-id=${v.step.id}
    data-state=${v.state}
    data-advance=${v.step.advance}
    @pointerdown=${stop}
    @mousedown=${stop}
    @click=${stop}
  >
    <header>
      ${title ? html`<h2 part="title" id="pw-title">${title}</h2>` : nothing}
      <button part="button close" class="close" aria-label=${s.close} @pointerdown=${h.press} @mousedown=${h.press} @click=${h.close}>
        ×
      </button>
    </header>
    <p part="body" class="body" id="pw-body">${fallback ? body : renderInlineMarkdown(body, v.policy)}</p>
    ${showGuide
      ? html`<a part="guide-link" class="guide" href=${v.guideUrl!} target="_blank" rel="noopener noreferrer">${s.openGuide}</a>`
      : nothing}
    <footer>
      <span part="progress" class="progress">${format(s.progress, { current: v.position.index + 1, total: v.position.total })}</span>
      ${fallback
        ? html`<button part="button skip" class="secondary" @pointerdown=${h.press} @mousedown=${h.press} @click=${h.skip}>
              ${s.skipStep}
            </button>
            <button part="button end primary" class="primary" @pointerdown=${h.press} @mousedown=${h.press} @click=${h.end}>
              ${s.endTour}
            </button>`
        : nextButton(v, h)}
    </footer>
  </section>`;
}

function pill(v: ViewModel, h: Handlers): TemplateResult {
  const s = v.strings;
  return html`<div part="pill" class="pill" role="group" aria-label=${s.paused} @pointerdown=${stop} @mousedown=${stop} @click=${stop}>
    <button part="button resume primary" class="primary" @pointerdown=${h.press} @mousedown=${h.press} @click=${h.resume}>
      ${s.resume}
    </button>
    <button part="button close" class="close" aria-label=${s.close} @pointerdown=${h.press} @mousedown=${h.press} @click=${h.close}>
      ×
    </button>
  </div>`;
}

export function renderLayer(v: ViewModel, h: Handlers): TemplateResult {
  return html`<div part="layer" class="layer" lang=${v.lang} dir=${v.dir} popover=${v.popover ? 'manual' : nothing}>
    ${v.mode === 'bubble' && v.anchored && v.state === 'shown' ? html`<div part="spotlight" class="spotlight"></div>` : nothing}
    ${v.mode === 'bubble' ? bubble(v, h) : v.mode === 'pill' ? pill(v, h) : nothing}
    <div class="sr-only" role="status" aria-live="polite" aria-atomic="true"></div>
  </div>`;
}
