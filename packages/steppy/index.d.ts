export type LocalizedText = string | { [locale: string]: string };

export type CursorMode = 'arrow' | 'hotspot' | 'off';

export interface GuideEntry {
  slug: string;
  title: LocalizedText;
  scenario: string;
  modes?: ('stepbystep' | 'interactive' | 'video')[];
  group?: string;
  cursor?: CursorMode;
  description?: LocalizedText;
}

export interface GroupConfig {
  slug: string;
  title: LocalizedText;
  description?: LocalizedText;
}

export interface OnboardingConfig {
  enabled: boolean;
  title?: LocalizedText;
  description?: LocalizedText;
  guides: string[];
}

export interface SiteConfig {
  title: LocalizedText;
}

export interface NavbarConfig {
  order: string[];
}

export type SeoMode = 'public' | 'private';

export interface SeoConfig {
  mode?: SeoMode;
  baseUrl?: string;
  description?: LocalizedText;
  openGraph?: boolean;
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
  scenariosRoot: string;
  /** Omitted ⇒ `output.screenshots_dir` from config.yaml, else "./output/screenshots". */
  screenshotsDir?: string;
  outputDir: string;
  assetsDir: string;
  role: string;
  locales?: string[];
  defaultLocale?: string;
  layouts?: string[];
  defaultLayout?: string;
  target?: 'angular' | 'static';
  cursor?: CursorMode;
  site: SiteConfig;
  guides: GuideEntry[];
  navbar: NavbarConfig;
  groups?: GroupConfig[];
  onboarding?: OnboardingConfig;
  seo?: SeoConfig;
  textStyles?: TextStylesConfig;
}

export declare function defineGuideConfig(config: GuideConfig): GuideConfig;
