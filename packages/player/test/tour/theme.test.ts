/** The theme surface: the key table, the stylesheet that reads it, and the host's ``theme`` / ``appearance``. */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import '../../src/tour/index.js';
import { SteppyTourHost } from '../../src/tour/host.js';
import { tourStyles } from '../../src/tour/styles.js';
import { TOUR_THEME_KEYS, privateProperty, themeDeclarations, themeProperty } from '../../src/tour/theme.js';
import { TOUR_HOST_TAG } from '../../src/ui/names.js';

const CSS = tourStyles.cssText;

describe('theme keys', () => {
  it('map to kebab-case public and private properties', () => {
    expect(themeProperty('accentFg')).toBe('--steppy-tour-accent-fg');
    expect(themeProperty('ringWidth')).toBe('--steppy-tour-ring-width');
    expect(privateProperty('bubbleBg')).toBe('--_pw-bubble-bg');
  });

  it('every key is read by the stylesheet, and the stylesheet reads no other public tour property', () => {
    for (const key of TOUR_THEME_KEYS) {
      expect(CSS, key).toContain(`var(${themeProperty(key)},`);
      expect(CSS, key).toContain(`var(${privateProperty(key)})`);
    }
    const read = new Set([...CSS.matchAll(/--steppy-tour-[a-z-]+/g)].map((m) => m[0]));
    expect([...read].sort()).toEqual(TOUR_THEME_KEYS.map(themeProperty).sort());
  });

  it('keeps the shared fallbacks', () => {
    expect(CSS).toContain('var(--steppy-tour-bubble-bg, var(--steppy-tooltip-bg,');
    expect(CSS).toContain('var(--steppy-tour-font-family, var(--steppy-font-family, inherit))');
  });

  it('inline-markup tokens fall back to the guide properties, then to the rendering they replace', () => {
    expect(CSS).toContain('--_pw-strong-weight: var(--steppy-tour-strong-weight, var(--steppy-strong-weight, bolder))');
    expect(CSS).toContain('--_pw-em-style: var(--steppy-tour-em-style, var(--steppy-em-style, italic))');
    expect(CSS).toContain('--_pw-link: var(--steppy-tour-link, var(--steppy-link-color, var(--_pw-bubble-fg)))');
    expect(CSS).toContain('--_pw-code-size: var(--steppy-tour-code-size, var(--steppy-code-size, 0.92em))');
    expect(CSS).toContain('--_pw-code-color: var(--steppy-tour-code-color, var(--steppy-code-color, var(--_pw-bubble-fg)))');
    // Only text inside the step body is restyled.
    expect(CSS).toMatch(/\.body strong\s*\{/);
    expect(CSS).not.toMatch(/(^|\n)\s*code\s*\{/);
  });

  it('the light and auto appearances swap only the defaults', () => {
    const light = CSS.slice(CSS.indexOf(":host([appearance='light'])"), CSS.indexOf('@media (prefers-color-scheme: light)'));
    expect(light).toContain('--_pw-bubble-bg: var(--steppy-tour-bubble-bg, var(--steppy-tooltip-bg, #ffffff))');
    expect(light).not.toContain('--_pw-width');
    expect(CSS).toMatch(/@media \(prefers-color-scheme: light\)\s*\{\s*:host\(\[appearance='auto'\]\)/);
  });

  it('declarations keep known, non-empty string values only', () => {
    const theme = { accent: ' #0b5cad ', width: '360px', bogus: 'red', muted: '', radius: 4 } as never;
    expect([...themeDeclarations(theme)]).toEqual([
      ['--steppy-tour-accent', '#0b5cad'],
      ['--steppy-tour-width', '360px'],
    ]);
    expect(themeDeclarations(undefined).size).toBe(0);
    expect(themeDeclarations(null).size).toBe(0);
  });
});

describe('<steppy-tour-host> theme and appearance', () => {
  let host: SteppyTourHost;
  beforeEach(() => {
    host = document.createElement(TOUR_HOST_TAG) as SteppyTourHost;
    document.body.append(host);
  });
  afterEach(() => host.remove());

  it('writes the theme inline, replaces it, and removes what a new theme drops', async () => {
    host.theme = { accent: '#0b5cad', width: '360px' };
    await host.updateComplete;
    expect(host.style.getPropertyValue('--steppy-tour-accent')).toBe('#0b5cad');
    expect(host.style.getPropertyValue('--steppy-tour-width')).toBe('360px');

    host.theme = { accent: '#123456' };
    await host.updateComplete;
    expect(host.style.getPropertyValue('--steppy-tour-accent')).toBe('#123456');
    expect(host.style.getPropertyValue('--steppy-tour-width')).toBe('');

    host.theme = undefined;
    await host.updateComplete;
    expect(host.style.getPropertyValue('--steppy-tour-accent')).toBe('');
  });

  it('leaves inline properties it did not write alone', async () => {
    host.style.setProperty('--steppy-tour-radius', '12px');
    host.theme = { accent: '#0b5cad' };
    await host.updateComplete;
    host.theme = {};
    await host.updateComplete;
    expect(host.style.getPropertyValue('--steppy-tour-radius')).toBe('12px');
    expect(host.style.getPropertyValue('--_pw-z')).not.toBe('');
  });

  it('reflects appearance, and reads it from the attribute', async () => {
    await host.updateComplete;
    expect(host.getAttribute('appearance')).toBe('dark');
    host.appearance = 'light';
    await host.updateComplete;
    expect(host.getAttribute('appearance')).toBe('light');
    host.setAttribute('appearance', 'auto');
    expect(host.appearance).toBe('auto');
  });
});
