/**
 * Legacy IIFE — drop-in replacement for the inline JS that previously lived
 * in [templates/static/cursor.js.j2](../../../../templates/static/cursor.js.j2)
 * and the cursor block of [templates/guide.html.j2](../../../../templates/guide.html.j2).
 *
 * Reads ``window.__STEPPY_STEPS__`` (per-step frame data) and
 * ``window.__STEPPY_TIMINGS__`` (cursor timing overrides), then animates each
 * step's cursor when its ``.steppy-step`` section scrolls into view. This is
 * the exact contract those Jinja templates already produce; making them
 * include this bundle replaces ~175 lines of duplicated JS with a single
 * built artifact.
 *
 * Two consumers:
 *
 * - **Static / standalone HTML** (``cursor.js.j2`` / ``guide.html.j2``): the
 *   page sets the ``__STEPPY_STEPS__`` globals before the bundle loads, so the
 *   bundle auto-inits on DOM-ready over the whole document.
 * - **Angular host** (``angular/component.ts.j2``): there are no globals and
 *   the guide HTML is injected via ``[innerHTML]`` *after* DOM-ready (and
 *   re-injected on locale/layout/mode change). The bundle never auto-inits in
 *   that case; instead it exposes ``window.__STEPPY_CURSOR__.mount(root, steps,
 *   timings)`` so the component can (re)drive the cursor over its own subtree
 *   each time the DOM is swapped. ``mount`` returns a disposer that disconnects
 *   the observer and aborts any in-flight animation.
 *
 * Output format: IIFE bundle. Vite builds this via ``vite.iife.config.ts``.
 */

import type { CursorTimings } from './types.js';
import { CursorAnimator } from './animator.js';

interface LegacyFrame {
  screenshot: string;
  viewport: { width: number; height: number } | null;
  cursor: Parameters<CursorAnimator['animateFrame']>[0]['cursor'];
}

interface LegacyStep {
  index: number;
  frames: LegacyFrame[];
}

type LegacyTimings = Partial<CursorTimings> | undefined;

interface SteppyCursorApi {
  /** Drive the cursor over every ``.steppy-step`` under ``root``, animating
   * each step as it scrolls into view. Returns a disposer. */
  mount(root: ParentNode, steps: LegacyStep[], timings?: LegacyTimings): () => void;
  /** Auto-init entry point used by the static/standalone templates (reads the
   * ``window.__STEPPY_STEPS__`` globals and mounts over ``document``). */
  init(): void;
}

declare global {
  interface Window {
    __STEPPY_STEPS__?: LegacyStep[];
    __STEPPY_STEPS_BY_LAYOUT__?: Record<string, LegacyStep[]>;
    __STEPPY_TIMINGS__?: Partial<CursorTimings>;
    __STEPPY_CURSOR__?: SteppyCursorApi;
  }
}

function animateStep(
  section: Element,
  step: LegacyStep | undefined,
  timings: LegacyTimings,
  animators?: CursorAnimator[],
): Promise<void> {
  if (!step) return Promise.resolve();
  const stages = Array.from(section.querySelectorAll<HTMLElement>('.steppy-stage'));
  const frames = step.frames ?? [];
  return (async () => {
    for (let i = 0; i < stages.length; i++) {
      const stage = stages[i]!;
      const frame = frames[i];
      if (!frame) continue;
      const animator = new CursorAnimator({ stage, timings });
      animators?.push(animator);
      await animator.animateFrame(frame);
    }
  })();
}

/**
 * Drive the cursor over a subtree. Used directly by the Angular host (which
 * passes its component root and the per-(locale, layout) step data) and
 * indirectly by ``init`` for the static templates (which pass ``document`` and
 * the ``window.__STEPPY_STEPS__`` globals).
 */
function mount(root: ParentNode, steps: LegacyStep[], timings?: LegacyTimings): () => void {
  const sections = Array.from(root.querySelectorAll<HTMLElement>('.steppy-step'));
  const animators: CursorAnimator[] = [];

  const runSection = (section: HTMLElement): Promise<void> => {
    const idxAttr = section.dataset.stepIndex;
    const idx = idxAttr !== undefined ? parseInt(idxAttr, 10) - 1 : -1;
    return animateStep(section, idx >= 0 ? steps[idx] : undefined, timings, animators);
  };

  let io: IntersectionObserver | null = null;
  if (!('IntersectionObserver' in window)) {
    // Fallback: animate every section sequentially.
    void (async () => {
      for (const section of sections) await runSection(section);
    })();
  } else {
    io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          io!.unobserve(entry.target);
          void runSection(entry.target as HTMLElement);
        });
      },
      { threshold: 0.5 },
    );
    sections.forEach((section) => io!.observe(section));
  }

  return function dispose(): void {
    if (io) io.disconnect();
    animators.forEach((a) => a.abort());
  };
}

function init(): void {
  mount(document, window.__STEPPY_STEPS__ ?? [], window.__STEPPY_TIMINGS__);
}

// Expose the programmatic API so SPA hosts (Angular) can (re)drive the cursor
// over content injected after page load. Harmless for the static templates.
window.__STEPPY_CURSOR__ = { mount, init };

// Auto-init only when the page pre-declared the step globals (static /
// standalone HTML). In an SPA the globals are absent and the host calls
// ``mount`` explicitly after each render, so auto-init would be a no-op anyway
// — guarding here avoids spinning up a stray document-wide observer.
function autoInit(): void {
  if (!window.__STEPPY_STEPS__ && !window.__STEPPY_STEPS_BY_LAYOUT__) return;
  init();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', autoInit);
} else {
  autoInit();
}
