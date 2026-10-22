import {
  type ApplicationConfig,
  inject,
  provideAppInitializer,
  provideBrowserGlobalErrorListeners,
  provideZoneChangeDetection,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { provideHttpClient } from '@angular/common/http';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { TranslateService, provideTranslateService } from '@ngx-translate/core';
import { provideTranslateHttpLoader } from '@ngx-translate/http-loader';
import { provideSteppyTours } from '@steppy/angular';
import Aura from '@primeuix/themes/aura';
import { providePrimeNG } from 'primeng/config';
import { map } from 'rxjs';

import { routes } from './app.routes';
import { DemoConfig } from './demo-config';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    // A zone.js app, like most Angular apps in the field (zoneless needs nothing more).
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(),
    provideTranslateService({
      loader: provideTranslateHttpLoader({ prefix: '/i18n/', suffix: '.json' }),
      fallbackLang: 'en',
      lang: 'en',
    }),
    providePrimeNG({ theme: { preset: Aura } }),
    provideAppInitializer(() => inject(DemoConfig).load()),
    provideSteppyTours({
      // Served by fixtures/tour-demo/serve.py; an app would ship them in public/.
      fallbackManifest: '/assets/tours/tours.manifest.json',
      // The tours follow ngx-translate; a switch mid-tour reloads the tour in place.
      locale: () => {
        const translate = inject(TranslateService);
        return toSignal(translate.onLangChange.pipe(map((e) => e.lang)), { initialValue: translate.getCurrentLang() ?? '' });
      },
    }),
  ],
};
