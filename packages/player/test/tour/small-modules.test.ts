/** Storage, tour strings and direction, default-colour contrast, logical CSS and value signatures. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { TOUR_RESUME_STORAGE_KEY } from '../../src/ui/names.js';
import { TOUR_DEFAULTS } from '../../src/ui/tokens.js';
import { CHROME_STRINGS } from '../../src/ui/strings.js';
import { clearResume, readResume, writeResume, RESUME_TTL_MS, type ResumeRecord } from '../../src/tour/storage.js';
import { DEFAULT_TOUR_STRINGS, instructionFor, resolveTourStrings, textDir } from '../../src/tour/strings.js';
import { tourStyles } from '../../src/tour/styles.js';
import { isComboboxLike, isExpanded, readFieldValue, valueSignature } from '../../src/tour/signature.js';
import { ADVANCE_KINDS, CHROME_STRING_LOCALES } from '../../src/tour/contract.js';

const record: ResumeRecord = {
  v: 1,
  tourId: 'create-invoice',
  stepId: 'invoice-title',
  locale: 'en',
  params: { invoiceId: '1001' },
  buildId: 'b-1',
  manifestKey: '/tours/tours.manifest.json',
  savedAt: 1000,
};

describe('resume storage', () => {
  beforeEach(() => sessionStorage.clear());

  it('round-trips a fresh record for the same manifest', () => {
    writeResume(record);
    expect(readResume(record.manifestKey, 2000)).toEqual(record);
  });

  it('ignores a record made for another host configuration, and keeps it for that host', () => {
    writeResume(record);
    expect(readResume('/other.json', 2000)).toBeNull();
    expect(readResume(record.manifestKey, 2000)).toEqual(record);
  });

  it.each([
    ['stale', JSON.stringify(record), 1000 + RESUME_TTL_MS],
    ['from the future', JSON.stringify(record), 999],
    ['malformed', '{"v":1,"tourId":3}', 2000],
    ['not JSON', 'not json', 2000],
    ['not an object', '5', 2000],
  ])('drops a %s record when it reads it', (_what, raw, now) => {
    sessionStorage.setItem(TOUR_RESUME_STORAGE_KEY, raw);
    expect(readResume(record.manifestKey, now)).toBeNull();
    expect(sessionStorage.getItem(TOUR_RESUME_STORAGE_KEY)).toBeNull();
  });

  describe('when storage throws', () => {
    afterEach(() => vi.unstubAllGlobals());
    it('degrades silently', () => {
      const fail = () => {
        throw new Error('denied');
      };
      vi.stubGlobal('sessionStorage', { getItem: fail, setItem: fail, removeItem: fail });
      expect(() => writeResume(record)).not.toThrow();
      expect(readResume(record.manifestKey, 2000)).toBeNull();
      expect(() => clearResume()).not.toThrow();
    });
  });
});

describe('tour strings', () => {
  it('every bundled locale has every key, with the same placeholders', () => {
    const keys = Object.keys(DEFAULT_TOUR_STRINGS.en!).sort();
    for (const [locale, table] of Object.entries(DEFAULT_TOUR_STRINGS)) {
      expect(Object.keys(table).sort(), locale).toEqual(keys);
      for (const key of keys) {
        const holes = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort();
        expect(holes(table[key as keyof typeof table]), `${locale}.${key}`).toEqual(holes(DEFAULT_TOUR_STRINGS.en![key as keyof typeof table]));
      }
    }
  });

  it('shares next/progress with the guide', () => {
    for (const [locale, chrome] of Object.entries(CHROME_STRINGS)) expect(DEFAULT_TOUR_STRINGS[locale]).toMatchObject(chrome);
  });

  it('resolves by the content locale, base language included, with overrides', () => {
    expect(resolveTourStrings('fr-BE').next).toBe('Suivant');
    expect(resolveTourStrings('es').next).toBe('Next');
    expect(resolveTourStrings('fr', { fr: { done: 'Fini' } }).done).toBe('Fini');
  });

  it('ships exactly the languages the compiler counts as built in', () => {
    // CHROME_STRING_LOCALES is mirrored by tour_spec.py, which warns chrome-strings-missing for any other.
    expect(Object.keys(DEFAULT_TOUR_STRINGS).sort()).toEqual([...CHROME_STRING_LOCALES].sort());
  });

  it('has a generic instruction for every advance kind', () => {
    for (const kind of ADVANCE_KINDS) expect(instructionFor(kind, DEFAULT_TOUR_STRINGS.en!)).toMatch(/\w/);
  });
});

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}
const contrast = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
};

describe('default colours (WCAG AA)', () => {
  it('text on the bubble and on the accent is at least 4.5:1', () => {
    expect(contrast(TOUR_DEFAULTS.bubbleFg, TOUR_DEFAULTS.bubbleBg)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(TOUR_DEFAULTS.muted, TOUR_DEFAULTS.bubbleBg)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(TOUR_DEFAULTS.accentFg, TOUR_DEFAULTS.accent)).toBeGreaterThanOrEqual(4.5);
  });
  it('the focus ring and the spotlight ring are at least 3:1 where they are drawn', () => {
    expect(contrast(TOUR_DEFAULTS.focus, TOUR_DEFAULTS.bubbleBg)).toBeGreaterThanOrEqual(3);
    expect(contrast(TOUR_DEFAULTS.accent, '#ffffff')).toBeGreaterThanOrEqual(3);
  });
});

describe('value signatures', () => {
  const html = (markup: string) => {
    document.body.innerHTML = markup;
    return document.body.firstElementChild!;
  };

  it('sees native selects, checkboxes through a label, and ARIA switches', () => {
    const sel = html('<select><option value="a">A</option><option value="b">B</option></select>') as HTMLSelectElement;
    const before = valueSignature(sel);
    sel.value = 'b';
    expect(valueSignature(sel)).not.toBe(before);

    const label = html('<label><input type="checkbox"> Remember</label>');
    const sig = valueSignature(label);
    (label.querySelector('input') as HTMLInputElement).checked = true;
    expect(valueSignature(label)).not.toBe(sig);

    const sw = html('<button role="switch" aria-checked="false">On</button>');
    const s0 = valueSignature(sw);
    sw.setAttribute('aria-checked', 'true');
    expect(valueSignature(sw)).not.toBe(s0);
  });

  it('sees a component that only changes its label (programmatic value)', () => {
    const combo = html('<div role="combobox" aria-expanded="false"><span class="label">Choose</span></div>');
    const s0 = valueSignature(combo);
    combo.querySelector('.label')!.textContent = 'Acme Corp';
    expect(valueSignature(combo)).not.toBe(s0);
    expect(isComboboxLike(combo)).toBe(true);
  });

  it('reports an open popup inside the target', () => {
    const combo = html('<div><button aria-expanded="true">x</button></div>');
    expect(isExpanded(combo)).toBe(true);
  });

  it('reads the field value of a wrapper', () => {
    const wrap = html('<div><label>Title</label><input value="Maintenance"></div>');
    expect(readFieldValue(wrap)).toBe('Maintenance');
    expect(readFieldValue(html('<div contenteditable="true">Hi</div>'))).toBe('Hi');
    expect(readFieldValue(html('<div></div>'))).toBe('');
  });
});

describe('direction', () => {
  it.each([
    ['ar', 'rtl'],
    ['ar-EG', 'rtl'],
    ['iw', 'rtl'],
    ['ckb', 'rtl'],
    ['az-Arab', 'rtl'],
    ['pa-Arab', 'rtl'],
    ['he_IL', 'rtl'],
    ['arn', 'ltr'],
    ['sd-Deva', 'ltr'],
    ['ckb-Latn', 'ltr'],
    ['fr_BE', 'ltr'],
    ['en', 'ltr'],
  ])('textDir(%s) -> %s', (tag, dir) => expect(textDir(tag)).toBe(dir));

  it('lays the bubble out with logical properties, so it mirrors under dir=rtl', () => {
    expect(tourStyles.cssText).not.toMatch(/(?:margin|padding)-(?:left|right)\s*:/);
    expect(tourStyles.cssText).toMatch(/inset-inline-end:\s*16px/);
  });
});
