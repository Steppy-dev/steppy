/**
 * UI strings for the player chrome (locale switcher, variant toggle, restart,
 * progress label). These are NOT the manifest's per-step ``text`` — they are
 * the static chrome labels that don't come from scenarios.
 *
 * Hosts override per-locale strings via the ``uiStrings`` property on
 * ``<steppy-guide>``. Sparse overrides are merged into the bundled defaults
 * per-locale. A regional locale falls back to its base language (``fr-BE``
 * reads the ``fr`` strings) before English.
 *
 * ``next`` and ``progress`` come from ``ui/strings.ts``'s ``CHROME_STRINGS``,
 * shared with the in-app tour overlay so both say the same thing.
 */

import { CHROME_STRINGS, resolveStrings } from '../ui/strings.js';

export { format } from '../ui/strings.js';

export interface UiStrings {
  restart: string;
  next: string;
  previous: string;
  /** Used as the accessible label for the hotspot button — interpolates the
   *  step's text. Placeholder: ``{step}``. */
  hotspotLabel: string;
  /** Progress label, e.g. "Step 3 of 7". Placeholders: ``{current}`` and ``{total}``. */
  progress: string;
  /** Tooltip shown after a wrong click. */
  miss: string;
  loading: string;
  errorTitle: string;
  errorRetry: string;
  localeSwitcher: string;
  variantSwitcher: string;
  done: string;
  /** Accessible label for the presentation-template segmented control. */
  templateSwitcher: string;
  /** Segmented-control button labels for each presentation template. */
  templateOverlay: string;
  templateSidebar: string;
  templateInline: string;
  /** Sidebar meta line. Placeholders: ``{count}`` and ``{duration}``. */
  stepsMeta: string;
  /** Duration estimate (minutes). Placeholder: ``{minutes}``. */
  durationApprox: string;
  /** Duration estimate (seconds). Placeholder: ``{seconds}``. */
  durationSeconds: string;
  /** Sub-label shown on a completed step in the sidebar timeline. */
  stepPassed: string;
}

export const DEFAULT_UI_STRINGS: Record<string, UiStrings> = {
  en: {
    ...CHROME_STRINGS.en,
    restart: 'Restart',
    previous: 'Previous',
    hotspotLabel: 'Click here to continue: {step}',
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
    ...CHROME_STRINGS.fr,
    restart: 'Recommencer',
    previous: 'Précédent',
    hotspotLabel: 'Cliquez ici pour continuer : {step}',
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
    ...CHROME_STRINGS.nl,
    restart: 'Opnieuw',
    previous: 'Vorige',
    hotspotLabel: 'Klik hier om door te gaan: {step}',
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
    ...CHROME_STRINGS.de,
    restart: 'Neu starten',
    previous: 'Zurück',
    hotspotLabel: 'Hier klicken, um fortzufahren: {step}',
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

export function getUiStrings(
  locale: string,
  overrides: Partial<Record<string, Partial<UiStrings>>> | undefined,
): UiStrings {
  return resolveStrings(DEFAULT_UI_STRINGS, locale, overrides, 'en');
}
