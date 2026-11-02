import { defineConfig } from 'vite';
import { resolve } from 'node:path';

/**
 * IIFE build of reader language / layout detection (``src/detect/iife.ts``).
 *
 * Loaded synchronously from ``<head>`` on the static target's pages through
 * ``templates/static/_steppy_detect.html.j2``, so it must stay small and
 * dependency-free: no Lit, no zod. Exposes ``window.SteppyDetect``.
 *
 * Output: ``dist/steppy-detect.js``
 */
export default defineConfig({
  build: {
    outDir: 'dist',
    // Runs after the ESM build; the default would wipe it.
    emptyOutDir: false,
    lib: {
      entry: resolve(__dirname, 'src/detect/iife.ts'),
      formats: ['iife'],
      name: '__steppyDetect',
      fileName: () => 'steppy-detect.js',
    },
    rollupOptions: {
      output: {
        extend: true,
      },
    },
    sourcemap: true,
    target: 'es2018',
  },
});
