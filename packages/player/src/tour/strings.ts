/**
 * The overlay's chrome strings. They resolve against the *loaded tour file's*
 * locale, never the page's, so the buttons and the step text are always in
 * one language. ``next`` and ``progress`` are the guide player's words
 * (``ui/strings.ts``); a host overrides any key per locale with ``strings``.
 *
 * The ``instruction*`` keys are the generic hint a step shows when its
 * scenario authored no text for that locale (the compiler warns about each).
 */

import { CHROME_STRINGS, resolveStrings } from '../ui/strings.js';
import type { AdvanceKind } from './contract.js';

export interface TourStrings {
  next: string;
  /** Placeholders: ``{current}`` and ``{total}``. */
  progress: string;
  /** The primary button on the last step. */
  done: string;
  close: string;
  skipStep: string;
  endTour: string;
  resume: string;
  paused: string;
  dialogLabel: string;
  openGuide: string;
  notFoundTitle: string;
  notFoundBody: string;
  instructionClick: string;
  instructionInput: string;
  instructionChange: string;
  instructionVisible: string;
  instructionManual: string;
}

export type TourStringOverrides = Partial<Record<string, Partial<TourStrings>>>;

export const DEFAULT_TOUR_STRINGS: Record<string, TourStrings> = {
  en: {
    ...CHROME_STRINGS.en,
    done: 'Done',
    close: 'Close the tour',
    skipStep: 'Skip this step',
    endTour: 'End the tour',
    resume: 'Resume tour',
    paused: 'Tour paused',
    dialogLabel: 'Guided tour',
    openGuide: 'Open the full guide',
    notFoundTitle: "Can't find this element",
    notFoundBody: 'The page may have changed. Skip this step or end the tour.',
    instructionClick: 'Click the highlighted element.',
    instructionInput: 'Fill in the highlighted field.',
    instructionChange: 'Choose a value in the highlighted field.',
    instructionVisible: 'Wait for the highlighted element to appear.',
    instructionManual: 'Select Next to continue.',
  },
  fr: {
    ...CHROME_STRINGS.fr,
    done: 'Terminer',
    close: 'Fermer la visite',
    skipStep: 'Passer cette étape',
    endTour: 'Terminer la visite',
    resume: 'Reprendre la visite',
    paused: 'Visite en pause',
    dialogLabel: 'Visite guidée',
    openGuide: 'Ouvrir le guide complet',
    notFoundTitle: 'Élément introuvable',
    notFoundBody: 'La page a peut-être changé. Passez cette étape ou terminez la visite.',
    instructionClick: "Cliquez sur l'élément mis en évidence.",
    instructionInput: 'Remplissez le champ mis en évidence.',
    instructionChange: 'Choisissez une valeur dans le champ mis en évidence.',
    instructionVisible: "Attendez que l'élément mis en évidence apparaisse.",
    instructionManual: 'Sélectionnez Suivant pour continuer.',
  },
  nl: {
    ...CHROME_STRINGS.nl,
    done: 'Klaar',
    close: 'Rondleiding sluiten',
    skipStep: 'Deze stap overslaan',
    endTour: 'Rondleiding beëindigen',
    resume: 'Rondleiding hervatten',
    paused: 'Rondleiding gepauzeerd',
    dialogLabel: 'Rondleiding',
    openGuide: 'Volledige gids openen',
    notFoundTitle: 'Dit element is niet gevonden',
    notFoundBody: 'De pagina is mogelijk gewijzigd. Sla deze stap over of beëindig de rondleiding.',
    instructionClick: 'Klik op het gemarkeerde element.',
    instructionInput: 'Vul het gemarkeerde veld in.',
    instructionChange: 'Kies een waarde in het gemarkeerde veld.',
    instructionVisible: 'Wacht tot het gemarkeerde element verschijnt.',
    instructionManual: 'Kies Volgende om verder te gaan.',
  },
  de: {
    ...CHROME_STRINGS.de,
    done: 'Fertig',
    close: 'Tour schließen',
    skipStep: 'Diesen Schritt überspringen',
    endTour: 'Tour beenden',
    resume: 'Tour fortsetzen',
    paused: 'Tour pausiert',
    dialogLabel: 'Geführte Tour',
    openGuide: 'Vollständige Anleitung öffnen',
    notFoundTitle: 'Element nicht gefunden',
    notFoundBody: 'Die Seite hat sich möglicherweise geändert. Überspringen Sie diesen Schritt oder beenden Sie die Tour.',
    instructionClick: 'Klicken Sie auf das hervorgehobene Element.',
    instructionInput: 'Füllen Sie das hervorgehobene Feld aus.',
    instructionChange: 'Wählen Sie im hervorgehobenen Feld einen Wert aus.',
    instructionVisible: 'Warten Sie, bis das hervorgehobene Element erscheint.',
    instructionManual: 'Wählen Sie Weiter, um fortzufahren.',
  },
};

export function resolveTourStrings(contentLocale: string, overrides?: TourStringOverrides): TourStrings {
  return resolveStrings(DEFAULT_TOUR_STRINGS, contentLocale, overrides, 'en');
}

/**
 * Writing direction of a locale tag: a script subtag decides (``az-Arab``,
 * ``sd-Deva``), else the language. Set on the overlay with ``lang``, so a tour
 * shown in another language than the page is read and laid out as its own.
 */
export function textDir(tag: string): 'rtl' | 'ltr' {
  const [lang, sub = ''] = tag.toLowerCase().split(/[-_]/);
  const rtl = /^[a-z]{4}$/.test(sub) ? /^(arab|hebr|thaa|syrc|nkoo|adlm|rohg)$/.test(sub) : /^(ar|he|iw|fa|ur|ps|sd|ug|yi|ckb|dv)$/.test(lang!);
  return rtl ? 'rtl' : 'ltr';
}

const INSTRUCTION: Record<AdvanceKind, keyof TourStrings> = {
  click: 'instructionClick',
  input: 'instructionInput',
  change: 'instructionChange',
  visible: 'instructionVisible',
  manual: 'instructionManual',
};

/** The generic hint for a step without authored text. */
export function instructionFor(kind: AdvanceKind, strings: TourStrings): string {
  return strings[INSTRUCTION[kind]];
}
