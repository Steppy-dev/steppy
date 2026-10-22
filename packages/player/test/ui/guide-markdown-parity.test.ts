/**
 * The shared renderer with the guide's default policy produces the same DOM
 * as the pre-extraction renderer (``legacy-markdown.ts``), for a set of tricky
 * inputs plus every ``guide-text`` value in the repo's scenarios. The only
 * intended difference: protocol-relative ``//host`` and ``/\host`` links now
 * stay literal.
 */

import { readFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';

import { render } from 'lit';
import { describe, expect, it } from 'vitest';

import { renderInlineMarkdown } from '../../src/ui/markdown.js';
import { legacyRenderInlineMarkdown } from './legacy-markdown.js';

const REPO = resolve(__dirname, '../../../..');

const INPUTS = [
  'plain text',
  'Click the **Create** button',
  '__Save__ then _undo_',
  'an *important* note',
  'run `steppy build` now',
  '`**not bold**`',
  'see [the docs](https://example.com/docs)',
  '[**bold label**](https://example.com)',
  '[mail us](mailto:team@example.com)',
  '[home](/)',
  '[section](/invoices/new)',
  '[anchor](#top)',
  '[bad](javascript:alert(1))',
  '[bad](data:text/html,x)',
  '[spaced](https://example.com/a b)',
  '[empty]()',
  'nested [*em* label](https://e.com) and **bold *em* bold**',
  'visit https://example.com/path, then stop.',
  'visit www.example.com!',
  'write to someone@example.com.',
  'line one\nline two\n\nline four',
  '**unclosed bold',
  '*a* *b* *c*',
  'a ** b ** c',
  'snake_case_name and __init__',
  'price: 5 * 3 = 15',
  '<b>not html</b> & entities &amp;',
  '[a](https://x.com) [b](https://y.com)',
  '[[double]](https://x.com)',
  '[label](https://x.com/(paren))',
  '`code` [link](/x) **bold** *em* https://u.com e@x.io',
  '*[em link](https://x.com)*',
  '[x](https://a.com)trailing',
  'emoji ✓ and accents éàü',
  '[bad](javascript:x) then **bold**',
  '[**x**](javascript:y)',
  'http://insecure.example.com/x?y=1&z=2',
  'tab\there',
  '',
  '   ',
];

const CHANGED = ['[proto](//evil.example/x)', '[proto](/\\evil.example/x)', 'go [here](//a.b) or [there](/c)'];

function dom(nodes: unknown): string {
  const el = document.createElement('div');
  render(nodes, el);
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_COMMENT);
  const comments: Node[] = [];
  while (walker.nextNode()) comments.push(walker.currentNode);
  comments.forEach((c) => c.parentNode?.removeChild(c));
  el.normalize();
  return el.innerHTML;
}

function unquote(raw: string): string {
  const v = raw.trim();
  if (v.length >= 2 && v.startsWith('"') && v.endsWith('"')) return v.slice(1, -1).replace(/\\"/g, '"').replace(/\\n/g, '\n');
  if (v.length >= 2 && v.startsWith("'") && v.endsWith("'")) return v.slice(1, -1).replace(/''/g, "'");
  return v;
}

/** Every value inside a ``guide-text`` block (inline, per-locale, or nested title/text). */
function guideTexts(file: string): string[] {
  const out: string[] = [];
  const lines = readFileSync(file, 'utf8').split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const head = /^- guide-text:\s*(.*)$/.exec(lines[i]!);
    if (!head) continue;
    if (head[1]) out.push(unquote(head[1]));
    for (let j = i + 1; j < lines.length && /^\s+\S/.test(lines[j]!); j++) {
      const kv = /^\s+[\w-]+:\s*(.+)$/.exec(lines[j]!);
      if (kv && kv[1] !== '|' && kv[1] !== '>') out.push(unquote(kv[1]!));
    }
  }
  return out;
}

function scenarioFiles(dir: string): string[] {
  try {
    return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
      entry.isDirectory() ? scenarioFiles(join(dir, entry.name)) : entry.name.endsWith('.md') ? [join(dir, entry.name)] : [],
    );
  } catch {
    return [];
  }
}

const CORPUS = [
  ...scenarioFiles(join(REPO, 'scenarios')),
  ...scenarioFiles(join(REPO, 'templates', 'init', 'scenarios')),
  ...scenarioFiles(join(REPO, 'fixtures')),
].flatMap(guideTexts);

describe('guide markdown parity with the pre-extraction renderer', () => {
  it('finds guide-text in the repo scenarios', () => {
    expect(CORPUS.length).toBeGreaterThan(20);
  });

  it.each(INPUTS)('renders %j identically', (input) => {
    expect(dom(renderInlineMarkdown(input))).toBe(dom(legacyRenderInlineMarkdown(input)));
  });

  it('renders every repo guide-text identically', () => {
    const differing = CORPUS.filter((text) => dom(renderInlineMarkdown(text)) !== dom(legacyRenderInlineMarkdown(text)));
    expect(differing).toEqual([]);
  });

  it.each(CHANGED)('now leaves the protocol-relative link in %j literal', (input) => {
    const now = document.createElement('div');
    render(renderInlineMarkdown(input), now);
    expect([...now.querySelectorAll('a')].map((a) => a.getAttribute('href'))).not.toContainEqual(
      expect.stringMatching(/^\/[/\\]/),
    );
    const legacy = document.createElement('div');
    render(legacyRenderInlineMarkdown(input), legacy);
    expect(legacy.querySelectorAll('a').length).toBeGreaterThan(now.querySelectorAll('a').length);
  });
});
