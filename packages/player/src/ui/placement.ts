/**
 * Bubble placement next to a highlighted rect, shared by the guide player's
 * tooltip and the in-app tour popover. Pure math, no DOM.
 *
 * Called without options, ``placeTooltip`` is the guide's historical rule,
 * byte for byte (guard: ``test/ui/placement.test.ts`` against a frozen copy).
 * With options it honours a preferred side, keeps a margin from the stage
 * edges, steers clear of rects to ``avoid`` and reports whether the bubble
 * could not avoid covering the hotspot.
 *
 * ``Rect`` is declared here rather than imported from ``cursor/`` so the tour's
 * type declarations do not pull the cursor animator in.
 */

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type TooltipSide = 'top' | 'bottom' | 'left' | 'right';

export interface TooltipPlacement {
  side: TooltipSide;
  /** Top-left offset, in display space, of the tooltip's bounding box. */
  x: number;
  y: number;
  /** Only with options: true when no side fits and the bubble covers the hotspot. */
  overlaps?: boolean;
}

export interface PlaceOptions {
  /** Side to try first; ``auto`` (the default) uses the historical bias. */
  preferred?: TooltipSide | 'auto';
  /** Distance kept from every stage edge (default 0). */
  margin?: number;
  /** Rects a fitting side should not cover (e.g. the next step's target). */
  avoid?: readonly Rect[];
}

type Size = { width: number; height: number };

const OPPOSITE: Record<TooltipSide, TooltipSide> = { top: 'bottom', bottom: 'top', left: 'right', right: 'left' };

/**
 * Decide which side of the hotspot the tooltip should sit on. Picks the side
 * with the most room, then biases toward ``bottom`` for ties (matches
 * Guideflow's default).
 *
 * All inputs and outputs are in display space (the same coordinate frame as
 * the hotspot rect).
 */
export function placeTooltip(hotspot: Rect, tooltip: Size, stage: Size, gap = 8, opts?: PlaceOptions): TooltipPlacement {
  if (opts === undefined) return legacyPlace(hotspot, tooltip, stage, gap);
  return placeWithOptions(hotspot, tooltip, stage, gap, opts);
}

function legacyPlace(hotspot: Rect, tooltip: Size, stage: Size, gap: number): TooltipPlacement {
  const above = hotspot.y;
  const below = stage.height - (hotspot.y + hotspot.height);
  const left = hotspot.x;
  const right = stage.width - (hotspot.x + hotspot.width);

  // Prefer vertical placement (above or below). Only fall back to horizontal
  // when neither vertical side has room for the tooltip's height plus gap.
  const verticalNeeded = tooltip.height + gap;
  const horizontalNeeded = tooltip.width + gap;
  const bestVertical: { side: TooltipSide; room: number } =
    below >= above ? { side: 'bottom', room: below } : { side: 'top', room: above };
  let pick: TooltipSide;
  if (bestVertical.room >= verticalNeeded) {
    pick = bestVertical.side;
  } else {
    const bestHorizontal: { side: TooltipSide; room: number } =
      right >= left ? { side: 'right', room: right } : { side: 'left', room: left };
    pick = bestHorizontal.room >= horizontalNeeded ? bestHorizontal.side : bestVertical.side;
  }

  const cx = hotspot.x + hotspot.width / 2;
  const cy = hotspot.y + hotspot.height / 2;

  let x = 0;
  let y = 0;
  if (pick === 'bottom') {
    x = clamp(cx - tooltip.width / 2, 0, stage.width - tooltip.width);
    y = Math.min(hotspot.y + hotspot.height + gap, stage.height - tooltip.height);
  } else if (pick === 'top') {
    x = clamp(cx - tooltip.width / 2, 0, stage.width - tooltip.width);
    y = Math.max(0, hotspot.y - tooltip.height - gap);
  } else if (pick === 'right') {
    x = Math.min(hotspot.x + hotspot.width + gap, stage.width - tooltip.width);
    y = clamp(cy - tooltip.height / 2, 0, stage.height - tooltip.height);
  } else {
    x = Math.max(0, hotspot.x - tooltip.width - gap);
    y = clamp(cy - tooltip.height / 2, 0, stage.height - tooltip.height);
  }
  return { side: pick, x, y };
}

function placeWithOptions(hotspot: Rect, tooltip: Size, stage: Size, gap: number, opts: PlaceOptions): TooltipPlacement {
  const margin = opts.margin ?? 0;
  const room: Record<TooltipSide, number> = {
    top: hotspot.y - margin,
    bottom: stage.height - (hotspot.y + hotspot.height) - margin,
    left: hotspot.x - margin,
    right: stage.width - (hotspot.x + hotspot.width) - margin,
  };
  const order = sideOrder(room, opts.preferred ?? 'auto');
  const fits = (side: TooltipSide): boolean => {
    const vertical = side === 'top' || side === 'bottom';
    const main = vertical ? tooltip.height : tooltip.width;
    const cross = vertical ? tooltip.width : tooltip.height;
    const crossStage = vertical ? stage.width : stage.height;
    return room[side] >= main + gap && cross <= crossStage - 2 * margin;
  };
  const place = (side: TooltipSide): TooltipPlacement => {
    const cx = hotspot.x + hotspot.width / 2;
    const cy = hotspot.y + hotspot.height / 2;
    const xCentred = clamp(cx - tooltip.width / 2, margin, stage.width - tooltip.width - margin);
    const yCentred = clamp(cy - tooltip.height / 2, margin, stage.height - tooltip.height - margin);
    if (side === 'bottom') return { side, x: xCentred, y: hotspot.y + hotspot.height + gap };
    if (side === 'top') return { side, x: xCentred, y: hotspot.y - gap - tooltip.height };
    if (side === 'right') return { side, x: hotspot.x + hotspot.width + gap, y: yCentred };
    return { side, x: hotspot.x - gap - tooltip.width, y: yCentred };
  };
  const avoid = opts.avoid ?? [];
  const hits = (p: TooltipPlacement): boolean =>
    avoid.some((r) => intersects({ x: p.x, y: p.y, width: tooltip.width, height: tooltip.height }, r));

  const fitting = order.filter(fits).map(place);
  const clear = fitting.filter((p) => !hits(p));
  const chosen = clear[0] ?? fitting[0];
  if (chosen) return { ...chosen, overlaps: false };

  // Nothing fits: best effort on the first side in order, kept inside the stage.
  const best = place(order[0]!);
  const x = clamp(best.x, margin, stage.width - tooltip.width - margin);
  const y = clamp(best.y, margin, stage.height - tooltip.height - margin);
  const overlaps = intersects({ x, y, width: tooltip.width, height: tooltip.height }, hotspot);
  return { side: best.side, x, y, overlaps };
}

function sideOrder(room: Record<TooltipSide, number>, preferred: TooltipSide | 'auto'): TooltipSide[] {
  if (preferred === 'auto') {
    const vertical: TooltipSide[] = room.bottom >= room.top ? ['bottom', 'top'] : ['top', 'bottom'];
    const horizontal: TooltipSide[] = room.right >= room.left ? ['right', 'left'] : ['left', 'right'];
    return [...vertical, ...horizontal];
  }
  const rest = (['top', 'bottom', 'left', 'right'] as TooltipSide[])
    .filter((s) => s !== preferred && s !== OPPOSITE[preferred])
    .sort((a, b) => room[b] - room[a]);
  return [preferred, OPPOSITE[preferred], ...rest];
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

/** True when ``a`` and ``b`` share a positive area. */
export function intersects(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

/** The overlap of ``a`` and ``b``, or ``null`` when they share no area. */
export function intersect(a: Rect, b: Rect): Rect | null {
  const x = Math.max(a.x, b.x);
  const y = Math.max(a.y, b.y);
  const right = Math.min(a.x + a.width, b.x + b.width);
  const bottom = Math.min(a.y + a.height, b.y + b.height);
  return right > x && bottom > y ? { x, y, width: right - x, height: bottom - y } : null;
}

/** The smallest rect containing both. */
export function union(a: Rect, b: Rect): Rect {
  const x = Math.min(a.x, b.x);
  const y = Math.min(a.y, b.y);
  return {
    x,
    y,
    width: Math.max(a.x + a.width, b.x + b.width) - x,
    height: Math.max(a.y + a.height, b.y + b.height) - y,
  };
}

/** ``r`` grown by ``pad`` on every side. */
export function inflate(r: Rect, pad: number): Rect {
  return { x: r.x - pad, y: r.y - pad, width: r.width + 2 * pad, height: r.height + 2 * pad };
}

export function containsPoint(r: Rect, x: number, y: number): boolean {
  return x >= r.x && x <= r.x + r.width && y >= r.y && y <= r.y + r.height;
}
