/**
 * Tests for the inline-markdown renderer used by the visible player body text.
 * Renders the returned parts into a happy-dom container and inspects the DOM
 * nodes (Lit inserts marker comments around dynamic children, so we assert on
 * the resolved element tree, not raw innerHTML).
 */

import { describe, expect, it } from 'vitest';
import { render } from 'lit';

import { renderInlineMarkdown } from '../src/ui/markdown.js';

function mount(src: string): HTMLElement {
  const el = document.createElement('div');
  render(renderInlineMarkdown(src), el);
  return el;
}

describe('renderInlineMarkdown', () => {
  it('renders **bold** as <strong>', () => {
    const el = mount('Click the **Create** button');
    expect(el.querySelector('strong')?.textContent).toBe('Create');
    expect(el.textContent).toBe('Click the Create button');
    expect(el.textContent).not.toContain('**');
  });

  it('renders __bold__ as <strong>', () => {
    expect(mount('__Save__').querySelector('strong')?.textContent).toBe('Save');
  });

  it('renders *italic* as <em>', () => {
    const el = mount('an *important* note');
    expect(el.querySelector('em')?.textContent).toBe('important');
    expect(el.textContent).toBe('an important note');
  });

  it('renders `code` as <code>', () => {
    expect(mount('run `steppy build`').querySelector('code')?.textContent).toBe('steppy build');
  });

  it('passes plain text through unchanged', () => {
    const el = mount('just plain text');
    expect(el.querySelector('strong, em, code')).toBeNull();
    expect(el.textContent).toBe('just plain text');
  });

  it('leaves a stray single asterisk literal', () => {
    const el = mount('2 * 3 = 6');
    expect(el.querySelector('em')).toBeNull();
    expect(el.textContent).toBe('2 * 3 = 6');
  });

  it('escapes HTML in literal text (no unsafeHTML)', () => {
    const el = mount('a <b>tag</b> and **bold**');
    // The literal angle-brackets are text, not a real <b> element.
    expect(el.querySelector('b')).toBeNull();
    expect(el.textContent).toContain('<b>tag</b>');
    expect(el.querySelector('strong')?.textContent).toBe('bold');
  });

  it('handles multiple emphases in one string', () => {
    const el = mount('**a** and *b* and `c`');
    expect(el.querySelector('strong')?.textContent).toBe('a');
    expect(el.querySelector('em')?.textContent).toBe('b');
    expect(el.querySelector('code')?.textContent).toBe('c');
  });

  it('nests emphasis (bold containing italic)', () => {
    const el = mount('**bold _italic_ end**');
    const strong = el.querySelector('strong');
    expect(strong).toBeTruthy();
    expect(strong!.querySelector('em')?.textContent).toBe('italic');
    expect(strong!.textContent).toBe('bold italic end');
  });

  it('renders a hard line break as <br>', () => {
    const el = mount('line one\nline two');
    expect(el.querySelectorAll('br').length).toBe(1);
    expect(el.textContent).toBe('line oneline two');
  });

  it('renders [text](url) as a new-tab link', () => {
    const a = mount('see the [Docs](https://x.io) page').querySelector('a');
    expect(a?.getAttribute('href')).toBe('https://x.io');
    expect(a?.getAttribute('target')).toBe('_blank');
    expect(a?.getAttribute('rel')).toBe('noopener noreferrer');
    expect(a?.textContent).toBe('Docs');
  });

  it('parses emphasis inside a link label', () => {
    const a = mount('[**Docs**](/guide)').querySelector('a');
    expect(a?.getAttribute('href')).toBe('/guide');
    expect(a?.querySelector('strong')?.textContent).toBe('Docs');
  });

  it('supports relative and anchor links', () => {
    expect(mount('[Home](/home)').querySelector('a')?.getAttribute('href')).toBe('/home');
    expect(mount('[Sec](#sec)').querySelector('a')?.getAttribute('href')).toBe('#sec');
  });

  it('leaves an unsafe-scheme link literal (no <a>)', () => {
    const el = mount('[x](javascript:alert(1))');
    expect(el.querySelector('a')).toBeNull();
    expect(el.textContent).toContain('[x](javascript:alert(1))');
  });

  it('autolinks a bare URL with its url as the visible text', () => {
    const a = mount('see https://x.io now').querySelector('a');
    expect(a?.getAttribute('href')).toBe('https://x.io');
    expect(a?.textContent).toBe('https://x.io');
  });

  it('strips trailing punctuation from an autolinked URL', () => {
    const el = mount('go to https://x.io.');
    expect(el.querySelector('a')?.getAttribute('href')).toBe('https://x.io');
    expect(el.textContent).toBe('go to https://x.io.');
  });

  it('autolinks a www. URL by prefixing https://', () => {
    const a = mount('visit www.x.io today').querySelector('a');
    expect(a?.getAttribute('href')).toBe('https://www.x.io');
    expect(a?.textContent).toBe('www.x.io');
  });

  it('autolinks an email as a mailto link', () => {
    const a = mount('mail me at a@b.io please').querySelector('a');
    expect(a?.getAttribute('href')).toBe('mailto:a@b.io');
    expect(a?.textContent).toBe('a@b.io');
  });
});
