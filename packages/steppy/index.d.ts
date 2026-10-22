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
}

export declare function defineGuideConfig(config: GuideConfig): GuideConfig;
