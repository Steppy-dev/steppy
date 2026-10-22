/**
 * Public entry: registers ``<steppy-guide>`` and re-exports the manifest +
 * cursor types so host apps can type their integration code.
 */
export { SteppyGuide, DEFAULT_UI_STRINGS } from './steppy-guide.js';
export type { UiStrings } from './steppy-guide.js';

export * from './manifest/index.js';
export {
  CursorAnimator,
  easeInOut,
  getDisplayScale,
  rectCenter,
  scaleBbox,
  scaleRect,
  travelDuration,
  bezierControlPoint,
} from './cursor/animator.js';
export type { AnimatorOptions } from './cursor/animator.js';
export type {
  AnimatorFrame,
  Bbox,
  CursorBehavior,
  CursorTimings,
  DisplayRect,
  DisplayScale,
  MultiTarget,
  SingleTarget,
  Viewport,
} from './cursor/types.js';
