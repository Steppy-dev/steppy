/**
 * The golden bridge: ``contracts/fixtures/tours/cases.json`` is run by both the
 * Python producer check (``tests/test_tours_golden.py``) and this validator,
 * each against its own expectation column. ``valid/`` is the compiler's real
 * output for ``fixtures/tour-demo/project``.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { parseJson, validateManifest, validateTour, type TourManifest } from '../../src/tour/validate.js';

const DIR = resolve(__dirname, '../../../../contracts/fixtures/tours');

interface Case {
  name: string;
  rule: string;
  kind: 'manifest' | 'tour';
  base: string;
  player: 'ok' | 'invalid-data' | 'schema-unsupported' | 'load-failed';
  set?: Record<string, unknown>;
  delete?: string[];
  raw?: string;
}

const CASES: Case[] = JSON.parse(readFileSync(resolve(DIR, 'cases.json'), 'utf8'));
const read = (name: string) => readFileSync(resolve(DIR, 'valid', name), 'utf8');

function walk(obj: unknown, path: string): [Record<string, unknown> | unknown[], string] {
  const keys = path.split('.');
  let cur = obj as Record<string, unknown>;
  for (const key of keys.slice(0, -1)) cur = cur[key] as Record<string, unknown>;
  return [cur, keys[keys.length - 1]!];
}

function patched(c: Case): string {
  if (c.raw !== undefined) return c.raw;
  const data = JSON.parse(read(c.base));
  for (const [path, value] of Object.entries(c.set ?? {})) {
    const [parent, key] = walk(data, path);
    (parent as Record<string, unknown>)[key] = value;
  }
  for (const path of c.delete ?? []) {
    const [parent, key] = walk(data, path);
    delete (parent as Record<string, unknown>)[key];
  }
  return JSON.stringify(data);
}

function manifest(): TourManifest {
  const r = validateManifest(JSON.parse(read('tours.manifest.json')));
  if (!r.ok) throw new Error(JSON.stringify(r.issues));
  return r.value;
}

function verdict(c: Case): string {
  const parsed = parseJson(patched(c));
  if (!parsed.ok) return parsed.reason;
  if (c.kind === 'manifest') {
    const r = validateManifest(parsed.value);
    return r.ok ? 'ok' : r.reason;
  }
  const m = manifest();
  const [tourId, locale] = c.base.split('.');
  const entry = m.tours.find((t) => t.id === tourId)!;
  const r = validateTour(parsed.value, { manifest: m, entry, locale: locale!, guideOrigin: m.guideOrigin });
  return r.ok ? 'ok' : r.reason;
}

describe('golden tour cases', () => {
  it('has cases', () => expect(CASES.length).toBeGreaterThan(30));

  it.each(CASES.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    expect(verdict(c), c.rule).toBe(c.player);
  });

  it('accepts every valid file', () => {
    const m = manifest();
    for (const tour of m.tours) {
      for (const [locale, file] of Object.entries(tour.files)) {
        const r = validateTour(JSON.parse(read(file)), { manifest: m, entry: tour, locale, guideOrigin: m.guideOrigin });
        expect(r.ok, file).toBe(true);
      }
    }
  });
});
