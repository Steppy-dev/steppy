import { defineConfig } from 'vite';
import { resolve } from 'node:path';

/**
 * ESM library build of the React wrapper. React and the player element are
 * kept external — consumers already have React, and ``@steppy/player`` ships
 * the (Lit-bundled) custom element separately. ``tsc -p tsconfig.build.json``
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
      external: [
        'react',
        'react/jsx-runtime',
        'react-dom',
        '@steppy/player',
        '@steppy/player/manifest',
      ],
    },
    sourcemap: true,
    target: 'es2022',
    emptyOutDir: true,
  },
  test: {
    include: ['test/**/*.test.ts', 'test/**/*.test.tsx'],
    environment: 'happy-dom',
    globals: false,
  },
});
