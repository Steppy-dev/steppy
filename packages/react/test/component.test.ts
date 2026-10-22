import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, createElement } from 'react';
import { createRoot } from 'react-dom/client';

import { SteppyGuide } from '../src/index.js';

// Opt into React's act() environment so passive effects (where the wrapper
// reflects props to attributes and attaches listeners) flush deterministically.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

describe('SteppyGuide (React wrapper)', () => {
  beforeEach(() => {
    // The player fetches ``src`` on connect; keep the test hermetic (no network).
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('no network in test'))),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('reflects props to attributes and forwards steppy:* events', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const onStep = vi.fn();

    const root = createRoot(container);
    await act(async () => {
      root.render(
        createElement(SteppyGuide, { src: './guide.json', locale: 'fr', onStep }),
      );
    });

    const el = container.querySelector('steppy-guide');
    expect(el).toBeTruthy();
    expect(el!.getAttribute('src')).toBe('./guide.json');
    expect(el!.getAttribute('locale')).toBe('fr');

    // Wrapper wiring is independent of the player's internals: a synthetic
    // event on the element must reach the callback with its detail intact.
    const detail = { index: 2, id: 's2', total: 3, action: 'click', interactive: true };
    await act(async () => {
      el!.dispatchEvent(
        new CustomEvent('steppy:step', { detail, bubbles: true, composed: true }),
      );
    });
    expect(onStep).toHaveBeenCalledWith(detail);

    await act(async () => {
      root.unmount();
    });
    container.remove();
  });
});
