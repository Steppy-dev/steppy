import { defineConfig } from 'vite';
import { resolve } from 'node:path';

/**
 * IIFE build of the legacy cursor animator. This drops into a ``<script>`` tag
 * in the existing stepbystep templates and reads
 * ``window.__STEPPY_STEPS__`` / ``window.__STEPPY_TIMINGS__``. Output is a
 * single self-invoking bundle with no globals exposed.
 *
 * Output: ``dist/steppy-cursor-legacy.js``
 */
export default defineConfig({
  build: {
    outDir: 'dist',
    emptyOutDir: false, // don't wipe the ESM build that runs first
    lib: {
      entry: resolve(__dirname, 'src/cursor/legacy-iife.ts'),
      formats: ['iife'],
      // A name is required for iife. The IIFE returns nothing meaningful
      // (side-effects only), so the global it assigns to is harmless.
      name: '__steppyCursorLegacy',
      fileName: () => 'steppy-cursor-legacy.js',
    },
    rollupOptions: {
      output: {
        // Match the existing inline JS pattern: no exports, just side-effects.
        extend: true,
      },
    },
    sourcemap: true,
    target: 'es2018', // older targets are fine; consumers may include old browsers
  },
});
