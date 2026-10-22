import { defineConfig } from 'vite';
import { resolve } from 'node:path';

/**
 * ESM build of the player. Bundles Lit so consumers (Angular host, static
 * HTML page) drop in one file with no dependencies. The legacy IIFE for the
 * stepbystep templates is built separately via ``vite.iife.config.ts``.
 */
export default defineConfig({
  build: {
    lib: {
      entry: {
        index: resolve(__dirname, 'src/index.ts'),
      },
      formats: ['es'],
      fileName: (_format, name) => (name === 'index' ? 'steppy-player.js' : `${name}.js`),
    },
    rollupOptions: {
      // No externals: bundle Lit so the player ships zero-dep.
      output: {
        assetFileNames: (asset) => {
          if (asset.name && /\.(css)$/.test(asset.name)) return 'steppy-player.css';
          return 'assets/[name][extname]';
        },
      },
    },
    sourcemap: true,
    target: 'es2022',
    emptyOutDir: true,
  },
  test: {
    include: ['test/**/*.test.ts'],
    environment: 'happy-dom',
    globals: false,
    setupFiles: ['./test/setup.ts'],
  },
});
