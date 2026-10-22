/**
 * Publish-readiness check for ``@steppy/player``: what ``npm pack`` would
 * ship must contain every file ``package.json`` points at (``main``,
 * ``types``, each ``exports`` target, ``typesVersions``) and nothing from the
 * sources or the tests. A dangling entry is invisible until someone installs
 * the package — the ``types`` and ``./manifest`` entries once pointed at files
 * no build produced.
 *
 * Run after ``npm run build``: ``npm run check:pack``.
 */

import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const here = resolve(import.meta.dirname, '..');
const pkg = JSON.parse(readFileSync(resolve(here, 'package.json'), 'utf8'));

// ``--ignore-scripts``: the check reads what is built, it never builds (prepack would).
const raw = execSync('npm pack --dry-run --json --ignore-scripts', { cwd: here, encoding: 'utf8' });
const packed = new Set<string>((JSON.parse(raw)[0].files as { path: string }[]).map((f) => f.path));

const targets = new Set<string>();
const add = (value: unknown): void => {
  if (typeof value === 'string') targets.add(value.replace(/^\.\//, ''));
  else if (Array.isArray(value)) value.forEach(add);
  else if (value && typeof value === 'object') Object.values(value).forEach(add);
};
add(pkg.main);
add(pkg.module);
add(pkg.types);
add(pkg.exports);
add(pkg.typesVersions);

const problems: string[] = [];
for (const target of targets) {
  if (!packed.has(target)) problems.push(`${target} is referenced by package.json but not packed`);
}
for (const required of ['LICENSE', 'NOTICE', 'THIRD_PARTY_LICENSES.txt']) {
  if (!packed.has(required)) problems.push(`${required} is not packed`);
}
for (const path of packed) {
  if (/^(src|test|scripts|dev)\//.test(path)) problems.push(`${path} should not be packed`);
}
if (pkg.private) problems.push('package.json is marked private');

if (problems.length) {
  console.error(`npm pack check failed for ${pkg.name}:\n  ${problems.join('\n  ')}`);
  process.exit(1);
}
console.log(`${pkg.name}@${pkg.version}: ${packed.size} files packed; ${targets.size} package.json targets present`);
