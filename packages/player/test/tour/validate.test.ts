/**
 * Validator rules beyond the shared golden cases (``golden.test.ts``):
 * context-dependent checks, caps, and what is copied.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { isBuildId, validateManifest, validateTour, type TourManifest } from '../../src/tour/validate.js';

const DIR = resolve(__dirname, '../../../../contracts/fixtures/tours/valid');
const json = (name: string) => JSON.parse(readFileSync(resolve(DIR, name), 'utf8'));

function manifest(patch: (m: Record<string, unknown>) => void = () => {}): TourManifest {
  const raw = json('tours.manifest.json');
  patch(raw);
  const r = validateManifest(raw);
  if (!r.ok) throw new Error(JSON.stringify(r.issues));
  return r.value;
}

function tour(patch: (t: Record<string, unknown>) => void, m = manifest(), guideOrigin: string | null = m.guideOrigin) {
  const raw = json('create-invoice.en.json');
  patch(raw);
  return validateTour(raw, { manifest: m, entry: m.tours[0]!, locale: 'en', guideOrigin });
}

describe('validateManifest', () => {
  it('copies known keys only, into frozen objects', () => {
    const m = manifest((raw) => {
      raw.extra = 1;
      (raw.tours as Record<string, unknown>[])[0]!.extra = 2;
    });
    expect('extra' in m).toBe(false);
    expect('extra' in m.tours[0]!).toBe(false);
    expect(Object.isFrozen(m) && Object.isFrozen(m.tours[0])).toBe(true);
  });

  it('rejects a duplicate tour id case-insensitively', () => {
    const raw = json('tours.manifest.json');
    raw.tours[1].id = 'Create-Invoice';
    raw.tours[1].files = { en: 'Create-Invoice.en.json' };
    raw.tours[1].title = { en: 'x' };
    raw.tours[1].integrity = { en: raw.tours[0].integrity.en };
    const r = validateManifest(raw);
    expect(r.ok === false && r.issues.map((i) => i.message)).toEqual(['is not unique']);
  });

  it('requires an SRI hash for every file', () => {
    const missing = json('tours.manifest.json');
    delete missing.tours[0].integrity;
    expect(validateManifest(missing).ok).toBe(false);
    const partial = json('tours.manifest.json');
    delete partial.tours[0].integrity.fr;
    const r = validateManifest(partial);
    expect(r.ok === false && r.issues[0]!.message).toBe('has no hash for "fr"');
  });

  it.each([
    [['en', 'fr', 'FR']],
    [['en', 'fr', 'fr-BE', 'fr_be']],
  ])('rejects locales that differ only in case or separator: %j', (locales) => {
    const raw = json('tours.manifest.json');
    raw.locales = locales;
    const r = validateManifest(raw);
    expect(r.ok === false && r.issues.some((i) => i.message === 'is listed twice')).toBe(true);
  });

  it.each([
    ['demo-0001', true],
    ['v1.con', true],
    ['con1.x', true],
    ['con', false],
    ['con.v2', false],
    ['NUL.1', false],
  ])('isBuildId(%s) -> %s', (id, ok) => expect(isBuildId(id)).toBe(ok));

  it('rejects integrity for a locale without a file, and layouts with bad queries', () => {
    expect(
      validateManifest({ ...json('tours.manifest.json'), layouts: { mobile: 'max-width: 600px; } body {' } }).ok,
    ).toBe(false);
    const raw = json('tours.manifest.json');
    raw.tours[0].integrity.nl = raw.tours[0].integrity.en;
    expect(validateManifest(raw).ok).toBe(false);
  });

  it('caps the issue list', () => {
    const raw = json('tours.manifest.json');
    raw.tours = Array.from({ length: 40 }, (_, i) => ({ id: `t${i}`, role: '!' }));
    const r = validateManifest(raw);
    expect(r.ok === false && r.issues.length).toBe(20);
  });
});

describe('validateTour', () => {
  it('accepts test-ids with colons and slashes', () => {
    expect(tour((t) => ((t.steps as Record<string, unknown>[])[0]!.target = 'invoice:list/new.btn')).ok).toBe(true);
  });

  it('rejects a guideUrl when no guide origin is trusted', () => {
    const withUrl = (t: Record<string, unknown>) => (t.guideUrl = 'https://guide.example.com/create-invoice/en.html');
    expect(tour(withUrl).ok).toBe(true);
    expect(tour(withUrl, manifest(), null).ok).toBe(false);
    expect(tour((t) => (t.guideUrl = 'https://user:pw@guide.example.com/x')).ok).toBe(false);
  });

  it('requires step layouts to be manifest layouts', () => {
    const m = manifest((raw) => (raw.layouts = { mobile: '(max-width: 767.98px)' }));
    expect(tour((t) => ((t.steps as Record<string, unknown>[])[0]!.layouts = ['mobile']), m).ok).toBe(true);
    expect(tour((t) => ((t.steps as Record<string, unknown>[])[0]!.layouts = ['tablet']), m).ok).toBe(false);
  });

  it('requires the step count and entry of the manifest', () => {
    expect(tour((t) => (t.steps as unknown[]).pop()).ok).toBe(false);
    expect(tour((t) => (t.entry = null)).ok).toBe(false);
  });

  it('counts characters, not UTF-16 units, against the text cap', () => {
    const text = '😀'.repeat(2000);
    expect(tour((t) => ((t.steps as Record<string, unknown>[])[0]!.text = text)).ok).toBe(true);
    expect(tour((t) => ((t.steps as Record<string, unknown>[])[0]!.text = `${text}x`)).ok).toBe(false);
  });

  it('rejects blank text and titles', () => {
    expect(tour((t) => ((t.steps as Record<string, unknown>[])[0]!.text = '   ')).ok).toBe(false);
  });
});
