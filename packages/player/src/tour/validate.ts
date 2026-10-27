/**
 * The overlay's security boundary: every manifest and tour file is validated
 * here before anything renders, and a file that breaks any rule is rejected
 * whole (spec 7.1). Hand-written rather than zod so the tour bundle stays small.
 *
 * Only known keys are copied into fresh objects — unknown keys are ignored, so
 * a newer producer's extra field never breaks an older overlay — but unknown
 * enum values and an unknown schema major are rejected: a tour file is data,
 * never code. The rules mirror ``src/steppy/contracts/tours.py`` (the
 * producer's self-check); ``contracts/fixtures/tours/cases.json`` runs both
 * sides over the same cases.
 */

import { TOURS_MANIFEST_KIND, TOUR_FILE_KIND } from '../ui/names.js';
import {
  ADVANCE_KINDS,
  BUILD_ID_PATTERN,
  LAYOUT_NAME_PATTERN,
  LOCALE_PATTERN,
  MAX_LAYOUTS,
  MAX_LOCALES,
  MAX_MEDIA_QUERY_CHARS,
  MAX_ROUTE_CHARS,
  MAX_STEPS,
  MAX_TEXT_CHARS,
  MAX_TITLE_CHARS,
  MAX_TOURS,
  MAX_URL_CHARS,
  MEDIA_QUERY_PATTERN,
  PLACEMENTS,
  ROLE_PATTERN,
  ROUTE_PATTERN,
  SRI_PATTERN,
  STEP_ID_PATTERN,
  TARGETED_ADVANCE_KINDS,
  TARGET_CLICK_MODES,
  TEST_ID_ATTRIBUTE_PATTERN,
  TEST_ID_PATTERN,
  TOUR_ID_PATTERN,
  TOUR_SCHEMA_VERSION,
  type AdvanceKind,
  type Placement,
  type TargetClickMode,
} from './contract.js';

export interface TourEntry {
  readonly route: string;
}

export interface ManifestTour {
  readonly id: string;
  readonly role: string;
  readonly title: Readonly<Record<string, string>>;
  readonly entry: TourEntry | null;
  readonly steps: number;
  readonly files: Readonly<Record<string, string>>;
  readonly integrity: Readonly<Record<string, string>>;
}

export interface TourManifest {
  readonly buildId: string;
  readonly defaultLocale: string;
  readonly locales: readonly string[];
  readonly testIdAttribute: string;
  readonly guideOrigin: string | null;
  readonly layouts: Readonly<Record<string, string>> | null;
  readonly tours: readonly ManifestTour[];
}

export interface TourStep {
  readonly id: string;
  readonly target: string | null;
  readonly advance: AdvanceKind;
  readonly waitFor: string | null;
  readonly title: string | null;
  readonly text: string | null;
  readonly placement: Placement | null;
  /** Test-id of an element the bubble must never cover (a hover menu the target sits in). */
  readonly hold: string | null;
  /** Information steps only: what a click on the target does. ``null``: the host's ``infoTargetClick``. */
  readonly targetClick: TargetClickMode | null;
  readonly layouts: readonly string[] | null;
}

export interface TourFile {
  readonly id: string;
  readonly buildId: string;
  readonly locale: string;
  readonly title: string;
  readonly guideUrl: string | null;
  readonly entry: TourEntry | null;
  readonly steps: readonly TourStep[];
}

export interface Issue {
  path: string;
  message: string;
}

export type FailReason = 'invalid-data' | 'schema-unsupported' | 'load-failed';

export type Result<T> = { ok: true; value: T } | { ok: false; reason: FailReason; issues: Issue[] };

const MAX_ISSUES = 20;
const RX = {
  testId: new RegExp(TEST_ID_PATTERN),
  stepId: new RegExp(STEP_ID_PATTERN),
  tourId: new RegExp(TOUR_ID_PATTERN),
  attr: new RegExp(TEST_ID_ATTRIBUTE_PATTERN),
  locale: new RegExp(LOCALE_PATTERN),
  layout: new RegExp(LAYOUT_NAME_PATTERN),
  role: new RegExp(ROLE_PATTERN),
  route: new RegExp(ROUTE_PATTERN),
  media: new RegExp(MEDIA_QUERY_PATTERN),
  sri: new RegExp(SRI_PATTERN),
  buildId: new RegExp(BUILD_ID_PATTERN),
};
const WINDOWS_RESERVED = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;
const BAD_PERCENT = /%(?![0-9A-Fa-f]{2})/;

/** A same-origin, base-relative route pattern (``/invoices/:invoiceId``). */
export function isRoutePattern(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length <= MAX_ROUTE_CHARS &&
    RX.route.test(value) &&
    !BAD_PERCENT.test(value) &&
    value
      .split('/')
      .slice(1)
      // ``%2e`` is a dot to a URL parser: ``/%2e%2e/x`` would climb out of the app's base.
      .every((seg) => !/^\.\.?$/.test(seg.replace(/%2e/gi, '.')))
  );
}

export function isBuildId(value: unknown): value is string {
  return typeof value === 'string' && RX.buildId.test(value) && !WINDOWS_RESERVED.test(value.split('.')[0]!);
}

/** ``scheme://host[:port]`` of an absolute http(s) URL without credentials, else null. */
export function httpOrigin(value: string): string | null {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if ((url.protocol !== 'https:' && url.protocol !== 'http:') || url.username || url.password) return null;
  return url.origin;
}

class Checker {
  issues: Issue[] = [];
  fail(path: string, message: string): void {
    if (this.issues.length < MAX_ISSUES) this.issues.push({ path, message });
  }
  get ok(): boolean {
    return this.issues.length === 0;
  }
  obj(v: unknown, path: string): Record<string, unknown> | null {
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) return v as Record<string, unknown>;
    this.fail(path, 'must be an object');
    return null;
  }
  str(v: unknown, path: string, rx?: RegExp): string | null {
    if (typeof v !== 'string') {
      this.fail(path, 'must be a string');
      return null;
    }
    if (rx && !rx.test(v)) {
      this.fail(path, `does not match ${rx.source}`);
      return null;
    }
    return v;
  }
  optStr(v: unknown, path: string, rx?: RegExp): string | null {
    return v === undefined || v === null ? null : this.str(v, path, rx);
  }
  text(v: unknown, path: string, max: number, optional: boolean): string | null {
    if (optional && (v === undefined || v === null)) return null;
    const s = this.str(v, path);
    if (s === null) return null;
    if (!s.trim()) this.fail(path, 'must not be empty');
    else if ([...s].length > max) this.fail(path, `is longer than ${max} characters`);
    return s;
  }
  int(v: unknown, path: string, lo: number, hi: number): number | null {
    if (typeof v === 'number' && Number.isInteger(v) && v >= lo && v <= hi) return v;
    this.fail(path, `must be an integer in ${lo}..${hi}`);
    return null;
  }
  oneOf<T extends string>(v: unknown, path: string, allowed: readonly T[]): T | null {
    if (typeof v === 'string' && (allowed as readonly string[]).includes(v)) return v as T;
    this.fail(path, `must be one of ${allowed.join(', ')}`);
    return null;
  }
  entry(v: unknown, path: string): TourEntry | null {
    if (v === null) return null;
    const o = this.obj(v, path);
    if (!o) return null;
    if (!isRoutePattern(o.route)) {
      this.fail(`${path}.route`, 'is not a same-origin, base-relative route pattern');
      return null;
    }
    return Object.freeze({ route: o.route });
  }
  stringMap(v: unknown, path: string, check: (key: string, value: unknown, p: string) => string | null): Record<string, string> {
    const out: Record<string, string> = {};
    const o = this.obj(v, path);
    if (!o) return out;
    for (const [key, value] of Object.entries(o)) {
      const s = check(key, value, `${path}.${key}`);
      if (s !== null) out[key] = s;
    }
    return Object.freeze(out);
  }
}

function header(c: Checker, root: Record<string, unknown>, kind: string): boolean {
  if (root.kind !== kind) c.fail('kind', `must be "${kind}"`);
  return c.ok;
}

/** Parse JSON text; an HTML body (an SPA fallback page) is a load failure, not bad data. */
export function parseJson(text: string): Result<unknown> {
  if (/^\s*</.test(text)) {
    return { ok: false, reason: 'load-failed', issues: [{ path: '', message: 'received HTML (missing file or SPA fallback?)' }] };
  }
  try {
    return { ok: true, value: JSON.parse(text) as unknown };
  } catch (err) {
    return { ok: false, reason: 'invalid-data', issues: [{ path: '', message: `not JSON: ${String(err)}` }] };
  }
}

function schemaGate(raw: unknown): Result<Record<string, unknown>> {
  const c = new Checker();
  const root = c.obj(raw, '');
  if (!root) return { ok: false, reason: 'invalid-data', issues: c.issues };
  const schema = root.schema;
  if (typeof schema !== 'number' || !Number.isInteger(schema)) {
    return { ok: false, reason: 'invalid-data', issues: [{ path: 'schema', message: 'must be an integer' }] };
  }
  if (schema !== TOUR_SCHEMA_VERSION) {
    return {
      ok: false,
      reason: 'schema-unsupported',
      issues: [{ path: 'schema', message: `schema ${schema} is not supported (this overlay reads ${TOUR_SCHEMA_VERSION})` }],
    };
  }
  return { ok: true, value: root };
}

export function validateManifest(raw: unknown): Result<TourManifest> {
  const gate = schemaGate(raw);
  if (!gate.ok) return gate;
  const root = gate.value;
  const c = new Checker();
  if (!header(c, root, TOURS_MANIFEST_KIND)) return { ok: false, reason: 'invalid-data', issues: c.issues };

  const buildId = isBuildId(root.buildId) ? root.buildId : (c.fail('buildId', 'is not a build id'), '');
  const testIdAttribute = c.str(root.testIdAttribute, 'testIdAttribute', RX.attr) ?? '';
  const locales: string[] = [];
  // Case and -/_ do not distinguish locales: ``fr-BE`` and ``fr_be`` name one file on most filesystems.
  const key = (l: string) => l.replace(/_/g, '-').toLowerCase();
  if (!Array.isArray(root.locales) || root.locales.length < 1 || root.locales.length > MAX_LOCALES) {
    c.fail('locales', `must list 1..${MAX_LOCALES} locales`);
  } else {
    root.locales.forEach((l, i) => {
      const s = c.str(l, `locales.${i}`, RX.locale);
      if (s !== null) locales.some((x) => key(x) === key(s)) ? c.fail(`locales.${i}`, 'is listed twice') : locales.push(s);
    });
  }
  const defaultLocale = c.str(root.defaultLocale, 'defaultLocale', RX.locale) ?? '';
  if (defaultLocale && !locales.includes(defaultLocale)) c.fail('defaultLocale', 'must be one of locales');

  let guideOrigin: string | null = null;
  if (root.guideOrigin !== undefined && root.guideOrigin !== null) {
    const s = c.str(root.guideOrigin, 'guideOrigin');
    if (s !== null && httpOrigin(s) !== s) c.fail('guideOrigin', 'must be an origin only (scheme://host[:port])');
    else guideOrigin = s;
  }

  let layouts: Record<string, string> | null = null;
  if (root.layouts !== undefined && root.layouts !== null) {
    layouts = c.stringMap(root.layouts, 'layouts', (name, query, p) => {
      if (!RX.layout.test(name)) return c.fail(p, 'is not a layout name'), null;
      if (typeof query !== 'string' || query.length > MAX_MEDIA_QUERY_CHARS || !RX.media.test(query)) {
        return c.fail(p, 'is not a media query'), null;
      }
      return query;
    });
    const n = Object.keys(layouts).length;
    if (n < 1 || n > MAX_LAYOUTS) c.fail('layouts', `must map 1..${MAX_LAYOUTS} layouts`);
  }

  const tours: ManifestTour[] = [];
  const seen = new Set<string>();
  if (!Array.isArray(root.tours) || root.tours.length > MAX_TOURS) {
    c.fail('tours', `must be a list of at most ${MAX_TOURS} tours`);
  } else {
    root.tours.forEach((t, i) => {
      const p = `tours.${i}`;
      const o = c.obj(t, p);
      if (!o) return;
      const id = c.str(o.id, `${p}.id`, RX.tourId);
      if (id !== null) {
        if (seen.has(id.toLowerCase())) c.fail(`${p}.id`, 'is not unique');
        seen.add(id.toLowerCase());
      }
      const inLocales = (key: string, path: string) => locales.includes(key) || (c.fail(path, 'is not one of locales'), false);
      const files = c.stringMap(o.files, `${p}.files`, (loc, name, fp) =>
        !inLocales(loc, fp) ? null : name !== `${id}.${loc}.json` ? (c.fail(fp, `must be "${id}.${loc}.json"`), null) : (name as string),
      );
      if (defaultLocale && !Object.hasOwn(files, defaultLocale)) c.fail(`${p}.files`, 'has no file for the default locale');
      const title = c.stringMap(o.title, `${p}.title`, (loc, v, tp) => (inLocales(loc, tp) ? c.text(v, tp, MAX_TITLE_CHARS, false) : null));
      // Every file carries its hash: a remote copy of it is fetched with that hash pinned.
      const integrity = c.stringMap(o.integrity, `${p}.integrity`, (loc, v, ip) =>
        !Object.hasOwn(files, loc) ? (c.fail(ip, 'has no matching file'), null) : c.str(v, ip, RX.sri),
      );
      for (const loc in files) if (!Object.hasOwn(integrity, loc)) c.fail(`${p}.integrity`, `has no hash for "${loc}"`);
      const tour: ManifestTour = Object.freeze({
        id: id ?? '',
        role: c.str(o.role, `${p}.role`, RX.role) ?? '',
        title,
        entry: c.entry(o.entry, `${p}.entry`),
        steps: c.int(o.steps, `${p}.steps`, 1, MAX_STEPS) ?? 0,
        files,
        integrity,
      });
      tours.push(tour);
    });
  }
  if (!c.ok) return { ok: false, reason: 'invalid-data', issues: c.issues };
  return {
    ok: true,
    value: Object.freeze({
      buildId,
      defaultLocale,
      locales: Object.freeze(locales),
      testIdAttribute,
      guideOrigin,
      layouts,
      tours: Object.freeze(tours),
    }),
  };
}

export interface TourContext {
  manifest: TourManifest;
  entry: ManifestTour;
  locale: string;
  /** The guide origin this overlay trusts for ``guideUrl`` (null: none). */
  guideOrigin: string | null;
}

export function validateTour(raw: unknown, ctx: TourContext): Result<TourFile> {
  const gate = schemaGate(raw);
  if (!gate.ok) return gate;
  const root = gate.value;
  const c = new Checker();
  if (!header(c, root, TOUR_FILE_KIND)) return { ok: false, reason: 'invalid-data', issues: c.issues };
  const { manifest, entry } = ctx;

  if (root.id !== entry.id) c.fail('id', `must be "${entry.id}"`);
  if (!isBuildId(root.buildId) || root.buildId !== manifest.buildId) c.fail('buildId', `must be the manifest's "${manifest.buildId}"`);
  if (root.locale !== ctx.locale) c.fail('locale', `must be "${ctx.locale}"`);
  const title = c.text(root.title, 'title', MAX_TITLE_CHARS, false) ?? '';

  let guideUrl: string | null = null;
  if (root.guideUrl !== undefined && root.guideUrl !== null) {
    const s = c.str(root.guideUrl, 'guideUrl');
    if (s !== null) {
      const origin = s.length <= MAX_URL_CHARS ? httpOrigin(s) : null;
      if (origin === null || ctx.guideOrigin === null || origin !== ctx.guideOrigin) {
        c.fail('guideUrl', 'must be an http(s) URL on the trusted guide origin');
      } else guideUrl = s;
    }
  }

  const tourEntry = root.entry === undefined ? (c.fail('entry', 'is required (null for none)'), null) : c.entry(root.entry, 'entry');
  if ((tourEntry?.route ?? null) !== (entry.entry?.route ?? null)) c.fail('entry', "must equal the manifest's entry");

  const steps: TourStep[] = [];
  const ids = new Set<string>();
  const layoutNames = Object.keys(manifest.layouts ?? {});
  if (!Array.isArray(root.steps) || root.steps.length !== entry.steps || root.steps.length > MAX_STEPS) {
    c.fail('steps', `must be a list of exactly ${entry.steps} steps`);
  } else {
    root.steps.forEach((s, i) => {
      const p = `steps.${i}`;
      const o = c.obj(s, p);
      if (!o) return;
      const id = c.str(o.id, `${p}.id`, RX.stepId) ?? '';
      if (ids.has(id)) c.fail(`${p}.id`, 'is not unique');
      ids.add(id);
      const target = o.target === null ? null : c.str(o.target, `${p}.target`, RX.testId);
      const advanceObj = c.obj(o.advance, `${p}.advance`);
      const advance = advanceObj ? c.oneOf(advanceObj.on, `${p}.advance.on`, ADVANCE_KINDS) : null;
      if (advance && (TARGETED_ADVANCE_KINDS as readonly string[]).includes(advance) && target === null) {
        c.fail(`${p}.target`, `is required for advance "${advance}"`);
      }
      let targetClick: TargetClickMode | null = null;
      if (o.targetClick !== undefined && o.targetClick !== null) {
        targetClick = c.oneOf(o.targetClick, `${p}.targetClick`, TARGET_CLICK_MODES);
        if (target === null || (advance !== null && advance !== 'manual')) {
          c.fail(`${p}.targetClick`, 'is only for a targeted step that advances on manual');
        }
      }
      let layouts: string[] | null = null;
      if (o.layouts !== undefined && o.layouts !== null) {
        if (!Array.isArray(o.layouts) || o.layouts.length < 1 || o.layouts.length > MAX_LAYOUTS) {
          c.fail(`${p}.layouts`, `must list 1..${MAX_LAYOUTS} layouts`);
        } else {
          layouts = o.layouts.filter((l): l is string => typeof l === 'string' && layoutNames.includes(l));
          if (layouts.length !== o.layouts.length) c.fail(`${p}.layouts`, "must name the manifest's layouts");
        }
      }
      steps.push(
        Object.freeze({
          id,
          target,
          advance: advance ?? 'manual',
          waitFor: c.optStr(o.waitFor, `${p}.waitFor`, RX.testId),
          title: c.text(o.title, `${p}.title`, MAX_TITLE_CHARS, true),
          text: c.text(o.text, `${p}.text`, MAX_TEXT_CHARS, true),
          placement: o.placement === undefined || o.placement === null ? null : c.oneOf(o.placement, `${p}.placement`, PLACEMENTS),
          hold: c.optStr(o.hold, `${p}.hold`, RX.testId),
          targetClick,
          layouts: layouts && Object.freeze(layouts),
        }),
      );
    });
  }
  if (!c.ok) return { ok: false, reason: 'invalid-data', issues: c.issues };
  return {
    ok: true,
    value: Object.freeze({
      id: entry.id,
      buildId: manifest.buildId,
      locale: ctx.locale,
      title,
      guideUrl,
      entry: tourEntry,
      steps: Object.freeze(steps),
    }),
  };
}
