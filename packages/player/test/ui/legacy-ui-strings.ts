/**
 * Frozen copy of ``DEFAULT_UI_STRINGS`` / ``getUiStrings`` from
 * ``src/i18n/ui-strings.ts`` before the shared ``src/ui/strings.ts`` existed.
 * Test-only oracle for ``ui-strings-parity.test.ts``.
 */

import type { UiStrings } from '../../src/i18n/ui-strings.js';

export const LEGACY_UI_STRINGS: Record<string, UiStrings> = {
  en: {
    restart: 'Restart',
    next: 'Next',
    previous: 'Previous',
    hotspotLabel: 'Click here to continue: {step}',
    progress: 'Step {current} of {total}',
    miss: 'Try clicking the highlighted area.',
    loading: 'Loading guide…',
    errorTitle: 'Could not load this guide',
    errorRetry: 'Retry',
    localeSwitcher: 'Language',
    variantSwitcher: 'View',
    done: 'Done!',
    templateSwitcher: 'Presentation',
    templateOverlay: 'Overlay',
    templateSidebar: 'Sidebar',
    templateInline: 'Inline',
    stepsMeta: '{count} steps · {duration}',
    durationApprox: '~{minutes} min',
    durationSeconds: '~{seconds} s',
    stepPassed: 'Done',
  },
  fr: {
    restart: 'Recommencer',
    next: 'Suivant',
    previous: 'Précédent',
    hotspotLabel: 'Cliquez ici pour continuer : {step}',
    progress: 'Étape {current} sur {total}',
    miss: 'Essayez de cliquer sur la zone surlignée.',
    loading: 'Chargement du guide…',
    errorTitle: 'Impossible de charger ce guide',
    errorRetry: 'Réessayer',
    localeSwitcher: 'Langue',
    variantSwitcher: 'Affichage',
    done: 'Terminé !',
    templateSwitcher: 'Présentation',
    templateOverlay: 'Superposé',
    templateSidebar: 'Latéral',
    templateInline: 'En ligne',
    stepsMeta: '{count} étapes · {duration}',
    durationApprox: '~{minutes} min',
    durationSeconds: '~{seconds} s',
    stepPassed: 'Terminé',
  },
  nl: {
    restart: 'Opnieuw',
    next: 'Volgende',
    previous: 'Vorige',
    hotspotLabel: 'Klik hier om door te gaan: {step}',
    progress: 'Stap {current} van {total}',
    miss: 'Probeer op de gemarkeerde zone te klikken.',
    loading: 'Gids laden…',
    errorTitle: 'Deze gids kon niet worden geladen',
    errorRetry: 'Opnieuw proberen',
    localeSwitcher: 'Taal',
    variantSwitcher: 'Weergave',
    done: 'Klaar!',
    templateSwitcher: 'Presentatie',
    templateOverlay: 'Overlay',
    templateSidebar: 'Zijbalk',
    templateInline: 'Inline',
    stepsMeta: '{count} stappen · {duration}',
    durationApprox: '~{minutes} min',
    durationSeconds: '~{seconds} s',
    stepPassed: 'Klaar',
  },
  de: {
    restart: 'Neu starten',
    next: 'Weiter',
    previous: 'Zurück',
    hotspotLabel: 'Hier klicken, um fortzufahren: {step}',
    progress: 'Schritt {current} von {total}',
    miss: 'Versuchen Sie, auf den hervorgehobenen Bereich zu klicken.',
    loading: 'Anleitung wird geladen…',
    errorTitle: 'Diese Anleitung konnte nicht geladen werden',
    errorRetry: 'Erneut versuchen',
    localeSwitcher: 'Sprache',
    variantSwitcher: 'Ansicht',
    done: 'Fertig!',
    templateSwitcher: 'Darstellung',
    templateOverlay: 'Overlay',
    templateSidebar: 'Seitlich',
    templateInline: 'Inline',
    stepsMeta: '{count} Schritte · {duration}',
    durationApprox: '~{minutes} min',
    durationSeconds: '~{seconds} s',
    stepPassed: 'Fertig',
  },
};

export function legacyGetUiStrings(
  locale: string,
  overrides: Partial<Record<string, Partial<UiStrings>>> | undefined,
): UiStrings {
  const fallback = LEGACY_UI_STRINGS[locale] ?? LEGACY_UI_STRINGS.en!;
  const override = overrides?.[locale];
  if (!override) return fallback;
  return { ...fallback, ...override };
}

