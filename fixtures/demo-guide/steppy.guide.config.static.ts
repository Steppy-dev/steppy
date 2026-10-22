import { defineGuideConfig } from 'steppy';

// Verification config for the static target. Stage 4 of the interactive
// guide feature lifted the static-target restriction, so invoices now
// keeps both modes — exercises the full ``<steppy-guide>`` mount path on a
// portable static build. Mixes plain-string and locale-map fields to
// exercise both forms in a single build.
export default defineGuideConfig({
  scenariosRoot: './e2e/scenarios',
  screenshotsDir: './output/screenshots',
  outputDir: './dist-static',
  assetsDir: './unused',
  role: 'editor',
  locales: ['fr', 'nl'],
  defaultLocale: 'fr',
  target: 'static',
  // Public SEO so the fixture exercises sitemap.xml, canonicals and hreflang;
  // the default is private, which emits none of them.
  seo: {
    mode: 'public',
    baseUrl: 'https://guides.example.com',
    openGraph: true,
  },
  site: {
    // Plain string — back-compat path: same value for every locale.
    title: 'Demo Guides',
  },
  guides: [
    {
      slug: 'onboarding',
      title: 'Onboarding',
      scenario: 'editor/onboarding.md',
      modes: ['stepbystep'],
    },
    {
      slug: 'invoices',
      // Locale map — different display per language.
      title: { fr: 'Factures', nl: 'Facturen' },
      scenario: 'editor/invoices.md',
      modes: ['stepbystep', 'interactive'],
    },
  ],
  navbar: {
    order: ['onboarding', 'invoices'],
  },
});
