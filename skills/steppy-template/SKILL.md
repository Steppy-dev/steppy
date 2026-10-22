---
name: steppy-template
description: |
  How to STYLE and customize the appearance of Steppy guides — the whole surface, both
  render modes. Use whenever a user wants to change how guides LOOK: "style the guides",
  "brand the guides / match our brand", "change the guide colors / accent / fonts",
  "restyle the step-by-step guide", "customize the static site / navbar / landing page",
  "override a guide template", "theme the interactive player", "use the sidebar layout",
  "set a default template", "hide the cursor in the player", "style the PDF / print layout",
  "brand the handbook", "brand the narrated video", "restyle / brand the run report",
  "theme the in-app tour", "style the tour popover", or edits guide CSS,
  `templates/guide.fragment.html.j2`,
  `templates/static/guides.css`, the `--steppy-*` variables, `<steppy-tour-host>` /
  `::part` rules, or `defaultTemplate` / `cursor` /
  a guide's `template` field in `steppy.guide.config.ts`. Covers: the project `templates/`
  override mechanism, step-by-step (`.steppy-guide`) CSS, the static-site chrome (navbar/index/
  landing pages), the interactive `<steppy-guide>` player templates (overlay/sidebar/inline),
  its cursor modes and `--steppy-*` theme tokens, the in-app tour overlay's
  `--steppy-tour-*` tokens, `::part()` names and chrome strings, the print/PDF + handbook
  layout, and video branding — plus how each maps to the Angular and static build
  targets. For writing the scenario STEPS themselves, see the steppy-scenario-author skill;
  for mounting the tour overlay in the app at all, see steppy-app-tours.
---

# Styling Steppy guides

Steppy renders a guide in up to three modes, and **they are styled by different
systems** that happen to share the `steppy-` name prefix:

| Mode | What it is | Styled by | Isolated? |
|---|---|---|---|
| **step-by-step** | A scrollable HTML guide (numbered step cards + animated cursor over screenshots). Default mode. | `.steppy-guide`-scoped CSS in Jinja **templates** you can override. | No — global CSS (`ViewEncapsulation.None` / linked stylesheet). You can target the classes directly. |
| **interactive** | The Lit `<steppy-guide>` **web component** (a click-through player with overlay/sidebar/inline templates). Opt-in via `modes: ['interactive']`. | `--steppy-*` **CSS custom properties** on the element. | Yes — Shadow DOM. Only custom properties cross the boundary; you cannot target inner classes. |
| **video** | A narrated MP4 rendered from the interactive manifest. Opt-in via `modes: ['video']` **and** `--video`. | The `video:` block in `config.yaml` (branding, captions, dimensions) — **not CSS**. The page around it is a template. | N/A — it's a baked file. Restyling means re-rendering. |
| **in-app tour** | The `<steppy-tour-host>` overlay: popovers over **your live app**, compiled from scenarios with `tour:`. Opt-in via `tours.enabled` in `config.yaml`. Not a guide mode — it never appears on a guide page. | `--steppy-tour-*` **CSS custom properties** + `::part()` rules, in the **app's** global stylesheet. | Yes — Shadow DOM, but every visible piece is exposed as a `::part()`. |

A guide can use any combination (`modes: ['stepbystep', 'interactive', 'video']`). On top of the
guide modes there's a **third surface — the site chrome and landing pages** (home/index,
group landing pages, the onboarding "meta-guide" page, the per-guide onboarding strip, navbar,
footer). **How that third surface is styled depends entirely on the build target:**

- **`target: 'static'`** ships **one stylesheet, `guides.css`, that styles everything** — chrome,
  every landing page, and guide content. Override that one file to rebrand the whole site.
- **`target: 'angular'` (default) ships NO stylesheet for the chrome/landing pages.** Steppy emits
  the group/onboarding/guide components with bare `.steppy-*` classes (`ViewEncapsulation.None`,
  no `styles:` block), so **your host Angular app owns all of that CSS** — and the home page +
  navbar aren't generated at all (the host renders them from the emitted `nav.json`).

Pick the section below for what you're restyling. **Interactive features require steppy ≥ 0.2.0**;
re-run `steppy build` after upgrading so the new `steppy-player.js` bundle is copied.

---

## The `templates/` override mechanism (step-by-step + static chrome)

Steppy renders guides through Jinja templates resolved by a `ChoiceLoader` that checks your
**project's `templates/` directory first**, then falls back to the bundled copies inside the
installed `steppy` package. So you restyle the step-by-step guides and the static site by
dropping an **overriding copy** of a template into your project root, mirroring its path:

```
your-project/
  steppy.guide.config.ts
  templates/                       # ← create this; project copies win
    guide.fragment.html.j2         # step-by-step CSS (Angular + standalone)
    static/
      guides.css                   # static-site stylesheet (chrome + guide content)
```

You override **whole files**, not fragments — copy the bundled original as your starting
point, then edit. Locate the bundled originals to copy from:

```bash
python -c "import steppy, pathlib; print(pathlib.Path(steppy.__file__).parent / 'templates')"
```

Because you own the full copy, re-diff it against the bundled version after a `steppy`
upgrade so you don't miss upstream changes. There is exactly **one** tree you edit — your
project's `templates/`. (`steppy update` reconciles the rest of the Steppy-owned scaffold
after an upgrade, but it will never touch a template you have overridden; merging upstream
template changes is manual, by design.)

The same rule serves every command — `generate`, `guide`, and `build` all resolve templates
this way — and it works **per file and per subdirectory**: a project containing only
`templates/static/guides.css` overrides that one file and inherits every other bundled
template, with no top-level `*.j2` required.

---

## Step-by-step guide CSS (`.steppy-guide`)

The step-by-step guide markup is scoped under `.steppy-guide`. Its style block lives in
`templates/guide.fragment.html.j2` (Angular target + the standalone `steppy guide` output
uses `guide.html.j2`). The class surface:

- `.steppy-guide` — root (font, max-width, base color)
- `.steppy-guide h1` + `.steppy-role-badge` — guide title + role pill
- `.steppy-step`, `.steppy-step h2`, `.steppy-step-number` (the numbered circle),
  `.steppy-step-subtitle`, `.steppy-step-content` — the step card
- `.steppy-stage`, `.steppy-cursor`, `.steppy-ripple`, `.steppy-kbd` — the screenshot stage +
  animated cursor

**What's a variable vs. hardcoded:** only the cursor and z-index layers are CSS variables
(set on `.steppy-guide`); the rest are **literal colours** (a blue `#3498db`/`#2563eb`
accent, slate `#2c3e50`, grey `#7f8c8d`). There is **no palette-token system here** (unlike
the interactive player).

```css
/* the only variables the step-by-step layer exposes */
.steppy-guide {
  --steppy-cursor-size: 24px;
  --steppy-cursor-color: #0f172a;
  --steppy-cursor-accent: #2563eb;
  --steppy-cursor-ring: rgba(37, 99, 235, 0.55);
  --steppy-cursor-shadow: 0 2px 6px rgba(0, 0, 0, 0.3);
  --steppy-z-screenshot: 1; --steppy-z-cursor: 10; --steppy-z-ripple: 11; --steppy-z-glyph: 12;
}
```

### Restyling it

- **Cursor / stacking** — set the `--steppy-cursor-*` / `--steppy-z-*` variables. You can do
  this from your host app's **global** CSS (`.steppy-guide { --steppy-cursor-color: … }`)
  without overriding any template, because step-by-step CSS is global.
- **Cards, badges, titles, accent colour** (the hardcoded parts) — two ways:
  - **Angular target:** the styles are global (`ViewEncapsulation.None`), so add global CSS
    rules targeting the classes — `.steppy-guide .steppy-step-number { background: var(--brand); }`,
    `.steppy-guide h1 { border-bottom-color: var(--brand); }`. Match or beat the injected
    block's specificity (the component's `<style>` renders after your `<head>` CSS, so use a
    slightly more specific selector or load your sheet so it wins).
  - **Static target:** restyle in `templates/static/guides.css` (see next section) — the
    static pages don't use the fragment's inline block.
  - **Either, for a deep change:** override `templates/guide.fragment.html.j2` and edit its
    `<style>` block. (`guide.html.j2` for the standalone `steppy guide` HTML.)

---

## Site chrome & landing pages (index, groups, onboarding)

This is the third surface: the home page, group landing pages, the onboarding meta-guide page,
the onboarding strip shown on guide pages, the header/breadcrumb/switchers, and the footer.
Same class names on both targets — but who provides the CSS differs sharply.

The full class inventory (these are what you write/override rules for):

| Region | Classes |
|---|---|
| Header / breadcrumb | `.steppy-site-header`, `.steppy-site-title`, `.steppy-breadcrumb`, `.steppy-breadcrumb-sep` |
| Language / layout switch | `.steppy-locale-switch`, `.steppy-layout-switch` |
| Home / index | `.steppy-index`, `.steppy-index-list`, `.steppy-index-title`, `.steppy-index-eyebrow`, `.steppy-index-meta`, `.steppy-index-desc`, `.steppy-index-empty`, `.steppy-index-onboarding`, `.steppy-index-group` |
| Group landing | `.steppy-index` (shared) + `.steppy-group-description` |
| Onboarding landing | `.steppy-onboarding`, `.steppy-onboarding-description`, `.steppy-onboarding-steps`, `.steppy-onboarding-step-number`, `.steppy-onboarding-step-title`, `.steppy-onboarding-cta`, `.steppy-onboarding-start` |
| Onboarding strip (on guide pages) | `.steppy-onboarding-strip`, `.steppy-onboarding-strip-progress`, `.steppy-onboarding-strip-nav`, `.steppy-onboarding-prev`, `.steppy-onboarding-next`, `.steppy-onboarding-disabled` |
| Footer | `.steppy-site-footer` |

### Static target — restyle `guides.css`

`target: 'static'` styles **all** of the above (plus the guide content) through one stylesheet,
`templates/static/guides.css` (a real file rendered through the Jinja loader, so a project copy
overrides it). The page templates (`templates/static/{index,page,group,onboarding,interactive}.html.j2`)
just `<link>` it. **Override `templates/static/guides.css` to rebrand the whole static site**;
override a page `.j2` only to change markup/structure. The colours there are all hardcoded
(blue `#3498db` accent, slate `#2c3e50`) — there's no token layer, so edit the literals. The
static **interactive** page also has a small inline `<style>` for its `.steppy-interactive-*`
shell (wrapper, back link) in `interactive.html.j2`.

### Angular target — the host app owns this CSS

`target: 'angular'` emits the group and onboarding landing pages as standalone components
(`_groups/<group>.group.component.ts`, `_onboarding/onboarding.component.ts`) and the
onboarding strip inside the guide component — all with `ViewEncapsulation.None` and **no
`styles:` block**. Steppy ships **no stylesheet** for them. So in Angular:

- **Style the landing pages + onboarding strip in your host app's global CSS**, targeting the
  classes above. The fastest start is to **copy the relevant blocks out of the bundled
  `static/guides.css`** into your global stylesheet and rebrand from there.
- **The home page and navbar are not generated** — your app renders them from the emitted
  `<role>.nav.json` (a tree of `{titleKey, path, kind}` nodes resolved through ngx-translate).
  Their look is 100% your app's.
- **The mode/layout switchers are PrimeNG `<p-selectButton>`** inside a `.guide-toolbar` wrapper
  (no Steppy CSS) — they inherit your app's **PrimeNG theme**. Style `.guide-toolbar` for layout;
  theme the buttons via PrimeNG.

---

## Interactive player — choosing a template

The interactive player has three swappable **templates**. They apply **only** to guides built
with `modes: ['interactive']` (step-by-step ignores them).

| Template | Layout | Best for |
|---|---|---|
| `overlay` *(default)* | Full-bleed preview + a floating callout pinned to the target + a bottom bar. | Short, spatial flows. |
| `sidebar` | Left rail (title, "N steps · ~duration", a step **timeline**, the current description) beside the preview. | Longer guides; overview + jump-to-step. |
| `inline` | Full-bleed preview + one slim bottom bar (counter, description, sub-label, nav). | Minimal, embedded walkthroughs. |

Set the default in `steppy.guide.config.ts` (both fields optional):

```ts
export default defineGuideConfig({
  // …
  defaultTemplate: 'sidebar',          // project-wide (falls back to 'overlay')
  guides: [
    { slug: 'settings', title: '…', scenario: 'editor/settings.md',
      modes: ['stepbystep', 'interactive'],
      template: 'inline' },            // per-guide override; beats defaultTemplate
  ],
});
```

**Resolution order** (what a viewer sees): explicit `template="…"` attribute → the viewer's
persisted choice in `localStorage['steppy:active-template']` → the guide's `defaultTemplate`
→ `'overlay'`. The player renders its own `[Sidebar | Overlay | Inline]` switcher (plus
language + viewport switchers), so your configured default is only the **initial** template;
the viewer's pick persists across reloads and never resets the current step.

### Make `guide-text` fit the template

The templates read the three structured `guide-text` fields (see steppy-scenario-author):

| field | overlay | sidebar | inline |
|---|---|---|---|
| `title` | (unused) | **timeline heading**; falls back to `text` | (unused) |
| `text` | callout body | description panel | bar description |
| `subtitle` | mono sub-label | mono sub-label per row | mono sub-label |

**If a guide uses `sidebar`, author a short structured `title` (+ a `subtitle`) on every
step** — otherwise the timeline heading falls back to the long description.

---

## Interactive player — cursor mode

Orthogonal to the template: how much pointer chrome the player draws, and **how the viewer
advances**. Set it project-wide with `cursor`, per guide with `guides[].cursor`.

| `cursor` | What's drawn | How a step advances |
|---|---|---|
| `arrow` *(default)* | Animated arrow travelling to the target, ripple on action, keyboard-glyph overlay, pulsing hotspot | Viewer clicks the hotspot |
| `hotspot` | No arrow, ripple, or glyph — just the pulsing hotspot | Viewer clicks the hotspot (Guideflow-style) |
| `off` | Nothing — no arrow, no hotspot | **Auto-advances** after each step's `waitAfter`, like a passive walkthrough |

```ts
export default defineGuideConfig({
  // …
  cursor: 'hotspot',                   // project-wide (falls back to 'arrow')
  guides: [
    { slug: 'tour', title: '…', scenario: 'editor/tour.md',
      modes: ['interactive'],
      cursor: 'off' },                 // per-guide override
  ],
});
```

`off` is the one that changes *behaviour*, not just looks — nothing is clickable, so the guide
plays itself. Pick it for a lobby-screen or embedded-demo loop; avoid it when the point is to
teach someone where to click. `cursor` applies to the interactive player only; the step-by-step
cursor is styled through `--steppy-cursor-*` (see above) and never disappears.

---

## Interactive player — theming with `--steppy-*` tokens

The `<steppy-guide>` web component exposes its whole palette + type scale as CSS custom
properties. Because custom properties **inherit through the Shadow DOM boundary**, you theme
it by setting them on the `steppy-guide` element from your host's **global** CSS — you cannot
target its inner classes (they're shadow-isolated), only the tokens.

```css
steppy-guide {
  --steppy-accent: #0067b8;                       /* highlight, current node, counters */
  --steppy-accent-weak: rgba(0, 103, 184, 0.35);  /* the highlight glow — alpha of accent */
  --steppy-success: #2e7d32;                       /* completed-step node (sidebar) */
  --steppy-bg: #fff; --steppy-surface: #fff; --steppy-chrome-bg: #f3f4f6;
  --steppy-fg: #111827; --steppy-muted: #6b7280; --steppy-border: #e5e7eb;
  --steppy-font-serif: 'Inter', Georgia, serif;    /* titles */
  --steppy-font-sans: 'Inter', system-ui, sans-serif;   /* body/buttons */
  --steppy-font-mono: 'JetBrains Mono', ui-monospace, monospace;  /* counters/sub-labels */
}
```

| Token | Controls |
|---|---|
| `--steppy-accent` / `--steppy-accent-weak` | Highlight outline + its glow, current node, counters, focus |
| `--steppy-success` | Completed-step node in the sidebar timeline |
| `--steppy-bg` / `--steppy-surface` / `--steppy-chrome-bg` | Background / panels-rail / control bars |
| `--steppy-fg` / `--steppy-muted` / `--steppy-border` | Primary text / secondary / hairlines |
| `--steppy-font-serif` / `-sans` / `-mono` | Titles / body / counters + sub-labels |
| `--steppy-cursor-color` / `--steppy-tooltip-bg` / `--steppy-tooltip-fg` | Cursor + overlay callout (default to fg/surface) |

Set only what differs from the warm-cream/rust/green defaults. Use **system font stacks** or
fonts your host already loads — the player bundles none and makes **no network requests**.

**Where to put the rule:**
- **Angular target** *(default)*: a **global** stylesheet (e.g. `src/styles.scss`). Not a
  component's scoped styles — those don't reach the custom element.
- **Static target:** add the `steppy-guide { … }` rule to your `templates/static/guides.css`
  override (it's already linked by `interactive.html.j2`).

---

## In-app tour overlay — tokens, parts, strings

In-app tours (opt-in: `tours.enabled: true` in `config.yaml`) render through a **different
element** from the guide player: `<steppy-tour-host>`, which the app imports into its own
bundle (`import '@steppy/player/tour'`) and mounts once in its shell. It draws popovers over
the live application, so it is styled from the **app's global stylesheet** — no `templates/`
file and no `steppy build` step is involved. It is Shadow DOM with exactly two ways in, both
deliberate: public custom properties and `::part()`. (Mounting it — the packages, the Angular
adapter, CSP, the language — is `steppy-app-tours`.)

### Custom properties

Set them on `:root` or on the element — custom properties inherit through the shadow boundary.

| Property | Default | Controls |
|---|---|---|
| `--steppy-tour-bubble-bg` | `#1f1d1a` | Popover background; falls back to `--steppy-tooltip-bg` |
| `--steppy-tour-bubble-fg` | `#fbf9f4` | Popover text; falls back to `--steppy-tooltip-fg` |
| `--steppy-tour-muted` | `#c9c5bd` | Progress text ("Step 2 of 5") |
| `--steppy-tour-accent` | `#a8410e` | Primary button, spotlight ring |
| `--steppy-tour-accent-fg` | `#ffffff` | Primary button text |
| `--steppy-tour-focus` | `#f09a6a` | Focus ring |
| `--steppy-tour-scrim` | `rgba(31, 29, 26, 0.45)` | The dimming around the spotlight |
| `--steppy-tour-radius` | `6px` | Corner radius |
| `--steppy-font-family` | inherited | Unset, the bubble uses **your app's font** |

```css
/* the app's global stylesheet */
:root {
  --steppy-tour-accent: #0b5cad;   /* primary button + spotlight ring — 6.7:1 against white text */
  --steppy-tour-radius: 10px;
}
```

The defaults are a dark bubble with a rust accent. Moving to a light bubble means setting
`-bubble-bg`, `-bubble-fg`, `-muted` and `-focus` **together** — the default muted grey and
focus ring are tuned for the dark background. A project that already sets
`--steppy-tooltip-bg` / `-fg` on `:root` gets a matching bubble for free.

### Parts

Every visible piece is a `::part()`:

| Part | Element |
|---|---|
| `layer` | The full-page layer holding everything (the top-layer popover) |
| `spotlight` | The ring and scrim around the target |
| `bubble` | The popover itself — a labelled, non-modal dialog |
| `title`, `body`, `progress` | Its heading, its text, and "Step 2 of 5" |
| `button` | Every button, which also carries `primary` or `secondary` (its style) and one of `next`, `close`, `skip`, `end`, `resume` (which button it is) |
| `pill` | The "Tour paused" pill, holding `resume` and `close` |
| `guide-link` | The "Open the full guide" link |

```css
steppy-tour-host::part(bubble) { border-radius: 12px; }
steppy-tour-host::part(primary) { font-weight: 600; }
```

`::part()` matches the part element itself and nothing inside it — `::part(bubble) span` is an
invalid selector, not a narrower one. Style the named parts (`title`, `body`, `progress`)
directly; anything that is not a part is not styleable, on purpose.

### Strings

The chrome strings — Next, Done, "Step {current} of {total}", Close the tour, Skip this step,
End the tour, Resume tour, "Can't find this element", "Open the full guide", and the generic
per-action hint a step shows when its scenario has no text in that locale — ship in **English,
French, Dutch and German**. They resolve by the **tour file's** locale, never the page's, so the
buttons and the step text are always in one language; a regional locale reads its base language
(`fr-BE` → `fr`), and an unknown one falls back to English. Override keys, or add a locale, with
the host's `strings` property (a JavaScript property, not an attribute):

```js
const host = document.querySelector('steppy-tour-host');
host.strings = {
  en: { next: 'Continue', done: 'Got it' },
  es: { next: 'Siguiente', done: 'Listo', progress: 'Paso {current} de {total}' },
};
```

Keys: `next`, `progress` (`{current}`, `{total}`), `done`, `close`, `skipStep`, `endTour`,
`resume`, `paused`, `dialogLabel`, `openGuide`, `notFoundTitle`, `notFoundBody`, and
`instructionClick` / `instructionInput` / `instructionChange` / `instructionVisible` /
`instructionManual`. A key you leave out keeps the bundled value (English for a locale Steppy
does not ship — en, fr, nl and de are shipped; the compiler warns `chrome-strings-missing` for
any other tour language, so give that language every key). Step text itself is not a string
here — it comes from the scenario's `guide-text` / `tour-text`.

**Direction.** The layer carries the tour file's `lang` and `dir`, so an Arabic or Hebrew tour
lays the bubble out right to left. When you restyle parts, use logical properties
(`margin-inline-start`, `padding-inline-end`, `inset-inline-end`) rather than `left`/`right`,
or the override breaks mirroring. Placement around the target stays physical.

### Stacking and contrast

- **Stacking.** The layer uses the browser's **top layer** (`popover`) where available, so no
  z-index of yours can sit above it — there is nothing to fight. Where the top layer is
  unavailable it falls back to `baseZIndex` (the `base-z-index` attribute, default
  `2147483000`); adjust that, never your own elements' z-index.
- **Contrast.** The defaults meet WCAG AA; keep that when you override them. Text
  (`-bubble-fg` and `-muted` on `-bubble-bg`) and the primary button (`-accent-fg` on
  `-accent`) need **at least 4.5:1**. The shipped accent `#a8410e` gives 6.13:1 on white; a
  brighter "on-brand" orange is the classic way to break it.
- **Focus and screen readers are behaviour, not styling.** Focus moves into the popover only for
  manual steps and the "can't find" fallback; each step is announced through a polite live
  region. The focus ring is an outline in `--steppy-tour-focus` — recolour it there rather
  than removing it with a `::part(button)` rule.

---

## Print / PDF layout (`--pdf`, `--handbook`)

Steppy lays out no PDF of its own. `--pdf` prints the **already-rendered guide HTML** through
headless Chromium — so **the `@media print` blocks in the templates *are* the PDF layout**, and
restyling the page restyles the PDF for free. There is no separate PDF stylesheet to maintain.

Three surfaces carry a `@media print` block; edit whichever one your target uses:

| File | Applies to |
|---|---|
| `static/guides.css` | Static site pages + everything `build --pdf` prints |
| `guide.fragment.html.j2` | Angular-embedded guides |
| `guide.html.j2` | Standalone `steppy guide` HTML |

What those blocks already do, and what you inherit by not fighting them:

- **Site chrome is hidden** (`.steppy-site-header`, `.steppy-breadcrumb`, both switchers, the
  onboarding strip, the PDF link, the footer). That is exactly what lets `build --pdf` print
  the *published* page as-is instead of needing a print-only page. Inactive layouts are already
  `display:none`, so a multi-layout page prints the layout the reader is looking at.
- **`print-color-adjust: exact`** on `body`, so step badges and the role chip keep their colour
  when the viewer has background graphics off. Drop this and your accent silently disappears
  from every PDF.
- **`page-break-inside: avoid`** on each `.steppy-step` and each screenshot.
- **The cursor, ripple, and keyboard glyph are hidden** — they animate, and a frozen arrow on
  paper reads as an artifact.

### The one token you will actually need

```css
:root { --steppy-print-max-image-height: 180mm; }   /* default */
```

**Chromium cannot split an image across pages.** An uncapped full-page screenshot taller than
the printable area gets *clipped*, not continued — so screenshots are capped at this height.
Symptoms and fixes:

- **Screenshots look too small in the PDF** → raise it (`220mm`), or switch `pdf.landscape: true`.
- **A screenshot is cut off at the page edge** → your override raised it past the printable
  height. Lower it, or add margin headroom in the `pdf:` block.

Paper itself (`format`, `margin`, `landscape`, `scale`, `print_background`) is **config, not
CSS** — the `pdf:` block in `config.yaml`. Keep `print_background: true`; Playwright's own
default is `false`, which would drop every step badge.

### Handbook

`--handbook` renders `guide.book.html.j2`: a cover, a table of contents, then every guide
fragment in order. It **inlines `static/guides.css`** and adds its own `.steppy-book`,
`.steppy-book-cover`, `.steppy-book-subtitle`, `.steppy-book-toc`, `.steppy-book-section`,
`.steppy-book-guide` classes. So:

- **Rebranding `static/guides.css` rebrands the handbook automatically** — there is no fourth
  copy of the guide CSS to keep in sync.
- **Restyle the cover / TOC** by overriding `guide.book.html.j2` and editing its own `<style>`
  block.

---

## Narrated video (`modes: ['video']`)

A video is a **baked MP4**, so none of the CSS above reaches it — restyling means re-rendering
(`steppy build --video` / `steppy guide --video`). Its look is configured, not styled, in the
`video:` block of `config.yaml`:

```yaml
video:
  width: 1280            # display width; also affects duration — cursor travel is measured in display px
  max_height: null
  captions: band         # band (default) | off
  branding:
    logo: null           # path to a logo image
    accent: null         # accent colour used by the intro/outro and caption band
    font_family: null
  stamp:
    enabled: false       # burn a version/date stamp into the frame
    text: "{version} · {date}"
    version: dev
```

- **`captions: band`** draws the caption band over the footage; `off` removes it (a `.vtt`
  track still ships beside the MP4, so a player can render its own).
- **`branding.accent`** is the video's equivalent of `--steppy-accent` — set both to the same
  colour or the video will not match the site it links from.
- **`width` is not only a quality setting**: cursor travel duration is computed from *display*
  distance, so changing it changes the video's length.

The **page around** the video is a normal template. The static target's `static/video.html.j2`
links `assets/guides.css` and adds its own inline `<style>` for the
`.steppy-video-shell` / `-header` / `-back` / `-player` / `-switch` / `-downloads` classes —
the same arrangement as `interactive.html.j2`. The Angular target emits
`angular/component-video.ts.j2`, which your host app styles.

**Re-rendering is expensive** (minutes of CPU per video, plus TTS cost). The render cache is
content-addressed, so a pure styling change to the *page* costs nothing — but touching
`video.branding`, `width`, or `captions` invalidates every video. Get branding right in one
pass; `steppy video check` first, and `steppy video cache` to see what you are about to
invalidate.

---

## Overridable templates — complete map

Every file you can drop into your project's `templates/` to change appearance. **Styling**
= controls look; **structure** = markup/data only (rarely touched for styling).

| File | Controls | Kind |
|---|---|---|
| `guide.fragment.html.j2` | Step-by-step guide CSS (`.steppy-guide` block) — Angular inlines it, static suppresses it | **styling** |
| `guide.html.j2` | Standalone `steppy guide` HTML page (self-contained styles) | **styling** |
| `guide.book.html.j2` | `--handbook` document: cover, TOC, `.steppy-book-*` classes. Inlines `guides.css` | **styling** (cover/TOC only) |
| `static/guides.css` | **Static target's entire stylesheet** — chrome, all landing pages, onboarding strip, guide content, **and the `@media print` / PDF layout** | **styling** |
| `static/index.html.j2` | Static home page markup | structure (styling via guides.css) |
| `static/group.html.j2` | Static group landing markup | structure (styling via guides.css) |
| `static/onboarding.html.j2` | Static onboarding landing markup | structure (styling via guides.css) |
| `static/page.html.j2` | Static guide page (header, switchers, onboarding strip markup) | structure (styling via guides.css) |
| `static/interactive.html.j2` | Static interactive page + inline `.steppy-interactive-*` shell CSS | **styling** (shell) |
| `static/video.html.j2` | Static video page + inline `.steppy-video-*` shell CSS (player, version switcher, download links). Links `guides.css` | **styling** (shell) |
| `static/_seo_head.html.j2` | Shared SEO `<head>` fragment (`robots`/canonical/`hreflang`/description/Open Graph) `{% include %}`d by every static page — no visual output | **not styling** |
| `static/robots.txt.j2` | Emitted by `seo.mode: 'public'` — no visual output | **not styling** |
| `static/_headers.j2` | `X-Robots-Tag` rules for artifacts that cannot carry a robots meta (PDFs). Read by Netlify / Cloudflare Pages, ignored by GitHub and GitLab Pages — no visual output | **not styling** |
| `static/_mode_links.html.j2` | The "Step by Step / Interactive / Video" row, `{% include %}`d by all three renderings of a guide. Styled by `.steppy-mode-links` in `guides.css` | structure (`guides.css` styles it) |
| `static/sitemap.xml.j2` | **Not bundled.** `sitemap.xml` is generated by `src/steppy/sitemap.py`; add this file to the project to override it (context: `urls` with `.loc` / `.alternates[].hreflang` / `.alternates[].href` / `.lastmod`, plus `lastmod`) | **not styling** |
| `angular/component.ts.j2` | Guide component markup: `.guide-toolbar`, PrimeNG switchers, onboarding strip (no inline CSS) | structure (host CSS styles it) |
| `angular/component-interactive.ts.j2` | `<steppy-guide>` host + `.steppy-guide-host` inline CSS | **styling** (host wrapper) |
| `angular/component-video.ts.j2` | Angular host for a guide's narrated video | structure (host CSS styles it) |
| `angular/group.component.ts.j2` | Group landing component (bare classes, no CSS) | structure (host CSS styles it) |
| `angular/onboarding.component.ts.j2` | Onboarding landing component (bare classes, no CSS) | structure (host CSS styles it) |
| `report/report.css` | **The run report's entire stylesheet** — a separate site, see below | **styling** |
| `report/{index,scenario,coverage}.html.j2` | Run report pages (`steppy report`) | structure (styling via `report/report.css`) |
| `report/_head.html.j2` | Shared report `<head>` (always `noindex`) — no visual output | **not styling** |
| `static/cursor.js.j2`, `angular/{content,routes}.ts.j2`, `angular/gitignore.j2`, `guide.md.j2` | Cursor driver / fragments / routes / nav data | **not styling** |

> **Angular reality check:** the `angular/*.component.ts.j2` files carry **no CSS** — overriding
> them changes markup, not look. To restyle Angular landing pages / onboarding strip, write
> CSS in your **host app** (seed it from `static/guides.css`), not by editing these templates.

---

## The run report is a *separate* site (`steppy report`)

`steppy report` renders run results, not guides, and it deliberately does **not** load
`static/guides.css` — that file is guide-site chrome plus the `@media print` rules that
*are* the PDF layout, none of which a report inherits. Override
`templates/report/report.css` instead, and note that only the report pages are affected.

It is token-driven, so branding it is usually a handful of custom properties on `:root`
(the file also redefines them under `@media (prefers-color-scheme: dark)`):

```css
:root {
    --accent: #2f5fd0;      /* links */
    --pass: #1a7f45;        /* passed chips + verdict */
    --fail: #c02b2b;        /* failed chips, cause counts, the failing step number */
    --warn: #9a6200;        /* notices: truncated, no timeline, unknown outcomes */
    --surface: #ffffff;     /* cards */
    --bg: #f7f7f8;
    --border: #e3e3e7;
    --radius: 8px;
}
```

Class prefixes: `.steppy-report-*` (header, main, section, meta, table),
`.steppy-chip[data-outcome="passed|failed|error|skipped|…"]`, `.steppy-verdict`,
`.steppy-cause*`, `.steppy-step*`, `.steppy-shots`, `.steppy-variant*`, `.steppy-notice`,
`pre.steppy-tail`.

> **Two gotchas, in the order they will bite you.**
>
> 1. **`report.css` must contain no Jinja delimiters — not even inside a CSS comment.** It
>    is loaded through the Jinja loader (that is what makes the override work), so a `{{`
>    or `{%` anywhere in the file is a `TemplateSyntaxError`. Writing a comment *warning
>    about* this is how you cause it.
> 2. **The report's Jinja environment has `autoescape=True`**, unlike every other Steppy
>    template. That is deliberate — it renders failure tails, which are arbitrary page
>    content — but it means an interpolated value would come out HTML-escaped. Use CSS
>    custom properties; there is nothing this file needs from Python.
>
> Two things are not styling knobs at all: every page is hardcoded `noindex` (a run report
> has no SEO value, and publishing one to Pages publishes its screenshots), and the images
> use `width: auto; max-width: 100%` so a dense capture renders at its true pixels and a
> low-res one is never upscaled — the same rule the guide stage uses.

---

## Responsive & motion

- The interactive **screenshot variant** (desktop/mobile capture) is the player's
  "Affichage"/View switcher (driven by the `layouts` you captured) — orthogonal to the
  template. The static site has its own desktop/mobile `.steppy-layout-switch`.
- The `sidebar` template collapses to a single column (rail above preview) below ~640px of
  **container** width; `overlay`/`inline` are already full-bleed.
- The interactive player honours `prefers-reduced-motion`. The step-by-step cursor animation
  is driven by a script; reduce its motion by lowering the `guide.cursor.*` timings in the
  scenario frontmatter or omitting `guide.enabled`.
- The **in-app tour overlay** turns its anchored bubble into a **bottom (or top) sheet** on
  narrow screens (640px viewport width and below), and whenever an anchored bubble would cover
  the target. It honours `prefers-reduced-motion`: **reduced motion removes its transitions and
  smooth scrolling**. Neither needs configuring.

---

## Common mistakes

1. **Confusing the two systems** — step-by-step is global `.steppy-guide` CSS you can target
   directly; the interactive player is shadow-isolated and themeable **only** via `--steppy-*`
   tokens. A rule like `steppy-guide .steppy-step-number {…}` does nothing to the player.
2. **Looking for a palette token in step-by-step** — only the cursor/z-index are variables;
   card/badge/accent colours are hardcoded. Override the template or add global class rules.
3. **Editing generated output** (`assets/guides.css`, `interactive.html`, components) — it's
   overwritten on the next `steppy build`. Override via `templates/…` or host CSS instead.
4. **Theming the player from a scoped component stylesheet** — won't reach the shadow DOM.
   Put `steppy-guide { --steppy-… }` in a **global** stylesheet.
5. **Changing `--steppy-accent` but not `--steppy-accent-weak`** — the highlight glow keeps
   the old colour. Set it to an alpha of the new accent.
6. **Setting `template` / `defaultTemplate` on a `stepbystep`-only guide** — templates need
   `modes: ['interactive']`.
7. **Expecting `defaultTemplate` to stick after a viewer switches** — it's only the *initial*
   template; the viewer's choice persists in `localStorage`. Clear
   `localStorage['steppy:active-template']` to re-test the default.
8. **Choosing `sidebar` but writing only `text`** — the timeline heading falls back to the
   long description. Author a short structured `title`.
9. **Forgetting to re-run `steppy build` / not on steppy ≥ 0.2.0** — the templated player only
   appears once the new `steppy-player.js` bundle is copied.
10. **Overriding a template and never re-diffing after an upgrade** — you own the full copy;
    upstream CSS/markup fixes won't reach you until you merge them.
11. **Expecting the Angular landing pages / onboarding strip to come pre-styled** — they don't;
    the Angular components ship bare classes with no CSS. Write the rules in your host app
    (seed them from `static/guides.css`). Editing `angular/*.component.ts.j2` changes markup,
    not look.
12. **Trying to style the Angular mode/layout switcher with Steppy CSS** — it's a PrimeNG
    `<p-selectButton>`; theme it through your app's PrimeNG theme (and `.guide-toolbar` for
    layout).
13. **Hunting for a generated Angular home page / navbar** — neither is generated. Your app
    renders them from `<role>.nav.json`, and their styling is entirely yours.
14. **Looking for a PDF stylesheet** — there isn't one. `--pdf` prints the rendered HTML, so
    the `@media print` block in `static/guides.css` (or the fragment/standalone template) *is*
    the PDF layout.
15. **Raising `--steppy-print-max-image-height` until screenshots look big** — past the
    printable height Chromium **clips** the image rather than splitting it. Raise `pdf.margin`
    headroom or switch to `pdf.landscape: true` instead of pushing the cap.
16. **Setting `pdf.print_background: false`** (or leaving a viewer's "background graphics" off
    without `print-color-adjust: exact`) — every step badge and role chip prints blank.
17. **Trying to restyle a video with CSS** — an MP4 is baked. Its look comes from the `video:`
    config block; changing it means re-rendering with `--video`, and `branding` / `width` /
    `captions` invalidate the whole render cache.
18. **Branding the site and forgetting `video.branding.accent`** — the video is the one surface
    that does not inherit `--steppy-accent`, so it ships in the old colour.
19. **Reaching for `cursor: 'off'` to "clean up" a teaching guide** — it removes the hotspot
    too, so nothing is clickable and the guide auto-advances. If you only want to drop the
    arrow and ripple, that's `cursor: 'hotspot'`.
20. **Styling the in-app tour from an Angular component's `styles`** — component-scoped CSS
    cannot reach into the overlay's shadow DOM, so `.bubble { … }` or `.title { … }` there does
    nothing. Use the `--steppy-tour-*` custom properties or
    `steppy-tour-host::part(…)` rules, in the app's **global** stylesheet.
21. **Writing `steppy-tour-host::part(bubble) span { … }`** — parts cannot be descended into;
    the selector is invalid and the whole rule is dropped. Target the named part instead
    (`::part(title)`, `::part(body)`, `::part(progress)`).
22. **Lowering `--steppy-tour-accent` below 4.5:1** against `--steppy-tour-accent-fg` —
    the primary button's text fails WCAG AA. The same 4.5:1 floor applies to `-bubble-fg` and
    `-muted` on `-bubble-bg`; check a brand colour before shipping it.
23. **Fighting z-index to get above (or below) the tour** — the overlay sits in the browser's
    top layer, where no z-index competes with it. Raising your header's z-index changes
    nothing; where the top layer is unavailable, set the host's `baseZIndex`
    (`base-z-index`) instead.
