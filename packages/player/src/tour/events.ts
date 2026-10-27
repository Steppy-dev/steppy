/**
 * The six DOM events the overlay dispatches on its host element — bubbling
 * and composed, JSON-only details. Colon-free names on purpose: Angular
 * templates read ``(a:b)`` as a global event target.
 */

import type { AdvanceKind } from './contract.js';
import type { DismissVia, StepReason, TourErrorDetail, TourSourceKind } from './engine.js';

export interface TourEventMeta {
  tourId: string;
  buildId: string;
  locale: string;
}

export interface TourStepPosition extends TourEventMeta {
  stepId: string;
  /** The step's target test-id, ``null`` for a centred step. ``stepId`` is not one: read this. */
  testId: string | null;
  /** 0-based, counting only the steps that apply to the current layout. */
  index: number;
  total: number;
}

export interface TourEventDetailMap {
  'tour-start': TourEventMeta & { total: number; source: TourSourceKind; resumed: boolean; entryNavigated: boolean };
  'tour-step': TourStepPosition & { advance: AdvanceKind; reason: StepReason };
  'tour-complete': TourEventMeta & { total: number };
  'tour-dismiss': TourStepPosition & { via: DismissVia; reason?: string };
  'tour-offpath': TourStepPosition & { reason: 'target-lost' };
  'tour-error': TourErrorDetail;
}

export function emitTourEvent<K extends keyof TourEventDetailMap>(host: EventTarget, name: K, detail: TourEventDetailMap[K]): void {
  host.dispatchEvent(new CustomEvent(name, { detail, bubbles: true, composed: true }));
}
