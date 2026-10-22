/**
 * The resume record: which step of which tour is running, kept in
 * ``sessionStorage`` so a full page load (a classic form post, a link the app
 * does not route) picks the tour up where it was. Per tab by design.
 *
 * Every access is wrapped: storage throws in private modes and sandboxed
 * frames, and a tour must keep working without it.
 */

import { TOUR_RESUME_STORAGE_KEY } from '../ui/names.js';

export const RESUME_TTL_MS = 30 * 60 * 1000;

export interface ResumeRecord {
  v: 1;
  tourId: string;
  stepId: string;
  locale: string;
  params: Record<string, string>;
  buildId: string;
  /** The manifest URL(s) the record was made with; another host config ignores it. */
  manifestKey: string;
  savedAt: number;
}

function store(): Storage | null {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}

export function writeResume(record: ResumeRecord): void {
  try {
    store()?.setItem(TOUR_RESUME_STORAGE_KEY, JSON.stringify(record));
  } catch {
    // Quota or access errors: the tour simply will not resume.
  }
}

export function clearResume(): void {
  try {
    store()?.removeItem(TOUR_RESUME_STORAGE_KEY);
  } catch {
    // Nothing to clean up then.
  }
}

/**
 * The stored record when it is well formed, fresh and made for ``manifestKey``.
 * A malformed or expired record is removed; one made by another host
 * configuration is left for that host.
 */
export function readResume(manifestKey: string, now: number): ResumeRecord | null {
  let raw: string | null = null;
  try {
    raw = store()?.getItem(TOUR_RESUME_STORAGE_KEY) ?? null;
  } catch {
    return null;
  }
  if (!raw) return null;
  let r: Partial<ResumeRecord> | null = null;
  try {
    r = JSON.parse(raw) as Partial<ResumeRecord>;
  } catch {
    // Unreadable: dropped below.
  }
  const fresh = typeof r?.savedAt === 'number' && now - r.savedAt >= 0 && now - r.savedAt < RESUME_TTL_MS;
  const shaped =
    r !== null &&
    typeof r === 'object' &&
    r.v === 1 &&
    typeof r.tourId === 'string' &&
    typeof r.stepId === 'string' &&
    typeof r.locale === 'string' &&
    typeof r.buildId === 'string' &&
    r.params !== null &&
    typeof r.params === 'object' &&
    Object.values(r.params).every((v) => typeof v === 'string');
  if (!shaped || !fresh) {
    clearResume();
    return null;
  }
  return r!.manifestKey === manifestKey ? (r as ResumeRecord) : null;
}
