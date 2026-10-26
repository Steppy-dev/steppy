/**
 * Steppy guide build configuration types.
 *
 * Usage in steppy.guide.config.ts:
 *
 *   import { defineGuideConfig } from 'steppy';
 *
 *   export default defineGuideConfig({ ... });
 */

/**
 * A user-facing string that can either be a single locale-agnostic value
 * or a {locale: string} map. Plain strings are treated as the default for
 * every locale. Maps support partial coverage — missing locales fall back
 * to the configured `defaultLocale` at build time.
 */
export type LocalizedText = string | { [locale: string]: string };

/**
 * Cursor presentation mode for the interactive ``<steppy-guide>`` player.
 * - "arrow"   : animated arrow + pulsing hotspot (default — current behaviour)
 * - "hotspot" : no arrow / ripple / kbd overlay; hotspot still pulses and the
 *               user clicks it to advance (Guideflow-style)
 * - "off"     : no arrow and no hotspot — auto-advance every step after
 *               ``waitAfter`` like a passive video walkthrough
 */
export type CursorMode = 'arrow' | 'hotspot' | 'off';

/**
 * Presentation template for the interactive ``<steppy-guide>`` player.
 * - "overlay" : full-bleed preview + floating callout pinned to the target
 *               (the current/default look)
 * - "sidebar" : left rail with a vertical step timeline + description
 * - "inline"  : full-bleed preview + a slim bottom caption bar
 * Runtime-switchable in the player; this is only the per-guide default.
 */
export type TemplateMode = 'sidebar' | 'overlay' | 'inline';

export interface GuideEntry {
  /** URL-safe identifier for the guide (e.g., "onboarding"). */
  slug: string;
  /** Display title shown in the guide header and navbar. Plain string or {locale: string} map. */
  title: LocalizedText;
  /** Path to the scenario .md file, relative to scenariosRoot. */
  scenario: string;
  /** Rendering modes. Default: ["stepbystep"]. `video` only renders when the
   *  build is armed with `steppy build --video`; declaring it alone is inert. */
  modes?: ('stepbystep' | 'interactive' | 'video')[];
  /** Optional group slug. References a `groups[].slug` entry. */
  group?: string;
  /** Per-guide cursor mode override. Defaults to the project-level `cursor`, then "arrow". */
  cursor?: CursorMode;
  /** Per-guide presentation template override. Defaults to the project-level `defaultTemplate`, then "overlay". */
  template?: TemplateMode;
  /** Per-guide meta description for public SEO / shared-link snippets. Falls back to `title`. Plain string or {locale: string} map. */
  description?: LocalizedText;
}

export interface GroupConfig {
  /** URL-safe identifier; also used as the path for the group landing page. */
  slug: string;
  /** Display title shown in the navbar and on the group landing page. Plain string or {locale: string} map. */
  title: LocalizedText;
  /** Optional intro text rendered on the group landing page. Plain string or {locale: string} map. */
  description?: LocalizedText;
}

export interface OnboardingConfig {
  /** When false (or omitted), no onboarding artifacts are emitted. */
  enabled: boolean;
  /** Display title. Default: "Getting Started". Plain string or {locale: string} map. */
  title?: LocalizedText;
  /** Optional intro text rendered on the onboarding landing page. Plain string or {locale: string} map. */
  description?: LocalizedText;
  /** Ordered list of guide slugs forming the onboarding path. */
  guides: string[];
}

export interface SiteConfig {
  /** Title displayed in the guide site header. Plain string or {locale: string} map. */
  title: LocalizedText;
}

export interface NavbarConfig {
  /**
   * Ordered list of top-level navbar entries. Each item may be a guide slug
   * (ungrouped guides only) or a group slug (places the entire group at this
   * position). Items not listed fall back to declaration order.
   */
  order: string[];
}

/**
 * SEO / indexing mode for the static build target.
 * - "private" (default): every page gets a noindex robots meta and robots.txt
 *   emits `Disallow: /` — nothing is indexable (safe default for client sites).
 * - "public": emits sitemap.xml, canonical links, hreflang alternates, and
 *   optional Open Graph tags. Requires `baseUrl`.
 */
export type SeoMode = 'public' | 'private';

/**
 * Static-site SEO / indexing controls. Applies to the `static` target only
 * (the Angular host owns its own meta). Omitting the block ⇒ private.
 * Indexing controls are defense-in-depth, not access control — private sites
 * still sit behind auth.
 */
export interface SeoConfig {
  /** Indexing mode. Default: "private". */
  mode?: SeoMode;
  /** Absolute site origin, e.g. "https://guides.example.com". Required when mode is "public". */
  baseUrl?: string;
  /** Site-wide default meta description (index page + fallback + OG). Plain string or {locale: string} map. */
  description?: LocalizedText;
  /** Emit Open Graph / Twitter card tags (public mode only). Default: false. */
  openGraph?: boolean;
  /**
   * Site-wide <lastmod> for every sitemap entry, as a W3C datetime
   * ("2026-09-14", or "2026-09-14T12:00:00Z" / "...+02:00" -- a timezone is
   * required whenever a time is given). Omitted by default, which is what keeps
   * a build byte-reproducible; there is deliberately no "auto" mode, because a
   * CI checkout stamps every file at clone time. Set it from the commit date:
   * `git log -1 --format=%cI`.
   */
  lastmod?: string;
}

/** `font-weight` and `color` of `**strong**` text. */
export interface StrongStyle {
  weight?: string | number;
  color?: string;
}

/** `font-style` and `color` of `*em*` text. */
export interface EmStyle {
  style?: 'italic' | 'normal' | 'oblique';
  color?: string;
}

/** `` `code` `` spans: font family and size, colours, padding and corner radius. */
export interface CodeStyle {
  font?: string;
  size?: string;
  color?: string;
  background?: string;
  padding?: string;
  radius?: string;
}

/** Links in step text: colour and `text-decoration`. */
export interface LinkStyle {
  color?: string;
  decoration?: string;
}

/**
 * Styles for the inline markup of step text, emitted as `--steppy-strong-*`,
 * `--steppy-em-*`, `--steppy-code-*` and `--steppy-link-*` custom properties
 * (the interactive player reads them too). Each value is one CSS value:
 * `;`, braces, `<`, `>`, backslashes, comments and line breaks are refused.
 */
export interface TextStylesConfig {
  strong?: StrongStyle;
  em?: EmStyle;
  code?: CodeStyle;
  link?: LinkStyle;
}

export interface GuideConfig {
  /** Directory containing scenario .md files. */
  scenariosRoot: string;
  /**
   * Directory containing captured screenshots. Omit it and the build reads
   * `output.screenshots_dir` from `config.yaml` — the same value the test run
   * captured to — falling back to `./output/screenshots`. Set it only to point
   * a build at captures the local `config.yaml` does not describe.
   */
  screenshotsDir?: string;
  /** Angular output directory for generated components. */
  outputDir: string;
  /**
   * Directory where screenshots are copied. The contents must end up under
   * the host app's ``public/assets/<role>/`` so the generated image URLs
   * (``/assets/<role>/<slug>/...``) resolve. Setting it directly to
   * ``./public/assets/<role>`` avoids a manual copy step.
   */
  assetsDir: string;
  /** Default role for guide step filtering. Also drives the i18n namespace
   * (``<ROLE>.guides.*``), the routes export name (``<ROLE>_ROUTES``), and
   * the screenshot URL prefix (``/assets/<role>/...``). */
  role: string;
  /**
   * Supported locales. Default: ["fr"].
   *
   * Each entry must be a BCP-47 language tag ("fr", "fr-BE", "pt-BR",
   * "zh-Hant"). The tag is used **verbatim** as a page filename and as a
   * screenshot directory segment, so it must match what `steppy run` captured;
   * only `hreflang` and `<html lang>` get the canonical BCP-47 spelling, which
   * is why `fr_BE` is accepted and left as written. A regional locale also
   * needs matching `guide-text:` keys in every scenario and a matching
   * `i18n.localstorage.values` entry in config.yaml.
   */
  locales?: string[];
  /** Default locale. Default: "fr". */
  defaultLocale?: string;
  /**
   * Optional layouts dimension. When non-empty, the build assembles each
   * guide once per (locale, layout) combination and renders an in-page
   * toggle. Layout names must match the keys declared under
   * `screenshot.layouts` in the project's config.yaml. Empty/missing =
   * single-layout build (back-compat).
   */
  layouts?: string[];
  /** Default layout shown on initial render. Must be a member of `layouts`. Defaults to the first declared layout. */
  defaultLayout?: string;
  /** Build output target. "static" generates a self-contained, framework-agnostic
   * site (the default); "angular" generates standalone Angular components. Default: "static". */
  target?: 'angular' | 'static';
  /**
   * Project-wide default cursor mode for the interactive player. Per-guide
   * `GuideEntry.cursor` overrides this. Default: "arrow".
   */
  cursor?: CursorMode;
  /**
   * Project-wide default presentation template for the interactive player.
   * Per-guide `GuideEntry.template` overrides this. Default: "overlay".
   */
  defaultTemplate?: TemplateMode;
  /** Site-level metadata. */
  site: SiteConfig;
  /** Guide entries to build. */
  guides: GuideEntry[];
  /** Navbar ordering configuration. */
  navbar: NavbarConfig;
  /** Optional groups. Guides reference a group via `GuideEntry.group`. */
  groups?: GroupConfig[];
  /**
   * Optional onboarding path. Renders a "Getting Started" entry that walks
   * the user through the listed guides in order. URL slug is fixed as
   * "getting-started"; no guide or group may share that slug.
   */
  onboarding?: OnboardingConfig;
  /**
   * Optional SEO / indexing controls for the `static` target. Omitting the
   * block ⇒ private (noindex + robots.txt Disallow). Set `mode: 'public'`
   * (with `baseUrl`) for indexable showcase sites.
   */
  seo?: SeoConfig;
  /**
   * Styles for `**strong**`, `*em*`, `` `code` `` and links in step text.
   * Omitted ⇒ the browser's defaults, and no CSS is emitted.
   */
  textStyles?: TextStylesConfig;
}

/**
 * Define a Steppy guide build configuration with full type checking.
 *
 * This is an identity function that provides TypeScript type inference
 * for the config object. At runtime it simply returns the config as-is.
 */
export function defineGuideConfig(config: GuideConfig): GuideConfig {
  return config;
}
