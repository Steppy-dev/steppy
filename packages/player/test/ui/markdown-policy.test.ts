/**
 * ``renderInlineMarkdown``'s link policy seam: what the guide's default policy
 * refuses, and how a caller-supplied policy (the tour overlay's) turns links
 * into same-tab routed links or plain text.
 */

import { render } from 'lit';
import { describe, expect, it, vi } from 'vitest';

import { GUIDE_LINK_POLICY, renderInlineMarkdown, type LinkPolicy } from '../../src/ui/markdown.js';

function mount(src: string, policy?: LinkPolicy): HTMLElement {
  const el = document.createElement('div');
  render(renderInlineMarkdown(src, policy), el);
  return el;
}

describe('the guide link policy', () => {
  it.each([
    ['https://example.com', true],
    ['http://example.com', true],
    ['mailto:a@example.com', true],
    ['/invoices', true],
    ['/', true],
    ['#top', true],
    ['//evil.example', false],
    ['/\\evil.example', false],
    ['javascript:alert(1)', false],
    ['data:text/html,x', false],
    ['ftp://example.com', false],
    ['invoices/1', false],
  ])('%s -> link %s', (url, linked) => {
    expect(GUIDE_LINK_POLICY.link(url) !== null).toBe(linked);
  });

  it('keeps external links in a new tab without an opener', () => {
    const a = mount('[docs](https://example.com)').querySelector('a')!;
    expect(a.getAttribute('target')).toBe('_blank');
    expect(a.getAttribute('rel')).toBe('noopener noreferrer');
  });
});

describe('a caller-supplied policy', () => {
  const onActivate = vi.fn((event: MouseEvent) => event.preventDefault());
  const routed: LinkPolicy = {
    autolink: false,
    link: (url) => (url.startsWith('/') && !url.startsWith('//') ? { kind: 'internal', href: url, onActivate } : null),
  };

  it('renders internal links in the same tab and hands clicks to the host', () => {
    const el = mount('open [the list](/invoices) now', routed);
    const a = el.querySelector('a')!;
    expect(a.getAttribute('href')).toBe('/invoices');
    expect(a.hasAttribute('target')).toBe(false);
    a.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    expect(onActivate).toHaveBeenCalledTimes(1);
  });

  it('leaves refused links literal, with their label unparsed', () => {
    const el = mount('see [**docs**](https://example.com)', routed);
    expect(el.querySelector('a')).toBeNull();
    expect(el.textContent).toBe('see [docs](https://example.com)');
    expect(el.querySelector('strong')?.textContent).toBe('docs');
  });

  it('does not autolink bare URLs or e-mail addresses', () => {
    const el = mount('visit https://example.com/**x** or mail a@example.com', routed);
    expect(el.querySelector('a')).toBeNull();
    expect(el.querySelector('strong')).toBeNull();
    expect(el.textContent).toBe('visit https://example.com/**x** or mail a@example.com');
  });

  it('applies the policy to nested labels too', () => {
    const el = mount('**see [the list](/invoices)**', routed);
    expect(el.querySelector('strong a')?.getAttribute('href')).toBe('/invoices');
  });

  it('never renders markup from text', () => {
    const el = mount('<img src=x onerror=alert(1)> [x](/a)', routed);
    expect(el.querySelector('img')).toBeNull();
    expect(el.textContent).toContain('<img src=x onerror=alert(1)>');
  });
});
