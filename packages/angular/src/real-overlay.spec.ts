/**
 * Against the real overlay module (``@steppy/player/tour``), not the fake:
 * the adapter sets properties on ``<steppy-tour-host>`` before the module
 * defines it, and Lit must keep them through the upgrade. The overlay's
 * behaviour itself is the player's and the browser suites' business.
 */

import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { TOUR_HOST_TAG, type TourHostElement } from './overlay';
import { provideSteppyTours } from './provide';
import { SteppyToursComponent } from './tours.component';
import { SteppyTours } from './tours.service';
import { settle } from './testing/fake-overlay';

describe('the real overlay', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('keeps the properties the adapter set before its upgrade, and reports a load failure', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('offline'))));
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideSteppyTours({ fallbackManifest: '/tours/tours.manifest.json', locale: 'fr' })],
    });
    const fixture = TestBed.createComponent(SteppyToursComponent);
    await fixture.whenStable();
    const tours = TestBed.inject(SteppyTours);
    const started = tours.start('create-invoice');
    await customElements.whenDefined(TOUR_HOST_TAG);
    await settle();
    const el = document.querySelector<TourHostElement>(TOUR_HOST_TAG)!;
    expect(customElements.get(TOUR_HOST_TAG)).toBeDefined();
    expect(Object.prototype.hasOwnProperty.call(el, 'fallbackManifest')).toBe(false); // now Lit's accessor
    expect([el.fallbackManifest, el.locale, typeof el.navigate]).toEqual(['/tours/tours.manifest.json', 'fr', 'function']);
    expect(await started).toBe(false);
    expect(tours.lastError()?.reason).toBe('load-failed');
  });
});
