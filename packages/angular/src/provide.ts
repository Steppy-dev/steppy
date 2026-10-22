import { inject, isDevMode, makeEnvironmentProviders, provideEnvironmentInitializer, type EnvironmentProviders } from '@angular/core';

import { STEPPY_TOURS_CONFIG, type SteppyToursConfig } from './config';
import { SteppyTours } from './tours.service';

/**
 * Configure in-app tours for the app, in ``app.config.ts``:
 *
 * ```ts
 * provideSteppyTours({
 *   fallbackManifest: 'tours/tours.manifest.json',
 *   locale: () => {
 *     const translate = inject(TranslateService);
 *     return toSignal(translate.onLangChange.pipe(map((e) => e.lang)), { initialValue: translate.getCurrentLang() ?? '' });
 *   },
 * })
 * ```
 *
 * Then place ``<steppy-tours />`` once in the app shell.
 */
export function provideSteppyTours(config: SteppyToursConfig = {}): EnvironmentProviders {
  return makeEnvironmentProviders([
    { provide: STEPPY_TOURS_CONFIG, useValue: config },
    provideEnvironmentInitializer(() => {
      if (isDevMode() && inject(SteppyTours).ɵconfig !== config) {
        console.warn(
          '[steppy-tours] provideSteppyTours() has no effect here: the tours service is a root ' +
            'singleton. Call it in the application providers (app.config.ts), not in a lazy route.',
        );
      }
    }),
  ]);
}
