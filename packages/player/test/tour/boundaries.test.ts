/**
 * What the overlay may import and do, read from its sources, and the public
 * surface an app or adapter programs against.
 */

import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import * as tour from '../../src/tour/index.js';

const SRC = resolve(__dirname, '../../src');
const sources = (dir: string) =>
  readdirSync(resolve(SRC, dir))
    .filter((f) => f.endsWith('.ts'))
    .map((f) => [`${dir}/${f}`, readFileSync(resolve(SRC, dir, f), 'utf8')] as const);
const ALL = [...sources('tour'), ...sources('ui')];

describe('import boundary', () => {
  it('src/tour and src/ui import only each other and lit', () => {
    for (const [name, text] of ALL) {
      for (const [, spec] of text.matchAll(/from '([^']+)'/g)) {
        const ok = spec!.startsWith('./') || spec!.startsWith('../ui/') || /^lit(?:$|\/)/.test(spec!);
        expect(ok, `${name} imports ${spec}`).toBe(true);
      }
    }
  });

  it('never parses HTML or evaluates code, and binds no style attribute', () => {
    const banned = [/innerHTML/, /unsafeHTML/, /insertAdjacentHTML/, /DOMParser/, /\beval\(/, /new Function/, /style=\$\{/, /styleMap/];
    for (const [name, text] of ALL) {
      const code = text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
      for (const pattern of banned) expect(pattern.test(code), `${name} matches ${pattern}`).toBe(false);
    }
  });
});

describe('public API', () => {
  it('exports the element, its definer, the contract values and the validators', () => {
    expect(Object.keys(tour).sort()).toEqual(
      [
        'ADVANCE_KINDS',
        'DEFAULT_TOUR_STRINGS',
        'PLACEMENTS',
        'SteppyTourHost',
        'TOURS_KILL_SWITCH_GLOBAL',
        'TOUR_ERROR_REASONS',
        'TOUR_EVENTS',
        'TOUR_HOST_TAG',
        'TOUR_PARTS',
        'TOUR_SCHEMA_VERSION',
        'TOUR_APPEARANCES',
        'TOUR_THEME_KEYS',
        'themeProperty',
        'defineTourHost',
        'validateManifest',
        'validateTour',
      ].sort(),
    );
    expect(tour.TOUR_HOST_TAG).toBe('steppy-tour-host');
    expect(tour.TOUR_EVENTS).toEqual(['tour-start', 'tour-step', 'tour-complete', 'tour-dismiss', 'tour-offpath', 'tour-error']);
    expect(customElements.get(tour.TOUR_HOST_TAG)).toBe(tour.SteppyTourHost);
  });

  it('every part the templates render is a declared part', () => {
    const templates = readFileSync(resolve(SRC, 'tour/templates.ts'), 'utf8');
    const used = new Set([...templates.matchAll(/part="([^"$]+)/g)].flatMap((m) => m[1]!.trim().split(/\s+/)));
    for (const name of used) expect(tour.TOUR_PARTS as readonly string[], name).toContain(name);
  });
});
