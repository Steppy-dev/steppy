/**
 * Entry routes: match the current location against a tour's ``entry.route``
 * (``/invoices/:invoiceId``), fill its parameters, and translate between app
 * paths (relative to ``<base href>``) and document paths.
 *
 * The compiler strips the app's full URL (path included), so routes are
 * base-relative; an app served under ``/app/`` has documents at
 * ``/app/invoices`` for the route ``/invoices``. Both forms of the current
 * path are tried, so an app without a base element works either way.
 */

export type RouteParams = Record<string, string>;

const PARAM = /^:([A-Za-z_][A-Za-z0-9_]{0,63})$/;

function segments(path: string): string[] {
  const trimmed = path.length > 1 && path.endsWith('/') ? path.slice(0, -1) : path;
  return trimmed === '/' ? [] : trimmed.split('/').slice(1);
}

/** Parameters of ``path`` under ``pattern``, or null when it does not match. */
export function matchRoute(pattern: string, path: string): RouteParams | null {
  const want = segments(pattern);
  const have = segments(path);
  if (want.length !== have.length) return null;
  const params: RouteParams = {};
  for (let i = 0; i < want.length; i++) {
    const param = PARAM.exec(want[i]!);
    if (!param) {
      if (want[i] !== have[i]) return null;
      continue;
    }
    if (!have[i]) return null;
    try {
      params[param[1]!] = decodeURIComponent(have[i]!);
    } catch {
      return null;
    }
  }
  return params;
}

/** The route's names, in order (``/a/:x/b/:y`` -> ``['x', 'y']``). */
export function routeParams(pattern: string): string[] {
  return segments(pattern).flatMap((seg) => {
    const m = PARAM.exec(seg);
    return m ? [m[1]!] : [];
  });
}

/** ``pattern`` with its parameters filled (URI-encoded), or the names missing a value. */
export function buildRoute(pattern: string, params: Record<string, unknown>): { path: string } | { missing: string[] } {
  const missing = routeParams(pattern).filter((name) => params[name] === undefined || params[name] === null || String(params[name]) === '');
  if (missing.length) return { missing };
  const path = segments(pattern)
    .map((seg) => {
      const m = PARAM.exec(seg);
      return m ? encodeURIComponent(String(params[m[1]!])) : seg;
    })
    .join('/');
  return { path: `/${path}` };
}

/** The document's base path (``/`` or ``/app/``); ``/`` when the base is cross-origin. */
export function basePath(baseURI: string, origin: string): string {
  try {
    const url = new URL(baseURI);
    if (url.origin !== origin) return '/';
    const dir = url.pathname.endsWith('/') ? url.pathname : url.pathname.replace(/[^/]*$/, '');
    return dir || '/';
  } catch {
    return '/';
  }
}

/** ``/app/invoices`` under base ``/app/`` -> ``/invoices``; paths outside the base are unchanged. */
export function toAppPath(documentPath: string, base: string): string {
  if (base === '/' || !documentPath.startsWith(base.slice(0, -1))) return documentPath;
  const rest = documentPath.slice(base.length - 1);
  if (rest !== '' && !rest.startsWith('/')) return documentPath;
  return rest || '/';
}

/** ``/invoices`` under base ``/app/`` -> ``/app/invoices``. */
export function toDocumentPath(appPath: string, base: string): string {
  return base === '/' ? appPath : base.slice(0, -1) + appPath;
}

/**
 * A link target the tour may follow in the same tab: a site-relative path —
 * not protocol-relative (``//host``, ``/\host``), no backslash, whitespace or
 * control characters.
 */
export function isSafeRelativeUrl(url: string): boolean {
  return /^\/(?![/\\])/.test(url) && !/[\\\s\u0000-\u001f\u007f]/.test(url);
}

/**
 * The same-tab href for a link in step text, or null. The URL is normalised
 * first and the *result* checked: dot segments (``/.//host``, ``/a/..//host``,
 * ``/%2e%2e//host``) collapse into a protocol-relative ``//host``, which the raw
 * string never looked like.
 */
export function linkHref(url: string, base: string, origin: string): string | null {
  if (!isSafeRelativeUrl(url)) return null;
  try {
    const u = new URL(url, origin);
    const href = toDocumentPath(u.pathname, base) + u.search + u.hash;
    return isSafeRelativeUrl(href) && new URL(href, origin).origin === origin ? href : null;
  } catch {
    return null;
  }
}
