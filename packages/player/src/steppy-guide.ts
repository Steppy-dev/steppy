/**
 * ``<steppy-guide>`` — Lit-based interactive guide player.
 *
 * Public API is HTML attributes + CustomEvents. See the Stage 1 design doc for
 * the full contract. Implementation notes:
 *
 * - Manifest is fetched lazily when ``src`` is set; cached in ``manifest``
 *   state. ``parseManifest`` does both JSON parsing and Zod validation.
 * - Coordinates flow capture-space → display-space at one point only:
 *   ``computeHotspotRect`` (which the cursor animator also uses via
 *   ``getDisplayScale``). Everything downstream — hotspot button, tooltip,
 *   cursor target — derives from the same scale, so resizes can never
 *   produce drift between cursor and hotspot.
 * - The cursor / ripple / kbd elements live at a fixed position in the
 *   template so Lit's incremental render doesn't churn them; the animator
 *   refreshes its DOM refs once on connect.
 * - Step progression is centralized in ``_advance`` and ``_playCurrentPosition``;
 *   the rest of the component is rendering.
 */

import { LitElement, html, nothing, type PropertyValues } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { createRef, ref, type Ref } from 'lit/directives/ref.js';

import { CursorAnimator, type AnimatorOptions } from './cursor/animator.js';
import type { AnimatorFrame, DisplayRect } from './cursor/types.js';

/** The animator was originally designed against legacy ``frames[i].viewport``;
 *  manifest frames use ``captureSize``. They're the same shape; this helper
 *  is the single point of translation. */
function toAnimatorFrame(frame: ManifestFrame): AnimatorFrame {
  return { cursor: frame.cursor, viewport: frame.captureSize };
}
import { DEFAULT_UI_STRINGS, format, getUiStrings, type UiStrings } from './i18n/ui-strings.js';
import {
  computeHotspotRect,
  placeTooltip,
  type TooltipPlacement,
} from './interaction/positioning.js';
import { renderInlineMarkdown } from './ui/markdown.js';
import { captureLayoutKey, resolveStepContent } from './manifest/content.js';
import { parseManifest, type ParseError } from './manifest/parse.js';
import type {
  Capture,
  CaptureContent,
  CursorMode,
  Frame as ManifestFrame,
  GuideManifest,
  ManifestStep,
  TemplateMode,
} from './manifest/schema.js';
import {
  ALL_TEMPLATES,
  isTemplateId,
  TEMPLATES,
  templateStyles,
} from './templates/index.js';
import type { StepView, TemplateContext, TemplateId } from './templates/types.js';

const CURSOR_MODE_VALUES: readonly CursorMode[] = ['arrow', 'hotspot', 'off'];
function isCursorMode(value: unknown): value is CursorMode {
  return typeof value === 'string' && (CURSOR_MODE_VALUES as readonly string[]).includes(value);
}

type StepRuntime = ManifestStep;

/** Animator-shaped subset the player actually calls. Tests can plug in a
 *  stub that skips real timing without owning the full CursorAnimator API. */
export interface AnimatorLike {
  setTimings(t: Partial<import('./cursor/types.js').CursorTimings>): void;
  setStage(stage: HTMLElement): void;
  refreshDom(): void;
  hide(): void;
  abort(): void;
  travelToTarget(frame: AnimatorFrame, targetIndex: number): Promise<void>;
  playTargetAction(frame: AnimatorFrame, targetIndex: number): Promise<void>;
}

@customElement('steppy-guide')
export class SteppyGuide extends LitElement {
  /** Factory hook — overridden in tests to swap in a fast/no-op animator. */
  static createAnimator: (opts: AnimatorOptions) => AnimatorLike = (opts) =>
    new CursorAnimator(opts);

  // Aggregated: shared base (tokens + stage + cursor/hotspot/tooltip + chrome
  // primitives) plus each template's layout-specific CSS. Unrendered templates
  // simply contribute no matching DOM.
  static override styles = templateStyles;

  // ── Public API ────────────────────────────────────────────────────────

  @property({ type: String, reflect: true }) src = '';
  @property({ type: String, reflect: true }) locale: string | null = null;
  @property({ type: String, reflect: true }) variant: string | null = null;
  @property({ type: Boolean, reflect: true }) autoplay = false;
  @property({ type: Number, reflect: true, attribute: 'start-index' })
  startIndex = 1;
  /**
   * Cursor presentation:
   * - 'arrow'   (default) animate the arrow + ripple/kbd; hotspot also pulses
   * - 'hotspot' skip arrow/ripple/kbd; hotspot pulses and user still clicks
   * - 'off'     skip arrow + hotspot; auto-advance every step after waitAfter
   * Unset (null) ⇒ fall back to manifest's `cursor.mode`, then 'arrow'.
   */
  @property({ type: String, reflect: true })
  cursor: CursorMode | null = null;

  /**
   * Presentation template (runtime-switchable viewer setting):
   * - 'overlay' (default) floating callout pinned to the target
   * - 'sidebar' left rail with a step timeline + description
   * - 'inline'  slim bottom caption bar
   * Resolution: this attribute → persisted localStorage → manifest
   * ``defaultTemplate`` → 'overlay'. Unknown strings fall through.
   */
  @property({ type: String, reflect: true })
  template: TemplateMode | null = null;

  /** When set, suppress the built-in layout (variant) switcher so a host can
   *  own the control (the generated Angular toolbar / static page nav drives
   *  `variant` directly). The locale switcher is unaffected. */
  @property({ type: Boolean, reflect: true, attribute: 'hide-layout-switcher' })
  hideLayoutSwitcher = false;

  /** Sparse per-locale UI string overrides. Property-only (no attribute). */
  @property({ attribute: false })
  uiStrings: Partial<Record<string, Partial<UiStrings>>> | undefined;

  // ── Internal state ────────────────────────────────────────────────────

  @state() private manifest: GuideManifest | null = null;
  @state() private loadError: ParseError | null = null;
  @state() private currentStepIndex = 0;
  @state() private currentFrameIndex = 0;
  @state() private currentTargetIndex = 0;
  @state() private waitingForInput = false;
  @state() private completed = false;
  @state() private missCount = 0;
  @state() private hotspotRect: DisplayRect | null = null;
  @state() private tooltipPlacement: TooltipPlacement | null = null;
  @state() private stageSize: { width: number; height: number } = { width: 0, height: 0 };

  /** Resolved presentation template the current render uses. */
  @state() private _activeTemplate: TemplateId = 'overlay';
  /** The stage element the ResizeObserver/animator are currently bound to.
   *  Tracked so a template switch (which relocates the stage) re-attaches. */
  private _observedStage: HTMLElement | null = null;

  /** Furthest step the user has reached. Used to gate ``goTo`` / ``prev``. */
  private maxReachedStepIndex = 0;
  private animator: AnimatorLike | null = null;
  private stageRef: Ref<HTMLElement> = createRef();
  private imgRef: Ref<HTMLImageElement> = createRef();
  private cursorRef: Ref<HTMLElement> = createRef();
  private rippleRef: Ref<HTMLElement> = createRef();
  private kbdRef: Ref<HTMLElement> = createRef();
  private tooltipRef: Ref<HTMLElement> = createRef();
  private resizeObserver: ResizeObserver | null = null;
  private autoAdvanceTimer: ReturnType<typeof setTimeout> | null = null;
  /** Token bumped on every ``_playCurrentPosition`` call so stale awaits drop. */
  private playToken = 0;
  /** Resolved on the user's correct click; the running play awaits this. */
  private waitForClick: Promise<void> | null = null;
  private resolveClick: (() => void) | null = null;

  // ── Lifecycle ─────────────────────────────────────────────────────────

  override connectedCallback(): void {
    super.connectedCallback();
    this.addEventListener('keydown', this._handleKeyDown);
  }

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.removeEventListener('keydown', this._handleKeyDown);
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    if (this.autoAdvanceTimer !== null) {
      clearTimeout(this.autoAdvanceTimer);
      this.autoAdvanceTimer = null;
    }
    this.animator?.abort();
  }

  override willUpdate(changed: PropertyValues): void {
    if (changed.has('src')) {
      void this._loadManifest();
    }
    if (changed.has('locale') && this.manifest && this.locale) {
      this._emit('steppy:locale', {
        from: changed.get('locale') as string | null,
        to: this.locale,
      });
    }
    if (changed.has('variant') && this.manifest && this.variant) {
      this._emit('steppy:variant', {
        from: changed.get('variant') as string | null,
        to: this.variant,
      });
    }

    // Layout (variant) switched after the manifest loaded — e.g. a host toggle
    // or the built-in dropdown. Restart the current step in the new layout
    // (frame counts can differ between layouts), drop any pending hotspot wait,
    // and replay so the new screenshot + cursor render. Skipped on the initial
    // variant set during manifest load (handled by _restartFromStart).
    if (changed.has('variant') && this.manifest && !changed.has('manifest')) {
      this.currentFrameIndex = 0;
      this.currentTargetIndex = 0;
      this.waitingForInput = false;
      this.hotspotRect = null;
      this.tooltipPlacement = null;
      const resolve = this.resolveClick;
      this.resolveClick = null;
      resolve?.();
      void this._playCurrentPosition();
    }

    // Cursor mode flipped at runtime (e.g., from dev tools). Release any
    // pending hotspot wait, clear interactive state, and re-enter the current
    // frame so the new mode takes effect without stranding waitingForInput.
    // Skipped on the initial property set (handled by manifest load instead).
    if (changed.has('cursor') && this.manifest) {
      this.waitingForInput = false;
      this.hotspotRect = null;
      this.tooltipPlacement = null;
      const resolve = this.resolveClick;
      this.resolveClick = null;
      resolve?.();
      void this._playCurrentPosition();
    }

    // Presentation template flipped (host attribute or the built-in switcher).
    // Deliberately NO replay and NO index/waiting reset — switching template is
    // a pure re-render of the same engine state. Just re-resolve the active
    // template and persist it; the stage subtree is preserved across the switch
    // (keyed in render + reattached in updated) so the in-flight animation
    // continues. A hotspot recompute is queued below.
    if (changed.has('template')) {
      this._activeTemplate = this._resolveTemplate();
      // Persist only a deliberate, valid choice (the built-in switcher or an
      // explicit ``template`` attribute). The initial ``null`` must NOT persist
      // — doing so would pin 'overlay' and starve the manifest's
      // ``defaultTemplate`` of its turn in the resolution order.
      if (isTemplateId(this.template)) {
        this._persistTemplate(this._activeTemplate);
      }
    }

    // Recompute hotspot/tooltip placement here (inside willUpdate) so the
    // resulting state assignments fold into the same render cycle instead
    // of scheduling a second one.
    if (
      changed.has('waitingForInput') ||
      changed.has('currentStepIndex') ||
      changed.has('currentFrameIndex') ||
      changed.has('currentTargetIndex') ||
      changed.has('locale') ||
      changed.has('variant') ||
      changed.has('stageSize') ||
      changed.has('template')
    ) {
      this._recomputeHotspot();
    }
  }

  override updated(changed: PropertyValues): void {
    const stageEl = this.stageRef.value ?? null;
    if (!this.animator && stageEl) {
      this.animator = (this.constructor as typeof SteppyGuide).createAnimator({
        stage: stageEl,
        timings: this.manifest?.cursor,
      });
      this._observedStage = stageEl;
    } else if (stageEl && stageEl !== this._observedStage) {
      // The stage subtree was re-mounted at a new DOM position — almost always
      // a template switch. Re-point the animator + ResizeObserver at the live
      // element and recompute placement so the cursor/hotspot survive the move
      // without restarting the step.
      this._reattachStage(stageEl);
    } else if (this.animator && stageEl) {
      this.animator.refreshDom();
      if (this.manifest) this.animator.setTimings(this.manifest.cursor);
    }

    if (changed.has('manifest') && this.manifest) {
      this._setupResizeObserver();
      this._observedStage = this.stageRef.value ?? null;
      void this._restartFromStart();
    }

    // stageSize stands in for "the layout moved": it is the one observed
    // dimension that changes when the viewport resizes or the layout variant
    // swaps, both of which can shorten the rail and strand the active row.
    if (
      changed.has('currentStepIndex') ||
      changed.has('_activeTemplate') ||
      changed.has('stageSize')
    ) {
      this._scrollActiveRowIntoView();
    }
  }

  /** Keep the current timeline row visible. The sidebar rail is height-capped
   *  and scrolls its timeline internally, so on a long guide ``next()`` would
   *  otherwise advance to a row below the fold. ``block: 'nearest'`` scrolls the
   *  minimum needed, which also means it does nothing when the row is already
   *  visible — including in the templates that have no timeline at all. */
  private _scrollActiveRowIntoView(): void {
    if (this._activeTemplate !== 'sidebar') return;
    const row = this.renderRoot.querySelector<HTMLElement>('.steppy-step--current');
    // happy-dom (and older browsers) may not implement scrollIntoView.
    if (typeof row?.scrollIntoView !== 'function') return;
    row.scrollIntoView({ block: 'nearest' });
  }

  /** Re-bind the ResizeObserver + animator to a relocated stage element (e.g.
   *  after a template switch) and reposition the hotspot/tooltip. Does not
   *  touch step state, so the in-flight play loop continues uninterrupted. */
  private _reattachStage(stageEl: HTMLElement): void {
    this._observedStage = stageEl;
    this._setupResizeObserver();
    this.animator?.setStage(stageEl);
    if (this.manifest) this.animator?.setTimings(this.manifest.cursor);
    this.stageSize = {
      width: stageEl.clientWidth || this.stageSize.width,
      height: stageEl.clientHeight || this.stageSize.height,
    };
    this._recomputeHotspot();
  }

  // ── Public methods ───────────────────────────────────────────────────

  next(): void {
    if (!this.manifest) return;
    if (this.waitingForInput) {
      // Forward nav stands in for clicking the highlighted target: resolve the
      // pending interactive wait via the same success path as a hotspot click
      // (runs the action, then advances). Applies in every template, including
      // overlay — the in-stage hotspot still works too.
      this._confirmHotspotClick();
      return;
    }
    this._advance();
  }

  prev(): void {
    if (!this.manifest) return;
    if (this.currentStepIndex <= 0) return;
    this._goToStep(this.currentStepIndex - 1, { reason: 'prev' });
  }

  restart(): void {
    if (!this.manifest) return;
    this.completed = false;
    this.maxReachedStepIndex = 0;
    this._goToStep(0, { reason: 'restart' });
  }

  goTo(index: number): void {
    if (!this.manifest) return;
    const target = index - 1;
    if (target < 0 || target > this.maxReachedStepIndex) {
      this._emit('steppy:error', {
        code: 'bad_attribute',
        message: `goTo(${index}) is out of range or beyond the furthest reached step`,
      });
      return;
    }
    this._goToStep(target, { reason: 'goTo' });
  }

  // ── Internals ────────────────────────────────────────────────────────

  private async _loadManifest(): Promise<void> {
    if (!this.src) {
      this.manifest = null;
      this.loadError = null;
      return;
    }
    this.manifest = null;
    this.loadError = null;
    try {
      const response = await fetch(this.src);
      if (!response.ok) {
        this.loadError = {
          code: 'fetch_failed',
          message: `${response.status} ${response.statusText} fetching ${this.src}`,
        };
        this._emit('steppy:error', this.loadError);
        return;
      }
      const text = await response.text();
      const result = parseManifest(text);
      if (!result.ok) {
        this.loadError = result.error;
        this._emit('steppy:error', result.error);
        return;
      }
      this.manifest = result.manifest;
      // Default-fill locale/variant from the manifest if attrs weren't set.
      if (!this.locale || !this.manifest.locales.includes(this.locale)) {
        this.locale = this.manifest.defaultLocale;
      }
      const targetVariant =
        this.variant ?? this.manifest.defaultLayout ?? this._fallbackLayoutKey();
      this.variant = this._resolveVariant(targetVariant);
      // Resolve the presentation template now (needs slug + defaultTemplate) so
      // the first content paint already uses the right template.
      this._activeTemplate = this._resolveTemplate();
    } catch (err) {
      this.loadError = {
        code: 'fetch_failed',
        message: err instanceof Error ? err.message : 'fetch failed',
      };
      this._emit('steppy:error', this.loadError);
    }
  }

  private _fallbackLayoutKey(): string {
    return this.manifest && this.manifest.layouts.length === 0 ? '_' : 'desktop';
  }

  private _resolveVariant(candidate: string | null): string {
    if (!this.manifest) return '_';
    const layoutKeys =
      this.manifest.layouts.length > 0 ? this.manifest.layouts : ['_'];
    if (candidate && layoutKeys.includes(candidate)) return candidate;
    return this.manifest.defaultLayout ?? layoutKeys[0]!;
  }

  private async _restartFromStart(): Promise<void> {
    if (!this.manifest) return;
    const initial = Math.max(
      0,
      Math.min(this.manifest.steps.length - 1, (this.startIndex || 1) - 1),
    );
    this.maxReachedStepIndex = initial;
    this._emit('steppy:ready', {
      slug: this.manifest.slug,
      total: this.manifest.steps.length,
      locale: this.locale,
      variant: this.variant,
    });
    this._goToStep(initial, { reason: 'init' });
  }

  private _setupResizeObserver(): void {
    this.resizeObserver?.disconnect();
    if (!this.stageRef.value || typeof ResizeObserver === 'undefined') return;
    this.resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      this.stageSize = {
        width: entry.contentRect.width,
        height: entry.contentRect.height,
      };
      this._recomputeHotspot();
    });
    this.resizeObserver.observe(this.stageRef.value);
  }

  private _currentStep(): StepRuntime | null {
    return this.manifest?.steps[this.currentStepIndex] ?? null;
  }

  /**
   * The `Capture` the active render reads from — screenshots AND caption.
   *
   * Both go through this one accessor on purpose: a step's text can be
   * overridden per layout (the twin-branch pattern), so reading the frame from
   * one layout and the wording from another would put "click the row of the
   * table" above a screenshot of a card list.
   */
  private _captureFor(step: StepRuntime, locale: string): Capture | null {
    if (!this.manifest) return null;
    const key = captureLayoutKey(step, locale, this.variant, this.manifest);
    return key === null ? null : (step.captures[locale]?.[key] ?? null);
  }

  private _contentFor(step: StepRuntime, locale: string): CaptureContent {
    const key = this.manifest ? captureLayoutKey(step, locale, this.variant, this.manifest) : null;
    return resolveStepContent(step, locale, key ?? '');
  }

  private _currentFrame(): ManifestFrame | null {
    const step = this._currentStep();
    if (!step || !this.locale) return null;
    return this._captureFor(step, this.locale)?.frames[this.currentFrameIndex] ?? null;
  }

  private _currentTargetBbox(): import('./cursor/types.js').Bbox | null {
    const frame = this._currentFrame();
    if (!frame || !frame.cursor) return null;
    const targets = CursorAnimator.normalizeTargets(frame.cursor);
    return targets[this.currentTargetIndex]?.bbox ?? null;
  }

  private _recomputeHotspot(): void {
    const img = this.imgRef.value;
    const frame = this._currentFrame();
    if (!img || !frame || !this.waitingForInput) {
      this.hotspotRect = null;
      this.tooltipPlacement = null;
      return;
    }
    const bbox = this._currentTargetBbox();
    this.hotspotRect = computeHotspotRect(img, frame.captureSize, bbox);
    this._placeTooltip();
  }

  private _placeTooltip(): void {
    if (!this.hotspotRect || !this.stageRef.value) {
      this.tooltipPlacement = null;
      return;
    }
    const stageRect = this.stageRef.value.getBoundingClientRect();
    const tip = this.tooltipRef.value;
    const tipSize = tip
      ? { width: tip.offsetWidth || 240, height: tip.offsetHeight || 60 }
      : { width: 240, height: 60 };
    this.tooltipPlacement = placeTooltip(
      this.hotspotRect,
      tipSize,
      { width: stageRect.width, height: stageRect.height },
    );
  }

  private _goToStep(stepIndex: number, opts: { reason: string }): void {
    if (!this.manifest) return;
    if (stepIndex < 0 || stepIndex >= this.manifest.steps.length) return;
    this.currentStepIndex = stepIndex;
    this.currentFrameIndex = 0;
    this.currentTargetIndex = 0;
    this.completed = false;
    this.missCount = 0;
    this.waitingForInput = false;
    if (stepIndex > this.maxReachedStepIndex) {
      this.maxReachedStepIndex = stepIndex;
    }
    const step = this._currentStep()!;
    this._emit('steppy:step', {
      index: step.index,
      id: step.id,
      total: this.manifest.steps.length,
      action: step.action,
      interactive: step.interactive,
      reason: opts.reason,
    });
    void this._playCurrentPosition();
  }

  /** Resolve the effective cursor mode. Host attribute wins; manifest is the
   *  fallback; 'arrow' is the final default. Unknown attribute strings fall
   *  through to the manifest so a typo can't strand the player. */
  private _effectiveCursorMode(): CursorMode {
    if (isCursorMode(this.cursor)) return this.cursor;
    const manifestMode = this.manifest?.cursor.mode;
    if (isCursorMode(manifestMode)) return manifestMode;
    return 'arrow';
  }

  private async _playCurrentPosition(): Promise<void> {
    const myToken = ++this.playToken;
    if (this.autoAdvanceTimer !== null) {
      clearTimeout(this.autoAdvanceTimer);
      this.autoAdvanceTimer = null;
    }
    const frame = this._currentFrame();
    if (!frame) return;
    const step = this._currentStep()!;
    const mode = this._effectiveCursorMode();

    // Wait for Lit to flush the new screenshot into the DOM.
    await this.updateComplete;
    if (this.playToken !== myToken) return;
    this.animator?.refreshDom();

    if (frame.cursor === null || mode === 'off') {
      // Non-interactive frame, or 'off' mode forcing auto-advance everywhere.
      this.animator?.hide();
      const delay = Math.max(0, step.waitAfter ?? 0) || 600;
      await new Promise<void>((resolve) => {
        this.autoAdvanceTimer = setTimeout(() => {
          this.autoAdvanceTimer = null;
          resolve();
        }, delay);
      });
      if (this.playToken !== myToken) return;
      this._advance();
      return;
    }

    // Interactive frame: in 'arrow' mode travel the cursor first; in
    // 'hotspot' mode skip the cursor animation entirely and go straight to
    // the click wait. The hotspot pulses either way.
    if (mode === 'arrow') {
      try {
        await this.animator?.travelToTarget(toAnimatorFrame(frame), this.currentTargetIndex);
      } catch {
        /* abort signal */
      }
      if (this.playToken !== myToken) return;
    } else {
      // Belt-and-suspenders: ensure no stale .visible state from a previous
      // 'arrow' run remains visible after a mid-play mode flip.
      this.animator?.hide();
    }
    this.waitingForInput = true;
    this._recomputeHotspot();
    this.waitForClick = new Promise<void>((resolve) => {
      this.resolveClick = resolve;
    });
    await this.waitForClick;
    if (this.playToken !== myToken) return;
    this.waitingForInput = false;
    this.hotspotRect = null;
    this.tooltipPlacement = null;
    if (mode === 'arrow') {
      try {
        await this.animator?.playTargetAction(toAnimatorFrame(frame), this.currentTargetIndex);
      } catch {
        /* abort signal */
      }
      if (this.playToken !== myToken) return;
    }
    this._advance();
  }

  private _advance(): void {
    if (!this.manifest) return;
    const step = this._currentStep();
    if (!step) return;
    const frame = this._currentFrame();
    if (!frame) return;

    if (frame.cursor) {
      const targets = CursorAnimator.normalizeTargets(frame.cursor);
      if (this.currentTargetIndex + 1 < targets.length) {
        this.currentTargetIndex += 1;
        void this._playCurrentPosition();
        return;
      }
    }
    // Next frame within step
    const captures = step.captures[this.locale!]?.[this.variant!] ??
      Object.values(step.captures[this.locale!] ?? {})[0];
    const frameCount = captures?.frames.length ?? 1;
    if (this.currentFrameIndex + 1 < frameCount) {
      this.currentFrameIndex += 1;
      this.currentTargetIndex = 0;
      void this._playCurrentPosition();
      return;
    }
    // Next step
    if (this.currentStepIndex + 1 < this.manifest.steps.length) {
      this._goToStep(this.currentStepIndex + 1, { reason: 'next' });
      return;
    }
    // End of guide
    this.completed = true;
    this.animator?.hide();
    this._emit('steppy:complete', {
      slug: this.manifest.slug,
      locale: this.locale,
      variant: this.variant,
    });
  }

  /** Idempotent: emits ``steppy:hotspot`` with target=correct and resolves the
   *  current wait. A second call is a no-op (the wait already advanced). */
  private _confirmHotspotClick(): void {
    if (!this.resolveClick) return;
    const step = this._currentStep();
    if (step) {
      this._emit('steppy:hotspot', {
        index: step.index,
        id: step.id,
        target: 'correct',
      });
    }
    const resolve = this.resolveClick;
    this.resolveClick = null;
    resolve();
  }

  private _onStageClick(ev: MouseEvent): void {
    if (!this.waitingForInput) return;
    // The hotspot button (if any) sits inside the stage and has its own
    // click handler that calls _confirmHotspotClick + stopPropagation, so we
    // only reach this code for clicks outside the hotspot. Classify as miss.
    const step = this._currentStep();
    if (!step) return;
    this.missCount += 1;
    this._emit('steppy:hotspot', {
      index: step.index,
      id: step.id,
      target: 'miss',
    });
    // No timer needed; the toast and shake auto-clear via CSS.
    void ev;
  }

  private _onHotspotClick(ev: Event): void {
    ev.stopPropagation();
    this._confirmHotspotClick();
  }

  private _handleKeyDown = (ev: KeyboardEvent): void => {
    if (!this.manifest) return;
    // Enter/Space on a focused hotspot button fires its native click, which
    // _onHotspotClick handles — no separate keyboard branch needed here.
    if (ev.key === 'ArrowRight') {
      // Forward-advance (resolving any pending interactive wait), matching the
      // Next button — consistent across all templates.
      this.next();
    } else if (ev.key === 'ArrowLeft') {
      this.prev();
    }
  };

  private _onLocaleChange = (ev: Event): void => {
    const value = (ev.target as HTMLSelectElement).value;
    if (this.manifest && this.manifest.locales.includes(value)) {
      this.locale = value;
      // Stay on the same step — re-trigger the play loop so the new locale's
      // screenshot loads and the cursor re-animates.
      void this._playCurrentPosition();
    }
  };

  private _onVariantChange = (ev: Event): void => {
    // Setting `variant` triggers the layout-switch replay in `willUpdate`.
    this.variant = this._resolveVariant((ev.target as HTMLSelectElement).value);
  };

  private _onImgLoad = (): void => {
    this._recomputeHotspot();
  };

  private _emit(name: string, detail: unknown): void {
    this.dispatchEvent(
      new CustomEvent(name, { detail, bubbles: true, composed: true }),
    );
  }

  // ── Rendering ────────────────────────────────────────────────────────

  override render() {
    const ui = getUiStrings(this.locale ?? 'en', this.uiStrings);
    if (this.loadError) return this._renderError(this.loadError, ui);
    if (!this.manifest) return this._renderLoading(ui);

    const step = this._currentStep();
    if (!step) return this._renderLoading(ui);

    // Dispatch to the active presentation template. All three render the shared
    // stage (via the ctx.renderStage thunk) plus their own chrome.
    const ctx = this._buildTemplateContext(ui);
    const template = TEMPLATES[this._activeTemplate] ?? TEMPLATES.overlay;
    return template.render(ctx);
  }

  /** The live stage subtree (screenshot + cursor/ripple/kbd + hotspot +
   *  miss-toast), shared verbatim by every template via the ``renderStage``
   *  thunk so the refs + handlers stay bound to the component.
   *
   *  A template *switch* swaps the whole top-level template, so Lit recreates
   *  the stage element. That is handled by ``updated`` → ``_reattachStage``
   *  (re-point the animator + ResizeObserver, recompute placement) WITHOUT
   *  resetting step state, so the in-flight animation survives the move. The
   *  floating tooltip is included only in overlay mode (sidebar and inline
   *  surface the description in their own chrome). */
  private _renderStage() {
    const ui = getUiStrings(this.locale ?? 'en', this.uiStrings);
    const step = this._currentStep();
    const frame = this._currentFrame();
    const screenshot = frame?.screenshot ?? '';
    // Resolved through the same layout key the frame came from, so the caption
    // always describes the screenshot beside it.
    const text = step && this.locale ? this._contentFor(step, this.locale).text : '';
    return html`
      <div class="steppy-stage" ${ref(this.stageRef)} @click=${this._onStageClick}>
        <img
          ${ref(this.imgRef)}
          src=${screenshot}
          alt=${text || step?.id || ''}
          @load=${this._onImgLoad}
        />
        <div class="steppy-cursor" ${ref(this.cursorRef)} aria-hidden="true"></div>
        <div class="steppy-ripple" ${ref(this.rippleRef)} aria-hidden="true"></div>
        <div class="steppy-kbd" ${ref(this.kbdRef)} aria-hidden="true"></div>
        ${step ? this._renderHotspot(ui, step, text) : nothing}
        ${this._activeTemplate === 'overlay' ? this._renderTooltip(text) : nothing}
        ${this.missCount > 0 ? this._renderMissToast(ui) : nothing}
      </div>
    `;
  }

  private _renderHotspot(ui: UiStrings, step: StepRuntime, text: string) {
    if (!this.waitingForInput || !this.hotspotRect) return nothing;
    const r = this.hotspotRect;
    const label = format(ui.hotspotLabel, { step: text || step.id });
    const missKey = this.missCount;
    return html`
      <button
        class="steppy-hotspot ${this.missCount > 0 ? 'miss' : ''}"
        style="left:${r.x}px;top:${r.y}px;width:${r.width}px;height:${r.height}px"
        aria-label=${label}
        data-miss-key=${missKey}
        @click=${this._onHotspotClick}
      ></button>
    `;
  }

  private _renderTooltip(text: string) {
    if (!this.waitingForInput || !this.hotspotRect || !this.tooltipPlacement) {
      return nothing;
    }
    const p = this.tooltipPlacement;
    return html`
      <div
        class="steppy-tooltip"
        ${ref(this.tooltipRef)}
        style="left:${p.x}px;top:${p.y}px"
        data-side=${p.side}
      >
        ${renderInlineMarkdown(text)}
      </div>
    `;
  }

  private _renderMissToast(ui: UiStrings) {
    // Keyed by missCount so each miss re-triggers the CSS animation.
    return html`<div class="steppy-miss-toast" data-key=${this.missCount}>${ui.miss}</div>`;
  }

  // ── Engine → template context ─────────────────────────────────────────

  /** Project the ordered manifest steps into presentation view models. Pure
   *  projection of engine state — no behavior, no duplicated gates. */
  private _buildStepViews(locale: string): StepView[] {
    if (!this.manifest) return [];
    return this.manifest.steps.map((s): StepView => {
      const i0 = s.index - 1;
      const status: StepView['status'] =
        i0 < this.currentStepIndex
          ? 'completed'
          : i0 === this.currentStepIndex
            ? 'current'
            : 'upcoming';
      // Per step, not once: each step owns its captures bag, and a layout
      // override may exist on some steps and not others. The whole list
      // re-labels on a layout switch, not just the current row.
      const content = this._contentFor(s, locale);
      return {
        index: s.index,
        title: content.title ?? content.text ?? s.id,
        description: content.text ?? '',
        subLabel: content.subtitle ?? '',
        status,
        reachable: i0 <= this.maxReachedStepIndex,
      };
    });
  }

  /** Display-only duration estimate (~8s/step). Never feeds the play loop. */
  private _durationLabel(ui: UiStrings): string {
    const seconds = (this.manifest?.steps.length ?? 0) * 8;
    if (seconds < 60) return format(ui.durationSeconds, { seconds });
    return format(ui.durationApprox, {
      minutes: Math.max(1, Math.round(seconds / 60)),
    });
  }

  private _buildTemplateContext(ui: UiStrings): TemplateContext {
    const manifest = this.manifest!;
    const locale = this.locale ?? manifest.defaultLocale;
    const layouts = manifest.layouts.length > 0 ? manifest.layouts : [];
    return {
      steps: this._buildStepViews(locale),
      currentIndex: this.currentStepIndex + 1,
      total: manifest.steps.length,
      durationLabel: this._durationLabel(ui),
      guideTitle: manifest.title[locale] ?? Object.values(manifest.title)[0] ?? '',
      locale,
      ui,
      waitingForInput: this.waitingForInput,
      completed: this.completed,
      onNext: () => this.next(),
      onPrev: () => this.prev(),
      onRestart: () => this.restart(),
      onGoTo: (i: number) => this.goTo(i),
      prevDisabled: this.currentStepIndex <= 0,
      nextDisabled: this.completed,
      locales: manifest.locales,
      layouts,
      templates: ALL_TEMPLATES,
      activeTemplate: this._activeTemplate,
      activeLocale: this.locale ?? '',
      activeLayout: this.variant,
      hideLayoutSwitcher: this.hideLayoutSwitcher,
      onLocaleChange: this._onLocaleChange,
      onLayoutChange: this._onVariantChange,
      onTemplateChange: (t) => this._onTemplateChange(t),
      renderStage: () => this._renderStage(),
    };
  }

  /** The built-in template switcher routes through the reflected property so
   *  ``willUpdate`` re-resolves + persists (and hosts observe the attribute). */
  private _onTemplateChange(t: TemplateId): void {
    this.template = t;
  }

  // ── Template persistence + resolution ──────────────────────────────────

  private static readonly TEMPLATE_STORAGE_KEY = 'steppy:active-template';

  private _readPersistedTemplate(): TemplateId | null {
    try {
      const v = localStorage.getItem(SteppyGuide.TEMPLATE_STORAGE_KEY);
      return isTemplateId(v) ? v : null;
    } catch {
      return null;
    }
  }

  private _persistTemplate(t: TemplateId): void {
    try {
      localStorage.setItem(SteppyGuide.TEMPLATE_STORAGE_KEY, t);
    } catch {
      /* private mode / storage unavailable — non-fatal */
    }
  }

  /** Resolve the effective template: explicit ``template`` attribute →
   *  persisted localStorage → manifest ``defaultTemplate`` → 'overlay'.
   *  Unknown attribute strings fall through so a typo can't strand the player. */
  private _resolveTemplate(): TemplateId {
    if (isTemplateId(this.template)) return this.template;
    const persisted = this._readPersistedTemplate();
    if (persisted) return persisted;
    const fromManifest = this.manifest?.defaultTemplate;
    if (isTemplateId(fromManifest)) return fromManifest;
    return 'overlay';
  }

  private _renderLoading(ui: UiStrings) {
    return html`<div class="steppy-state" role="status">${ui.loading}</div>`;
  }

  private _renderError(error: ParseError, ui: UiStrings) {
    const detail =
      error.issues && error.issues.length > 0
        ? html`<div class="steppy-state-detail">
            ${error.issues.map((i) => html`<div>${i.path}: ${i.message}</div>`)}
          </div>`
        : html`<div class="steppy-state-detail">${error.message}</div>`;
    return html`
      <div class="steppy-state error" role="alert">
        <strong>${ui.errorTitle}</strong>
        ${detail}
        <button
          style="margin-top: 12px"
          @click=${() => void this._loadManifest()}
        >
          ${ui.errorRetry}
        </button>
      </div>
    `;
  }
}

// Reserved for future host-side strict typing.
declare global {
  interface HTMLElementTagNameMap {
    'steppy-guide': SteppyGuide;
  }
}

export { DEFAULT_UI_STRINGS };
export type { UiStrings };
