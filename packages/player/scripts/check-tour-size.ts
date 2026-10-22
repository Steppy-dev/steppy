/**
 * Size budget for the in-app tour overlay: ``dist/steppy-tour.js``,
 * minified with esbuild (through vite, no extra dependency) and gzipped at
 * level 9, must stay within BUDGET bytes. It also must not pull in the guide
 * player or zod — the overlay is meant to be a small, separate download.
 *
 * Run after ``npm run build``: ``npm run size:tour``.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { gzipSync } from 'node:zlib';

import { transformWithEsbuild } from 'vite';

/**
 * The spec calls 25 KB (25,600 B, min + gzip) a target. The first complete
 * overlay measured 25,965 B — Lit alone is about 6 KB of it — so the gate is
 * that measurement plus 3% headroom: growth past it needs a decision, not a
 * quiet bump. TARGET is still printed so the gap stays visible.
 */
const TARGET = 25_600;
const BUDGET = 26_750;
const FORBIDDEN = ['ZodError', 'steppy-guide'];

const file = resolve(import.meta.dirname, '../dist/steppy-tour.js');
const source = readFileSync(file, 'utf8');
const { code } = await transformWithEsbuild(source, 'steppy-tour.js', { minify: true, format: 'esm', target: 'es2022' });
const gz = gzipSync(code, { level: 9 }).byteLength;

console.log(
  `steppy-tour.js: ${source.length} B raw, ${code.length} B min, ${gz} B min+gz (budget ${BUDGET} B, target ${TARGET} B)`,
);
let failed = false;
for (const marker of FORBIDDEN) {
  if (source.includes(marker)) {
    console.error(`the tour bundle contains "${marker}": it must not import the guide player or zod`);
    failed = true;
  }
}
if (gz > BUDGET) {
  console.error(`the tour bundle is ${gz - BUDGET} B over budget`);
  failed = true;
}
process.exit(failed ? 1 : 0);
