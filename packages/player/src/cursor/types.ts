/**
 * Cursor data shapes — shared by the Lit player, the legacy IIFE bundle, and
 * the manifest validator. Kept dependency-free (no Zod) so the legacy IIFE
 * stays a tiny standalone bundle.
 *
 * The manifest schema in ``../manifest/schema.ts`` produces types that are
 * structurally identical to these — by design.
 */

export interface Bbox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Viewport {
  width: number;
  height: number;
}

export interface SingleTarget {
  selector?: string;
  bbox: Bbox;
}

export interface MultiTarget extends SingleTarget {
  value?: string;
}

export type CursorBehavior =
  | { kind: 'click'; target: SingleTarget }
  | { kind: 'hover'; target: SingleTarget }
  | { kind: 'type'; target: SingleTarget; value?: string }
  | { kind: 'type-multi'; targets: MultiTarget[] };

export interface CursorTimings {
  pre_action_ms: number;
  action_ms: number;
  post_action_ms: number;
  travel_speed_px_per_ms: number;
}

export const DEFAULT_TIMINGS: CursorTimings = {
  pre_action_ms: 200,
  action_ms: 300,
  post_action_ms: 150,
  travel_speed_px_per_ms: 1.5,
};

export interface AnimatorFrame {
  cursor: CursorBehavior | null;
  viewport: Viewport | null;
}

/** A scaled, displayed-space rect derived from a bbox + image size + viewport. */
export interface DisplayRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface DisplayScale {
  sx: number;
  sy: number;
}
