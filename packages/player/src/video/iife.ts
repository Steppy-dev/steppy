/**
 * IIFE entry for the video renderer.
 *
 * Exposes ``window.__STEPPY_VIDEO__`` so the Python side (``steppy/video.py``)
 * can drive frame-by-frame capture over CDP:
 *
 * ```js
 * const stage = window.__STEPPY_VIDEO__.mount(manifest, options);
 * await stage.ready();
 * stage.size();          // -> {width, height}, both even, for the viewport
 * stage.seek(i);         // -> false when the frame is pixel-identical
 * ```
 *
 * This bundle is **build-time only**. It is inlined into a throwaway harness
 * page and never copied into a published guide site — which is exactly why it
 * is a separate artifact from ``steppy-player.js`` rather than an export of it:
 * viewers should not download rendering code, and this module needs no Lit.
 */

import type { GuideManifest } from '../manifest/schema.js';
import { mountVideoStage } from './stage.js';
import type { VideoStage, VideoStageOptions } from './stage.js';

export interface SteppyVideoGlobal {
  mount(manifest: GuideManifest, options: VideoStageOptions, host?: HTMLElement): VideoStage;
  /** Set by the mount call so the driver can reach the live stage by name. */
  stage: VideoStage | null;
}

const api: SteppyVideoGlobal = {
  stage: null,
  mount(manifest, options, host) {
    const target = host ?? document.getElementById('steppy-video') ?? document.body;
    const stage = mountVideoStage(target as HTMLElement, manifest, options);
    api.stage = stage;
    return stage;
  },
};

declare global {
  interface Window {
    __STEPPY_VIDEO__?: SteppyVideoGlobal;
  }
}

window.__STEPPY_VIDEO__ = api;

export { mountVideoStage };
export type { VideoStage, VideoStageOptions };
