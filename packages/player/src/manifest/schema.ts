import { z } from 'zod';

export const SUPPORTED_MAJOR_VERSION = 1;

const VERSION_RE = /^\d+\.\d+$/;

const NonEmptyString = z.string().min(1);

const Bbox = z
  .object({
    x: z.number(),
    y: z.number(),
    width: z.number().positive(),
    height: z.number().positive(),
  })
  .strict();

const CaptureSize = z
  .object({
    width: z.number().positive(),
    height: z.number().positive(),
  })
  .strict();

const SingleTarget = z
  .object({
    selector: NonEmptyString.optional(),
    bbox: Bbox,
  })
  .strict();

const MultiTarget = z
  .object({
    selector: NonEmptyString.optional(),
    bbox: Bbox,
    value: z.string().optional(),
  })
  .strict();

const CursorBehavior = z.discriminatedUnion('kind', [
  z.object({ kind: z.literal('click'), target: SingleTarget }).strict(),
  z.object({ kind: z.literal('hover'), target: SingleTarget }).strict(),
  z
    .object({
      kind: z.literal('type'),
      target: SingleTarget,
      value: z.string().optional(),
    })
    .strict(),
  z
    .object({
      kind: z.literal('type-multi'),
      targets: z.array(MultiTarget).min(1),
    })
    .strict(),
]);

const Frame = z
  .object({
    screenshot: NonEmptyString,
    captureSize: CaptureSize,
    cursor: CursorBehavior.nullable(),
  })
  .strict();

/**
 * Layout-varying guide text for one (locale, layout) cell.
 *
 * Present only when this cell's wording differs from the step-level value —
 * the desktop/mobile twin-branch pattern, where one logical step is authored
 * once per layout because the DOM path differs ("click the row of the table"
 * vs "tap the card"). Absent ⇒ inherit the step.
 *
 * Plain strings, not `LocalizedString`: `captures[locale]` is already
 * locale-scoped. All three fields are required together, so an explicit "this
 * layout has no subtitle" stays distinguishable from "inherit the subtitle" —
 * a partial override would make the sidebar show one layout's heading above
 * another layout's body.
 */
const CaptureContent = z
  .object({
    title: z.string().nullable(),
    text: z.string(),
    subtitle: z.string().nullable(),
  })
  .strict();

const Capture = z
  .object({
    frames: z.array(Frame).min(1),
    content: CaptureContent.optional(),
  })
  .strict();

const LayoutMap = z.record(NonEmptyString, Capture);
const LocaleMap = z.record(NonEmptyString, LayoutMap);

const LocalizedString = z.record(NonEmptyString, z.string().nullable());
const LocalizedRequired = z.record(NonEmptyString, NonEmptyString);

const ManifestAction = z.enum([
  'navigate',
  'click',
  'fill',
  'fill-form',
  'select',
  'wait',
  'wait-for',
  'wait-for-text',
  'scroll',
  'hover',
  'keyboard',
  'assert',
  'custom',
  'login',
  'auth0-login',
  'capture',
  'upload',
  'select-option',
  'confirm',
  'fill-date',
  'toggle',
  'pick-date',
  'pick-time',
  'expand',
  'call',
]);

const ManifestStep = z
  .object({
    id: NonEmptyString,
    index: z.number().int().positive(),
    action: ManifestAction,
    interactive: z.boolean(),
    screenshotWhen: z.enum(['before', 'after']),
    waitAfter: z.number().int().nonnegative(),
    // Short per-step heading (e.g. for the sidebar timeline). Optional + nullable
    // for back-compat with already-built manifests and locales lacking guide-text;
    // the player falls back to ``text`` when absent.
    title: LocalizedString.optional(),
    text: LocalizedString,
    subtitle: LocalizedString.optional(),
    captures: LocaleMap,
  })
  .strict();

export const CURSOR_MODES = ['arrow', 'hotspot', 'off'] as const;
const CursorModeSchema = z.enum(CURSOR_MODES);

export const TEMPLATE_MODES = ['sidebar', 'overlay', 'inline'] as const;
const TemplateModeSchema = z.enum(TEMPLATE_MODES);

const CursorTimings = z
  .object({
    pre_action_ms: z.number().int().positive(),
    action_ms: z.number().int().positive(),
    post_action_ms: z.number().int().nonnegative(),
    travel_speed_px_per_ms: z.number().positive(),
    // Optional. Absent ⇒ host attribute or 'arrow' default. See <steppy-guide>'s
    // cursor property for the precedence rules.
    mode: CursorModeSchema.optional(),
  })
  .strict();

export const GuideManifestSchema = z
  .object({
    version: z.string().regex(VERSION_RE, 'version must match "<major>.<minor>"'),
    slug: NonEmptyString,
    mode: z.literal('interactive'),
    role: NonEmptyString,
    title: LocalizedRequired,
    locales: z.array(NonEmptyString).min(1),
    defaultLocale: NonEmptyString,
    layouts: z.array(NonEmptyString),
    defaultLayout: NonEmptyString.nullable(),
    cursor: CursorTimings,
    // Per-guide default presentation template. Optional for back-compat; the
    // player resolves attr → persisted → this → 'overlay'.
    defaultTemplate: TemplateModeSchema.optional(),
    steps: z.array(ManifestStep).min(1),
  })
  .strict()
  .superRefine((manifest, ctx) => {
    if (!manifest.locales.includes(manifest.defaultLocale)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['defaultLocale'],
        message: `defaultLocale '${manifest.defaultLocale}' must appear in locales`,
      });
    }

    if (manifest.layouts.length === 0) {
      if (manifest.defaultLayout !== null) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['defaultLayout'],
          message: 'defaultLayout must be null when layouts is empty',
        });
      }
    } else {
      if (manifest.defaultLayout === null) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['defaultLayout'],
          message: 'defaultLayout must be set when layouts is non-empty',
        });
      } else if (!manifest.layouts.includes(manifest.defaultLayout)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['defaultLayout'],
          message: `defaultLayout '${manifest.defaultLayout}' must appear in layouts`,
        });
      }
    }

    for (const locale of manifest.locales) {
      if (!(locale in manifest.title)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['title', locale],
          message: `title missing for locale '${locale}'`,
        });
      }
    }

    // Layout iteration key: empty layout list ⇒ one synthetic key (""),
    // matching how the build emits a single "_" layout slot for layout-less guides.
    const layoutKeys = manifest.layouts.length > 0 ? manifest.layouts : ['_'];

    const localeSet = new Set(manifest.locales);
    const layoutSet = new Set(layoutKeys);

    manifest.steps.forEach((step, idx) => {
      for (const locale of manifest.locales) {
        const localeBag = step.captures[locale];
        if (!localeBag) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ['steps', idx, 'captures', locale],
            message: `step ${step.index}: missing captures for locale '${locale}'`,
          });
          continue;
        }
        for (const layout of layoutKeys) {
          if (!(layout in localeBag)) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['steps', idx, 'captures', locale, layout],
              message: `step ${step.index}: missing captures for locale '${locale}' / layout '${layout}'`,
            });
            continue;
          }
          // An override that overrides the text with nothing renders a blank
          // caption on that layout only — invisible in the default layout the
          // author is looking at. The step-level `text` has no such hole
          // because it is `LocalizedString`, checked below.
          if (localeBag[layout]!.content?.text === '') {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['steps', idx, 'captures', locale, layout, 'content', 'text'],
              message: `step ${step.index}: empty content.text for locale '${locale}' / layout '${layout}'`,
            });
          }
        }
        if (!(locale in step.text)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ['steps', idx, 'text', locale],
            message: `step ${step.index}: missing text for locale '${locale}'`,
          });
        }
      }
      for (const locale of Object.keys(step.captures)) {
        if (!localeSet.has(locale)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ['steps', idx, 'captures', locale],
            message: `step ${step.index}: unexpected locale '${locale}' in captures (not in manifest.locales)`,
          });
          continue;
        }
        for (const layout of Object.keys(step.captures[locale]!)) {
          if (!layoutSet.has(layout)) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['steps', idx, 'captures', locale, layout],
              message: `step ${step.index}: unexpected layout '${layout}' (not in manifest.layouts)`,
            });
          }
        }
      }
      for (const locale of Object.keys(step.text)) {
        if (!localeSet.has(locale)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ['steps', idx, 'text', locale],
            message: `step ${step.index}: unexpected locale '${locale}' in text`,
          });
        }
      }
      if (step.title) {
        for (const locale of Object.keys(step.title)) {
          if (!localeSet.has(locale)) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['steps', idx, 'title', locale],
              message: `step ${step.index}: unexpected locale '${locale}' in title`,
            });
          }
        }
      }
      if (step.subtitle) {
        for (const locale of Object.keys(step.subtitle)) {
          if (!localeSet.has(locale)) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ['steps', idx, 'subtitle', locale],
              message: `step ${step.index}: unexpected locale '${locale}' in subtitle`,
            });
          }
        }
      }
    });

    for (const locale of Object.keys(manifest.title)) {
      if (!localeSet.has(locale)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['title', locale],
          message: `unexpected locale '${locale}' in title (not in manifest.locales)`,
        });
      }
    }
  });

export type GuideManifest = z.infer<typeof GuideManifestSchema>;
export type ManifestStep = z.infer<typeof ManifestStep>;
export type Frame = z.infer<typeof Frame>;
export type Capture = z.infer<typeof Capture>;
export type CaptureContent = z.infer<typeof CaptureContent>;
export type Bbox = z.infer<typeof Bbox>;
export type CaptureSize = z.infer<typeof CaptureSize>;
export type CursorBehavior = z.infer<typeof CursorBehavior>;
export type CursorTimings = z.infer<typeof CursorTimings>;
export type CursorMode = z.infer<typeof CursorModeSchema>;
export type TemplateMode = z.infer<typeof TemplateModeSchema>;
export type ManifestAction = z.infer<typeof ManifestAction>;
