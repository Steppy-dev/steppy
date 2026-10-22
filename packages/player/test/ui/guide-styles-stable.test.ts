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

const RECORDED = 'dc30df8cfc8b30bae08c2c0102e30c6f28ca6f1c5e8e086ffbc20ef8bcd4d2b5';

describe('guide base styles', () => {
  it('are byte-identical to the pre-token stylesheet', () => {
    const digest = createHash('sha256').update(baseStyles.cssText, 'utf8').digest('hex');
    expect(baseStyles.cssText.length).toBe(8163);
    expect(digest).toBe(RECORDED);
  });
});
