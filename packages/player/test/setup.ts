/**
 * Vitest setup — runs once before tests. happy-dom polyfills DOM APIs but
 * lacks ``HTMLImageElement.complete`` semantics by default; tests that need
 * the animator to "wait for image ready" stub it explicitly per-test.
 *
 * No-op for now; kept as a hook for future setup.
 */
export {};
