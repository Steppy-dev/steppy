---
name: steppy-scenario-author
description: |
  Authoritative guide for writing Steppy scenario files. Steppy is a Python CLI that
  generates Playwright pytest tests AND user-facing HTML/Markdown guides from a single
  Markdown source. Use whenever a user asks to "add a scenario", "write a Steppy test",
  "refine a recorded scenario draft", "create a user guide for X flow", "build Angular
  guides", "build a static guide site", "publish guides to GitHub Pages", "export the
  guides to PDF", "make a handbook", "render a narrated video guide", "create an in-app
  tour", "turn this flow into a product tour", writes `tour-text`, asks about
  `tours.manifest.json`, or edits any file in `scenarios/` or
  `steppy.guide.config.ts`. This skill covers the full scenario grammar: frontmatter,
  step actions, screenshots, multilingual narration, assertions, fill-form, auth, custom
  code, the cursor/guide layer, the `steppy build` integration for both Angular and
  self-contained static-site targets, the PDF / narrated-video export paths, and the
  in-app tour annotations (`tour:` and the `tour-*` step keys).

  For CI wiring, the `steppy report` run-report site, publishing results to Pages, the
  e2e-surface coverage badge and `coverage.non_ui`, use `steppy-ci-report` instead. For
  styling and theming guides, use `steppy-template`. For wiring in-app tours into the
  app's own code (mounting the overlay, the Angular adapter, test-ids, CSP), use
  `steppy-app-tours`.
---

# Writing Steppy Scenarios

Steppy turns one Markdown file into **both** an executable Playwright pytest test **and** a screenshotted HTML user guide with an animated cursor. The same file drives both — so selectors must be stable, narration must exist per role that wants a guide, and the step order must make sense to a human reader as well as a test runner.

Regenerate after editing: `steppy generate`. Validate without running: `steppy validate`.

Scenarios can be written by hand or **bootstrapped from a browser recording** with `steppy record` (see the Workflow section below) — a recording lands as a *draft* you then refine toward the grammar below.

---

## File anatomy

```markdown
---
title: Create a project   # REQUIRED
steppy: 1                     # optional; scenario-format version. Absent ⇒ 1
description: Short one-line purpose               # optional
tags:                                             # optional; enables filtering + guides
  - e2e
  - user-guide-editor          # generates an editor guide for this scenario
  - user-guide-admin       # generates an admin guide
seed: base                    # optional; string or {script, run}
priority: critical            # optional; critical|high|medium|low
auth: true                    # default true; set false ONLY for login-flow scenarios
timeout: 30000                # optional; default timeout (ms) for EACH step, not a whole-test limit
layouts:                      # optional; viewports this flow must pass in (keys of screenshot.layouts)
  - desktop
  - mobile
exclusive: billing            # optional; never run at the same time as other scenarios naming this group
tour: true                    # optional; also compile an in-app tour (needs tours.enabled) — see In-app tours
guide:                        # optional; cursor animation timings for the HTML guide
  enabled: true               # opt this scenario into `steppy run --guide` / `steppy guide --guide`
  cursor:
    pre_action_ms: 200
    action_ms: 300
    post_action_ms: 150
    travel_speed_px_per_ms: 1.5
---

### 1. Step title in plain language
- role: editor
- action: click
- selector: [data-testid="create-project-btn"]
- screenshot: editor
- screenshot-when: before       # optional; "before" | "after" (default)
- only-in: guide                # optional; "test" | "guide". Default: runs in both modes
- layouts:                      # optional; whitelist of layouts for THIS step. Default: all layouts
  - desktop
  - mobile
- guide-text:
    fr: "Cliquez sur 'Nouveau projet' en haut à droite."
    en: "Click 'New project' in the top right."
```

**Hard rules:**
- Steps are `### N. Title` headings. The number is cosmetic; order is what counts.
- Every step field is a hyphen-prefixed `- key: value` line. Nested blocks (`guide-text`, `tour-text`, `fields`, `guide-cursor`, `layouts`) are indented one level. Dict children use plain `key: value` lines (no hyphens); list children use `- item` lines.
- `extra: forbid` is enforced — unknown keys **fail validation**. If something doesn't parse, you typed a key the parser doesn't know.
- Nested blocks are **one level deep only**, except for `guide-text` which supports two levels (locale → structured dict with title/subtitle/text) when using the structured format. The same holds for `tour-text`, which takes exactly the shapes `guide-text` does.
- `steppy: <int>` declares the **scenario-format version**. Omit it and the file is version 1 — which every scenario written against this document is. It exists so a file using a future grammar fails loudly on an old Steppy (`scenario declares steppy: 2 but this Steppy supports up to version 1`) instead of dying on a confusing `extra="forbid"` error. Don't bump it by hand; add it only when a Steppy release tells you to.

---

## Actions (closed set — do not invent new ones)

| Action | Required fields | Notes |
|---|---|---|
| `navigate` | `target` (or `reload: true`) | Goes to URL; cursor hidden in guide. `wait:` tunes the load-state strategy; `reload: true` refetches the current page — see Navigation below |
| `click` | `selector` | Highlighted screenshot; cursor ripples in guide. Supports `match`/`within` (disambiguation) and `synthetic: true` |
| `fill` | `selector`, `value` | Cursor types value char-by-char in guide. Supports `clear`/`sequential`/`blur-key` flags for framework inputs |
| `fill-form` | `fields` (dict) | Cursor visits each field in declaration order. `interactive-progressive: true` captures one frame per field — see fill-form below |
| `fill-date` | `selector`, `date` | Computes a relative/dynamic date **at run time** and types it in. See Dates below |
| `select` | `selector`, `value` | Native `<select>` only. For component dropdowns (PrimeNG `p-select`/`p-autocomplete`/`p-multiselect`) use `select-option` |
| `select-option` | `selector`, (`option` or `multiple`) | Component-dropdown select: open → optional `search:` → pick. See Component dropdowns below |
| `confirm` | (nothing) | Clicks a confirmation-dialog button (PrimeNG `p-confirmDialog`). Default = accept. See Dialogs below |
| `toggle` | `selector` | PrimeNG switch (`state: on\|off`, omit = flip) or segmented button (`option: <label>`). See Toggles below |
| `expand` | `selector` | Opens a collapsible `p-fieldset` (idempotent — no-op if already open). See Expand below |
| `pick-date` | `selector`, `date` | Drives a calendar widget (month-steps + day-cell). See Date/time pickers below |
| `pick-time` | `selector`, `time` | Drives a clock widget (`time: "HH:MM"`). See Date/time pickers below |
| `hover` | `selector` | Supports `match`/`within` |
| `scroll` | `selector` | `scroll_into_view_if_needed` |
| `wait` | `duration` (e.g. `"3s"`, `"500ms"`) | `role` NOT required; used for async propagation |
| `wait-for` | `selector` | Waits for element. `state:` waits for `hidden`/`detached`/`attached` (default `visible`); supports `match`/`within`. Cursor hidden in guide |
| `wait-for-text` | `value` | Waits for text node; cursor hidden |
| `keyboard` | `value` (e.g. `"Enter"`, `"Tab"`) | Cursor hidden |
| `assert` | `expect` | See Assertions below; cursor hidden. `contains(...)` and comparator `count(...)` supported |
| `capture` | `store`, `from` | Fetch value from redis/http/shell/**dom** into a variable. See Capture below |
| `upload` | `selector`, `value` | File upload via Playwright `set_input_files`. `selector` targets the `<input type="file">`; `value` is the file path (resolved from project root). For hidden inputs behind a styled trigger, add `guide-cursor` so the screenshot/cursor lands on the visible button — see Uploads below. |
| `custom` | `code` | Arbitrary Playwright Python; cursor hidden unless `guide-cursor` override. **Last resort** — prefer the first-class actions above (see Component dropdowns, Dialogs, Dates, Toggles, etc.) |
| `login` | (nothing) | Runs the login-form flow for `role`; cursor hidden (`auth0-login` is a kept-forever alias) |

Every step except `wait` needs `role` (matching an app in `config.yaml`).

> **Prefer first-class actions over `custom`.** The actions `select-option`, `confirm`,
> `fill-date`, `toggle`, `pick-date`, `pick-time`, and `expand` — plus the `wait-for state:`,
> `assert contains/count`, `match`/`within`, `navigate wait/reload`, `capture from dom`, `fill`
> flags, and `click synthetic:` extensions — exist specifically to replace the hand-rolled
> `custom` blocks that used to dominate scenarios. They bake in event/network waits (no hard
> sleeps), manage overlay lifecycles (no `force=True`), and auto-derive the guide cursor (no
> manual `guide-cursor:`). Reach for `custom` only when none of them fit.

---

## Selectors

Use `[data-testid="..."]` whenever possible. CSS selectors work too, but test IDs survive refactors. Playwright's text selectors work via `wait-for-text` (`value: "Project created"`).

**Do NOT** use XPath, `:has-text()`, or Playwright's role selectors — Steppy emits standard `page.click(selector)` calls and the simpler the selector the better.

---

## Conditional steps — `only-in` and `layouts`

Two orthogonal step modifiers let you split a single scenario into different shapes for tests and guides, and into different shapes for desktop and mobile guides.

> **Two different `layouts:` keys.** In **frontmatter** it declares which viewports the whole flow must pass in (see below). On a **step** it whitelists the layouts that one step runs in. They compose: the frontmatter declaration picks the parametrization, the step filter narrows individual steps inside it.

### Frontmatter `layouts: [desktop, mobile]` — declare the flow's viewports

Makes responsive coverage a versioned property of the scenario instead of a flag someone has to remember:

- **Absent** — unchanged behavior: the scenario runs at the default viewport unless the run parametrizes layouts.
- **Declared** — **authoritative**: even a plain `steppy run` parametrizes the whole flow over the declared layouts.
- **Declared + `--layouts` / `--guide`** — intersection, in run-selected order. A scenario declaring `[desktop]` is silently deselected from a `--layouts mobile` run instead of producing phantom mobile captures, and `steppy build` renders it with a desktop-only layout toggle.

Declare it on flows whose responsive behavior actually matters (public/user-facing journeys); leave it off desktop-only admin flows unless you want them pinned to a viewport.

```markdown
---
title: Book an appointment
layouts:
  - desktop
  - mobile
---
```

### Frontmatter `exclusive: <group>` — flows that must not run concurrently

`--workers N` assumes every scenario is independent of every other. When two are not — they mutate the same record, drain the same mailbox, share a third-party sandbox — give them the same group name and Steppy will never run them at the same time:

```markdown
---
title: Checkout with a stored card
exclusive: billing
---
```

- **Absent** — unchanged behavior.
- **Declared** — every scenario naming that group is serialised against every other one that does. Different groups still run concurrently, and ungrouped scenarios are untouched.
- **Under `steppy run` / `--workers 1`** — inert, correctly: one pytest process already runs every file in sequence.

Mechanically the group becomes **one queue task** that a single worker claims and runs member by member, so a run can never finish faster than the group's total duration. A group is free while that total stays under *(suite total ÷ workers)*; past that, the makespan *is* the heaviest group's total. Declare the smallest group that removes the contention, not the safest-looking one.

Three things it does **not** promise:

- **Ordering.** Members may run in any order, and a retry pass re-runs only the ones that failed.
- **Verification.** The name is your assertion; two differently-named groups contending on the same system still overlap. If a flow touches two shared resources, every scenario touching *either* must use the **same** name — naming the combination (`stripe-and-mailbox`) does not exclude it against a scenario named `stripe`.
- **Cross-run exclusion.** Two concurrent `steppy run` invocations don't see each other's groups.

Name it after the contended resource (`stripe-sandbox`, `shared-mailbox`), lowercase, `[a-z0-9._+-]`. `steppy validate` warns when a group has a single member — that is what a typo looks like.

### `only-in: test` / `only-in: guide`

Restricts the step to a single mode. Default = no field = step runs in both modes.

| Value | Runs in test mode (`steppy run`) | Runs in guide mode (`steppy run --guide`) |
|---|---|---|
| (omitted) | yes | yes |
| `only-in: test` | yes | **no** |
| `only-in: guide` | **no** | yes |

**When to use `only-in: guide`** — pedagogical steps that exist purely to make the guide make sense, but don't belong in regression tests:
- A `hover` over a tooltip just to capture it for the guide screenshot
- An extra "tap hamburger menu" navigation step on mobile that desktop tests don't need
- A `wait` to let an animation finish before the guide screenshot, when the test doesn't care

**When to use `only-in: test`** — strict assertions that are flaky or noisy in multilingual guide capture but matter in CI:
- A `text(...)` assertion that's tied to a specific locale and breaks under multi-locale parametrization
- An assertion that double-checks something already covered by the visible guide flow

### Step-level `layouts: [desktop, mobile]`

Whitelists layouts the step runs in (works in both test and guide modes). **The filter only applies when a layout is actively parametrized** (i.e. the run set `STEPPY_LAYOUTS` via `--layouts` or via `--guide` with `screenshot.layouts` configured). When no layout is active — plain test runs without `--layouts`, or guide runs that don't parametrize over layouts — the layout filter is ignored and the step runs at the default viewport. Default = no field = step runs in every configured layout.

```markdown
### 3. Tap hamburger menu (mobile only)
- role: editor
- action: click
- selector: [data-testid="hamburger"]
- screenshot: editor
- only-in: guide        # this step is purely a guide concern
- layouts:              # ...and only relevant on mobile
  - mobile
- guide-text:
    fr: "Touchez l'icône menu pour ouvrir la navigation."
    en: "Tap the menu icon to open navigation."
```

The desktop guide page skips this step entirely; the mobile guide page renders it. Layouts must be declared in `config.yaml` under `screenshot.layouts:` (see "Per-layout screenshots" below).

### The twin-branch pattern (and the one rule it must obey)

When the same logical step is reached differently per layout — a sidebar link on
desktop, a hamburger drawer on mobile; a table row on desktop, a card on mobile —
author it **twice**, once per layout, as *twins*:

```markdown
### 9. Open "Profil" from the sidebar (desktop)
- role: admin
- action: click
- selector: '[data-testid="navbar__menu--profile"] a[href="/users"]'
- screenshot: admin
- layouts:
  - desktop
- guide-text:
    fr: "Dans la barre latérale, cliquez sur « Profil »."

### 12. Open "Profil" from the drawer (mobile)
- role: admin
- action: click
- selector: '[data-testid="navbar__menu--profile"] a[href="/users"]'
- screenshot: admin
- layouts:
  - mobile
- guide-text:
    fr: "Touchez l'icône du menu, puis « Profil »."
```

**The rule: every layout must contribute the same number of *captured* steps**
(steps carrying `screenshot:`). The interactive player renders one step list with
a layout toggle, so twins are merged by **position** — desktop's 9th capture and
mobile's 9th capture become manifest step 9 — and an unmatched capture in one
layout has no slot to live in. `steppy build` fails with `non-uniform step
coverage` and prints which scenario steps each layout covers. Scaffolding steps
(the desktop `hover`, the mobile `wait-for` + hamburger tap) carry **no**
`screenshot:`, so they never count — only the twins have to balance.

Each twin keeps its **own** `guide-text`, and both the step-by-step page and the
interactive player show the right wording per layout. Twins should share the same
`action` (a mismatch warns — it usually means the twins are paired out of order)
and must agree on whether they are cursor-bearing (a `click` twinned with a
`navigate` is a hard error, because the player cannot both wait for a click and
auto-advance on the same step).

### Combining `only-in` and `layouts`

**Every combination is valid** — the two filters are independent and compose:
- `only-in: test` + `layouts: [mobile]` — runs only *outside* guide mode, and only when the mobile layout is parametrized. This became meaningful once `steppy run --layouts desktop,mobile` started working in test mode: it is how you assert something that is true only at the mobile viewport without putting that assertion in the guide.
- `only-in: guide` + `layouts: [mobile]` — guide-only AND mobile-only (most common for nav-flow differences). When the guide run is parametrized over layouts, this step runs only in the mobile pass; when guide mode is on but no layouts are parametrized, the layout filter is ignored and the step runs once at the default viewport.
- `layouts: [mobile]` alone — runs in test mode unchanged (layout filter is inert without `STEPPY_LAYOUTS`); in a layout-parametrized guide run, runs only on mobile.
- `only-in: guide` alone — runs in every layout pass during guide capture, never during `steppy run`.

---

## Screenshots and the `screenshot` field

`screenshot` controls WHEN a step is captured AND which role-specific guide it appears in.

| Value | Meaning |
|---|---|
| `false` / omitted | No screenshot taken |
| `true` | Always captured, appears in all role guides |
| `"editor"` (or other role) | Captured and appears only in that role's guide |
| `"all"` | Captured and appears in every role guide |

**Rule of thumb**: if a step produces a user-visible state change that a human guide reader needs to see, set `screenshot: <role>` and write `guide-text`. Otherwise set `screenshot: false`. Intermediate UI steps (typing into a search box, opening a dropdown) are usually `false`.

By default, screenshots are captured **after** the action fires, showing the post-action state. The bounding box of the target element is captured after scrolling it into view, and stored in a JSON sidecar next to the PNG — this drives the cursor animation in the HTML guide. You don't need to configure this; it happens automatically.

### `screenshot-when: before` — capture before the action

Sometimes the action destroys or navigates away from the target, so an "after" screenshot makes the guide nonsense (the cursor would point at a button that is no longer on screen). Opt into pre-action capture with `screenshot-when: before`:

```markdown
### 6. Click "New project"
- role: editor
- action: click
- selector: [data-testid="project-list-btn-create"]
- screenshot: editor
- screenshot-when: before     # capture the list with the cursor on the button,
                              # NOT the modal that opens after the click
- guide-text:
    fr: "Cliquez sur **Nouveau projet** pour ouvrir le formulaire."
    en: "Click **New project** to open the form."
```

Supported on every interactive action: `click`, `fill`, `fill-form`, `fill-date`, `select`, `select-option`, `confirm`, `toggle`, `expand`, `pick-date`, `pick-time`, `hover`, `upload`, `custom`. Non-interactive actions (`wait`, `wait-for`, `assert`, `keyboard`, `capture`, …) ignore the key. Default is `after`.

**When to use `before`**:
- `click` that opens a modal, navigates to a new page, or unmounts the element
- `click` on the final "Submit" button of a form (the post-click state is a toast or a redirect)
- `custom` step whose code navigates away

**When to leave as `after` (default)**:
- `fill` / `fill-form` where you want the guide reader to see the populated values
- `click` that reveals a dropdown or in-place change on the same page

---

## Guide text (multilingual narration)

Every step with a screenshot that appears in a guide **MUST** have `guide-text` for every locale the project uses (typically `fr`, `en`; sometimes `nl`, `de`). Missing narration fails `steppy guide`.

`guide-text` is also **the script a narrated video speaks** — see "Video guides" below. If the project renders videos, write it to be heard, not only read.

### Simple format (backward compatible)

```markdown
- guide-text:
    fr: "Depuis le menu, cliquez sur 'Projets'."
    en: "From the menu, click 'Projects'."
```

The first sentence (before the first period) becomes the step heading in the rendered guide. The full text appears as the description.

### Structured format (title, subtitle, text)

For more control over guide presentation, use the structured format:

```markdown
- guide-text:
    fr:
      title: "Accéder aux projets"
      subtitle: "Navigation principale"
      text: "Depuis le menu principal, cliquez sur 'Projets' pour voir la liste complète de vos projets."
    en:
      title: "Access projects"
      subtitle: "Main navigation"
      text: "From the main menu, click 'Projects' to see the full list of your projects."
```

**Fields:**
- `title` (required) — Short heading displayed in the step badge (e.g., "Access dashboard")
- `subtitle` (optional) — Secondary heading in italic below the title (e.g., "Main navigation")
- `text` (optional) — Full description paragraph displayed after the subtitle

**When to use structured format:**
- When you need a concise title separate from detailed instructions
- When you want to group related steps under a common subtitle (e.g., "Form filling", "Data validation")
- When the simple format's auto-extracted title is awkward (no period in sight, or first sentence too long)

**You can mix both formats in the same scenario** — some steps simple, others structured. Both work identically in generated guides.

**Tone**: user-facing, imperative, no jargon. Titles should be 2-5 words max.

---

## Assertions — the `expect` field

`expect` adds a Playwright assertion after the action. It is a short DSL string, not raw Python:

| Form | Example |
|---|---|
| Toast/snackbar visible | `toast("Projet créé")` |
| Row contains text | `row-contains("Website redesign")` |
| Element visible | `visible([data-testid="foo"])` |
| Element hidden | `hidden([data-testid="foo"])` |
| Element has text (exact) | `text([data-testid="status"], "Paid")` |
| Element **contains** text (substring) | `contains([data-testid="status-tag"], "In progress")` |
| Element count (exact) | `count(.row, 3)` |
| Element count (**comparator**) | `count([data-testid^="site-list-row-"], >=1)` |
| Element count == 0 (removed from DOM) | `count([data-testid="temp-picker"], 0)` |
| URL contains path | `url-contains(/orders)` |
| Not empty | `not-empty([data-testid="list"])` |
| Disabled / enabled | `disabled(button#submit)` / `enabled(...)` |

`expect` can be attached to any action (not just `assert`). A standalone verification step uses `action: assert` and only `expect`.

**`contains(...)` vs `text(...)`**: `text(...)` is exact full-text and `contains(...)` is a
substring match (Playwright `to_contain_text`, auto-waiting). Use `contains` for tags/badges
whose surrounding text varies — it replaces the old `assert "X" in el.inner_text()` custom blocks.

**`count(...)` comparators**: the second argument accepts `>= <= == != > <` (e.g.
`count(.row, >=1)`, `count(.row, !=0)`). A **bare integer** (`count(.row, 3)`) keeps the exact
`to_have_count` check. The dominant uses are `>=1` ("at least one row appeared") and `0` ("the
row was removed"). Like every other assertion, a comparator **waits** — it re-counts until the
comparison holds or the step's timeout (`timeout:` → scenario `timeout:` → `timing.step_timeout`)
runs out. So `<` and `<=`, like `count(.row, 0)`, pass at once on a list that has not rendered
yet: assert something that proves the page loaded first. Interpolation works in the selector: `contains([data-testid="item-{{record_id}}"], "...")`.

---

## `fill-form` — multi-field forms

One step, many fields, visited in declaration order by the guide cursor:

```markdown
### 3. Editor fills the new member form
- role: editor
- action: fill-form
- fields:
    [data-testid="member-first-name"]: "Jean"
    [data-testid="member-last-name"]: "Dupont"
    [data-testid="member-email"]: "jean@example.com"
- screenshot: editor
- guide-text:
    fr: "Remplissez les informations du membre."
    en: "Fill in the member's information."
```

**Tall forms auto-split into multiple frames.** If the form is too tall to fit in one viewport, Steppy automatically captures additional screenshots (`step-NN-a.png`, `step-NN-b.png`, …) so that every field is fully visible in at least one frame. The HTML guide stacks the frames vertically under the same narration, and the animated cursor visits each field **exactly once** — in the first frame where that field appears. You don't configure this; the runtime decides how many frames are needed based on field coverage.

**`screenshot-when: before`** on a fill-form captures the **empty** form (pre-fill) across as many frames as needed — useful when the guide reader should see pristine fields before narration walks them through filling.

### `interactive-progressive: true` — watch the form fill up, field by field

Default fill-form capture shows the form once (empty or filled). `interactive-progressive: true` instead captures **one frame per field**: for each field in declaration order it scrolls the field into view, screenshots the form's *current* state with the cursor aimed at that one field, then fills it — followed by a final settled frame with the whole form completed and no cursor.

```markdown
### 3. Editor fills the new member form
- role: editor
- action: fill-form
- interactive-progressive: true
- fields:
    [data-testid="member-first-name"]: "Jean"
    [data-testid="member-last-name"]: "Dupont"
    [data-testid="member-email"]: "jean@example.com"
- screenshot: editor
- guide-text:
    fr: "Remplissez les informations du membre."
    en: "Fill in the member's information."
```

- **Only valid on `fill-form`** — any other action fails validation with `'interactive-progressive' is only valid on fill-form steps`.
- Frames reuse the same `step-NN-a`, `-b`, … letters as the automatic tall-form split, so the guide builder consumes them unchanged. A 3-field form produces 4 frames (three fills + settled).
- **Where it pays off**: the interactive player, which advances frame-by-frame on a click, so the viewer watches the form populate one field at a time instead of jumping from empty to full.
- **What it costs**: one screenshot *and* one fill per field, so capture time grows with field count. Opt in on the forms where the progression teaches something — not on every form.

---

## Timing helpers

- `wait_after: 500` — sleeps 500ms after the action, before the screenshot. Use sparingly, only when UI animations need to settle.
- `action: wait` with `duration: "5s"` — a standalone wait step (no role needed). Use for async backend propagation (SQS, webhooks). Add a `note:` explaining why.
- Prefer `wait-for` / `wait-for-text` over `wait` wherever possible — explicit beats timed.

### Timing and flakiness

A fixed `wait` encodes an assumption about how fast the machine is. It holds on your laptop and breaks on a loaded CI box or under `steppy run --workers N`, where several stacks compete for CPU — which is exactly when a suite starts failing intermittently for no visible reason.

`steppy flaky` ranks scenarios by how often they fail-then-pass. When one of yours shows up there, the fix is almost always in the scenario, not in the runner:

- Replace a `wait` with `wait-for` on the element the next step actually needs.
- After a save, wait for the *success signal* rather than a duration: `wait-for` with `state: hidden` on the dialog, or `wait-for-text` on the toast.
- After a navigation, let `navigate`'s `wait:` do the work instead of sleeping.
- If a step genuinely needs a fixed delay (backend propagation you cannot observe from the UI), keep the `wait` and put the reason in `note:` so the next reader doesn't "optimise" it away.

A scenario that `steppy flaky` reports as failing *the same way every time* (it prints a single-fingerprint hint) is not flaky at all — that is a real bug or a stale selector.

---

## Authentication

Scenarios default to `auth: true`, which means the runner pre-authenticates every role's page via the mode in `config.yaml` (`bypass`, `form`, `storage-state`, `token`). You don't write login steps unless you're testing login itself.

For form-based login flows the scenario uses `auth: false` and `action: login` per role:

```markdown
---
title: Login flow
auth: false
---

### 1. Navigate to login
- role: editor
- action: navigate
- target: /login

### 2. Complete the login form
- role: editor
- action: login
```

---

## `custom` — escape hatch for arbitrary Playwright

When no built-in action fits (drag-and-drop, niche keyboard choreography, etc.) use `custom` with raw Python. The `page` variable is bound to the step's role page. **Check the action table first** — most things that used to need `custom` now have a first-class action: component dropdowns → `select-option`, confirmation dialogs → `confirm`, relative dates → `fill-date`, calendar/clock widgets → `pick-date`/`pick-time`, switches → `toggle`, collapsibles → `expand`, file uploads → `upload`, DOM value reads → `capture from.type: dom`, and `.first`/`.nth` strict-mode escapes → `match`/`within`.

```markdown
### 5. Type a date into a masked input
- role: editor
- action: custom
- code: |
    page.locator('[data-testid="start-date"] input').click()
    page.keyboard.type("21-06-1985")
- screenshot: editor
- guide-cursor:
    behavior: type
    target: '[data-testid="start-date"] input'
    value: "21-06-1985"
- guide-text:
    fr: "Saisissez la date au clavier."
    en: "Type the date on the keyboard."
```

The optional **`guide-cursor`** block tells the HTML guide renderer to show a cursor on this custom step (custom steps hide the cursor by default). `behavior` is `click | type | hover`; `target` is a CSS selector; `value` applies only to `type`.

---

## `upload` — file uploads

`action: upload` codegens to Playwright's `locator(selector).set_input_files(value)` and wires up bbox capture, screenshots, and cursor animation the same way `click` / `fill` do.

```markdown
### 3. Editor uploads the report PDF (visible input)
- role: editor
- action: upload
- selector: input[data-testid="report-file"]
- value: fixtures/uploads/report.pdf
- screenshot: editor
- guide-text:
    fr: "Sélectionnez le fichier PDF du rapport depuis votre ordinateur."
    en: "Pick the report PDF file from your computer."
```

- `selector` (required) — the `<input type="file">` element.
- `value` (required) — the file path passed to `set_input_files`. Resolved relative to the project root (pytest's CWD). Conventional location is `fixtures/uploads/<name>`. Variables interpolate (`{{record_id}}.pdf`).
- All the usual fields apply: `screenshot`, `screenshot-when`, `wait_after`, `expect`, `only-in`, `layouts`, `guide-text`.

### Hidden inputs behind a styled button or drop zone

Most modern UIs visually hide the `<input type="file">` and render a styled button or drop zone as the visible trigger. `set_input_files` must still target the hidden input, but the **screenshot and cursor** should land on the visible thing. Add `guide-cursor` with the visible selector:

```markdown
### 4. Editor uploads an attachment via the drop zone (hidden input)
- role: editor
- action: upload
- selector: input[data-testid="attachment-file"]          # hidden input — Playwright targets this
- value: fixtures/uploads/attachment.png
- guide-cursor:
    behavior: click
    target: '[data-testid="attachment-dropzone"]'          # visible trigger — cursor + bbox land here
- screenshot: editor
- guide-text:
    fr: "Glissez votre fichier sur la zone de dépôt, ou cliquez pour parcourir vos fichiers."
    en: "Drop your file on the drop zone, or click to browse your files."
```

The `guide-cursor.target` selector is quoted because the brackets would otherwise be parsed as a YAML list when nested under `guide-cursor:`. Same gotcha as any nested CSS-attribute selector.

For multiple files in one input, use `custom` for now — `value:` is a single path in v1.

---

## `capture` — fetch values from external sources

Stores a value into `_vars` for later interpolation. Used for dynamic IDs, OTP codes, etc.

```markdown
### 4. Fetch generated record ID from Redis
- role: editor
- action: capture
- store: record_id
- from:
    type: redis
    key: "latest_record_id"
- retry: 5
- interval: "500ms"
```

Use `{{record_id}}` in later `target`, `selector`, `value`, or `custom` code — Steppy interpolates at test time.

Valid `from.type`: `redis` (needs `key`), `http` (needs `url`), `shell` (needs `command`), `dom` (needs `selector` + the step's `role`).

### `from.type: dom` — read a value off the live page

Grabs a generated ID, OTP, or status off a rendered element and stores it in `_vars` (interpolatable) — replacing the old `globals()['record_id'] = ...` custom hack. Requires `role` (it reads that role's page).

```markdown
### 5. Capture the generated record id from the status badge
- role: editor
- action: capture
- store: record_id
- from:
    type: dom
    selector: '[data-testid^="items-list-status-"]'
    extract: attribute                   # text (default) | attribute | regex
    attribute: data-testid               # required when extract: attribute
    regex: 'items-list-status-(.+)'     # post-filter: capture group 1 → value
```

- `extract: text` (default) — the element's inner text.
- `extract: attribute` — the value of `attribute:` (required).
- `extract: regex` — runs `regex:` (required) against the element's **inner text** and stores capture group 1.
- `regex:` also acts as an optional post-filter on the other extract modes — group 1 (or the whole match when the pattern has no groups) becomes the stored value. The example above reads `data-testid`, then strips the prefix with the regex.

Then reuse anywhere: `expect: visible([data-testid="pending-item-{{record_id}}"])`.

---

## Component dropdowns — `select-option`

PrimeNG-style component dropdowns (`p-select`, `p-autocomplete`, `p-multiselect`) are **not** native `<select>`, so `action: select` does not drive them — use `select-option`. The `selector` is the dropdown **trigger/host**; the action opens it, optionally types into the filter box, waits for the option list to settle, then picks.

```markdown
### 4. Pick an assignee from the searchable dropdown
- role: editor
- action: select-option
- selector: '[data-testid="task-select-assignee"]'
- search: "Mart"          # optional: type into the filter box first
- option: first             # first | last | N (index) | text:"Alex Martin"
- screenshot: editor
- guide-text:
    fr: "Choisissez le responsable dans la liste."
    en: "Pick the assignee from the list."
```

- `option:` — exactly one of `first`, `last`, an integer index (`option: 3`), or `text:"..."`.
- `search:` (optional) — typed into the filter box; the action then settles per `config.dropdown.settle` (`api` = wait for network-idle, `none` = no wait).
- **Multiselect** — replace `option:` with `multiple:` as an **indented YAML list** (inline `[a, b]` is **not** parsed as a list); it auto-`Escape`s to close:

```markdown
- action: select-option
- selector: '[data-testid="role-select"]'
- multiple:
    - "Role A"
    - "Role B"
```

`option` and `multiple` are mutually exclusive. Framework selectors come from the `dropdown:` config preset (PrimeNG defaults, so it works with no config — see Config presets below). The guide cursor auto-targets the trigger.

---

## Dialogs — `confirm`

Clicks a confirmation-dialog button (PrimeNG `p-confirmDialog`, whose accept/reject buttons have **no `data-testid`**). No `selector` needed — selectors come from the `dialog:` config preset. Default = accept the active (`.last`) overlay.

```markdown
### 6. Confirm the deletion
- role: editor
- action: confirm           # default = accept
- screenshot: editor
- guide-text:
    fr: "Confirmez la suppression."
    en: "Confirm the deletion."
```

Optional fields:
- `variant: reject` — cancel instead of accept.
- `danger: true` — prefer the destructive/danger button.
- `button: "Oui"` — match by visible label (mutually exclusive with `variant`/`danger`).

The cursor auto-targets the resolved button.

---

## Dates — `fill-date`

Computes a relative/dynamic date **at test run time** (so the test stays valid forever) and types it into a date input via sequential keystrokes + `Tab` (fires Angular's `ngModelChange`). Replaces the `datetime`/`timedelta`/`strftime` boilerplate that used to live in `custom`.

```markdown
### 5. Set the start date to this Monday
- role: editor
- action: fill-date
- selector: '[data-testid="task-input-start-date"] input'
- date: this-monday         # see grammar below
- format: "%d/%m/%Y"        # optional; default "%d/%m/%Y"
- screenshot: editor
- guide-text:
    fr: "Choisissez le lundi de cette semaine comme date de début."
    en: "Set this Monday as the start date."
```

**`date:` grammar** — `today` | `this-<weekday>` | `next-<weekday>` | `±N[d|w|m|y]` (e.g. `+7d`, `-1m`, `+2w`) | ISO literal `YYYY-MM-DD`.

`method: calendar` is **rejected** — for a read-only field whose only input is a popup widget, use `pick-date` instead.

---

## Date/time pickers — `pick-date` / `pick-time`

When a date/time field is read-only and only its popup widget works, these drive the calendar (month-step + day-cell) and clock (hour/minute spinner) so you don't hand-write the loops.

```markdown
### 7. Pick a date 7 days out from the calendar
- role: editor
- action: pick-date
- selector: '[data-testid="task-form-date-datepicker"]'
- date: +7d                 # same grammar as fill-date

### 8. Pick the start time
- role: editor
- action: pick-time
- selector: '[data-testid="event-start-time-datepicker"]'
- time: "09:00"             # 24h HH:MM
```

Widget selectors come from the `datepicker:` config preset (PrimeNG defaults). The cursor auto-targets the widget.

---

## Toggles — `toggle`

Drives a PrimeNG `ToggleSwitch` (a styled `input[role="switch"]`) or a segmented `p-togglebutton` group.

```markdown
### 9. Enable recurrence (switch)
- role: editor
- action: toggle
- selector: '[data-testid="task-toggle-recurrence"]'
- state: on                 # on | off | (omit = flip)

### 10. Choose the "Range" mode (segmented button)
- role: editor
- action: toggle
- selector: '[data-testid="task-mode-select"]'
- option: "Range"           # pick by visible label or index
```

`state` (switch) and `option` (segmented) are mutually exclusive. Switch/segment selectors come from the `toggle:` config preset.

---

## Expand — `expand`

Opens a collapsible PrimeNG `p-fieldset` by clicking its legend/toggle. **Idempotent** — a no-op if the section is already open — and waits for the body via an event (not a hard sleep).

```markdown
### 4. Open the advanced settings section
- role: editor
- action: expand
- selector: '[data-testid="project-detail-tab-advanced"]'
- screenshot: editor
- guide-text:
    fr: "Dépliez la section des paramètres avancés."
    en: "Expand the advanced settings section."
```

Toggle/content selectors come from the `fieldset:` config preset. The cursor auto-targets the toggle.

---

## Navigation — `navigate` `wait:` and `reload:`

`navigate` defaults to awaiting `networkidle` after `goto`. On apps with long-lived SSE/streaming connections that never settles, so tune the strategy with `wait:`:

```markdown
- action: navigate
- target: /projects/{{project_id}}
- wait: domcontentloaded    # networkidle (default) | load | domcontentloaded | none
```

`reload: true` hard-refetches the **current** page (the post-async/SQS-propagation idiom) — it reuses the same `wait:` handling and must **not** be combined with `target:`:

```markdown
- action: navigate
- reload: true
- wait: networkidle
```

Omitting both keeps existing `navigate` steps byte-identical.

---

## Locator disambiguation — `match` / `within`

When a selector matches more than one element (any list or table row), Playwright throws a strict-mode violation. Add `match` / `within` to `click`, `hover`, `wait-for`, or `assert` instead of dropping to a `custom` `.first`/`.nth(...)` block:

```markdown
### 3. Click the first item row
- role: editor
- action: click
- selector: '[data-testid="item-row"]'
- match: first              # first | last | N (index)

### 4. Click the assignee select inside that row
- role: editor
- action: click
- selector: 'app-assignee-select'
- within: '[data-testid="item-row"]'   # scope to a parent first
- match: 0
```

`within` scopes to a parent before resolving; `match` picks among the matches. Absent ⇒ today's behavior (byte-identical). Guide-mode bbox resolves the same disambiguated locator.

---

## `fill` flags — framework inputs

Framework inputs (`p-inputNumber`, masked fields) ignore a plain `page.fill`. The flags click → select-all → type sequentially → commit. All default off ⇒ existing `fill` steps stay byte-identical.

```markdown
- action: fill
- selector: '[data-testid="room-form-capacity-input"] input'
- value: "2"
- clear: true               # select-all + delete before typing
- sequential: true          # press_sequentially (fires keydown/input) instead of fill
- blur-key: Tab             # key pressed after typing to commit the change
```

---

## `click synthetic:` — elements that swallow `.click()`

For elements that ignore Playwright's native click, `synthetic: true` dispatches a manual mousedown/mouseup/click sequence via `evaluate`. **First** try a better selector or a normal click; reach for this only if those fail. Composes with `match`/`within`; absent ⇒ byte-identical native `.click()`.

```markdown
- action: click
- selector: '[data-testid="new-member-type__btn--next"]'
- synthetic: true
```

---

## Config presets — framework selectors

The `select-option`, `confirm`, `toggle`, `pick-date`/`pick-time`, and `expand` actions read their framework-specific selectors from preset blocks in `config.yaml`. **Defaults target PrimeNG**, so a PrimeNG app needs no config at all. Override only what differs:

```yaml
# config.yaml — every block is optional; shown values are the PrimeNG defaults
dropdown:                    # select-option
  trigger: "p-select"
  overlay: ".p-select-overlay"
  option:  ".p-select-option, .p-select-list-container li"
  filter:  ".p-select-filter-container input, .p-select-filter"
  settle:  "api"             # api = wait networkidle after typing | none

dialog:                      # confirm
  overlay: ".p-confirmdialog, p-confirmdialog"
  accept:  "button.p-confirmdialog-accept-button, button.p-confirm-dialog-accept"
  reject:  "button.p-confirmdialog-reject-button, button.p-confirm-dialog-reject"
  danger:  "button.p-button-danger, button.p-confirmdialog-accept-button"

toggle:                      # toggle
  switch: 'input[role="switch"]'
  segment: ".p-togglebutton"

datepicker:                  # pick-date / pick-time
  panel: ".p-datepicker-panel"
  next_button: ".p-datepicker-next-button"
  prev_button: ".p-datepicker-prev-button"
  day_cell: ".p-datepicker-day-cell:not(.p-datepicker-other-month)"
  trigger: "button.p-datepicker-dropdown, button.p-datepicker-trigger"
  hour_picker: ".p-datepicker-hour-picker"
  minute_picker: ".p-datepicker-minute-picker"

fieldset:                    # expand
  toggle: ".p-fieldset-toggle-button, .p-fieldset-legend"
  content: ".p-toggleable-content, .p-fieldset-content-container"
```

To convert existing `action: custom` blocks into these declarative steps mechanically, run `steppy migrate custom --diff` (a dry run by default; `--write` applies, `--skipped` lists what was left alone and why). A block is rewritten only when every statement in it maps to a declarative step and the rewrite verifies.

---

## Tags — how guides get generated

Tags are the bridge between scenarios and guide generation:

- `e2e` — standard tag for end-to-end tests; filters in CI.
- `user-guide-{role}` — when present, running `steppy guide --tag user-guide-editor` generates a Markdown + HTML user guide for that role, using only the steps where `screenshot: editor` (or `"all"`). Pair with `guide.enabled: true` in frontmatter to include the scenario in the one-shot `steppy run --guide` / `steppy guide --guide` workflow.
- Project-specific tags (e.g. `billing`, `regression`, `smoke`) — freely invented for filtering.

A scenario can appear in multiple role guides: add both `user-guide-editor` and `user-guide-admin` tags, and steps tagged with each role get filtered into the respective guide.

---

## Common mistakes (the ones that actually bite)

1. **Forgetting `guide-text` on a screenshot step** — `steppy guide` fails with "Missing guide-text for locale 'fr' in steps: [3, 7]". Fix: add the translations.
2. **Typo in a field name** — `extra = "forbid"` rejects it. Error looks like `ValidationError: ... Extra inputs are not permitted`. Fix: check the action table above.
3. **Inventing new action types** — only the actions in the table are valid. `action: type` is wrong; it's `fill`. `action: press` is wrong; it's `keyboard`.
4. **Nesting `guide-text` values too deep or forgetting the indent** — `guide-text:` must be followed by a 2-space-indented `fr: "..."` line. No hyphens on nested lines.
5. **`screenshot: editor` without a matching role** — the `role` must exist in `config.yaml` under `apps:`. Unknown roles fail validation.
6. **Using relative URLs without a leading slash** — `target: orders` is ambiguous. Always write `target: /tasks`.
7. **Missing `auth: false` on a login-flow scenario** — the runner will try to pre-auth before the test runs, short-circuiting the flow. Login scenarios must opt out.
8. **Raw `page.*` calls outside `custom`** — every non-custom action generates the right Playwright call for you. Don't embed Python in regular steps.
9. **Multi-line string in simple `guide-text` format without quotes** — wrap in double quotes: `fr: "First sentence. Second sentence."`. Or switch to structured format with explicit `title` and `text` fields.
10. **Missing `title` in structured `guide-text`** — when using the structured format (`fr: { title: ..., subtitle: ..., text: ... }`), the `title` field is mandatory. `subtitle` and `text` are optional.
11. **Selectors with single quotes inside double-quoted `custom` code** — use Python triple quotes or escape, same as any Python string.
12. **Click step whose screenshot shows the *next* page, not the button you're clicking** — the default is `screenshot-when: after`, so a click that navigates unmounts the target before the screenshot. Add `screenshot-when: before` to that step. Same applies to "Submit" clicks whose post-action state is a toast + redirect.
13. **`fill-form` screenshot showing a half-filled form** — the whole form is captured *after* all fills by default. If you want the pre-fill state, add `screenshot-when: before`.
14. **Expecting `layouts: [mobile]` to exclude a step from a plain `steppy run`** — the layout filter only fires when a layout is *actively parametrized* (`--layouts`, `--guide`, or a frontmatter `layouts:` declaration). With no layout active, the filter is ignored and the step runs at the default viewport. Use `only-in:` to gate by mode; `layouts:` gates by viewport, and the two compose freely (all four combinations are valid).
15. **Referencing a layout name that isn't declared in `config.yaml`** — `layouts: [tablet]` fails validation when `screenshot.layouts` only declares `desktop` and `mobile`. The error names the unknown layout and lists the configured ones.
16. **Adding `layouts:` to every step "to be safe"** — unnecessary. Steps without the field run in every layout (the desired default). Only add `layouts:` when the step genuinely diverges between desktop and mobile (e.g., an extra hamburger-menu tap on mobile).
17. **`action: upload` with `guide-cursor.target` left unquoted** — `target: [data-testid="dropzone"]` nested under `guide-cursor:` is parsed as a YAML list. Quote it: `target: '[data-testid="dropzone"]'`. Top-level `selector:` lines don't need quoting because the parser treats them as raw strings, but anything inside a nested block does.
18. **Reaching for `custom` to upload a file** — `action: upload` is now first-class. Use it. `custom` is for the genuinely-no-built-in-action cases (drag-and-drop, niche keyboard choreography), not for things with a built-in action.
19. **Using `action: select` for a component dropdown** — `select` only drives native `<select>`. PrimeNG `p-select`/`p-autocomplete`/`p-multiselect` need `select-option` (the `selector` is the trigger/host).
19b. **Reaching for `exclusive:` to express ordering, or naming the combination of two resources.** It promises non-overlap only — members run in any order, and a retry re-runs just the failures. And `exclusive: stripe-and-mailbox` does **not** exclude against `exclusive: stripe`: they are two names, so two tasks, so concurrent. Every scenario touching either resource has to use the same name.
20. **Writing `multiple: [a, b]` inline on `select-option`** — the inline form is parsed as a string, not a list. Use an indented YAML list (`- a` / `- b`), the same shape as `layouts:`. And don't combine `option:` with `multiple:` — they're mutually exclusive.
21. **Giving `confirm` a `selector`** — it takes none; dialog buttons resolve from the `dialog:` config preset. Pick a non-default button with `variant: reject`, `danger: true`, or `button: "<label>"` (not all three).
22. **`fill-date` with `method: calendar`** — rejected. The `type` method (default) types into the input; for a read-only field with only a popup, use the `pick-date` action.
23. **Putting `wait:`/`reload:` on a non-`navigate` action, or `state: on/off` on `wait-for`** — `wait`/`reload` are navigate-only; `wait-for state` accepts only `visible`/`hidden`/`detached`/`attached` (`on`/`off` belong to `toggle`).
24. **Forgetting `role` on `capture from.type: dom`** — DOM capture reads the live page, so it needs `role` (unlike redis/http/shell capture).
25. **Reaching for a `custom` `.first`/`.nth(...)` block on a strict-mode violation** — add `match:`/`within:` to the `click`/`hover`/`wait-for`/`assert` step instead.
26. **Padding a scenario with `wait` steps to stop it failing intermittently** — a fixed sleep encodes an assumption about machine speed that breaks on loaded CI and under `steppy run --workers N`. Wait for the thing you actually need (`wait-for`, `wait-for` with `state: hidden`, `wait-for-text`, `navigate` `wait:`), and keep a fixed `wait` only for backend propagation you cannot observe from the UI — with the reason in `note:`. See **Timing and flakiness**.
27. **Treating a FLAKY result as "passed, nothing to do"** — it did not fail the build, but it is the scenario most likely to waste someone's morning next week. Check `steppy flaky`: a high rate means fix the waits; a single repeated fingerprint means it is not flaky at all, it is broken.
28. **Adding `modes: ['video']` and expecting a video** — that is only the first of two gates. Nothing renders until `steppy build --video` / `steppy guide --video` arms it. Deliberate: a render costs minutes of CPU and, on a paid TTS provider, real money.
29. **Confusing `steppy build --video` with `steppy run --video`** — the first renders narrated guide MP4s; the second records Playwright debugging footage of *failed tests* into `output/videos/`. Unrelated features that share a flag name.
30. **Writing narration that only works on the page** — with video, `guide-text`'s `text` is read aloud. Markdown emphasis, parenthetical asides, and "see step 4" all sound like noise. Write it to be heard.
31. **`steppy build --pdf` / `--handbook` on the Angular target** — rejected; both are static-target only. The Angular host owns its own pages. `steppy guide --pdf` works regardless of build target.
32. **Putting `interactive-progressive: true` on anything but `fill-form`** — rejected at validation. It describes a per-field capture sequence; no other action has fields.
33. **Upgrading Steppy and not running `steppy update`** — the config schema, the CI workflow, and the skills in `.claude/skills/` are Steppy-owned scaffold that does not upgrade with the package. Stale skills are worse than none: an assistant will confidently write against a grammar you no longer have.
34. **Letting a fixture value leak into a tour step's text** — a tour shows instructions, never test data. `guide-text: "Type Quarterly maintenance"` on a `fill` step makes every real user's popover say that; the compiler warns `fixture-leak`. Keep the guide wording if you like, and add a `tour-text` without the value ("Give the invoice a short title").
35. **`tour-text` / `tour-advance` / `tour-placement` on a step the tour leaves out** — a `hover`, `wait`, `navigate`, `confirm` or `scroll` step is skipped by inference, so its `tour-*` keys are the error `annotation-on-skipped-step`. Add `tour: keep` to show it (centred when it has no test-id), or drop the keys. `tour: skip` together with other `tour-*` keys is an error too.
36. **A compound CSS selector on a step the tour keeps** — a tour stores one test-id, never a selector. `.toolbar > button.primary`, `[data-testid^="row-"]`, a `>>` chain, a `{{var}}` id, `within:` or `match:` other than `first` all fail with `target-not-test-id`. Give the element its own `data-testid` in the app, or mark the step `tour: skip` (or `tour: keep` for a centred step).
37. **Clicking through JavaScript in `custom` on a tour flow** — the overlay cannot point at code, so the step becomes a centred step or vanishes, with an `unguided-action` warning. Use `action: click` with `synthetic: true`, or add a `guide-cursor` block naming the visible target.
38. **Expecting the tour to type, click or submit for the user** — it never does. The overlay highlights and explains; the *user* acts, and the tour advances when they do. A step only the test can perform (a `capture`, a `keyboard` shortcut, a backend `wait`) runs in the CI replay and is never shown.

---

## Interpolation

Anywhere a value supports `{{var}}` (target, selector, value, fields, custom code), Steppy substitutes from `_vars`. Variables come from:
- `capture` steps (`store: X` → `{{X}}`)
- Seed scripts writing to Redis/env/etc.
- Anything you set in a `custom` step: `_vars['foo'] = 'bar'`

---

## Workflow

0. **Set the project up once** — `steppy init` scaffolds `config.yaml`, an example scenario, the JSON config schema, a `.gitignore`, and installs these skills into `.claude/skills/` (`--no-skills` opts out). **After upgrading Steppy, run `steppy update`**: it reconciles the Steppy-owned scaffold — config schema, these skills, the CI workflow — with the installed version, so an assistant reading `.claude/skills/` is describing the Steppy you actually have. `steppy update --check` is the CI-able read-only form: it scans scenarios and config for deprecated usage and exits non-zero on anything breaking.
1. **Draft — or bootstrap — the scenario** in `scenarios/my-flow.md`. Write it by hand following the grammar above, or bootstrap a **draft** from a live click-through: `steppy record <url> --role admin --slug my-flow` wraps `playwright codegen` and post-processes the recording into `scenarios/my-flow.md`. It collapses a username/password/submit sequence to a single `action: login`, placeholderizes secret fields (password/token/…) to `${VAR}` refs, suggests an `input[type='password']` screenshot mask, and runs the selector lint — then you refine it here. Recordings are **drafts for review** (a foundational V1 — CLI plumbing), so always read one before `steppy generate`.
2. `steppy validate` — catches typos, missing fields, unknown actions **before** you run anything.
3. `steppy generate` — rebuilds `tests/e2e/conftest.py` and `tests/e2e/test_my_flow.py`.
4. `steppy run --guide` — runs every `guide.enabled` scenario with screenshots on (or `steppy run -s my-flow --screenshots` for a single scenario). When `apps.<role>.i18n.localstorage` is configured, `--guide` auto-parametrizes the run over every locale in `config.yaml` (one screenshot set per language) **and auto-enables `--no-asserts`** (text assertions like `toast(...)` and `row-contains(...)` are language-specific and would fail every non-default locale). When `screenshot.layouts` is configured, `--guide` also auto-parametrizes over every layout (cartesian with locales). Use `steppy run --locales fr,nl` to capture an explicit subset of locales, or `steppy run --guide --layouts desktop` to capture an explicit subset of layouts. `steppy run --layouts desktop,mobile` (without `--guide`) works in test mode too — useful to verify plain test scenarios across device viewports. Pass `steppy run --no-asserts` standalone if you want to skip assertions without going through `--guide`.
5. `steppy guide --guide --locale fr` — renders the HTML/Markdown guides with animated cursors for all `guide.enabled` scenarios across every `user-guide-*` role they expose.
6. Open `output/guides/editor/fr/my-flow.html` in a browser to review.
7. *(Optional)* `steppy build` — bundles guides into a deployable artifact. Two targets, chosen by the `target` field in `steppy.guide.config.ts`: `'angular'` (default) generates Angular 21 standalone components, routes, and navbar metadata into a target Angular app; `'static'` generates a self-contained static site (full HTML pages + index + manifest.json + assets) for GitHub Pages, Netlify, S3, or any plain static host. See the **`steppy build` — bundling guides** section below.
8. *(Optional)* `steppy run --workers N` — for slow suites, opt into parallel execution: N Docker Compose stacks run scenarios concurrently from a shared queue. Scenario file format is unchanged. Requires a `parallel:` block + per-app `internal_url` in `config.yaml`, a docker-compose file with healthchecks, and Docker Compose 2.20+. `--workers 1` (or unset) is byte-identical to today's single-process path. `--workers N --guide` composes cleanly — each scenario is claimed by one worker that handles the full (locale × layout) cartesian sequentially in one pytest process; screenshots land at the same `output/screenshots/<scenario>/[<locale>/][<layout>/]step-NN.png` paths a single-worker `--guide` run produces, so `steppy guide --guide` and `steppy build` consume them identically. On failures, the coordinator persists per-scenario `pytest.log` + `summary.txt` under `output/failures/<slug>/` and full per-worker stack logs under `output/failures/_worker-logs/` (they survive the run-dir teardown). If each parallel stack runs an isolated DB, set `auth.storage_state_ttl: 0` so workers don't reuse cached sessions from each other's stacks — or set `parallel.isolate_auth: true` to give each worker a private `.auth` dir instead of sharing one read-write across all N (costs N logins instead of 1; mutually exclusive with `pre_auth`). Seed scripts must read `STEPPY_BASE_URL_OVERRIDE` (JSON `{role: internal_url}`) instead of hard-coded `http://localhost:…` because they execute **inside** the worker container. If you use `mode: storage-state`, add a `parallel.pre_auth: { command: "…" }` hook — storage-state sessions are **origin-bound**, so a host-captured `.auth/<role>.json` (captured at `localhost`) won't authenticate a worker that reaches the app at its `internal_url`, and every scenario redirects to login. The hook runs your capture command **in-network** in a one-off container (gated to finish before any worker claims a scenario, deduped once per run) so it populates the shared `.auth/` cache at the internal origin.

9. *(Optional)* `steppy flaky` — ranks scenarios by flake rate across recent parallel runs, with median/p95 durations, reading the history each run appends to `.steppy/history.jsonl`. This is the list to work from: five ranked slugs beat twenty separate red runs. `--last N` sets the window (default 20), `--min-rate` hides the healthy, `--json` for scripting. See **Timing and flakiness** above for what to actually change once a scenario shows up. Only parallel runs record history — `--workers 1` produces no per-scenario records.

10. *(Optional)* Export the guides in another format — `steppy guide --pdf --handbook` (or `steppy build --pdf --handbook`, static target only) for printable guides; `steppy build --video` / `steppy guide --video` for narrated MP4s of the guides declaring `modes: ['video']`. See **PDF and handbook export** and **Narrated video guides** below.

11. *(Optional)* `steppy run --coverage` — records per-scenario coverage signals (route trail, API requests, JS coverage) and merges them into a persistent map at `.steppy/coverage.db`. Then `steppy coverage refresh` + `steppy coverage report` prints the gap analysis: the `declared-unreached` bucket is your work queue for step 1. See **Other commands** below.

12. *(Optional)* `steppy run --bundle` then `steppy report` — render the run to a browsable static site (outcomes, grouped failure causes, the failing step named plus its screenshot), and/or `steppy bundle create` to package `output/` into a portable, self-describing archive to attach to a CI job. See **Other commands** below, and the **`steppy-ci-report`** skill for CI wiring, publishing and the coverage badge.

13. *(Optional)* **In-app tours** — when the scenario opts into a tour (`tour:` in its frontmatter, with `tours.enabled: true` in `config.yaml`), run `steppy validate` and fix every tour error it prints (they fail validation with exit 1, and nothing is written until they are gone), then `steppy generate`: it writes the tour files and appends the `test_<slug>__tour` CI replay to the scenario's test file, which `steppy run` then runs with the scenario. See **In-app tours** below.

---

## Reading a big parallel run

At a few hundred scenarios the run's *output* matters as much as its result. Four things shape it, all on by default except where noted:

- **Exclusion groups.** A scenario carrying `exclusive: <group>` is serialised against every other scenario naming that group — they become one queue task that a single worker runs member by member. The run header prints the census (`exclusion groups: billing[3], mail[2] — …`); two near-identical names on that line are a typo, and a group of one serialises nothing. A group is also the run's floor: no amount of workers finishes faster than the heaviest group's total duration.
- **Retry pass.** After the queue drains, failed scenarios re-run at reduced concurrency (default: 1 worker). One that then passes is reported as **FLAKY** in its own section and does **not** fail the build — it failed because of contention, not because it is broken. One that fails twice is a real failure. Tune with `parallel.retry` or `--retry-pass N` (`0` disables); `--strict-flaky` makes flakes fail the build. Flaky scenarios are excluded from the last-failed cache, so a follow-up `steppy rerun` only re-runs genuine failures. A worker that dies mid-scenario is retried too, rather than being counted as a hard loss.
- **Failure causes.** The summary leads with failures grouped by cause, so one broken selector across forty scenarios prints as one line instead of forty tracebacks. `--verbose` restores the full per-scenario dump.
- **`--trace` / `--video`.** These work in parallel mode: `steppy run --workers N --trace` writes a Playwright trace zip per failed test to `output/traces/`. Open one with `playwright show-trace <zip>` — a DOM snapshot per action, plus network and console. It is by far the fastest way to understand a failure you cannot reproduce locally.
- **Circuit breaker.** If ≥10 scenarios have completed and ≥90% of them failed, the run aborts with a pointer to `output/failures/_worker-logs/`. That pattern means the app, auth, or a seed step is down — not that your scenarios are wrong. Disable for one run with `--no-fail-fast`.

As an author, the part of this you control is the **FLAKY** list. Everything else is reporting; that list is a work queue. See **Timing and flakiness**.

---

## Other commands

The authoring loop is `validate` → `generate` → `run` → `guide` / `build`. The rest of the CLI, and when you'd reach for it:

| Command | What it's for |
|---|---|
| `steppy init` | Scaffold a project: `config.yaml`, example scenario, JSON config schema, `.gitignore`, and these skills into `.claude/skills/` (`--no-skills` to skip). `--ci` adds a GitHub Actions workflow, `--ci-gitlab` a GitLab CI pipeline, `--app-dir DIR` puts `steppy-app-tours` in the app's repository. Safe to re-run in an existing project: it only adds what is missing |
| `steppy update` | **Run this after upgrading Steppy.** Reconciles the Steppy-owned scaffold — config schema, skills, CI workflow — with the installed version, installs skills shipped since, and reports old hand-copied skill folders. `--check` is a read-only CI gate that fails on deprecated usage |
| `steppy list` | Every scenario with its metadata (tags, priority, roles, step count). The fastest way to see what a repo actually covers |
| `steppy validate` | Parse-check every scenario without generating or running anything |
| `steppy tours` | Write only the in-app tour files (no tests): `--dry-run` previews; `--check` (CI) writes nothing and exits 1 when a committed `app-assets` copy is missing or its content is stale — compared for the build id that copy records, so a newer commit alone never fails it; `--build-id ID` overrides `tours.build_id` (this command only); `--explain` also prints why each step was left out; `-d/--scenarios-dir` picks the directory. See In-app tours |
| `steppy record [URL]` | Bootstrap a **draft** scenario from a live click-through (see Workflow step 1) |
| `steppy seed <name>` | Run one seed script by hand, outside a test run |
| `steppy rerun` | Re-run only the previously failed tests |
| `steppy auth login \| status \| clear` | Log in for a role (headed by hand, or `--auto`: headless with a form role's credentials, no terminal needed), inspect cached sessions, clear `.auth/` |
| `steppy flaky` | Rank scenarios by flake rate across recent parallel runs |
| `steppy stacks cleanup` | Rescue command: tear down orphaned `steppy-run-*` Compose projects left by a force-killed parallel run |
| `steppy video check \| cache` | Preflight and cache management for narrated video (see Narrated video guides) |
| `steppy coverage refresh \| report \| merge \| triage` | The coverage map — see below |
| `steppy report` | Render one run to a browsable static site: outcomes, grouped failure causes, the failing step named — see below |
| `steppy bundle create \| inspect \| verify \| upload` | Portable results archives — see below |

### Coverage map — what your scenarios *don't* reach

`steppy run --coverage` records what each scenario actually touched (route trail, API requests, JS coverage) and merges it into a persistent map at `.steppy/coverage.db`. It composes with `--scenario`/`--tag`/`--guide`/`--workers` like the other mode flags.

```bash
steppy run --coverage                 # record + auto-merge
steppy coverage refresh               # read the declared surface from config's OpenAPI/sitemap
steppy coverage report                # the gap report
steppy coverage triage set <node> --flag non_ui   # one node: "this has no UI, stop counting it"
```

The buckets: **tested** (declared and reached, *strong* when an asserting scenario covered it), **declared-unreached** (the one that matters — routes or endpoints your app exposes that no scenario touches), **discovered-uncovered** (exercised but not asserted), **discovered-tested** (exercised but missing from the declared surface — a stale spec), and **unclassified** (requests matching no configured `base_urls`).

As an author this is the answer to **"what should I write a scenario for next?"** — a ranked list rather than a guess. Work the `declared-unreached` bucket.

Two things it depends on, both worth checking before trusting a number: `coverage.apis[].openapi` (a URL *or* a committed file path) is what defines the declared surface, and `coverage.non_ui` in `config.yaml` is what stops health checks, webhooks and cron callbacks from counting against you — the equivalent `triage set` flag lives in the gitignored `.steppy/coverage.db`, so CI starts with none of them.

> Everything about running this in CI, the e2e-surface **badge**, `coverage.non_ui`'s pattern grammar, and how a *detached bundle* resolves coverage lives in the **`steppy-ci-report`** skill. This section is the authoring view.

### `steppy report` — read a run without scrolling a log

```bash
steppy run --bundle    # records per-test AND per-step events
steppy report          # → output/report/index.html
```

A self-contained static site for **one** run: outcomes, failure causes grouped so one broken selector across forty scenarios is one row instead of forty tracebacks, **the failing step named** with its timeline and the screenshot at that moment, and the coverage buckets. It also reads a `.tar.zst` bundle or an unpacked bundle directory, so a failure from CI is one download away from being readable.

The part that matters for authoring: `--bundle` is what makes the report able to say *step 7 of `order-lifecycle` in nl/mobile*, rather than only *the test failed*. Without it you get outcomes and no step detail, and the report says so rather than rendering an empty timeline.

> Publishing it, the Pages warnings, the badge and the CI wiring: **`steppy-ci-report`**.

### Results bundles — one run, one portable archive

```bash
steppy run --bundle           # capture the extra artifacts: per-test AND per-step events + JUnit XML
steppy bundle create          # → output/bundles/steppy-bundle-<run-id>.tar.zst
steppy bundle inspect <file>  # print the manifest without extracting
steppy bundle verify <file>   # re-hash every member against the manifest
```

`--bundle` is off by default because it changes the pytest invocation; it does **not** create the bundle, it captures what a bundle needs. `steppy bundle create` then packages `output/` **exactly as Steppy wrote it** — no path rewriting — plus a generated `manifest.json`, a merged `junit.xml`, and the event JSONL. Unpacking it yields a tree `steppy guide` and `steppy build` consume unchanged.

Useful with no server involved at all: a self-describing archive to attach to a CI job, so a failed run is one download rather than a scavenger hunt through artifacts. Per-step events are what let the manifest name the *step* a test died on, not just the test. `--no-videos` / `--no-traces` / `--no-guides` trim it; `--max-size 2GiB` drops the cheapest-to-lose artifacts first and marks the manifest `truncated`.

**Point `steppy report` at one to read it**: `steppy report <bundle>.tar.zst -o ./site` renders the same site the run would have produced locally, so reviewing a stranger's failed CI run needs no unpacking and no guessing. Unpacking works too (`tar -xf` then `steppy report ./unpacked`), and so do `steppy guide` / `steppy build` on the unpacked tree — that is the whole reason the archive holds `output/` unrewritten.

### Uploading a bundle to a control plane

`steppy bundle upload` pushes a run's results to a **Steppy control plane** — the hosted side that remembers runs, diffs them against a baseline, and tracks flake rate over time. Everything above works with no plane at all; this section only matters if the project has one. Nothing in Steppy assumes it exists.

```bash
steppy run --bundle              # 1. capture the artifacts a bundle needs
steppy bundle upload             # 2. push straight from output/ — no archive written
```

**Run `steppy run --bundle` first.** Two things depend on it, and both fail quietly otherwise:

- **Per-test event records.** Without them the plane receives an empty `tests[]` — the upload succeeds and the run shows no failure detail at all.
- **The run identity** in `output/.steppy-run.json`, which doubles as the **idempotency key**. With it, a retried upload *attaches* to the same run; without it a fresh run id is minted per invocation, so a flaky network turns one run into three. Pin it with `--run-id` if the identity was never recorded, or `--attach <run-id>` to upload into a run the plane already created.

#### Content-addressed, so re-runs are cheap

The client offers the **manifest** first; the server replies with presigned upload URLs **only for the digests it does not already hold**; finalize re-verifies them before ingestion is queued. A re-run that changed two screenshots uploads two screenshots — not the 3,000-file tree around them. This is also why `steppy bundle create`'s archives are byte-reproducible: without that, every upload would look entirely new.

**Interrupted uploads just resume.** Run the command again — it attaches to the same run and sends only what is missing. There is no state file to clean up and no `--resume` flag; the offer is keyed to the run and reports what is already held, so resumability falls out of the protocol rather than being bookkept.

> **Reading the output**: `uploaded` and `already_held` are counted in **blobs** (distinct content digests), while `total_files` counts **manifest entries** (paths). Byte-identical files collapse to one blob, so the two numbers routinely disagree — one real capture reported 3,204 files as 1,587 blobs. That gap is the deduplication working, not a discrepancy to chase.

#### No archive by default

With no argument, the upload reads **straight from `output/`** and writes no `.tar.zst`. The protocol is per-file, so an archive would be created only to be read back. Pass a path when an earlier CI step already built one:

```bash
steppy bundle create                             # CI step A — produces an archive to keep as a job artifact
steppy bundle upload output/bundles/steppy-bundle-<run-id>.tar.zst   # CI step B — uploads that exact archive
```

#### Endpoint and token

| Setting | Resolution order |
|---|---|
| Endpoint | `--url` → `$STEPPY_CLOUD_URL` → `cloud.url` in `config.yaml` |
| Token | `--token` → `$STEPPY_CLOUD_TOKEN` — **and nothing else** |

```yaml
# config.yaml — the URL is fine to commit
cloud:
  url: https://plane.example.com
```

**There is deliberately no config key for the token.** `config.yaml` is a committed file; a `cloud.token:` field would exist purely to be filled in and pushed. Use the environment variable — in CI, a repository secret.

#### Trimming and rehearsing

Same artifact switches as `bundle create`, plus upload-specific ones:

```bash
steppy bundle upload --dry-run                 # show what would be sent; contacts nothing
steppy bundle upload --no-videos --no-traces   # skip the heavy artifacts
steppy bundle upload --max-size 2GiB           # drop cheapest-to-lose first, mark manifest truncated
steppy bundle upload --json                    # machine-readable outcome, for a CI step that gates on it
steppy bundle upload --concurrency 8           # parallel blob PUTs (ignored when uploading an archive)
```

`--dry-run` is worth running once when wiring up CI — it tells you what the payload looks like before you discover the size in a billing dashboard.

#### CI shape

```yaml
- run: steppy run --bundle --workers 4
- run: steppy bundle upload
  if: always()                    # a failed run is exactly the one worth uploading
  env:
    STEPPY_CLOUD_URL: ${{ vars.STEPPY_CLOUD_URL }}
    STEPPY_CLOUD_TOKEN: ${{ secrets.STEPPY_CLOUD_TOKEN }}
```

`if: always()` is the load-bearing line. A step that only runs on green uploads nothing on the days you need the results.

---

## `steppy build` — bundling guides

`steppy build` reads a TypeScript config (`steppy.guide.config.ts`) and bundles the scenario-driven guides into a deployable artifact. It reuses the same guide HTML generation as `steppy guide`, then dispatches on the config's `target` field:

- **`target: 'angular'`** (default) — emits Angular 21 standalone components, routes, navbar metadata, and a typed manifest into a target Angular app, ready for lazy loading.
- **`target: 'static'`** — emits a self-contained static site (full HTML pages, an `index.html`, a `manifest.json`, a shared `assets/guides.css`, and copied screenshots) ready to deploy on GitHub Pages, Netlify, S3, Apache, or any plain static host.

Both targets support all three rendering modes. **`stepbystep`** (default) renders a scrollable HTML guide with an animated cursor; **`interactive`** mounts the Lit `<steppy-guide>` web component for a Guideflow-style click-through demo; **`video`** renders a narrated MP4 from the same manifest. A guide can opt into any combination via `modes: ['stepbystep', 'interactive', 'video']`. See the **Interactive mode** and **Narrated video guides** sections below.

Pick the target based on where the guides will live: inside an existing Angular SPA → `angular`; standalone documentation site → `static`.

### Config file (`steppy.guide.config.ts`)

Place at the root of the target project:

```ts
import { defineGuideConfig } from 'steppy';

export default defineGuideConfig({
  scenariosRoot: './e2e/scenarios',       // scenario .md files
  screenshotsDir: './output/screenshots', // captured screenshots from steppy run
  outputDir: './src/app/features/guides/generated',  // angular: into the app; static: anywhere deployable
  assetsDir: './public/assets/editor',     // angular only — where screenshots are copied (URLs serve from /assets/<role>/...)
  role: 'editor',                          // default role for step filtering — also drives URL prefix and i18n namespace
  locales: ['fr', 'nl'],                  // supported locales (generates HTML per locale)
  defaultLocale: 'fr',
  target: 'angular',                      // 'angular' (default) | 'static'
  site: { title: { fr: 'Guides éditeur', nl: 'Editor-gidsen' } },
  guides: [
    { slug: 'login',         title: { fr: 'Connexion',     nl: 'Aanmelden' },     scenario: 'editor/login.md' },
    { slug: 'create-project', title: { fr: 'Créer un projet', nl: 'Project aanmaken' }, scenario: 'editor/create-project.md', group: 'projects' },
    { slug: 'edit-project',   title: { fr: 'Modifier un projet', nl: 'Project bewerken' },  scenario: 'editor/edit-project.md',   group: 'projects' },
    { slug: 'reports',  title: 'Reports',                                       scenario: 'editor/reports.md', modes: ['stepbystep', 'interactive'] },
  ],
  groups: [
    { slug: 'projects', title: { fr: 'Projets', nl: 'Projecten' }, description: { fr: 'Gérer les projets.', nl: 'Projecten beheren.' } },
  ],
  onboarding: {
    enabled: true,
    title:       { fr: 'Premiers pas', nl: 'Aan de slag' },
    description: { fr: 'Suivez ces guides dans l\'ordre.', nl: 'Volg deze gidsen op volgorde.' },
    guides: ['login', 'create-project'],
  },
  // Navbar references group slugs (not individual guides inside a group)
  // and any ungrouped guide slug. The reserved 'getting-started' onboarding
  // entry is added automatically — do not list it here.
  navbar: { order: ['projects', 'reports'] },
});
```

Install the types package (both targets): add `"steppy": "file:../path-to-steppy/packages/steppy"` to `devDependencies` in the host project's `package.json`.

### Localized titles and descriptions

Every user-facing string in the build config — `site.title`, every `guides[].title`, every `groups[].title`/`description`, and every `onboarding.title`/`description` — is **`LocalizedText`**: either a plain string (locale-agnostic) or a `{locale: string}` map. Plain strings remain valid; mix freely with locale maps in the same config:

```ts
site:   { title: 'My App Guides' },                                    // same for every locale
guides: [
  { slug: 'login', title: { fr: 'Connexion', nl: 'Aanmelden' }, ... }, // per-locale
  { slug: 'misc',  title: 'Miscellaneous',                  ... },     // plain string OK
],
```

Resolution at build time: if the locale being rendered isn't in the map, the build falls back to `defaultLocale`. Empty strings and empty maps are rejected at validation time. The Angular target emits resolved values into per-locale i18n JSON bundles consumed by `@ngx-translate/core` in the host app — calling `translate.use('nl')` updates every title across the UI in one click. The static target bakes one sibling page per locale at build time (see file tree below).

### Groups and onboarding ("meta guide")

Two structural concepts on top of individual guide entries:

- **Groups** (`groups: GroupConfig[]`) — collect related guides under a navbar section + landing page. Opt a guide in by setting `group: '<group-slug>'` on the guide entry. The navbar references the group slug (not the inner guide slugs); clicking a group opens its landing page, which lists its child guides. Group slugs share URL space with guide slugs, so they must not collide.
- **Onboarding** (`onboarding: OnboardingConfig`) — an ordered path through a subset of existing guides, surfaced as a top-of-navbar "Getting Started" entry that always appears first. Fields: `enabled` (default `true`), `title`/`description` (LocalizedText), and `guides` (an ordered list of guide slugs from your `guides[]`). The landing page lives at the **reserved URL `/getting-started`** — no guide or group may use the slug `getting-started` while onboarding is enabled.

A guide listed in `onboarding.guides` can still belong to a group; onboarding is purely an ordered overlay, not an exclusive membership. The navbar always renders onboarding first (when enabled), then the entries in `navbar.order`, then any unlisted top-level entries in declaration order.

### Angular target (`target: 'angular'`)

#### What it generates

```
outputDir/
  <role>.routes.ts                  # Routes array (export <ROLE>_ROUTES) with lazy loadComponent
  <role>.nav.json                   # navigation tree with translation-key references (no inlined LocalizedText)
  i18n/<role>/<locale>.json         # ngx-translate bundle per locale, namespaced under "<ROLE>"
  {slug}/
    {slug}.component.ts             # standalone component, ngx-translate driven (host owns locale state)
    {slug}.content.ts               # CONTENT[locale][layout] map of pre-rendered HTML fragments
    {slug}-stepbystep-{locale}.html # human-readable debug fragment (not imported)
    {slug}-interactive.component.ts # if interactive mode: mounts <steppy-guide> + bridges ngx-translate locale
  _groups/<group>.group.component.ts   # group landing page (lists child guides) — one per entry in groups[]
  _onboarding/onboarding.component.ts  # onboarding landing page (ordered guide list) — only if onboarding.enabled
  .gitignore                        # * (everything regenerated)

assetsDir/                          # point this at host's public/assets/<role>/ for direct integration
  steppy-player.js                  # if any guide uses interactive mode: ESM bundle of <steppy-guide> (Lit)
  {slug}/
    guide.json                      # if interactive mode: manifest consumed by <steppy-guide>
    step-01.png, step-02.png, ...   # screenshots copied from screenshotsDir
```

#### Generated component behavior

- **Host-owned locale via `@ngx-translate/core`**: components inject `TranslateService` and derive their locale signal from `translate.onLangChange` via `toSignal`. Calling `translate.use('nl')` from anywhere in the host app flips every title, description, and screenshot in one click. The host app loads the per-locale bundles (`i18n/<role>/<locale>.json`) into ngx-translate (typically via `TranslateHttpLoader` or static imports merged into `translate.setTranslation`).
- **Layout switcher**: when `layouts` has more than one entry, a PrimeNG `SelectButton` drives a component-local `selectedLayout` signal. The trustedHtml `computed()` picks the right (locale, layout) HTML fragment from the `CONTENT` map.
- **Mode switcher**: when a guide has `modes: ['stepbystep', 'interactive']`, a second `SelectButton` switches between the step-by-step HTML and a real interactive component that mounts the Lit `<steppy-guide>` web component against the build-emitted `guide.json` manifest. The interactive component bridges `TranslateService.onLangChange` into the player's `locale` attribute, and lazily injects a one-shot `<script type="module" src="/assets/<role>/steppy-player.js">` on first mount (idempotent across multiple interactive guides).
- **Signals**: components use Angular signals (`signal()`, `computed()`) for reactive state.
- **ViewEncapsulation.None**: guide CSS (scoped with `.steppy-guide` prefix) is injected via innerHTML `<style>` blocks.

#### Integration in the target app

```ts
// app.routes.ts (hand-authored — steppy never touches this)
import { EDITOR_ROUTES } from './features/guides/generated/editor.routes';
export const routes: Routes = [
  { path: 'guides', children: EDITOR_ROUTES },
];

// navbar.component.ts (hand-authored)
import navData from './features/guides/generated/editor.nav.json';
// resolve titleKey / descriptionKey via TranslatePipe in your navbar template

// app.config.ts — feed i18n bundles into ngx-translate
import frTranslations from './features/guides/generated/i18n/editor/fr.json';
import nlTranslations from './features/guides/generated/i18n/editor/nl.json';
// translate.setTranslation('fr', frTranslations, true);
// translate.setTranslation('nl', nlTranslations, true);
```

For guides that opt into `modes: ['interactive']`: no extra host wiring needed. The generated `<slug>-interactive.component.ts` lazily injects `<script type="module" src="/assets/<role>/steppy-player.js">` on first mount (idempotent), so the only host requirement is that the build's `assetsDir` resolves to the host's `public/assets/<role>/` (so `/assets/<role>/steppy-player.js` and `/assets/<role>/<slug>/guide.json` are served). The Lit `<steppy-guide>` element is registered globally once the bundle loads — subsequent interactive components reuse it.

### Static target (`target: 'static'`)

A self-contained, host-neutral build for GitHub Pages, Jekyll, Netlify, S3, or any plain static host. No JavaScript framework required — just HTML, CSS, and PNG assets.

#### What it generates

```
outputDir/
  index.html                        # landing page in defaultLocale (navbar.order)
  index.{locale}.html               # one per non-default locale (e.g., index.nl.html)
  manifest.json                     # JSON manifest (site, locales, guides[].pages map, groups, onboarding)
  .nojekyll                         # disables GH Pages' Jekyll preprocessing (harmless elsewhere)
  _headers                          # X-Robots-Tag for PDFs (Netlify/Cloudflare read it; GH/GitLab Pages ignore it)
  robots.txt                        # always; sitemap.xml too when seo.mode is 'public'
  handbook/{locale}.html + .pdf     # --handbook only: the whole site as one document
  {slug}/{locale}.pdf               # --pdf only: each guide page printed
  assets/
    guides.css                      # shared stylesheet (one copy, not duplicated per page)
    cursor.js                       # shared stepbystep cursor animation driver (IIFE)
    steppy-player.js                # if any guide uses interactive mode: ESM bundle of <steppy-guide>
    {slug}/
      step-01.png, step-02.png, ... # screenshots copied from screenshotsDir
  {slug}/
    index.html                      # guide page in defaultLocale (or redirect)
    {locale}.html                   # one per locale (e.g., reports/fr.html, reports/nl.html)
    guide.json                      # if interactive mode: manifest consumed by <steppy-guide>
    interactive.html                # if interactive mode: self-contained interactive page (loads ../assets/steppy-player.js)
  {group-slug}/                     # one per entry in groups[]
    index.html                      # group landing in defaultLocale (or redirect to defaultLocale page)
    {locale}.html                   # group landing per locale
  getting-started/                  # only if onboarding.enabled (reserved slug)
    index.html                      # onboarding landing in defaultLocale (or redirect)
    {locale}.html                   # onboarding landing per locale
```

#### Behavior

- **Self-contained output**: the static target writes screenshots to `outputDir/assets/<slug>/` regardless of the configured `assetsDir`, and uses **relative** screenshot URLs (`../assets/<slug>/step-NN.png`). The whole `outputDir` is portable — drop it anywhere and it works at any deploy root, including GitHub Pages project subpaths like `/repo-name/`.
- **`assetsDir` is ignored** for `target: 'static'`. It remains in the schema for angular compatibility; static builds simply don't read it.
- **Per-locale pages everywhere**: `index.html` (default locale) lives next to `index.<locale>.html` for every non-default locale. Group landings (`<group>/<locale>.html`) and the onboarding landing (`getting-started/<locale>.html`) follow the same pattern, with an `index.html` redirect to the default-locale sibling. The locale switcher is rendered on every page (index, group, onboarding, guide) and links between siblings — no JavaScript needed.
- **Index ordering**: the landing page lists onboarding first (when enabled), then top-level entries (groups + ungrouped guides) in `navbar.order`, with declaration-order fallback for anything not listed (mirrors the Angular navbar logic).
- **Shared CSS**: the inline `<style>` block from `guide.fragment.html.j2` is suppressed in static pages; all styling lives in the single `assets/guides.css` so pages stay small and the browser caches one stylesheet.
- **Interactive mode is supported** (stage 4 lifted the previous restriction). A guide with `modes: ['interactive']` emits `<slug>/interactive.html` — a self-contained page that loads `../assets/steppy-player.js` (the same Lit `<steppy-guide>` bundle the Angular target uses) and mounts it against `./guide.json`. Stepbystep and interactive coexist in the same directory: `<slug>/fr.html`, `<slug>/nl.html` (stepbystep) sit next to `<slug>/interactive.html` (interactive). The locale switcher inside `<steppy-guide>`'s chrome handles switching mid-guide — no URL navigation needed. The whole build still runs offline: bundle, manifest, and screenshots are all relative paths from `<slug>/interactive.html`.

#### SEO / indexing (the `seo` block)

Static builds are **private by default** — with no `seo` block, every page gets `<meta name="robots" content="noindex,nofollow,noarchive">` and a root `robots.txt` with `Disallow: /`, so an accidentally-deployed internal guide site is not indexable. (Indexing controls are defense-in-depth, not access control — keep private sites behind auth.) `seo` applies to the **static target only**; the Angular host owns its own `<head>`.

For a public documentation or marketing site, opt in with `mode: 'public'` (which requires an absolute `baseUrl`):

```ts
export default defineGuideConfig({
  // …
  target: 'static',
  seo: {
    mode: 'public',                          // 'private' (default) | 'public'
    baseUrl: 'https://guides.example.com',   // required when public — absolute site origin
    description: { fr: 'Guides éditeur', nl: 'Editor-gidsen' }, // optional site-wide default meta description
    openGraph: true,                         // optional Open Graph / Twitter cards (default false)
  },
  guides: [
    { slug: 'login', title: 'Sign in', scenario: 'editor/login.md',
      description: 'Sign in to the editor workspace' },  // optional per-guide meta description
  ],
});
```

Public mode additionally emits:

- **`sitemap.xml`** at the site root, listing exactly the pages that are **their own canonical**: the index (as `https://…/`, not `/index.html`) plus every per-locale guide, group and onboarding page, with absolute URLs and per-locale `<xhtml:link rel="alternate" hreflang="…">` entries (plus `x-default`). Excluded, each for a reason visible in the page itself: redirect stubs and `interactive.html` / `video.html` canonical elsewhere, and handbook pages are `noindex`. No `<lastmod>` unless `seo.lastmod` is set (reproducible builds). Generated by `src/steppy/sitemap.py`, not from a bundled template — a project `templates/static/sitemap.xml.j2` still overrides it.
- **`robots.txt`** with `Allow: /` and a `Sitemap:` pointer.
- On every page `<head>`: an absolute `<link rel="canonical">`, `<link rel="alternate" hreflang="…">` per locale + `x-default` (only when there is more than one locale), and `<meta name="description">`.
- When `openGraph: true`: Open Graph + Twitter card tags (`og:type`/`title`/`description`/`url`/`site_name`/`locale`; guide pages also get `og:image` = the guide's first screenshot).

Meta-description text resolves per locale through a fallback chain, so you only author what matters:

| Page | `<meta name="description">` source |
|---|---|
| Guide | `guides[].description` → guide `title` |
| Group | `groups[].description` → group `title` |
| Onboarding | `onboarding.description` → onboarding `title` |
| Index | `seo.description` → `site.title` |

`guides[].description` is a new optional `LocalizedText` field — the per-guide snippet for search results and shared-link previews; it falls back to the guide `title` when omitted.

#### Deploying to GitHub Pages

```bash
steppy build --config steppy.guide.config.ts
# Push outputDir to the gh-pages branch (or set GH Pages "deploy from branch" → /docs):
git subtree push --prefix dist-static origin gh-pages
```

The `.nojekyll` file ensures GH Pages serves files as-is without Jekyll preprocessing.

#### Customizing the static layout

Override `static/page.html.j2`, `static/index.html.j2`, or `static/guides.css` by copying any of them from `src/steppy/templates/static/` into your project's `templates/static/` directory. See the **Customizing templates per project** section.

### Interactive mode — Lit `<steppy-guide>` web component

Adding `'interactive'` to a guide's `modes` makes Steppy emit a Guideflow-style interactive demo alongside (or instead of) the stepbystep guide. The same Markdown scenario drives both — interactive mode is a renderer, not new scenario syntax.

```ts
guides: [
  { slug: 'create-project',
    title: 'Create a project',
    scenario: 'editor/create-project.md',
    modes: ['stepbystep', 'interactive'] },   // emit both
],
```

What the build adds per guide:

| File | Where | Purpose |
|---|---|---|
| `guide.json` | Angular: `assetsDir/<slug>/`; static: `outputDir/<slug>/` | Manifest consumed by `<steppy-guide>` — locales × layouts × frames with screenshots, hotspot bboxes, cursor timings. Schema in [`packages/player/schema/guide.schema.json`](../packages/player/schema/guide.schema.json) |
| `<slug>-interactive.component.ts` | Angular: `outputDir/<slug>/` | Standalone Angular component that mounts `<steppy-guide src="…/guide.json">` and bridges ngx-translate's locale signal |
| `interactive.html` | Static: `outputDir/<slug>/` | Self-contained HTML page that loads `../assets/steppy-player.js` and mounts `<steppy-guide src="./guide.json">` |
| `steppy-player.js` | Angular: `assetsDir/`; static: `outputDir/assets/` | One-time copy of the Lit ESM bundle (Lit is bundled in — no host npm dep). Skipped when no guide opts in |

The player is a single Lit web component (`<steppy-guide>`) with:
- **Attributes** — `src`, `locale`, `variant` (layout), `autoplay`, `start-index`
- **Methods** — `next()`, `prev()`, `restart()`, `goTo(n)`
- **Events** (CustomEvent, `composed: true`) — `steppy:ready`, `steppy:step`, `steppy:hotspot {target:'correct'|'miss'}`, `steppy:locale`, `steppy:variant`, `steppy:complete`, `steppy:error`
- Built-in chrome (progress, restart, locale + variant switchers) and keyboard a11y (`Enter`/`Space` on focused hotspot, `←`/`→` review nav)

The player's source lives in [`packages/player/`](../packages/player/). Build it once locally before running `steppy build` on a project that uses interactive mode:

```bash
npm --prefix packages/player install
npm --prefix packages/player run build
# Then re-copy the three vendored bundles, each .js + .js.map (CI does this on release):
for b in steppy-player steppy-cursor-legacy steppy-video; do
  cp "packages/player/dist/$b.js" "packages/player/dist/$b.js.map" src/steppy/_player_bundle/
done
```

The build also writes `steppy-tour.js`, the in-app tour overlay (`@steppy/player/tour`). It is **not** vendored: an app bundles it itself, so do not copy it into `_player_bundle/`.

`steppy build` raises `CursorBundleMissingError` with the same hint if a bundle it needs is missing — that's your cue to run the build above.

A dev harness at [`packages/player/dev/index.html`](../packages/player/dev/index.html) loads a sample manifest with SVG placeholder screenshots so you can interactively test the player UX without capturing real screenshots: `npx http-server packages/player -p 8765` then open `http://localhost:8765/dev/index.html`.

### CLI usage

```bash
steppy build                                  # uses ./steppy.guide.config.ts
steppy build --config path/to/config.ts       # custom config path
steppy build --dry-run                        # preview without writing
steppy build --pdf --handbook                 # + per-page PDFs and a one-document handbook (static target only)
steppy build --video                          # + narrated MP4s for guides declaring modes: ['video']
```

**Prerequisites**: Node.js with `tsx` available via `npx` (used to evaluate the TypeScript config). Screenshots must be captured first via `steppy run --screenshots` (or `steppy run --guide`).

### Config field reference

| Field | Required | Default | Description |
|---|---|---|---|
| `scenariosRoot` | yes | — | Directory containing `.md` scenario files |
| `screenshotsDir` | no | `./output/screenshots` | Source directory for captured screenshots |
| `outputDir` | yes | — | Output directory (Angular code root or static-site root) |
| `assetsDir` | angular only | — | Where screenshots are copied (e.g., `./public/assets/<role>`). Ignored for `target: 'static'`. Image URLs in generated HTML are absolute `/assets/<role>/...`, so point this directly at the host's `public/assets/<role>/` |
| `role` | yes | — | Default role for guide step filtering. Also drives the i18n namespace (`<ROLE>.guides.*`), the routes export name (`<ROLE>_ROUTES`), and the screenshot URL prefix (`/assets/<role>/`) |
| `locales` | no | `['fr']` | Supported locales; HTML generated per locale |
| `defaultLocale` | no | `'fr'` | Default selected locale (Angular: in components; static: which locale `index.html` links to) |
| `layouts` | no | `[]` | Optional layouts dimension. When non-empty, the build assembles each guide once per (locale, layout) and renders an in-page toggle. Layout names must match the keys declared under `screenshot.layouts` in `config.yaml`. Empty/missing = single-layout build (back-compat) |
| `defaultLayout` | no | first declared layout | Layout shown on initial render. Must be a member of `layouts`. Auto-defaults to the first declared layout when omitted |
| `localeLabels` | no | built-in endonyms | `{locale: label}` overrides for the locale switcher's display names. Steppy ships `fr → Français`, `nl → Nederlands`, `en → English`, `de → Deutsch`, `es`, `it`, `pt`, `ca`, …; an unmapped locale falls back to its uppercased code (`pt-BR` → `PT-BR`). Add a row only for a locale Steppy doesn't know, or to override a label |
| `defaultTemplate` | no | `'overlay'` | Project-wide default interactive presentation template (`'overlay'` \| `'sidebar'` \| `'inline'`). Per-guide `template` wins. Runtime-switchable by the viewer — this is only the *initial* pick. See the steppy-template skill |
| `cursor` | no | `'arrow'` | Project-wide default interactive cursor mode (`'arrow'` \| `'hotspot'` \| `'off'`). Per-guide `cursor` wins |
| `target` | no | `'angular'` | `'angular'` or `'static'` |
| `site.title` | yes | — | LocalizedText. Site title used in manifest, page `<title>`, and the static landing page |
| `guides[].slug` | yes | — | URL-safe identifier (alphanumeric, hyphens, underscores) |
| `guides[].title` | yes | — | LocalizedText. Display title in header and navbar |
| `guides[].description` | no | — | LocalizedText. Per-guide meta description for public `seo` mode (search snippet / link preview); falls back to `title` |
| `guides[].scenario` | yes | — | Path to `.md` file relative to `scenariosRoot` |
| `guides[].modes` | no | `['stepbystep']` | Any combination of `'stepbystep'`, `'interactive'` and `'video'`. All three are supported on both `'angular'` and `'static'` targets — interactive emits a per-guide `<steppy-guide>` host (Angular component or static HTML page) plus a `guide.json` manifest and copies the `steppy-player.js` ESM bundle once per build; `'video'` is **inert until `steppy build --video`** arms it (see Narrated video guides) |
| `guides[].template` | no | inherits `defaultTemplate` | `'overlay'` \| `'sidebar'` \| `'inline'` — the interactive player's presentation layout. Per-guide override. Ignored by `stepbystep` |
| `guides[].cursor` | no | inherits project `cursor` | `'arrow'` \| `'hotspot'` \| `'off'` — the interactive player's cursor presentation. Per-guide override |
| `guides[].group` | no | — | Slug of the parent group. Guide is hidden from `navbar.order` and surfaced on the group's landing page instead |
| `groups` | no | `[]` | Group definitions (see below). Groups are top-level navbar entries with their own landing page |
| `groups[].slug` | yes | — | URL-safe identifier; shares URL space with guide slugs (collisions rejected) |
| `groups[].title` | yes | — | LocalizedText. Display title in navbar and on landing page |
| `groups[].description` | no | — | LocalizedText. Optional intro text on the group's landing page |
| `onboarding` | no | — | Onboarding overlay (see below). When `enabled`, occupies the reserved URL `/getting-started` |
| `onboarding.enabled` | no | `true` | Toggle the onboarding entry without removing the block |
| `onboarding.title` | no | `'Getting Started'` | LocalizedText. Title in navbar and on the onboarding landing page |
| `onboarding.description` | no | — | LocalizedText. Optional intro text on the onboarding landing page |
| `onboarding.guides` | yes if enabled | — | Ordered list of guide slugs (must reference existing `guides[].slug`s) |
| `navbar.order` | yes | — | Ordered list of top-level slugs — group slugs and ungrouped-guide slugs only. Onboarding is added automatically; do not list `getting-started`. Guides with a `group:` set must not appear here |
| `seo` | no | private | Static-target SEO / indexing controls (see the Static target section). Omitted ⇒ private |
| `seo.mode` | no | `'private'` | `'private'` (noindex + `robots.txt` Disallow) or `'public'` (sitemap, canonical, hreflang, descriptions, optional OG) |
| `seo.baseUrl` | public only | — | Absolute site origin (e.g. `https://guides.example.com`). Required when `mode: 'public'` |
| `seo.description` | no | — | LocalizedText. Site-wide default meta description (index page + ultimate fallback) |
| `seo.openGraph` | no | `false` | Emit Open Graph / Twitter card tags (public mode only). `og:locale` is emitted only for a `language_TERRITORY` locale — omitted for `fr`, `zh-Hant`, `es-419` |
| `seo.lastmod` | no | — | W3C datetime (`2026-09-14`, or `2026-09-14T12:00:00Z` / `…+02:00`) stamped on every sitemap entry. Unset ⇒ no `<lastmod>` ⇒ byte-reproducible builds. No `'auto'` mode — feed it `git log -1 --format=%cI` |

### Validation rules

- All guide slugs must be unique; all group slugs must be unique; guide and group slugs must not collide
- Every slug in `navbar.order` must exist in `guides` (ungrouped) or `groups`. A guide with a `group:` set is rejected if listed in `navbar.order`
- Every guide's `group` must reference an existing group slug
- `defaultLocale` must be in `locales`
- LocalizedText fields reject empty strings, empty maps, and non-string values
- Referenced scenario files must exist
- Screenshots directory must exist
- `'interactive'` mode is supported on both `'angular'` and `'static'` targets (the historical static-target rejection was lifted in stage 4 of the interactive-player feature).
- When `onboarding.enabled` is true: `onboarding.guides` must be non-empty, contain no duplicates, and every slug must reference an existing guide; the slug `getting-started` is reserved and may not be used by any guide or group
- `layouts` entries must be unique non-empty strings. `defaultLayout` (when set) must be a member of `layouts`; when `layouts` is non-empty and `defaultLayout` is omitted, it auto-defaults to the first declared layout
- `seo` affects the **static target only**. `seo.baseUrl` is required when `seo.mode` is `'public'` and must be an absolute `http(s)` URL; with no `seo` block the build is private (noindex + `robots.txt` Disallow)
- `seo.lastmod` must be a W3C datetime — a timezone is mandatory whenever a time is given, so `2026-09-14T12:00:00` is rejected
- Every entry in `locales` must be a BCP-47 language tag: a 2-3-letter language, optionally a 4-letter script, a 2-letter or 3-digit region, and variant subtags. `-` and `_` are both accepted and case is free, and **the tag is kept exactly as written** for filenames, screenshot directories and `guide-text:` / `LocalizedText` keys — only `hreflang`, `<html lang>` and `og:locale` are normalised. Two locales that normalise to the same tag are rejected, as is a name that also appears in `layouts` (a capture path carries both as bare directory segments). The same grammar applies on the capture side — `config.yaml`'s `locales:`, `steppy run --locales` and `steppy guide --locale` — so the two halves of a project cannot disagree about what a locale is. A regional locale resolves *content* through its bare language: `guide-text: { fr: … }` and an `i18n.localstorage.values` entry keyed `fr` both serve `fr-BE`, an exact `fr-BE:` key wins where one is written, and a locale the app cannot be switched into at all is warned about rather than skipped silently

---

## PDF and handbook export (`--pdf` / `--handbook`)

Steppy lays out no PDF of its own — it prints the **already-generated guide HTML** through headless Chromium. So the `@media print` rules in the guide templates *are* the PDF layout, and a PDF always looks like the page it came from. Both flags work on `steppy guide` and `steppy build`; nothing in a scenario file changes.

```bash
steppy guide --guide --pdf              # one PDF beside each generated guide page
steppy guide --guide --pdf --handbook   # + <role>-handbook.html/.pdf per role/locale/layout
steppy build --pdf --handbook           # static target only: per-page PDFs + handbook/<locale>.html/.pdf
```

- **`--pdf`** prints each guide page. `steppy build --pdf` also links the PDF from the published page.
- **`--handbook`** assembles every guide into **one document** — cover, table of contents, then each guide in navbar order. `steppy guide` writes `<role>-handbook.html` (+ `.pdf`) beside the per-guide pages; `steppy build` writes `handbook/<locale>.html` (+ `.pdf`).
- **`steppy build --pdf` / `--handbook` are static-target only** — the Angular target rejects both, because the host app owns its own pages.
- `--dry-run` reports the PDFs it *would* print and never launches a browser.
- A failed page is reported and skipped, never fatal. The one hard error is Chromium failing to launch — the message tells you to run `playwright install chromium`.

Paper settings come from an optional `pdf:` block in `config.yaml` (shown values are the defaults):

```yaml
pdf:
  format: A4                 # any Chromium paper format
  landscape: false
  margin: { top: 12mm, right: 12mm, bottom: 14mm, left: 12mm }
  print_background: true     # keep true — false drops every step badge and screenshot frame
  scale: 1.0
  page_numbers: true
  timeout: 30000             # ms to wait for below-the-fold images before printing anyway
```

**The one authoring-visible constraint**: Chromium cannot split an image across pages, so a full-page screenshot taller than the printable area would be *clipped*. The templates cap it with `--steppy-print-max-image-height` (default `180mm`). If your PDFs show shrunken screenshots, that's the knob — see the steppy-template skill.

---

## Narrated video guides (`modes: ['video']`)

A third rendering mode: the same manifest that drives the interactive player is laid onto a millisecond timeline and rendered to a narrated MP4 (+ WebVTT captions + a poster image), one per **(guide × locale × layout)**. Steppy screen-records nothing — it replays the captured screenshots and cursor animation frame by frame, so the video is as clean as the guide.

### Two gates, on purpose

```ts
// steppy.guide.config.ts — gate 1: the content decision
guides: [
  { slug: 'create-project', title: 'Create a project', scenario: 'editor/create-project.md',
    modes: ['stepbystep', 'video'] },
],
```

```bash
# gate 2: the cost decision
steppy build --video          # renders the guides above
steppy guide --video          # same, for the standalone guide pipeline
```

**Declaring `modes: ['video']` alone renders nothing.** The mode says *this guide deserves a video*; the flag says *pay for it now*. A render costs minutes of CPU per video and, with a paid TTS provider, real money — so an unarmed build stays cheap and CI doesn't accidentally re-render the corpus.

> **`steppy build --video` / `steppy guide --video` is unrelated to `steppy run --video`**, which records Playwright debugging footage of *failed tests* into `output/videos/`. Same word, different feature.

### Where the files land

| Entry point | Output |
|---|---|
| `steppy guide --video` | Beside each guide page: `output/guides/<role>/<locale>[/<layout>]/<slug>.mp4` |
| `steppy build --video` (static) | `outputDir/<slug>/<locale>.mp4` — or `<locale>-<layout>.mp4` when the guide has more than one layout |
| `steppy build --video` (angular) | `assetsDir/<slug>/<locale>[-<layout>].mp4` |

Each MP4 ships with a `.vtt` caption track and a poster image. The static target also emits a small video page per guide.

If two layouts resolve to the *same* captures, Steppy warns and renders once instead of producing identical footage twice — the fix is to actually capture the layout (`steppy run --guide --layouts …`) or drop it from the build config.

### Narration is your `guide-text`

The voiceover speaks each step's **`text`** — the same string the guide reader sees — falling back to `title` when a step only has one. That has real authoring consequences:

- **Write for the ear.** `"Cliquez sur le bouton en haut à droite"` reads fine and speaks fine. `"Cliquez sur **Nouveau projet** (cf. §3)"` does not — markdown emphasis and cross-references are noise when spoken.
- **Every locale you render needs narration.** A missing `guide-text` locale is already a `steppy guide` failure; with video it also means a silent step.
- **Steps stretch to fit the voiceover.** The timeline never truncates narration: a step is held at least `min_step_ms` and long enough for its clip plus `narration_pad_ms`. A three-sentence step makes a three-sentence-long scene — pacing is written, not configured.
- Prefer the **structured `guide-text`** format on video guides: a short `title` for the on-screen caption band, a fuller `text` for what gets spoken.

### Voices — `video.tts`

The shipped default is **`provider: none`: captions only, no audio, no network, no bill.** That is a genuinely useful mode — a silent captioned walkthrough — and it is what you get until you opt in.

Available providers: `none`, `piper`, `espeak` (local), `http` (any HTTP API), plus ready-made presets `openai`, `elevenlabs`, `azure`, `google` over the same HTTP engine.

```yaml
video:
  tts:
    provider: openai         # none (default) | piper | espeak | http | openai | elevenlabs | azure | google
    voice: alloy
    api_key: ${OPENAI_API_KEY}    # env interpolation — never commit a literal key
    speed: 1.0
```

`api_key: ${VAR}` uses the same `${VAR}` / `${VAR:-default}` interpolation as the rest of `config.yaml`, which is the only correct way to do this — `config.yaml` is a committed file.

### Tuning the render — `video`

Every field is optional; shown values are the defaults:

```yaml
video:
  fps: 24
  width: 1280              # display width; also affects duration (cursor travel is measured in display px)
  max_height: null
  captions: band           # band | off
  crf: 23                  # x264 quality — lower is better/bigger
  preset: medium
  jpeg_quality: 90
  intro_ms: 1200
  outro_ms: 1500
  hold_ms: 700             # pause on a step after its action
  min_step_ms: 1500        # floor per step, before narration stretches it
  narration_pad_ms: 400    # silence after each clip
  ffmpeg_path: null        # explicit binary, if not on PATH
  timeout: 120000
  cache: true
  cache_dir: .steppy/video-cache
  branding: { logo: null, accent: null, font_family: null }
  stamp:   { enabled: false, text: "{version} · {date}", version: dev }
```

### Before a long render

```bash
steppy video check      # ffmpeg / ffprobe / Chromium / the TTS provider / the API key — all at once
steppy video cache      # what the render + narration caches are holding
steppy video cache --prune-older-than 30      # reclaim least-recently-used entries
steppy video cache --max-size 2000            # evict until each cache fits in 2 GB
steppy video cache --clear --tts              # empty just the narration clips
```

`steppy video check` exists because a 20-minute render that dies on a missing `ffmpeg` — or an unset API key — is the expensive way to learn that. Run it once when you set video up, and in CI before the render step.

Both caches are content-addressed and **grow monotonically — nothing else ever collects them**. `steppy video cache` is the only reaper, so put a `--prune-older-than` or `--max-size` call somewhere if videos are part of a regular build.

**Requires `ffmpeg`** (and `ffprobe`) on PATH, or `video.ffmpeg_path` pointing at it.

---

## In-app tours — `tour:` and the `tour-*` keys

A scenario can also compile into an **in-app tour**: popovers over the *live* application that walk a user through the flow, pointing at the real buttons and fields. The app mounts one `<steppy-tour-host>` element and calls `host.start('<tour-id>')`; a tour file is JSON data (never code) that names **one test-id per step**. The overlay highlights and explains — it never clicks, types or submits — and **CI replays every tour**, so a renamed test-id fails the build instead of quietly breaking the tour in production. Full reference: [docs/tours.md](https://github.com/Steppy-dev/steppy/blob/main/docs/tours.md).

### Prerequisites — the `tours:` block

Off unless `config.yaml` enables it. An absent block and `enabled: false` behave identically: nothing tour-related is written and every generated file stays byte-identical.

```yaml
tours:
  enabled: true                          # master switch (default false)
  default: false                         # true = opt every scenario in; a scenario's own `tour:` still wins
  build_id: git-sha                      # default; also git-sha:<path>, a literal, "${APP_BUILD_ID:-git-sha}"
  guide_base_url: https://guide.example.com/   # optional: the "Open the full guide" link
  targets:                               # at least one
    - type: app-assets
      path: ../invoice-app/public/tours  # a folder the app serves: tours.manifest.json + <tour-id>.<locale>.json
```

Also available: `locales` / `default_locale` (default: the project's), `test_id_attribute` (default `data-testid`, `data-*` only), `layouts` (see the rules below) and `replay` (default `true`). A `- type: guide` target makes the static `steppy build` republish the tour files at `<site>/tours/<build-id>/`.

### Opting a scenario in

```yaml
tour: true                         # or false, or a block:
tour:
  id: create-invoice               # default: the scenario slug — what the app passes to start()
  title: {en: Create an invoice, fr: Créer une facture}   # default: the scenario title
  entry: /invoices/:invoiceId      # the route the tour starts on (default: inferred, below)
  role: editor                     # required when the scenario uses several roles
  guide_slug: create-invoice       # the guide page "Open the full guide" links to
```

Frontmatter `tour:` opts the scenario in; a step's `- tour:` overrides one step. They are different keys — `- tour: true` on a step is rejected.

- **`id`** (default: the file slug) is 1–64 characters `[A-Za-z0-9_-]`, starts with a letter or digit, is not a Windows device name (`con`, `nul`, …), and is unique case-insensitively across scenarios (`tour-id-invalid`, `tour-id-duplicate`).
- **`title`** as a map is read: exact tag → base language → the default locale's entry → the scenario title (`title-fallback`). Give every multi-language tour a `title` map: apps list tours by it.
- **`guide_slug`** defaults to the slug only when the scenario has `guide: {enabled: true}`; the link also needs `tours.guide_base_url`.
- **`enabled: false`** in the block (or `tour: false`) opts out, even under `tours.default: true`.

### How a scenario becomes a tour

**Start point.** With `tour.entry`, right after the `navigate` whose target matches it (`{{var}}` in a target matches `:param` in the pattern). Without it, right after the first `navigate` following the last `login`. Everything before is setup: the replay runs it, the tour never shows it.

| Scenario step | In the tour |
|---|---|
| `click`, `expand` | click (`expand` warns `may-not-change`: it only clicks when the section is closed) |
| `toggle` | change (`may-not-change` when `state:` is set) |
| `select`, `select-option`, `pick-date`, `pick-time` | change |
| `fill`, `fill-date` | input |
| `fill-form` | one input step per field, with the same text |
| `upload` | with `guide-cursor`: the cursor's target, manual. Otherwise change |
| `assert`, `wait-for`, a trailing `expect:` | no text: a hidden gate (the next step waits for that test-id), dropped when a skipped step in between may change the page — a click, a confirm, a navigate, `custom` code that does more than read and wait (`gate-stale`). With text: an information step (manual) |
| `custom`, `call` | with `guide-cursor` click/type: click/input. Text without a cursor: a centred manual step (`unguided-action`). Otherwise skipped |
| `confirm` | skipped (`unguided-action`). With `tour: keep` and text: a manual note shown beside the open dialog, never over it |
| `navigate`, `login`, a reload after the start | skipped (`navigate-in-tour`, `reloads-page`) |
| `hover`, `scroll`, `keyboard`, `wait`, `wait-for-text`, `capture`, steps with `when:` or `optional:` | skipped |
| `only-in: test` / `only-in: guide` steps | dropped / kept |

**Text**, per locale: `tour-text`, then `guide-text` (the exact tag, then its base language: `fr-BE` reads `fr`; `fr` never reads `fr-BE`). A step with neither shows a generic hint ("Click the highlighted element.") and warns `generic-text`. Languages are never mixed. A locale is emitted when at least one step has text in it; the default locale always is — a configured locale with no text at all gets no file (`locale-not-emitted`), and its users see the default language. **Step ids** are the target's test-id (`save-btn`, then `save-btn-2` on a repeat) or a slug of the step heading — never a position.

**Localization rules an author controls:**

- Write locale keys exactly as `config.yaml` spells them: `fr-BE`, not `fr_be` or `FR` (`tour-text-locale-untargeted` says "did you mean").
- Translate every step you want shown in a language; a missing one shows that language's generic hint, never another language's text.
- The overlay's buttons and generic hints exist in **en, fr, nl, de** only. For any other tour language the compiler warns `chrome-strings-missing`; the *app* must then pass the host's `strings` for it (see "In the app" below), or users get English buttons around their text.
- The overlay follows the app's language at run time (the host's `locale`, else `<html lang>`), mid-tour included, and sets `lang`/`dir` on the popover (right-to-left for Arabic, Hebrew, …). `tour-placement: left/right` stay physical.

### The four step keys

| Key | Values | Effect |
|---|---|---|
| `tour` | `skip`, `keep` | `skip` leaves the step out (it still runs in the test and the replay); `keep` keeps a step inference would skip (centred when it has no test-id) |
| `tour-text` | like `guide-text` | The step's tour text, per locale. Wins over `guide-text` |
| `tour-advance` | `click`, `input`, `change`, `visible`, `manual` | What moves the tour on. Must be something the step's own action does (table below) |
| `tour-placement` | `top`, `bottom`, `left`, `right`, `auto` | The preferred side of the target (ignored, with `placement-ignored`, on a centred step) |

`tour-advance` values a step accepts — anything else is `advance-not-replayable`, because the CI replay performs the step's own action and would wait forever:

| The step's action | Accepted (first = default) |
|---|---|
| `click`, `expand`, `custom`/`call` with a click `guide-cursor` | `click`, `manual`, `visible` |
| `fill`, `fill-date`, each `fill-form` field, `custom`/`call` with a type cursor | `input`, `change`, `manual` |
| `toggle`, `select`, `select-option`, `pick-date`, `pick-time` | `change`, `click`, `manual` |
| `upload` | `change` (`manual` behind a `guide-cursor` trigger), `manual` |
| an assertion or a `custom` with text, a step kept by `tour: keep` | `manual`, `visible` |

`visible` advances as soon as the step's own target is visible (the step shows centred until then): use it for an element that appears as a consequence of something the user did. `click`/`input`/`change`/`visible` need a target (`advance-needs-target`). `tour: keep` is refused on a `when:`/`optional:` step (`keep-not-allowed`), and `tour: skip` cannot be combined with other `tour-*` keys (`skip-and-annotations`).

```markdown
### 4. Name the invoice
- role: editor
- action: fill
- selector: '[data-testid="invoice-title"]'
- value: "Quarterly maintenance"
- tour-placement: bottom
- guide-text:
    en: "Type a title, for example Quarterly maintenance."
- tour-text:
    en:
      title: Title
      text: Give the invoice a short title your customer will recognise.
```

The test still types the value, the guide keeps its wording, and the tour shows an instruction with no test data in it.

### Authoring rules

- **A kept step targets exactly one test-id.** `[data-testid="x"]`, `button[data-testid="x"]` and `[data-testid="x"] input` all reduce to `x`; `match: first` is accepted. A class path, a prefix match (`^=`), a selector list, a `>>` chain, a `{{var}}` id, `within:`, or `match:` other than `first` is `target-not-test-id` — add a test-id in the app, or mark the step `tour: skip` / `tour: keep` (centred). Test-id values are `^[A-Za-z0-9_.:/-]{1,128}$`.
- **The target must be findable by the overlay, not just by Playwright.** The overlay looks in the main document's light DOM and takes the first *visible* match; Playwright pierces shadow roots and takes the first DOM match. An element inside another component's shadow root or an iframe passes the test and fails the tour (the replay catches it). Duplicate test-ids on one screen are a smell for the same reason.
- **Never put fixture values in step text.** Tours show instructions, never test data. When `guide-text` repeats what the test types, the compiler warns `fixture-leak` — write a `tour-text` without the value.
- **No JavaScript-dispatched clicks in `custom`.** The overlay cannot point at code. Replace them with `action: click` + `synthetic: true`, or add a `guide-cursor` block (`click`/`type`) naming the visible target.
- **Set `tour.entry` when the flow starts on another page** than the first `navigate` after login — typically a detail route. The app then supplies the params: `host.start('send-invoice', { params: { invoiceId: 1001 } })`.
- **Layout twins need `tours.layouts`.** A step restricted by `layouts:` compiles only when each of its layouts maps to a media query (else `layouts-unmapped`); the overlay and the replay then skip the twin that does not match the screen:

  ```yaml
  tours:
    layouts:
      desktop: "(min-width: 768px)"
      mobile: "(max-width: 767.98px)"
  ```
- **Assertions with text become information steps.** An `assert` carrying `guide-text` ("Your invoice is saved as a draft") is shown as a manual step with a Next button; without text it is an invisible gate. Leave the text off a check users should not see.
- **`tour-*` keys only where the tour keeps the step** — otherwise add `tour: keep` (see Common mistakes). And the replay re-runs the scenario's seed and steps, so the flow must be repeatable.

### Errors and warnings

Every message names the tour, the scenario, the step and its heading, and ends with a fix. Errors fail `steppy validate` (exit 1) and stop `generate` before anything is written; warnings never change an exit code. Infos (`step-skipped`, `conditional-skipped`, `gate-dropped`, `gate-stale`, `reduced-to-host`, `layout-media-unchecked`) print only with `steppy tours --explain`, which says why each step was left out.

| Code | Severity | Fix |
|---|---|---|
| `target-not-test-id` | error | Point the selector at one test-id, or `tour: skip` / `tour: keep` |
| `entry-invalid` | error | `tour.entry` (or the navigation it is inferred from) is not a same-origin route pattern: no hash route, no other origin, no `.`/`..`/`%2e` segment — set `tour.entry: /path/:param` |
| `entry-no-match` | error | No `navigate` matches `tour.entry`: fix the pattern or the step's target |
| `no-entry` | error | Add `tour.entry` or a `navigate` step (a warning when `replay: false`) |
| `tour-role-required` | error | Several roles in the scenario: set `tour.role` |
| `tour-role-unknown` | error | `tour.role` names a role the scenario does not use |
| `tour-no-role` | error | No step has a `role:` |
| `tour-id-invalid` | error | The id (or file slug) breaks the id grammar: set `tour.id` |
| `tour-id-duplicate` | error | Two scenarios produce the same id: set distinct `tour.id`s |
| `advance-not-replayable` | error | Choose a `tour-advance` the step's action does (a warning when `replay: false`) |
| `advance-needs-target` | error | Use `manual`, or give the step a test-id |
| `annotation-on-skipped-step` | error | Add `tour: keep`, or remove the `tour-*` keys |
| `annotation-outside-tour` | error | Remove `tour*` keys from steps before the start, `only-in: test` steps, or another role's steps |
| `skip-and-annotations` | error | `tour: skip` or the other `tour-*` keys, not both |
| `keep-not-allowed` | error | Remove `tour: keep` from the `when:`/`optional:` step |
| `no-kept-steps` | error | Nothing left to show: check the start point and the annotations |
| `layouts-unmapped` | error | Map every step layout under `tours.layouts` |
| `layout-media-mismatch` | error | Make the `tours.layouts` query agree with its `screenshot.layouts` width |
| `limit-exceeded` | error | Over a size cap (100 steps, 200-character title, 2000-character text, …): shorten |
| `scenario-unreadable` | error | Another scenario in the folder does not parse (tours compile from all of them) |
| `build-id` | error | Fix `tours.build_id` (`git-sha` needs a repository; set any `${VAR}` it uses) |
| `target-path` | error | Point the `app-assets` target at a folder of its own; never overwrite a foreign file there |
| `fixture-leak` | warning | Write `tour-text` without the typed value |
| `generic-text` | warning | Add `guide-text` or `tour-text` for that locale |
| `no-authored-text` | warning | No step has text in the default locale: add some |
| `locale-not-emitted` | warning | Add text in that language, or accept the default for it |
| `chrome-strings-missing` | warning | Tell the app to set the host's `strings` for that language |
| `title-fallback` | warning | Complete the `tour.title` map |
| `tour-text-locale-untargeted` | warning | Fix the locale key (exact spelling) or drop it |
| `unguided-action` | warning | Add `guide-cursor` (or use `click` + `synthetic: true`), or accept the centred step |
| `may-not-change` | warning | `tour-advance: manual` or `tour: skip` |
| `upload-target-may-be-hidden` | warning | Add `guide-cursor` naming the visible upload trigger |
| `navigate-in-tour` | warning | Start the tour later (`tour.entry`) or let the user click there |
| `reloads-page` | warning | Move the reload/login before the start if you can |
| `escape-in-tour` | warning | Check the replay: Escape may dismiss the tour |
| `entry-query-dropped` / `entry-id-segment` | warning | Set `tour.entry` with `:params` and no query |
| `placement-ignored` | warning | Drop `tour-placement` from the centred step |
| `text-link-dropped` | warning | Link only app routes or the guide site |
| `no-tours` | warning | Tours are enabled but no scenario opts in: add `tour: true` to one |
| `app-url-unresolved` | warning | An `apps.<role>.url` or `tours.guide_base_url` still holds an unset `${VAR}` at compile time: set it where you compile |
| `layout-media-overlap` | warning | Two `tours.layouts` queries match the same width: make them disjoint |
| `app-assets-skipped` | warning | The `app-assets` folder's parent is missing, so `generate` skipped the copy (`steppy tours` makes it an error): check out the app, or ignore it in scenario-only CI |

### In the app — what a developer wires once

Not scenario work: it happens in the app's own code, and **`steppy-app-tours`** is the skill for it (serving the files, the Angular adapter, plain JS, React and Vue, test-ids in components, CSP, the language, troubleshooting in the running app). `steppy init --app-dir <app repository>` installs that skill where the app's code lives. In short:

- The app serves the `app-assets` folder and mounts the overlay once: `provideSteppyTours({fallbackManifest: 'tours/tours.manifest.json'})` + `<steppy-tours />` in an Angular app (`@steppy/angular`), else `import '@steppy/player/tour'` and one `<steppy-tour-host fallback-manifest="…">`. Neither package is on npm yet.
- It starts a tour from its own UI: `start('<tour-id>', {params})`, the params filling `tour.entry`'s `:params`.
- It owns the test-ids. A `target-not-test-id` error is usually fixed in the app: give the element its own `data-testid`, then point the step at it.
- Rebuild the app after `steppy tours`/`generate` so it serves the new files; commit the `app-assets` copy (CI checks it with `steppy tours --check`).

### What `generate` adds — the CI replay

`steppy generate` writes the tour files (`output/tours/<build-id>/` plus a flat copy in every `app-assets` target) **and appends `test_<slug>__tour` to the scenario's own test file**. The replay runs the setup steps, starts the tour through the host's API, and for each tour step checks that the popover is anchored on the element, performs the scenario's own action code, and checks that the tour moved on; it ends by checking `tour-complete` fired and no `tour-error` did. It compiles *every* opted-in scenario even when you generate one file, because the manifest lists them all. `steppy tours` writes the tour files only, without touching tests. Wiring the replay into CI, and reading a failed one: the **`steppy-ci-report`** skill.

---

## Per-locale screenshots

Guides switch on-page **text** by locale automatically (Angular: `CONTENT[locale]` signal toggled by the language selector; static: one full `<slug>/<locale>.html` page per locale). To also switch the **screenshots** — so the guide reader sees the app in their own language — capture the screenshots once per locale and let `steppy build` bake locale-specific image URLs.

The mechanism: most apps store the user's UI language in `localStorage` under a single key, with a different value per locale (e.g. `user-language: "fr_BE" | "nl_BE"`). Configure that mapping per role and Steppy will set it before any page script runs, on every test run that's parametrized over locales. Multilingual capture only happens at guide-build time — plain `steppy run` (CI / regression) keeps a single un-localed pass so you don't pay an N× test-time tax.

### 1. Declare the localStorage mapping per app

In `config.yaml`, add an optional `i18n.localstorage` block under each app that supports multiple languages:

```yaml
apps:
  editor:
    name: "My App — Editor"
    url: "http://localhost:4200"
    auth:
      mode: form
      credentials: { username: ..., password: ... }
    i18n:
      localstorage:
        key: "user-language"          # the localStorage key the app reads
        values:
          fr: "fr_BE"                 # locale code → value the app expects
          nl: "nl_BE"
          en: "en_US"
          de: "de_DE"
```

- The same `key` is written for every locale; only the `values` differ.
- Different apps in the same project can use different keys (e.g. one app reads `user-language`, another reads `i18nextLng`).
- The block is optional. Apps without `i18n` are never parametrized over locales — useful when only one role of a multi-app project has a language switcher.

### Pinning browser locale + timezone (deterministic dates / numbers)

Two optional sibling fields on each app pin the Playwright `BrowserContext` locale and timezone so date / number / `Intl.*` formatting is byte-identical between a host-OS run, a CI runner, and a Linux worker container:

```yaml
apps:
  editor:
    name: "My App — Editor"
    url: "http://localhost:4200"
    auth: { ... }
    locale: "fr-FR"                # BCP-47; sets navigator.language + Accept-Language
    timezone: "Europe/Brussels"    # IANA tz; pins the browser's reported zone
    i18n:
      localstorage: { ... }
```

- `locale` and `timezone` are **independent of `i18n.localstorage`**. The localStorage block tells *your app* which UI language to render; `locale` / `timezone` tell the *browser* what to report to JS APIs (`new Date().toLocaleString()`, `Intl.DateTimeFormat`, etc.). Set both for full determinism.
- During `--guide` / `--locales` runs that parametrize over `STEPPY_LOCALES`, the per-parametrization locale **overrides** the static `apps.<role>.locale` — so multilingual captures still render dates in the locale they're being captured for. `timezone` is not parametrized; it's whatever `config.yaml` declares.
- Both fields are optional. When unset, Playwright uses the browser's default (i.e. previous behaviour — no migration required).

### 2. Capture screenshots for each locale

Two ways to drive a multilingual run:

| Command | Behavior |
|---|---|
| `steppy run --guide` | Runs every `guide.enabled: true` scenario with screenshots ON, parametrized over **every locale in `config.yaml`'s `locales:` list**. |
| `steppy run --locales fr,nl` | Explicitly parametrize over a chosen subset. Mutually exclusive with `--no-guide`. Combine with `--guide` to override the auto-populated set. |
| `steppy run` (no flag) | Single un-localed pass. No `localStorage` is set. Use for normal CI / regression. |

Internally these set the `STEPPY_LOCALES=fr,nl` env var, which the generated `conftest.py` reads to parametrize each role page fixture. For each locale, an `add_init_script` writes `localStorage.setItem("user-language", "fr_BE")` (or whatever the app declared) before the first page navigation.

**On disk**, parametrized runs land in a per-locale subdir:

```
output/screenshots/
  <scenario>/
    fr/
      step-01.png, step-01.json
      step-02.png, ...
    nl/
      step-01.png, step-01.json
      ...
```

A non-parametrized `steppy run --screenshots` keeps writing to the legacy flat layout `output/screenshots/<scenario>/step-NN.png` — no migration required for single-language projects.

### 3. Build picks up locale-specific URLs automatically

`steppy build` looks for `<scenario>/<locale>/` first and only falls back to the flat layout when the per-locale dir is missing. The screenshot URL resolver inserts a `<locale>/` segment in image URLs when sourced from a per-locale dir, and omits it otherwise:

| Source layout | Static URL | Angular URL |
|---|---|---|
| `screenshots/<scenario>/<locale>/step-NN.png` | `../assets/<slug>/<locale>/step-NN.png` | `/assets/<role>/<slug>/<locale>/step-NN.png` |
| Flat `screenshots/<scenario>/step-NN.png` | `../assets/<slug>/step-NN.png` | `/assets/<role>/<slug>/step-NN.png` |

For the static target, each `<slug>/fr.html` ends up referencing `../assets/<slug>/fr/step-NN.png` and `<slug>/nl.html` references `../assets/<slug>/nl/step-NN.png`. The locale switcher already routes between sibling files — no template changes required.

For the Angular target, `<slug>.content.ts` renders one HTML fragment per locale into the `CONTENT` map. Each fragment bakes its own image URLs at build time. The active locale is owned by the host app via `@ngx-translate/core`'s `TranslateService` — calling `translate.use('nl')` flips both copy and screenshots in one click (the per-guide component subscribes to `translate.onLangChange` via `toSignal`).

### Workflow summary

```bash
# 1. Edit config.yaml: add i18n.localstorage to the relevant app.
# 2. Regenerate tests (the conftest now reads STEPPY_LOCALES at runtime).
steppy generate

# 3. Capture per-locale screenshots.
steppy run --guide                    # all locales from config.yaml
# or
steppy run --locales fr,nl --screenshots    # explicit subset

# 4. Build the guide artifacts; per-locale screenshots are picked up automatically.
steppy build
```

### Common mistakes

- **Capturing with `steppy run` (no flag) and expecting per-locale dirs.** Plain run is single-pass; you need `--guide` or `--locales`.
- **Adding `i18n.localstorage` but the app reads a different key.** The init script no-ops if `key` is wrong; you'll get correct text but the *app* will still render in its default language. Inspect the running app's `localStorage` in DevTools to confirm the key.
- **Capturing for fewer locales than the build configures.** If `steppy.guide.config.ts` lists `locales: ['fr', 'nl']` but you only ran `--locales fr`, the build will fall back to the flat layout for `nl` (or fail if no flat screenshots exist either). Either capture every configured locale or trim the build config.
- **Mixing flat and per-locale on disk.** When `<scenario>/<locale>/` exists for one locale but not another, the build uses the per-locale dir for the locales that have one and the flat dir for the rest. Recapture cleanly to avoid drift.
- **Forgetting to recapture after changing locale values.** Editing `apps.<role>.i18n.localstorage.values.fr` from `"fr"` to `"fr_BE"` doesn't retroactively rewrite existing screenshots — rerun `steppy run --guide` to refresh them.

---

## Per-layout screenshots (desktop / mobile)

When the app's UI differs significantly between desktop and mobile (different navigation, different button placement, different flow), Steppy can capture the same scenario at multiple viewports and ship one guide page that toggles between them.

### 1. Declare layouts in `config.yaml`

Add a `screenshot.layouts` block (alongside the existing `screenshot.viewport`):

```yaml
screenshot:
  viewport:                          # used in test mode and as default fallback
    width: 1280
    height: 720
  device_scale_factor: 1             # optional; default devicePixelRatio (see below)
  layouts:                           # NEW: optional. Empty = no layout dimension.
    desktop:
      width: 1280
      height: 720
    mobile:
      width: 390
      height: 844
      device_scale_factor: 3         # capture mobile at 3× density so it isn't blurry
```

Each entry maps a name to a Playwright viewport. Layout names are arbitrary — `tablet`, `wide`, `phone-small` all work — but most projects stick to `desktop` and `mobile`.

#### `device_scale_factor` — crisp mobile screenshots on high-DPI displays

A narrow mobile layout (e.g. `390` wide) captured at the default `device_scale_factor: 1` produces a **390-pixel-wide PNG**. The guide then displays it in a wider content column, so the browser upscales it and it looks soft/blurry on retina screens — while the wide desktop capture (1280px) stays crisp. Set `device_scale_factor` on the layout to capture at a higher pixel density (a value of `3` makes a 390px viewport produce a 1170px-wide PNG, matching iPhone-class screens):

- Per-layout `device_scale_factor` (under each `layouts.<name>`) overrides the top-level `screenshot.device_scale_factor` default. Both default to `1`, so projects that don't set it are unchanged.
- It controls **only pixel density** — it does **not** set `is_mobile`/`has_touch`, so the page renders exactly the same layout, just at higher resolution. (Width-based CSS breakpoints fire off the viewport width, unaffected.)
- Tradeoff: density scales pixels², so `device_scale_factor: 3` makes those PNGs roughly **9×** the file size (≈4× at `2`). Bump it on the narrow layouts that need it (usually only `mobile`); leave wide desktop layouts at `1`.
- The guide templates pair this with a no-upscale display cap (`width: auto; max-width: 100%` on the screenshot `<img>`), so a dense capture fills the column crisply while a low-res capture renders at its native size rather than being stretched and blurred. No scenario changes are needed — this is purely a `config.yaml` concern.

### 2. Capture once per layout

`steppy run --guide` automatically parametrizes over **every** configured layout (cartesian with locales), and sets `STEPPY_LAYOUTS` so the generated `conftest.py` swaps the browser viewport per fixture parametrization. Capture an explicit subset with `--layouts`:

| Command | Behavior |
|---|---|
| `steppy run --guide` | Auto-parametrizes over every locale × every configured layout |
| `steppy run --guide --layouts desktop` | Captures only the desktop layout (× every locale) |
| `steppy run --layouts desktop,mobile` | Test-mode multi-viewport run: every test runs once per layout at the configured viewport. No screenshots unless `--screenshots` is also passed. Use this to verify scenarios work across device sizes. |

**On disk**, layouts add a subdirectory under the locale segment:

```
output/screenshots/
  <scenario>/
    fr/
      desktop/
        step-01.png, step-01.json, ...
      mobile/
        step-01.png, step-02.png, ...   # mobile may have an extra step (mobile-only)
    nl/
      desktop/ ...
      mobile/ ...
```

When `screenshot.layouts` is empty/missing, behavior is identical to before (single viewport, flat path) — single-layout projects need no migration.

### 2b. Declare which viewports a flow covers (frontmatter `layouts:`)

Optional, but it is what stops a desktop-only flow from producing phantom mobile captures — and, downstream, from shipping a "Mobile" tab on the built site that swaps between identical screenshots. Add it to the scenarios whose responsive behavior matters:

```markdown
---
title: Book an appointment
layouts:
  - desktop
  - mobile
---
```

Absent = unchanged behavior. Declared = the whole flow parametrizes over those viewports (even on a plain `steppy run`), and `--layouts`/`--guide` intersects with the declaration. See "Conditional steps" above for the full semantics.

### 3. Differentiate steps with `layouts: [...]`

Steps that should run in both layouts need no `layouts:` field. Steps that diverge between desktop and mobile use a whitelist:

```markdown
### 1. Open the tasks list
- role: editor
- action: navigate
- target: /tasks
- screenshot: editor
- guide-text:
    fr: "Ouvrez la liste des tâches."

### 2. Tap hamburger menu (mobile only)
- role: editor
- action: click
- selector: [data-testid="hamburger"]
- screenshot: editor
- only-in: guide
- layouts:
  - mobile
- guide-text:
    fr: "Touchez le menu pour révéler la navigation."

### 3. Click the task
- role: editor
- action: click
- selector: [data-testid="task-row"]:first-child
- screenshot: editor
- guide-text:
    fr: "Cliquez sur la tâche à traiter."
```

The desktop guide page renders steps 1 and 3. The mobile guide page renders all three. Step numbers stay stable — the rendered guide simply skips the steps the active layout doesn't include.

### 4. `steppy guide` auto-discovers layouts on disk

After capture, `steppy guide --guide` (or `steppy guide --tag user-guide-<role>`) **scans the on-disk screenshot tree** for layout subdirs and renders one guide per (locale, layout) automatically. No new CLI flag — when the assembler finds `<slug>/<locale>/desktop/` and `<slug>/<locale>/mobile/`, it emits:

```
output/guides/<role>/<locale>/desktop/<slug>.{md,html}
output/guides/<role>/<locale>/mobile/<slug>.{md,html}
```

When the layout subdirs aren't present (single-layout / flat capture), output keeps the legacy path: `output/guides/<role>/<locale>/<slug>.{md,html}`. So existing single-layout projects keep working with no changes.

### 5. `steppy build` picks up layouts automatically

Add `layouts` and (optionally) `defaultLayout` to your `steppy.guide.config.ts`:

```ts
export default defineGuideConfig({
  // ...locales, guides, navbar...
  layouts: ['desktop', 'mobile'],
  defaultLayout: 'desktop',          // optional; defaults to first declared layout
});
```

The build assembles each guide once per (locale, layout) and renders an in-page toggle (mirroring the locale switcher, but client-side instead of separate URLs):

- **Static target**: `<slug>/<locale>.html` bundles both layouts in `<div data-layout="...">` wrappers; a small JS toggle swaps which one is visible. Active layout persists via `localStorage`. Layout-specific screenshot URLs are baked at build time.
- **Angular target**: emits `CONTENT[locale][layoutKey]` and each per-guide component owns a local `selectedLayout` signal initialized to `defaultLayout`. The component shows a layout `SelectButton` when more than one layout is configured.

When `layouts` is empty in build config, the build runs unchanged — no toggle, single-layout output.

### Common layout mistakes

- **Forgetting to capture every (locale, layout) combination.** Build needs every cell of the grid. Run `steppy run --guide` (no `--layouts`) so it picks up the full set from `config.yaml`.
- **Listing a layout in `BuildConfig.layouts` that isn't in `config.yaml`'s `screenshot.layouts`.** The build resolves screenshots from disk, so a layout name that was never captured produces a "missing screenshot" error.
- **Combining `only-in: test` with `layouts: [...]`** is now valid: it means "run only outside guide mode, only when the listed layout is parametrized". Useful when a step is mobile-only **and** shouldn't appear in guide screenshots. (Layouts are no longer guide-mode-only — `steppy run --layouts ...` works in test mode too.)
- **Putting `layouts:` on a step that has no `screenshot:` field.** Harmless but pointless: the step doesn't appear in any guide regardless of layout.
- **Mixing layout-filtered steps with `screenshot-when: before` on a navigating click.** Still works — the layout filter is applied first, then the surviving step runs as normal. But double-check the screenshot timing for each layout's flow.

---

## Customizing templates per project

Steppy ships bundled Jinja2 templates for the generated test files **and** the HTML/Markdown guides, but any downstream app can override them to personalize output (branding, layout, extra CSS, project-specific conftest fixtures, etc.).

**How it works**: **one resolution rule, shared by every command** (`generate`, `guide`, `build`) — Steppy builds a Jinja loader that looks up each template in this order:

1. `./templates/<name>` relative to the project root (which is the directory you invoke the command from).
2. The bundled fallback inside the installed `steppy` package.

The first match wins, **per template file** — so you can override `guide.html.j2` alone and let `guide.md.j2`, `conftest.py.j2`, and `test_scenario.py.j2` stay on the bundled defaults. Subdirectory-only overrides count: a project with just `templates/static/guides.css` and no top-level `*.j2` works fine.

**Overridable template files:**

| File | Purpose |
|---|---|
| `guide.html.j2` | HTML user guide with animated cursor — the main personalization target |
| `guide.md.j2` | Markdown user guide |
| `guide.fragment.html.j2` | innerHTML-safe HTML fragment shared by Angular embedding and the static target. Pass `include_styles=false` to suppress its inline `<style>` block (the static target does this and links to `assets/guides.css` instead) |
| `guide.book.html.j2` | The `--handbook` document — cover, table of contents, every guide fragment. Inlines `guides.css`, so restyling that restyles the handbook too |
| `test_scenario.py.j2` | Generated pytest file (one per scenario) |
| `conftest.py.j2` | Generated `conftest.py` with fixtures, screenshot helpers, bbox capture |
| `angular/component.ts.j2` | Angular standalone component per guide |
| `angular/component-interactive.ts.j2` | Angular host that mounts `<steppy-guide>` |
| `angular/component-video.ts.j2` | Angular host for a guide's narrated video |
| `angular/content.ts.j2` | Per-locale/per-layout HTML content as TS string constants |
| `angular/routes.ts.j2` | Angular routes with lazy loading |
| `angular/group.component.ts.j2`, `angular/onboarding.component.ts.j2` | Group / onboarding landing components |
| `static/page.html.j2` | Full HTML page wrapping the fragment (static target) — header, breadcrumb, locale switcher, footer |
| `static/index.html.j2` | Static-site landing page listing all guides |
| `static/group.html.j2`, `static/onboarding.html.j2` | Group / onboarding landing pages |
| `static/interactive.html.j2` | Self-contained interactive-player page |
| `static/video.html.j2` | Self-contained video page (player, captions, download links) |
| `static/_seo_head.html.j2` | Shared `<head>` fragment: robots, canonical, hreflang, description, Open Graph |
| `static/robots.txt.j2` | Emitted by `seo.mode: 'public'`. `sitemap.xml` has **no bundled template** — it is generated by `src/steppy/sitemap.py`; drop a `templates/static/sitemap.xml.j2` into the project to take over |
| `static/cursor.js.j2` | Step-by-step cursor animation driver |
| `static/guides.css` | Shared stylesheet for the static target (chrome + guide content + print rules) |

For *styling* decisions specifically — which of these files controls what, the `--steppy-*` token surfaces, and how the Angular target differs — see the **steppy-template** skill.

**To customize:**

1. Create a `templates/` directory at your project root (same level as `scenarios/`).
2. Copy the specific file(s) you want to change out of the installed package into your project `templates/`, mirroring the subdirectory path. Locate them with:
   ```bash
   python -c "import steppy, pathlib; print(pathlib.Path(steppy.__file__).parent / 'templates')"
   ```
3. Edit freely — add your logo, change the CSS, swap fonts, restructure the step layout.
4. Run `steppy generate` / `steppy guide` from your project root. The CWD lookup means **the command must be invoked from the directory containing `templates/`** for the override to be picked up.

**Contract — what variables the template receives:**

Custom templates must consume the same Jinja context as the bundled versions. Don't rename variables or drop blocks the generator expects. For `guide.html.j2` / `guide.md.j2` the context includes `scenario`, `steps` (with screenshot paths, bbox metadata, frame lists for multi-frame fill-form), and locale-resolved `guide-text`. For `conftest.py.j2` the cursor-bbox timing invariant (`_safe_bbox_scrolled` scrolls the element into view before measuring) must be preserved, or the HTML cursor will land on stale coordinates.

**Rule of thumb**: start by copying the bundled file verbatim, regenerate to confirm nothing broke, then make incremental changes. Diffing against the bundled original is the fastest way to merge upstream fixes later.

---

## Quick reference — minimal scenario skeleton

```markdown
---
title: Short title of the user-facing flow
tags:
  - e2e
  - user-guide-editor
---

### 1. First visible action
- role: editor
- action: navigate
- target: /some/path
- screenshot: editor
- guide-text:
    fr: "Texte narratif en français."
    en: "English narration text."

### 2. A click that does something visible
- role: editor
- action: click
- selector: [data-testid="primary-cta"]
- expect: toast("Succès")
- screenshot: editor
- screenshot-when: before   # omit for "after" (default); set when the click navigates away
- guide-text:
    fr: "..."
    en: "..."

### 3. Example with structured guide-text
- role: editor
- action: fill-form
- fields:
    [data-testid="field1"]: "value1"
    [data-testid="field2"]: "value2"
- screenshot: editor
- guide-text:
    fr:
      title: "Remplir le formulaire"
      subtitle: "Saisie des données"
      text: "Remplissez tous les champs obligatoires avant de continuer."
    en:
      title: "Fill the form"
      subtitle: "Data entry"
      text: "Fill in all required fields before continuing."

### 4. Mobile-only navigation step (guide pipeline only)
- role: editor
- action: click
- selector: [data-testid="hamburger"]
- screenshot: editor
- only-in: guide        # don't burden test runs with this UI affordance
- layouts:              # only relevant on mobile; desktop has a top navbar instead
  - mobile
- guide-text:
    fr: "Touchez l'icône menu pour révéler la navigation."
    en: "Tap the menu icon to reveal navigation."
```

That's enough to produce both a running test and a user guide. Everything else on this page is additional capability you reach for only when the flow requires it.
