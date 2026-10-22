import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createApp, h } from 'vue';

import { SteppyGuide } from '../src/index.js';

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('SteppyGuide (Vue wrapper)', () => {
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

  it('reflects props to attributes and re-emits steppy:* events', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const onStep = vi.fn();

    const app = createApp({
      render: () => h(SteppyGuide, { src: './guide.json', locale: 'fr', onStep }),
    });
    app.mount(container);
    await tick();

    const el = container.querySelector('steppy-guide');
    expect(el).toBeTruthy();
    expect(el!.getAttribute('src')).toBe('./guide.json');
    expect(el!.getAttribute('locale')).toBe('fr');

    // A synthetic DOM event must re-emit through the Vue ``onStep`` listener.
    const detail = { index: 2, id: 's2', total: 3, action: 'click', interactive: true };
    el!.dispatchEvent(
      new CustomEvent('steppy:step', { detail, bubbles: true, composed: true }),
    );
    expect(onStep).toHaveBeenCalledWith(detail);

    app.unmount();
    container.remove();
  });
});
