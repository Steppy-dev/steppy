/** Entry routes (``src/tour/routes.ts``) and the route grammar (``validate.isRoutePattern``). */

import { describe, expect, it } from 'vitest';

import { basePath, buildRoute, isSafeRelativeUrl, linkHref, matchRoute, toAppPath, toDocumentPath } from '../../src/tour/routes.js';
import { isRoutePattern } from '../../src/tour/validate.js';

describe('route grammar', () => {
  it.each(['/', '/invoices', '/invoices/:invoiceId', '/a/:x/b/:y', '/caf%C3%A9', '/a~b!$&()*+,;=@', '/a%2eb', '/%2e%2e%2e'])(
    'accepts %s',
    (r) => {
      expect(isRoutePattern(r)).toBe(true);
    },
  );
  it.each([
    // A URL parser reads %2e as a dot: these climb out of the app's base path.
    '/%2e%2e/admin',
    '/a/%2E',
    '/.%2e/x',
    '/%2e/x',
    'invoices',
    '//evil.example',
    '/\\evil',
    'javascript:alert(1)',
    'https://x.example/a',
    '/a?b=1',
    '/a#b',
    '/a/../b',
    '/./a',
    '/a b',
    '/%zz',
    '/a//b',
    "/it's",
    `/${'a'.repeat(600)}`,
  ])('rejects %s', (r) => {
    expect(isRoutePattern(r)).toBe(false);
  });
});

describe('matching', () => {
  it('captures and decodes parameters, tolerating a trailing slash', () => {
    expect(matchRoute('/invoices/:invoiceId', '/invoices/1001')).toEqual({ invoiceId: '1001' });
    expect(matchRoute('/invoices/:invoiceId', '/invoices/a%20b/')).toEqual({ invoiceId: 'a b' });
    expect(matchRoute('/', '/')).toEqual({});
  });
  it('rejects other shapes and undecodable segments', () => {
    expect(matchRoute('/invoices/:id', '/invoices')).toBeNull();
    expect(matchRoute('/invoices/:id', '/invoices/1/lines')).toBeNull();
    expect(matchRoute('/invoices/new', '/invoices/1')).toBeNull();
    expect(matchRoute('/x/:id', '/x/%E0%A4%A')).toBeNull();
  });
});

describe('building', () => {
  it('encodes values and lists what is missing', () => {
    expect(buildRoute('/invoices/:id', { id: 'a/b c' })).toEqual({ path: '/invoices/a%2Fb%20c' });
    expect(buildRoute('/a/:x/b/:y', { x: '1' })).toEqual({ missing: ['y'] });
    expect(buildRoute('/a/:x', { x: '' })).toEqual({ missing: ['x'] });
  });
});

describe('base paths', () => {
  it('reads the base element and ignores a cross-origin one', () => {
    expect(basePath('https://app.example/app/', 'https://app.example')).toBe('/app/');
    expect(basePath('https://app.example/app/index.html', 'https://app.example')).toBe('/app/');
    expect(basePath('https://cdn.example/app/', 'https://app.example')).toBe('/');
  });
  it('translates between document and app paths', () => {
    expect(toAppPath('/app/invoices', '/app/')).toBe('/invoices');
    expect(toAppPath('/app', '/app/')).toBe('/');
    expect(toAppPath('/application', '/app/')).toBe('/application');
    expect(toAppPath('/invoices', '/')).toBe('/invoices');
    expect(toDocumentPath('/invoices', '/app/')).toBe('/app/invoices');
    expect(toDocumentPath('/invoices', '/')).toBe('/invoices');
  });
});

describe('links in step text', () => {
  it.each([
    ['/invoices', true],
    ['/invoices?tab=1#x', true],
    ['//evil.example', false],
    ['/\\evil.example', false],
    ['/a b', false],
    ['/a\u0001', false],
    ['#top', false],
    ['invoices', false],
  ])('%s -> %s', (url, ok) => expect(isSafeRelativeUrl(url)).toBe(ok));

  // The raw string is not what the browser follows: dot segments collapse
  // into a protocol-relative ``//host`` once the URL is resolved.
  const origin = 'https://app.example';
  it.each([
    ['/invoices?tab=1#x', '/', '/invoices?tab=1#x'],
    ['/a/../b', '/', '/b'],
    ['/invoices', '/app/', '/app/invoices'],
    ['/.//evil.example/x', '/', null],
    ['/a/..//evil.example', '/', null],
    ['/%2e%2e//evil.example', '/', null],
    ['/%2E//evil.example', '/', null],
    ['//evil.example', '/', null],
    ['https://evil.example/x', '/', null],
  ])('linkHref(%s) under base %s -> %s', (url, base, href) => expect(linkHref(url, base, origin)).toBe(href));
});
