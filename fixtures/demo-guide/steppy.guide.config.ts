import { defineGuideConfig } from 'steppy';

export default defineGuideConfig({
  scenariosRoot: './e2e/scenarios',
  screenshotsDir: './output/screenshots',
  outputDir: './src/app/features/guides/generated',
  assetsDir: './public/assets/editor',
  role: 'editor',
  // The default target is now 'static'; this fixture demonstrates the Angular
  // target, so it opts in explicitly.
  target: 'angular',
  locales: ['fr', 'nl'],
  defaultLocale: 'fr',
  site: {
    title: 'Demo Guides',
  },
  groups: [
    {
      slug: 'invoices-module',
      title: { fr: 'Factures', nl: 'Facturen' },
      description: {
        fr: 'Tout ce qui concerne la gestion des factures.',
        nl: 'Alles wat te maken heeft met het beheren van facturen.',
      },
    },
  ],
  guides: [
    {
      slug: 'onboarding',
      title: 'Onboarding',
      scenario: 'editor/onboarding.md',
      modes: ['stepbystep'],
    },
    {
      slug: 'invoices',
      title: { fr: 'Factures', nl: 'Facturen' },
      scenario: 'editor/invoices.md',
      modes: ['stepbystep', 'interactive'],
      group: 'invoices-module',
    },
  ],
  navbar: {
    order: ['onboarding', 'invoices-module'],
  },
  onboarding: {
    enabled: true,
    title: { fr: 'Pour commencer', nl: 'Aan de slag' },
    description: {
      fr: 'Un parcours rapide à travers les bases pour les nouveaux utilisateurs.',
      nl: 'Een snelle weg door de basis voor nieuwe gebruikers.',
    },
    guides: ['onboarding', 'invoices'],
  },
});
