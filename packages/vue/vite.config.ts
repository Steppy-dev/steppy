import { defineConfig } from 'vite';
import { resolve } from 'node:path';

/**
 * ESM library build of the Vue wrapper. Vue and the player element are kept
 * external — consumers already have Vue, and ``@steppy/player`` ships the
 * (Lit-bundled) custom element separately. ``tsc -p tsconfig.build.json``
 * emits the ``.d.ts`` alongside (see the ``build`` script).
 */
export default defineConfig({
  build: {
    lib: {
      entry: resolve(__dirname, 'src/index.ts'),
      formats: ['es'],
      fileName: () => 'index.js',
    },
    rollupOptions: {
      external: ['vue', '@steppy/player', '@steppy/player/manifest'],
    },
    sourcemap: true,
    target: 'es2022',
    emptyOutDir: true,
  },
  test: {
    include: ['test/**/*.test.ts'],
    environment: 'happy-dom',
    globals: false,
  },
});
