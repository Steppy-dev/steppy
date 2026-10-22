/**
 * Engine → template contract.
 *
 * The engine (the ``<steppy-guide>`` component) owns ALL state and behavior:
 * manifest, step indices, cursor animation, hotspot/tooltip placement,
 * navigation, i18n, persistence. A *template* is a pure render function over an
 * immutable {@link TemplateContext} — it decides only where the chrome lives and
 * how it looks. Templates never hold step state and never re-implement engine
 * gates; everything they need (including derived values like step ``status`` and
 * disabled flags) is precomputed on the context.
 *
 * Anything that needs a live DOM ref — the screenshot stage with its
 * cursor/ripple/kbd/hotspot, and the overlay's floating callout — is supplied as
 * the {@link TemplateContext.renderStage} thunk, which the component owns so the
 * ``ref()`` directives and event handlers stay bound to it across template
 * switches.
 */

import type { CSSResult, TemplateResult } from 'lit';

import type { UiStrings } from '../i18n/ui-strings.js';
import type { TemplateMode } from '../manifest/schema.js';

export type TemplateId = TemplateMode; // 'sidebar' | 'overlay' | 'inline'

/** A single step projected for presentation. Pure projection of engine state —
 *  no behavior, no duplicated gates. */
export interface StepView {
  /** 1-based step index (matches the manifest ``step.index``). */
  index: number;
  /** Short heading (sidebar timeline). Falls back to the description. */
  title: string;
  /** Full localized instruction body (the manifest ``text``). */
  description: string;
  /** Mono meta sub-label (the manifest ``subtitle``). May be empty. */
  subLabel: string;
  /** Position relative to the current step. */
  status: 'completed' | 'current' | 'upcoming';
  /** True when the step is reachable via ``goTo`` (index ≤ furthest reached). */
  reachable: boolean;
}

export interface TemplateContext {
  // ── Data ───────────────────────────────────────────────────────────────
  steps: readonly StepView[];
  /** 1-based index of the current step. */
  currentIndex: number;
  total: number;
  /** Localized, display-only duration estimate (e.g. "~3 min"). */
  durationLabel: string;
  /** Localized guide title for the current locale. */
  guideTitle: string;
  locale: string;
  ui: UiStrings;

  // ── Interactive state ──────────────────────────────────────────────────
  waitingForInput: boolean;
  completed: boolean;

  // ── Navigation (gates precomputed on the engine) ───────────────────────
  onNext: () => void;
  onPrev: () => void;
  onRestart: () => void;
  /** Jump to a 1-based step index (engine gates to reachable steps). */
  onGoTo: (index1: number) => void;
  prevDisabled: boolean;
  nextDisabled: boolean;

  // ── Switchers ──────────────────────────────────────────────────────────
  locales: readonly string[];
  layouts: readonly string[];
  templates: readonly TemplateId[];
  activeTemplate: TemplateId;
  activeLocale: string;
  activeLayout: string | null;
  hideLayoutSwitcher: boolean;
  onLocaleChange: (ev: Event) => void;
  onLayoutChange: (ev: Event) => void;
  onTemplateChange: (t: TemplateId) => void;

  // ── Shared live thunk ──────────────────────────────────────────────────
  /** The ``.steppy-stage`` subtree (img + cursor + ripple + kbd + hotspot +
   *  miss-toast, and the floating tooltip in overlay mode). Carries the live
   *  refs + handlers; identical element shape in every template. */
  renderStage: () => TemplateResult;
}

export interface TemplateModule {
  render(ctx: TemplateContext): TemplateResult;
  styles: CSSResult;
}
