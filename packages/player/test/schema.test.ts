import { describe, expect, it } from 'vitest';
import { parseManifest } from '../src/manifest/parse.js';
import { GuideManifestSchema, SUPPORTED_MAJOR_VERSION } from '../src/manifest/schema.js';
import { makeManifest, withLayoutContent } from './fixtures.js';

describe('parseManifest — happy path', () => {
  it('accepts a minimal valid manifest', () => {
    const result = parseManifest(makeManifest());
    expect(result.ok).toBe(true);
  });

  it('accepts a manifest passed as a JSON string', () => {
    const json = JSON.stringify(makeManifest());
    const result = parseManifest(json);
    expect(result.ok).toBe(true);
  });

  it('accepts type-multi cursor for fill-form steps', () => {
    const m = makeManifest({
      steps: [
        {
          id: 'step-1',
          index: 1,
          action: 'fill-form',
          interactive: true,
          screenshotWhen: 'after',
          waitAfter: 0,
          text: { fr: 'Remplir', nl: 'Vullen' },
          captures: {
            fr: {
              desktop: {
                frames: [
                  {
                    screenshot: './screenshots/fr/desktop/step-01-a.png',
                    captureSize: { width: 1440, height: 900 },
                    cursor: {
                      kind: 'type-multi',
                      targets: [
                        {
                          selector: '[name=first]',
                          value: 'Jean',
                          bbox: { x: 10, y: 20, width: 200, height: 30 },
                        },
                        {
                          selector: '[name=last]',
                          value: 'Dupont',
                          bbox: { x: 10, y: 60, width: 200, height: 30 },
                        },
                      ],
                    },
                  },
                ],
              },
              mobile: {
                frames: [
                  {
                    screenshot: './screenshots/fr/mobile/step-01-a.png',
                    captureSize: { width: 390, height: 844 },
                    cursor: null,
                  },
                ],
              },
            },
            nl: {
              desktop: {
                frames: [
                  {
                    screenshot: './screenshots/nl/desktop/step-01-a.png',
                    captureSize: { width: 1440, height: 900 },
                    cursor: null,
                  },
                ],
              },
              mobile: {
                frames: [
                  {
                    screenshot: './screenshots/nl/mobile/step-01-a.png',
                    captureSize: { width: 390, height: 844 },
                    cursor: null,
                  },
                ],
              },
            },
          },
        },
      ],
    });
    const result = parseManifest(m);
    expect(result.ok).toBe(true);
  });

  it('accepts a layout-less manifest (layouts: [], defaultLayout: null)', () => {
    // For layout-less manifests the build emits a synthetic "_" layout key
    // (see schema.ts superRefine). The captures map must use "_" as the layout.
    const m = makeManifest({
      layouts: [],
      defaultLayout: null,
      steps: [
        {
          id: 'step-1',
          index: 1,
          action: 'click',
          interactive: true,
          screenshotWhen: 'after',
          waitAfter: 0,
          text: { fr: 'OK', nl: 'OK' },
          captures: {
            fr: {
              _: {
                frames: [
                  {
                    screenshot: './screenshots/fr/step-01.png',
                    captureSize: { width: 1280, height: 720 },
                    cursor: {
                      kind: 'click',
                      target: { bbox: { x: 0, y: 0, width: 10, height: 10 } },
                    },
                  },
                ],
              },
            },
            nl: {
              _: {
                frames: [
                  {
                    screenshot: './screenshots/nl/step-01.png',
                    captureSize: { width: 1280, height: 720 },
                    cursor: null,
                  },
                ],
              },
            },
          },
        },
      ],
    });
    const result = parseManifest(m);
    expect(result.ok).toBe(true);
  });
});

describe('parseManifest — version handling', () => {
  it('rejects a missing version', () => {
    const m = makeManifest();
    delete (m as Partial<typeof m>).version;
    const result = parseManifest(m);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.code).toBe('version_missing');
  });

  it('rejects a malformed version', () => {
    const result = parseManifest(makeManifest({ version: 'one-point-zero' }));
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.code).toBe('version_missing');
  });

  it('rejects an unsupported major version', () => {
    const future = `${SUPPORTED_MAJOR_VERSION + 1}.0`;
    const result = parseManifest(makeManifest({ version: future }));
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.code).toBe('version_unsupported');
  });

  it('accepts a minor version bump', () => {
    const result = parseManifest(makeManifest({ version: `${SUPPORTED_MAJOR_VERSION}.99` }));
    expect(result.ok).toBe(true);
  });
});

describe('parseManifest — JSON parsing', () => {
  it('rejects invalid JSON', () => {
    const result = parseManifest('{not valid json}');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.code).toBe('invalid_json');
  });
});

describe('parseManifest — cross-field validation', () => {
  it('rejects defaultLocale not in locales', () => {
    const result = parseManifest(makeManifest({ defaultLocale: 'de' }));
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.code).toBe('schema_invalid');
      expect(result.error.issues?.some((i) => i.path === 'defaultLocale')).toBe(true);
    }
  });

  it('rejects defaultLayout not in layouts (when non-empty)', () => {
    const result = parseManifest(makeManifest({ defaultLayout: 'tablet' }));
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.issues?.some((i) => i.path === 'defaultLayout')).toBe(true);
    }
  });

  it('rejects defaultLayout set when layouts is empty', () => {
    const result = parseManifest(makeManifest({ layouts: [], defaultLayout: 'desktop' }));
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.issues?.some((i) => i.path === 'defaultLayout')).toBe(true);
    }
  });

  it('rejects defaultLayout null when layouts is non-empty', () => {
    const result = parseManifest(makeManifest({ defaultLayout: null }));
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.issues?.some((i) => i.path === 'defaultLayout')).toBe(true);
    }
  });

  it('rejects missing title for a declared locale', () => {
    const result = parseManifest(makeManifest({ title: { fr: 'OK' } }));
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.issues?.some((i) => i.path === 'title.nl')).toBe(true);
    }
  });

  it('rejects a step missing captures for one (locale, layout)', () => {
    const m = makeManifest();
    delete m.steps[0]!.captures.nl!.mobile;
    const result = parseManifest(m);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(
        result.error.issues?.some((i) => i.path === 'steps.0.captures.nl.mobile'),
      ).toBe(true);
    }
  });

  it('rejects a step missing text for a declared locale', () => {
    const m = makeManifest();
    m.steps[0]!.text = { fr: 'only-fr' };
    const result = parseManifest(m);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.issues?.some((i) => i.path === 'steps.0.text.nl')).toBe(true);
    }
  });

  it('rejects captures keyed by an undeclared locale', () => {
    const m = makeManifest();
    m.steps[0]!.captures.de = m.steps[0]!.captures.fr!;
    const result = parseManifest(m);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(
        result.error.issues?.some((i) => i.path === 'steps.0.captures.de'),
      ).toBe(true);
    }
  });

  it('rejects captures keyed by an undeclared layout', () => {
    const m = makeManifest();
    m.steps[0]!.captures.fr!.tablet = m.steps[0]!.captures.fr!.desktop!;
    const result = parseManifest(m);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(
        result.error.issues?.some((i) => i.path === 'steps.0.captures.fr.tablet'),
      ).toBe(true);
    }
  });

  it('rejects title keyed by an undeclared locale', () => {
    const result = parseManifest(
      makeManifest({ title: { fr: 'a', nl: 'b', de: 'c' } }),
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.issues?.some((i) => i.path === 'title.de')).toBe(true);
    }
  });
});

describe('parseManifest — strict mode', () => {
  it('rejects unknown top-level keys', () => {
    const m = { ...makeManifest(), unexpected: 'nope' };
    const result = parseManifest(m);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.code).toBe('schema_invalid');
  });

  it('rejects unknown keys inside a cursor target', () => {
    const m = makeManifest();
    const cursor = m.steps[0]!.captures.fr!.desktop!.frames[0]!.cursor;
    expect(cursor && cursor.kind === 'click').toBe(true);
    // Inject an unknown property by going through a parsed-JSON detour so the
    // type checker doesn't reject the test setup itself.
    const cloned = JSON.parse(JSON.stringify(m));
    cloned.steps[0].captures.fr.desktop.frames[0].cursor.target.unknown = 1;
    const result = parseManifest(cloned);
    expect(result.ok).toBe(false);
  });
});

describe('parseManifest — cursor variants', () => {
  it('accepts a hover cursor', () => {
    const m = makeManifest();
    m.steps[0]!.captures.fr!.desktop!.frames[0]!.cursor = {
      kind: 'hover',
      target: { bbox: { x: 0, y: 0, width: 5, height: 5 } },
    };
    m.steps[0]!.captures.fr!.mobile!.frames[0]!.cursor = null;
    m.steps[0]!.captures.nl!.desktop!.frames[0]!.cursor = null;
    m.steps[0]!.captures.nl!.mobile!.frames[0]!.cursor = null;
    const result = parseManifest(m);
    expect(result.ok).toBe(true);
  });

  it('accepts a type cursor with a value', () => {
    const m = makeManifest();
    m.steps[0]!.captures.fr!.desktop!.frames[0]!.cursor = {
      kind: 'type',
      target: {
        selector: '[name=search]',
        bbox: { x: 10, y: 10, width: 100, height: 24 },
      },
      value: 'notebook',
    };
    m.steps[0]!.action = 'fill';
    const result = parseManifest(m);
    expect(result.ok).toBe(true);
  });

  it('rejects type-multi with empty targets array', () => {
    const m = makeManifest();
    m.steps[0]!.captures.fr!.desktop!.frames[0]!.cursor = {
      kind: 'type-multi',
      targets: [],
    };
    const result = parseManifest(m);
    expect(result.ok).toBe(false);
  });
});

describe('Schema directly (Zod parse)', () => {
  it('infers GuideManifest type and round-trips', () => {
    const parsed = GuideManifestSchema.parse(makeManifest());
    expect(parsed.slug).toBe('invoices');
    expect(parsed.steps).toHaveLength(1);
  });
});

describe('cursor.mode field', () => {
  it.each(['arrow', 'hotspot', 'off'])('accepts mode=%s', (mode) => {
    const m = makeManifest();
    m.cursor = { ...m.cursor, mode: mode as 'arrow' | 'hotspot' | 'off' };
    const result = parseManifest(m);
    expect(result.ok).toBe(true);
  });

  it('accepts omitted mode (back-compat for existing manifests)', () => {
    const m = makeManifest();
    // Default fixture has no mode set.
    expect((m.cursor as Record<string, unknown>).mode).toBeUndefined();
    const result = parseManifest(m);
    expect(result.ok).toBe(true);
  });

  it('rejects an unknown mode value', () => {
    const m = makeManifest();
    (m.cursor as Record<string, unknown>).mode = 'sparkly';
    const result = parseManifest(m);
    expect(result.ok).toBe(false);
  });
});

describe('defaultTemplate field', () => {
  it.each(['sidebar', 'overlay', 'inline'])('accepts defaultTemplate=%s', (t) => {
    const m = { ...makeManifest(), defaultTemplate: t };
    const result = parseManifest(m);
    expect(result.ok).toBe(true);
  });

  it('accepts an omitted defaultTemplate (back-compat)', () => {
    const m = makeManifest();
    expect((m as Record<string, unknown>).defaultTemplate).toBeUndefined();
    expect(parseManifest(m).ok).toBe(true);
  });

  it('rejects an unknown defaultTemplate value', () => {
    const m = { ...makeManifest(), defaultTemplate: 'popover' };
    const result = parseManifest(m);
    expect(result.ok).toBe(false);
  });
});

describe('step.title field', () => {
  it('accepts a per-step title for declared locales', () => {
    const m = makeManifest();
    m.steps[0]!.title = { fr: 'Titre', nl: 'Titel' };
    expect(parseManifest(m).ok).toBe(true);
  });

  it('accepts a null per-locale title (locale lacking guide-text)', () => {
    const m = makeManifest();
    m.steps[0]!.title = { fr: 'Titre', nl: null };
    expect(parseManifest(m).ok).toBe(true);
  });

  it('accepts an omitted title (back-compat)', () => {
    const m = makeManifest();
    expect((m.steps[0]! as Record<string, unknown>).title).toBeUndefined();
    expect(parseManifest(m).ok).toBe(true);
  });

  it('rejects a title keyed by an undeclared locale', () => {
    const m = makeManifest();
    m.steps[0]!.title = { fr: 'Titre', nl: 'Titel', es: 'Título' };
    expect(parseManifest(m).ok).toBe(false);
  });
});

describe('capture.content (per-layout guide text)', () => {
  const override = { title: 'Ouvrir le menu', text: 'Touchez la carte', subtitle: null };

  it('accepts a full override on one (locale, layout) cell', () => {
    const m = withLayoutContent(makeManifest(), 0, 'fr', 'mobile', override);
    expect(parseManifest(m).ok).toBe(true);
  });

  it('accepts null title and subtitle', () => {
    const m = withLayoutContent(makeManifest(), 0, 'fr', 'mobile', {
      title: null,
      text: 'Touchez la carte',
      subtitle: null,
    });
    expect(parseManifest(m).ok).toBe(true);
  });

  it('accepts an omitted content (back-compat with 1.0 manifests)', () => {
    const m = makeManifest();
    expect(m.steps[0]!.captures.fr!.mobile!.content).toBeUndefined();
    expect(parseManifest(m).ok).toBe(true);
  });

  it('accepts an override on some locales but not others', () => {
    // The emitter compares per (locale, layout) cell, so a twin translated
    // identically in `nl` overrides only `fr`. Presence is not uniform.
    const m = withLayoutContent(makeManifest(), 0, 'fr', 'mobile', override);
    expect(m.steps[0]!.captures.nl!.mobile!.content).toBeUndefined();
    expect(parseManifest(m).ok).toBe(true);
  });

  it('rejects a partial override', () => {
    // Atomic on purpose: a text-only override would leave the sidebar showing
    // one layout's heading above another layout's body.
    const m = makeManifest();
    (m.steps[0]!.captures.fr!.mobile! as Record<string, unknown>).content = {
      text: 'Touchez la carte',
    };
    expect(parseManifest(m).ok).toBe(false);
  });

  it('rejects an unknown key inside content', () => {
    const m = withLayoutContent(makeManifest(), 0, 'fr', 'mobile', override);
    (m.steps[0]!.captures.fr!.mobile!.content as Record<string, unknown>).heading = 'x';
    expect(parseManifest(m).ok).toBe(false);
  });

  it('rejects an empty content.text', () => {
    const m = withLayoutContent(makeManifest(), 0, 'fr', 'mobile', {
      title: 'T',
      text: '',
      subtitle: null,
    });
    const result = parseManifest(m);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(JSON.stringify(result.error.issues)).toContain('empty content.text');
    }
  });
});
