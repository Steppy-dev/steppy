import { defineConfig, type Plugin } from 'vite';
import { resolve } from 'node:path';

/**
 * Collapse the whitespace inside the overlay stylesheet's ``css`` tagged
 * template, which esbuild leaves untouched. Conservative on purpose: runs of
 * whitespace become one space, the space next to ``{ } ; , >`` and after a
 * ``:`` goes — nothing else, so ``calc(100vw - 16px)`` and descendant
 * combinators survive. ``${...}`` interpolations are kept as written.
 */
export function minifyCss(body: string): string {
  return body
    .split(/(\$\{[^}]*\})/)
    .map((part, i) =>
      i % 2
        ? part
        : part
            .replace(/\/\*[\s\S]*?\*\//g, '')
            .replace(/\s+/g, ' ')
            .replace(/\s*([{};,>])\s*/g, '$1')
            .replace(/:\s+/g, ':')
            .replace(/;}/g, '}'),
    )
    .join('');
}

function minifyTourCss(): Plugin {
  return {
    name: 'steppy-minify-tour-css',
    transform(code, id) {
      if (!/[\\/]src[\\/]tour[\\/]styles\.ts$/.test(id)) return null;
      return { code: code.replace(/\bcss`([^`]*)`/g, (_m, body: string) => `css\`${minifyCss(body)}\``), map: null };
    },
  };
}

/**
 * ESM build of the in-app tour overlay (``@steppy/player/tour``).
 *
 * Ships to apps through npm only — it is not vendored into the Python wheel
 * (no Python code serves it). Lit is bundled, zod is not used: the overlay
 * validates its data by hand to stay inside the size budget
 * (``scripts/check-tour-size.ts``).
 *
 * Output: ``dist/steppy-tour.js``
 */
export default defineConfig({
  plugins: [minifyTourCss()],
  build: {
    outDir: 'dist',
    // Runs fourth, after the guide, legacy-cursor and video builds; the
    // default would wipe them.
    emptyOutDir: false,
    lib: {
      entry: resolve(__dirname, 'src/tour/index.ts'),
      formats: ['es'],
      fileName: () => 'steppy-tour.js',
    },
    sourcemap: true,
    target: 'es2022',
  },
});
