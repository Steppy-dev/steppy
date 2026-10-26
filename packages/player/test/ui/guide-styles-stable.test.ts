/**
 * The guide's stylesheet is assembled from ``ui/tokens.ts`` through
 * ``unsafeCSS``. Lit concatenates those values verbatim, so the result must be
 * the exact stylesheet the literal source produced before the extraction —
 * recorded here as a hash. A legitimate style change updates the hash in the
 * same commit (and re-vendors the player and video bundles).
 */

import { createHash } from 'node:crypto';

import { describe, expect, it } from 'vitest';

import { baseStyles } from '../../src/templates/base-styles.js';

const RECORDED = '036bf5face9c9f1d469ca95930c1e587864cc53952acb4a2e70e6517226afa96';

describe('guide base styles', () => {
  it('are byte-identical to the pre-token stylesheet', () => {
    const digest = createHash('sha256').update(baseStyles.cssText, 'utf8').digest('hex');
    expect(baseStyles.cssText.length).toBe(9396);
    expect(digest).toBe(RECORDED);
  });
});

describe('inline markup in step text', () => {
  it('reads every --steppy-<element>-* property textStyles can set, with a browser-default fallback', () => {
    const css = baseStyles.cssText;
    for (const [prop, fallback] of [
      ['--steppy-strong-weight', 'bolder'],
      ['--steppy-strong-color', 'inherit'],
      ['--steppy-em-style', 'italic'],
      ['--steppy-em-color', 'inherit'],
      ['--steppy-code-font', 'var(--steppy-font-mono)'],
      ['--steppy-code-size', 'inherit'],
      ['--steppy-code-color', 'inherit'],
      ['--steppy-code-bg', 'transparent'],
      ['--steppy-code-padding', '0'],
      ['--steppy-code-radius', '0'],
      ['--steppy-link-color', 'LinkText'],
      ['--steppy-link-decoration', 'underline'],
    ]) {
      expect(css, prop).toContain(`var(${prop}, ${fallback})`);
    }
  });
});
