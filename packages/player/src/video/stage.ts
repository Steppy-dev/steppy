/**
 * Video stage — the DOM half of `modes: ['video']`.
 *
 * Builds a fixed-size, chrome-free surface over the guide's captures and paints
 * exactly one instant of ``timeline.ts`` per call to ``seek``. It is driven from
 * outside (headless Chromium, one screenshot per frame), so nothing here reads
 * the clock: every cursor position, pulse scale, ripple radius and typed
 * substring is computed from the frame index.
 *
 * Two things it deliberately does *not* do, both for pixel parity with the live
 * player:
 *
 * * **It reuses ``baseStyles`` verbatim**, inside a shadow root so the ``:host``
 *   rules apply. The cursor's clip-path, the ripple's border, the keycap chip
 *   and every design token therefore come from one source — there is no second
 *   copy of the look to drift.
 * * **It disables the CSS animations and transitions in that sheet** rather than
 *   trying to scrub them. ``animation-delay`` cannot address a ``transition``
 *   at all (``.steppy-cursor`` fades opacity over 200ms that way), and
 *   ``prefers-reduced-motion`` would silently strip the rest. The timeline
 *   supplies the same values as inline styles instead, which is both
 *   deterministic and immune to the render host's motion settings.
 *
 * Screenshots are all mounted up front and toggled by opacity — never swapped
 * into one ``<img>``'s ``src``, which would risk capturing a frame mid-decode.
 */

import { DEFAULT_TIMINGS } from '../cursor/types.js';
import type { CursorTimings } from '../cursor/types.js';
import { resolveStepContent } from '../manifest/content.js';
import type { GuideManifest } from '../manifest/schema.js';
import { baseStyles } from '../templates/base-styles.js';
import {
  buildTimeline,
  frameAt,
  frameStateEquals,
  framesFor,
  resolveLayoutKey,
} from './timeline.js';
import type { Cue, FrameState, Timeline, VideoTimelineOptions } from './timeline.js';

export interface VideoStageOptions extends Omit<VideoTimelineOptions, 'scale'> {
  /** Output canvas width in CSS px. Height follows the capture's aspect. */
  width: number;
  /** Optional ceiling on total height; the stage shrinks to fit rather than crop. */
  maxHeight?: number | null;
  captions?: 'band' | 'off';
  /** Card + band copy. All optional; the cards are skipped when empty. */
  title?: string;
  subtitle?: string;
  outroText?: string;
  /** Data URI. Never a remote URL — the page renders offline over file://. */
  logo?: string;
  accent?: string;
  /** Provenance line burned into every frame (e.g. "v2.14 · 2026-08-05"). */
  stamp?: string;
  fontFamily?: string;
  /** Per-image load budget. Exceeding it renders the gap instead of hanging. */
  imageTimeoutMs?: number;
}

export interface VideoStage {
  readonly timeline: Timeline;
  readonly durationMs: number;
  readonly frameCount: number;
  ready(): Promise<void>;
  size(): { width: number; height: number };
  cues(): Cue[];
  /** Screenshot URLs that never loaded. Non-empty means a gappy video. */
  missingShots(): string[];
  /** Paint frame ``index``. Returns false when the pixels are unchanged. */
  seek(index: number): boolean;
  destroy(): void;
}

/** libx264 with yuv420p rejects odd dimensions; round down, never up. */
export function evenDown(value: number): number {
  const n = Math.max(2, Math.floor(value));
  return n % 2 === 0 ? n : n - 1;
}

const VIDEO_CSS = `
  :host {
    display: block;
    position: relative;
    overflow: hidden;
    background: var(--steppy-bg);
    --steppy-video-band-pad: 20px;
  }
  /* The live player animates these with CSS keyframes and transitions, both
     wall-clock driven. The timeline supplies the same values per frame. */
  .steppy-stage img,
  .steppy-cursor,
  .steppy-ripple,
  .steppy-kbd {
    animation: none !important;
    transition: none !important;
  }
  .steppy-video-frame {
    position: relative;
    display: flex;
    justify-content: center;
    align-items: flex-start;
    background: var(--steppy-bg);
  }
  .steppy-stage {
    position: relative;
    overflow: hidden;
    background: var(--steppy-chrome-bg);
  }
  .steppy-stage img {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: auto;
    display: block;
    opacity: 0;
    max-width: none;
  }
  .steppy-stage img.on { opacity: 1; }
  .steppy-cursor, .steppy-ripple, .steppy-kbd { opacity: 0; }

  .steppy-video-band {
    box-sizing: border-box;
    padding: var(--steppy-video-band-pad);
    background: var(--steppy-chrome-bg);
    border-top: 1px solid var(--steppy-border);
    display: flex;
    flex-direction: column;
    gap: 12px;
    justify-content: center;
  }
  .steppy-video-progress {
    height: 4px;
    border-radius: 999px;
    background: var(--steppy-border);
    overflow: hidden;
  }
  .steppy-video-progress > i {
    display: block;
    height: 100%;
    width: 0%;
    background: var(--steppy-accent);
  }
  .steppy-video-caption {
    font-family: var(--steppy-font-sans);
    font-size: 20px;
    line-height: 1.45;
    color: var(--steppy-fg);
    margin: 0;
  }
  .steppy-video-caption b { font-weight: 600; }

  .steppy-video-card {
    position: absolute;
    inset: 0;
    display: none;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 18px;
    padding: 8%;
    text-align: center;
    background: var(--steppy-bg);
    z-index: 10;
  }
  .steppy-video-card.on { display: flex; }
  .steppy-video-card h1 {
    font-family: var(--steppy-font-serif);
    font-size: 44px;
    line-height: 1.15;
    margin: 0;
    color: var(--steppy-fg);
  }
  .steppy-video-card p {
    font-family: var(--steppy-font-sans);
    font-size: 22px;
    margin: 0;
    color: var(--steppy-muted);
  }
  .steppy-video-card img { max-width: 200px; max-height: 80px; }
  .steppy-video-card .rule {
    width: 72px;
    height: 4px;
    border-radius: 999px;
    background: var(--steppy-accent);
  }

  .steppy-video-watermark {
    position: absolute;
    right: 20px;
    top: 16px;
    max-height: 32px;
    opacity: 0.85;
    z-index: 8;
  }
  .steppy-video-stamp {
    position: absolute;
    right: 16px;
    bottom: 10px;
    font-family: var(--steppy-font-mono);
    font-size: 12px;
    color: var(--steppy-muted);
    z-index: 8;
  }
`;

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  cls?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  return node;
}

/**
 * Wait for one screenshot to be usable, but never indefinitely.
 *
 * A 404 over ``file://`` fires ``error`` and settles fine — the dangerous case
 * is a request that neither loads nor errors, which would wedge the whole batch
 * on one guide with no diagnostic. Same call the PDF exporter makes: bound the
 * wait, then render anyway and let the caller report the gap.
 */
function decoded(img: HTMLImageElement, timeoutMs: number): Promise<boolean> {
  if (img.complete && img.naturalWidth) return Promise.resolve(true);
  return new Promise<boolean>((resolve) => {
    let settled = false;
    const done = (ok: boolean) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(ok);
    };
    const timer = setTimeout(() => done(false), Math.max(0, timeoutMs));
    img.addEventListener('load', () => done(true), { once: true });
    img.addEventListener('error', () => done(false), { once: true });
  });
}

/**
 * Mount the stage into ``host`` and prepare it for frame-by-frame capture.
 *
 * The timeline is built *after* the images are decoded, because travel duration
 * depends on display-space distance and therefore on the real capture scale —
 * which is ``naturalWidth / captureSize.width``, a factor the manifest does not
 * carry (see ``timeline.ts``).
 */
export function mountVideoStage(
  host: HTMLElement,
  manifest: GuideManifest,
  options: VideoStageOptions,
): VideoStage {
  const captions = options.captions ?? 'band';
  const layoutKey = resolveLayoutKey(manifest, options.layout);
  const timings: CursorTimings = { ...DEFAULT_TIMINGS, ...manifest.cursor };

  const root = host.shadowRoot ?? host.attachShadow({ mode: 'open' });
  root.innerHTML = '';

  const style = el('style');
  style.textContent = baseStyles.cssText + VIDEO_CSS;
  root.appendChild(style);

  if (options.accent) host.style.setProperty('--steppy-accent', options.accent);
  if (options.fontFamily) host.style.setProperty('--steppy-font-family', options.fontFamily);

  const frame = el('div', 'steppy-video-frame');
  const stage = el('div', 'steppy-stage');
  frame.appendChild(stage);
  root.appendChild(frame);

  // One <img> per distinct capture, stacked and toggled by opacity. Swapping a
  // single src would risk screenshotting a frame mid-decode.
  const shots = new Map<string, HTMLImageElement>();
  const order: string[] = [];
  for (const step of manifest.steps) {
    for (const f of framesFor(step, options.locale, layoutKey)) {
      if (shots.has(f.screenshot)) continue;
      const img = el('img');
      img.src = f.screenshot;
      img.alt = '';
      shots.set(f.screenshot, img);
      order.push(f.screenshot);
      stage.appendChild(img);
    }
  }

  const cursor = el('div', 'steppy-cursor');
  const ripple = el('div', 'steppy-ripple');
  const kbd = el('div', 'steppy-kbd');
  stage.append(cursor, ripple, kbd);

  const band = el('div', 'steppy-video-band');
  const progress = el('div', 'steppy-video-progress');
  const progressBar = el('i');
  progress.appendChild(progressBar);
  const caption = el('div', 'steppy-video-caption');
  band.append(progress, caption);
  if (captions !== 'off') root.appendChild(band);

  const card = el('div', 'steppy-video-card');
  const cardTitle = el('h1');
  const cardRule = el('div', 'rule');
  const cardText = el('p');
  const cardLogo = el('img');
  if (options.logo) {
    cardLogo.src = options.logo;
    card.appendChild(cardLogo);
  }
  card.append(cardTitle, cardRule, cardText);
  root.appendChild(card);

  let watermark: HTMLImageElement | null = null;
  if (options.logo) {
    watermark = el('img', 'steppy-video-watermark');
    watermark.src = options.logo;
    root.appendChild(watermark);
  }
  if (options.stamp) {
    const stamp = el('div', 'steppy-video-stamp');
    stamp.textContent = options.stamp;
    root.appendChild(stamp);
  }

  const width = evenDown(options.width);
  let height = width;
  let timeline: Timeline | null = null;
  let previous: FrameState | null = null;
  let lastShot = '';
  let missing: string[] = [];

  /** Widest caption across every step, so the band height never shifts mid-video. */
  function measureBand(): number {
    if (captions === 'off') return 0;
    let tallest = 0;
    // Must read the same (locale, layout) cell the timeline captions from, or
    // an overridden step sizes the band from text the video never shows.
    const texts = manifest.steps.map((s) => resolveStepContent(s, options.locale, layoutKey).text);
    for (const text of texts.length ? texts : ['']) {
      caption.textContent = text;
      tallest = Math.max(tallest, band.scrollHeight || band.getBoundingClientRect().height || 0);
    }
    caption.textContent = '';
    // happy-dom reports no layout; fall back to a sane fixed band so unit tests
    // exercise the same code path the browser does.
    return Math.ceil(tallest) || 96;
  }

  async function ready(): Promise<void> {
    const budget = options.imageTimeoutMs ?? 15000;
    const results = await Promise.all(order.map((src) => decoded(shots.get(src)!, budget)));
    missing = order.filter((_, i) => !results[i]);

    // Text is burned into every frame, so a font swapping in mid-render would
    // change the pixels partway through the video.
    const fonts = (document as Document & { fonts?: FontFaceSet }).fonts;
    if (fonts?.ready) await fonts.ready;

    const first = order.length ? shots.get(order[0]!)! : null;
    const captureSize = firstCaptureSize(manifest, options.locale, layoutKey);
    // Truth on disk beats the manifest: naturalWidth already includes the
    // capture's device_scale_factor, which the manifest does not record.
    const naturalW = first?.naturalWidth || captureSize.width;
    const naturalH = first?.naturalHeight || captureSize.height;

    let stageW = width;
    let stageH = Math.round((naturalH * stageW) / naturalW);

    const bandH = measureBand();
    const cap = options.maxHeight ?? null;
    if (cap && stageH + bandH > cap) {
      const avail = Math.max(1, cap - bandH);
      stageW = Math.max(2, Math.round((naturalW * avail) / naturalH));
      stageH = avail;
    }

    stage.style.width = `${stageW}px`;
    stage.style.height = `${stageH}px`;
    frame.style.width = `${width}px`;
    frame.style.height = `${stageH}px`;
    if (captions !== 'off') {
      band.style.width = `${width}px`;
      band.style.height = `${bandH}px`;
    }

    height = evenDown(stageH + bandH);
    host.style.width = `${width}px`;
    host.style.height = `${height}px`;

    const sx = stageW / captureSize.width;
    const sy = stageH / captureSize.height;
    timeline = buildTimeline(manifest, { ...options, scale: { sx, sy } });
  }

  function paint(state: FrameState): void {
    if (state.card) {
      card.classList.add('on');
      const isOutro = state.card === 'outro';
      cardTitle.textContent = options.title ?? '';
      cardText.textContent = (isOutro ? options.outroText : options.subtitle) ?? '';
      cardText.style.display = cardText.textContent ? '' : 'none';
    } else {
      card.classList.remove('on');
    }

    if (state.screenshot !== lastShot) {
      if (lastShot) shots.get(lastShot)?.classList.remove('on');
      shots.get(state.screenshot)?.classList.add('on');
      lastShot = state.screenshot;
    }

    if (state.cursor) {
      cursor.style.opacity = String(state.cursor.opacity);
      cursor.style.left = `${state.cursor.x}px`;
      cursor.style.top = `${state.cursor.y}px`;
      cursor.style.transform = `scale(${state.cursor.scale})`;
    } else {
      cursor.style.opacity = '0';
    }

    if (state.ripple) {
      ripple.style.opacity = String(state.ripple.opacity);
      ripple.style.left = `${state.ripple.x}px`;
      ripple.style.top = `${state.ripple.y}px`;
      ripple.style.transform = `scale(${state.ripple.scale})`;
    } else {
      ripple.style.opacity = '0';
    }

    if (state.kbd) {
      kbd.style.opacity = '1';
      kbd.style.left = `${state.kbd.x}px`;
      kbd.style.top = `${state.kbd.y}px`;
      kbd.textContent = state.kbd.text;
    } else {
      kbd.style.opacity = '0';
      kbd.textContent = '';
    }

    caption.textContent = state.caption;
    progressBar.style.width = `${Math.round(state.progress * 1000) / 10}%`;
  }

  return {
    get timeline() {
      return requireTimeline(timeline);
    },
    get durationMs() {
      return requireTimeline(timeline).durationMs;
    },
    get frameCount() {
      return requireTimeline(timeline).frameCount;
    },
    ready,
    size: () => ({ width, height }),
    cues: () => requireTimeline(timeline).cues,
    missingShots: () => [...missing],
    seek(index: number): boolean {
      const tl = requireTimeline(timeline);
      const state = frameAt(tl, index, timings);
      if (previous && frameStateEquals(previous, state)) return false;
      paint(state);
      previous = state;
      return true;
    },
    destroy() {
      root.innerHTML = '';
      previous = null;
    },
  };
}

function requireTimeline(timeline: Timeline | null): Timeline {
  if (!timeline) throw new Error('steppy-video: await ready() before using the stage');
  return timeline;
}

function firstCaptureSize(
  manifest: GuideManifest,
  locale: string,
  layoutKey: string,
): { width: number; height: number } {
  let size: { width: number; height: number } | null = null;
  for (const step of manifest.steps) {
    for (const frame of framesFor(step, locale, layoutKey)) {
      if (!size) {
        size = frame.captureSize;
        continue;
      }
      // Uniformity holds because one run means one viewport — but nothing
      // validates it when the manifest is built, and a screenshots directory of
      // mixed vintage would silently letterbox or crop half the video.
      if (frame.captureSize.width !== size.width || frame.captureSize.height !== size.height) {
        throw new Error(
          `steppy-video: step ${step.index} was captured at ` +
            `${frame.captureSize.width}x${frame.captureSize.height} but the guide starts at ` +
            `${size.width}x${size.height}. A video needs one fixed stage — recapture this guide ` +
            `(steppy run --guide) so every screenshot shares a viewport.`,
        );
      }
    }
  }
  if (!size) {
    throw new Error(
      `steppy-video: no captures for locale '${locale}' / layout '${layoutKey}'.`,
    );
  }
  return size;
}
