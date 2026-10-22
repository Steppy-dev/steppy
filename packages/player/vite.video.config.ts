import { defineConfig } from 'vite';
import { resolve } from 'node:path';

/**
 * IIFE build of the video renderer (`modes: ['video']`).
 *
 * Inlined into the throwaway harness page ``steppy/video.py`` drives over CDP;
 * it exposes ``window.__STEPPY_VIDEO__`` and is never served to a guide viewer.
 * Kept out of ``steppy-player.js`` on purpose: published sites should not carry
 * rendering code, and this entry pulls in no Lit component machinery.
 *
 * Output: ``dist/steppy-video.js``
 */
export default defineConfig({
  build: {
    outDir: 'dist',
    // Runs third, after the ESM and legacy-cursor builds. The default would
    // wipe both of them.
    emptyOutDir: false,
    lib: {
      entry: resolve(__dirname, 'src/video/iife.ts'),
      formats: ['iife'],
      name: '__steppyVideo',
      fileName: () => 'steppy-video.js',
    },
    rollupOptions: {
      output: {
        // Side-effects only (the global is assigned in the entry module).
        extend: true,
      },
    },
    sourcemap: true,
    target: 'es2022',
  },
});
