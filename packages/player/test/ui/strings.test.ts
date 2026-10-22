/**
 * ``ui/strings.ts`` and the guide's ``getUiStrings`` built on it: same tables
 * as before the extraction, plus the base-language rung (``fr-BE`` -> ``fr``).
 */

import { describe, expect, it } from 'vitest';

import { DEFAULT_UI_STRINGS, getUiStrings } from '../../src/i18n/ui-strings.js';
import { CHROME_STRINGS, baseLanguage, format, lookupLocale, resolveStrings } from '../../src/ui/strings.js';
import { LEGACY_UI_STRINGS, legacyGetUiStrings } from './legacy-ui-strings.js';

describe('lookupLocale', () => {
  const keys = ['en', 'fr', 'fr-BE', 'nl'];
  it.each([
    ['fr', 'fr'],
    ['FR', 'fr'],
    ['fr-BE', 'fr-BE'],
    ['fr_BE', 'fr-BE'],
    ['fr-be', 'fr-BE'],
    ['fr-CA', 'fr'],
    ['nl-BE', 'nl'],
    ['de', null],
    ['', null],
  ])('%s -> %s', (requested, expected) => {
    expect(lookupLocale(keys, requested)).toBe(expected);
  });

  it('never widens a query', () => {
    expect(lookupLocale(['fr-BE'], 'fr')).toBeNull();
  });

  it('baseLanguage mirrors the Python rule', () => {
    expect(baseLanguage('fr-BE')).toBe('fr');
    expect(baseLanguage('PT_br')).toBe('pt');
    expect(baseLanguage('zh-Hant-TW')).toBe('zh');
    expect(baseLanguage('')).toBe('');
  });
});

describe('resolveStrings', () => {
  const table = { en: { a: 'A', b: 'B' }, fr: { a: 'Fa', b: 'Fb' } };
  it('falls back through the base language, then the fallback locale', () => {
    expect(resolveStrings(table, 'fr-BE')).toBe(table.fr);
    expect(resolveStrings(table, 'de')).toBe(table.en);
  });
  it('merges sparse overrides found through the same rungs', () => {
    expect(resolveStrings(table, 'fr-BE', { fr: { a: 'X' } })).toEqual({ a: 'X', b: 'Fb' });
    expect(resolveStrings(table, 'fr', { 'fr-BE': { a: 'X' } })).toBe(table.fr);
  });
});

describe('the guide UI strings', () => {
  it('have the same values as before for every bundled locale', () => {
    expect(DEFAULT_UI_STRINGS).toEqual(LEGACY_UI_STRINGS);
    for (const locale of ['en', 'fr', 'nl', 'de', 'xx']) {
      expect(getUiStrings(locale, undefined)).toEqual(legacyGetUiStrings(locale, undefined));
      expect(getUiStrings(locale, { [locale]: { next: 'N' } })).toEqual(
        legacyGetUiStrings(locale, { [locale]: { next: 'N' } }),
      );
    }
  });

  it('now give a regional locale its base language', () => {
    expect(getUiStrings('fr-BE', undefined).next).toBe('Suivant');
    expect(legacyGetUiStrings('fr-BE', undefined).next).toBe('Next');
  });

  it('share next/progress with the tour chrome', () => {
    for (const [locale, chrome] of Object.entries(CHROME_STRINGS)) {
      expect(DEFAULT_UI_STRINGS[locale]).toMatchObject(chrome);
    }
  });
});

describe('format', () => {
  it('fills known placeholders and keeps unknown ones', () => {
    expect(format('Step {current} of {total}', { current: 2, total: 5 })).toBe('Step 2 of 5');
    expect(format('{a} {missing}', { a: 'x' })).toBe('x {missing}');
  });
});
