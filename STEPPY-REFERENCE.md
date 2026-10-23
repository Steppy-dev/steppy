# Steppy Scenario Reference

The complete, authoritative reference for the **Steppy scenario file format** — the
Markdown-with-YAML input language that `steppy generate` compiles into standalone
Playwright pytest files (and that `steppy run` / `steppy guide` / `steppy build`
turn into multilingual user guides).

This document covers the scenario **input language** only: frontmatter, steps,
actions, and the assertion grammar. Project-wide configuration (roles, base URLs,
locales, auth, component-driver presets) lives in `config.yaml` — see
[CLAUDE.md](CLAUDE.md) for the architecture and config surface.

**Bootstrapping:** rather than hand-writing a scenario, you can *record* a draft —
`steppy record <url> --role <role> --slug <name>` drives Playwright's recorder and
writes a best-effort `scenarios/<name>.md` (secrets placeholderized, a login sequence
collapsed to `- action: login`) that you then refine against this reference. See the
README's CLI reference → Record. It's a V1 starting point, not a finished scenario.

> **Strictness:** every model uses `extra = "forbid"`. An unknown frontmatter key,
> step field, or action name is a **hard error** at `steppy validate` / `steppy
> generate` time — not a silent skip. Field names accept both the hyphenated form
> shown here (`screenshot-when`, `guide-text`) and the underscore form
> (`screenshot_when`, `guide_text`).

---

## 1. Scenario file anatomy

A scenario is a `.md` file with two parts:

```markdown
---
title: Create a project
description: An admin creates a new project and assigns its owner
tags:
  - e2e
  - user-guide-admin
priority: high
guide:
  enabled: true
---

### 1. Admin opens the projects list
- role: admin
- action: navigate
- target: /projects
- screenshot: admin

### 2. Admin starts a new project
- role: admin
- action: click
- selector: [data-testid="new-project-btn"]
- screenshot: admin
```

1. **YAML frontmatter** (between the `---` fences) — scenario-level metadata (§2).
2. **Step body** — one step per `### N. Title` heading, each followed by a list of
   `- key: value` lines describing the action.

### How steps are parsed

- Each `### ...` heading starts a new step; its text becomes the step title (used in
  comments and guides). The leading number is conventional, not required.
- Under a heading, only lines beginning with `- ` are read as step fields. A field is
  `- key: value`.
- A field whose value is empty and is followed by an **indented block** is parsed as
  nested YAML — used for `fields:`, `guide-text:`, `tour-text:`, `guide-cursor:`,
  `multiple:`, and `layouts:`. Both dict children (`fr: "..."`) and list children
  (`- item`) work, two levels deep (`fr:` then `title:`/`text:`).
- A YAML **block scalar** (`| `, `|-`, `>`, …) captures a multi-line value — used for
  `custom` step `code:` and multi-line `value:`.
- Surrounding quotes on a simple `key: value` are stripped for you, so
  `- value: "Notebook"` and `- value: Notebook` are equivalent. **Interior**
  quotes and apostrophes are preserved and safely escaped in the generated code, so
  `- value: L'adresse` and `- expect: toast("L'élément a été créé")` are valid.

---

## 2. Frontmatter reference

| Key | Type | Default | Notes |
|---|---|---|---|
| `steppy` | int | `1` | Scenario-format version. Omit it (⇒ `1`) unless a future format needs it. A file declaring a version newer than the installed Steppy supports fails validation with a clear "upgrade Steppy" message. |
| `title` | string | — | **Required.** Human title; becomes the test docstring. |
| `description` | string | — | Optional longer description. |
| `tags` | list of strings | `[]` | Free-form. `user-guide-<role>` tags drive guide discovery; `--tag X` filters runs. |
| `priority` | `critical` \| `high` \| `medium` \| `low` | — | Metadata only. |
| `auth` | bool | `true` | `false` = the scenario tests the login flow itself (no pre-auth fixture). |
| `timeout` | int (ms) | — | Default timeout for **each step** of this scenario (a step's own `timeout:` wins; `timing.step_timeout` applies when neither is set). Not a limit on the whole test: `steppy validate` warns above 60000. |
| `layouts` | list of strings | — | Viewports this scenario must pass in (keys of `screenshot.layouts`). **Authoritative**: even a plain `steppy run` parametrizes the whole flow over these; `--layouts`/`--guide` filters the declared set (disjoint ⇒ the scenario is deselected). Omit ⇒ no scenario-level layout dimension (unchanged behavior). Composes with the step-level `layouts:` in §3. |
| `exclusive` | string | — | Exclusion group. Two scenarios naming the same group never run **at the same time** under `steppy run --workers N`; different groups still run concurrently. Lowercase `[a-z0-9._+-]`, starting alphanumeric (the name becomes a filename). Promises non-overlap only — not ordering, not indivisibility. Inert without `--workers N ≥ 2`, where one pytest process is already sequential. |
| `seed` | string \| object | — | Seed script to run around the test. String `base` ≡ `{script: base, run: before}`. `run` = `before` \| `after` \| `both`. Scripts live in `seed/<script>.py`. |
| `guide` | object | `{enabled: false}` | `enabled: true` opts the scenario into the guide pipeline. `cursor:` overrides animation timings (`pre_action_ms`, `action_ms`, `post_action_ms`, `travel_speed_px_per_ms`). |
| `tour` | bool \| object | `tours.default` | Opts the scenario into an **in-app tour** (needs `tours.enabled` in `config.yaml`). The block form takes `enabled`, `id` (default: the slug), `title` (string or per-locale map), `entry` (route pattern such as `/invoices/:invoiceId`), `role` (required for multi-role scenarios) and `guide_slug` (the guide page the tour links to). See [docs/tours.md](docs/tours.md#scenario-annotations). |

---

## 3. Cross-cutting step fields

These apply to most actions (each action section notes what it needs):

| Field | Type | Applies to | Meaning |
|---|---|---|---|
| `action` | string | all | **Required.** One of the 23 actions in §4. |
| `role` | string | most | Which app/role page runs the step. Must be a role defined in `config.yaml`'s `apps`. Optional only for `wait`. |
| `selector` | string | element actions | CSS / Playwright selector for the target element. |
| `value` | string | fill/select/keyboard/upload/wait-for-text | The typed text, option, key, or path. |
| `screenshot` | `false` \| `true` \| `"all"` \| `<role>` | all | Capture a screenshot at this step. `true`/`<role>` capture for that role; `"all"` for every role; `false` (default) captures nothing. |
| `screenshot-when` | `before` \| `after` | interactive actions | Capture **before** the action (required when the action navigates away or unmounts the target) or after (default). |
| `guide-text` | per-locale map | guide steps | Narration shown in the rendered guide. Each locale (`fr`/`en`/`nl`/`de`) is a string, or `{title, subtitle, text}`. |
| `guide-cursor` | object | `custom`, `upload` | Where the guide cursor should land: `{behavior: click\|type\|hover, target: <selector>, value?: <text>}`. |
| `within` | string | click/hover/wait-for/assert | Scope selector — resolves `selector` inside it. |
| `match` | `first` \| `last` \| `<N>` | click/hover/wait-for/assert | Pick one when `selector` matches several (strict-mode escape). |
| `only-in` | `test` \| `guide` | all | Restrict the step to test mode or guide mode (default: both). |
| `layouts` | list of strings | all | Restrict the step to specific layouts in guide mode (default: all configured). |
| `wait_after` | int (ms) | most | Sleep after the action. |
| `note` | string | all | Free-form comment emitted into the generated code. |
| `tour` | `skip` \| `keep` | all | In-app tours: leave this step out of the tour, or keep a step inference would skip (centred when it has no test-id). Different from the frontmatter `tour:`. |
| `tour-text` | per-locale map | all | The step's tour text, same shapes as `guide-text`; wins over it. Keep test data out of it. |
| `tour-advance` | `click` \| `input` \| `change` \| `visible` \| `manual` | all | What moves the tour past this step. Must be something the step's own action does. |
| `tour-placement` | `top` \| `bottom` \| `left` \| `right` \| `auto` | all | Preferred side of the target for the tour popover. |

`{{var}}` placeholders in `selector`/`value`/`target`/assert text are substituted at
run time from values stored by `capture` (§4.15).

The `tour-*` keys on a step the tour leaves out are an error unless the step also
says `tour: keep`. How each action maps to a tour step is in
[docs/tours.md](docs/tours.md#inference).

---

## 4. Actions

### 4.1 `navigate`
Go to a URL, or reload the current page.

| Field | Notes |
|---|---|
| `target` | Path/URL to `goto` (resolved against the role's base URL). Required unless `reload`. |
| `reload` | `true` → `page.reload()` instead of `goto` (the hard-refetch idiom). |
| `wait` | Load-state to await: `networkidle` (default) \| `load` \| `domcontentloaded` \| `none`. |

```markdown
- action: navigate
- target: /orders
- wait: networkidle
```

### 4.2 `click`
Click an element.

| Field | Notes |
|---|---|
| `selector` | Target element. |
| `within` / `match` | Disambiguate a multi-match selector. |
| `synthetic` | `true` → dispatch synthetic `MouseEvent`s via `evaluate()` for elements that swallow native clicks. |

### 4.3 `fill`
Type text into an input.

| Field | Notes |
|---|---|
| `selector` | The input. |
| `value` | Text to type. |
| `clear` | `true` → select-all + delete before typing. |
| `sequential` | `true` → `press_sequentially` (fires keydown/input) instead of `fill`. |
| `blur-key` | Key pressed after typing to commit (e.g. `Tab`, `Enter`). |

### 4.4 `fill-form`
Fill several fields in one step.

| Field | Notes |
|---|---|
| `fields` | Indented map of `<selector>: <value>` pairs. |
| `interactive-progressive` | `true` → capture one guide frame per field as the form fills (guide mode). |

```markdown
- action: fill-form
- fields:
    input[name="first"]: Jean
    input[name="last"]: Dupont
```

### 4.5 `select`
Choose an option in a native `<select>`.

| Field | Notes |
|---|---|
| `selector` | The `<select>`. |
| `value` | Option value/label. |

### 4.6 `select-option`
Drive a **component** dropdown (PrimeNG `p-select` / `p-autocomplete` / `p-multiselect`).
Framework selectors come from the `dropdown:` config preset (PrimeNG defaults).

| Field | Notes |
|---|---|
| `selector` | The trigger / host element. **Required.** |
| `option` | Single pick: `first` \| `last` \| `<N>` (index) \| `text:"..."`. |
| `multiple` | Multiselect: an **indented list** of labels (auto-Escapes to close). |
| `search` | Type into the dropdown's filter box before picking. |

Exactly one of `option` or `multiple` is required.

```markdown
- action: select-option
- selector: [data-testid="site-select"]
- search: Brussels
- option: text:"Brussels Central"
```

### 4.7 `wait`
Sleep for a fixed duration.

| Field | Notes |
|---|---|
| `duration` | e.g. `3s`, `500ms`. |

### 4.8 `wait-for`
Wait for a selector to reach a state.

| Field | Notes |
|---|---|
| `selector` | Target. |
| `state` | `visible` \| `hidden` \| `detached` \| `attached`. Omit for default (visible/attached). `hidden` is the usual "wait for the dialog to close" success signal. |
| `within` / `match` | Disambiguate. |

### 4.9 `wait-for-text`
Wait until text appears on the page.

| Field | Notes |
|---|---|
| `value` | The text to wait for. |

### 4.10 `scroll`
Scroll a selector into view (`scroll_into_view_if_needed`).

| Field | Notes |
|---|---|
| `selector` | Element to scroll to. |

### 4.11 `hover`
Hover over an element.

| Field | Notes |
|---|---|
| `selector` | Target. |
| `within` / `match` | Disambiguate. |

### 4.12 `keyboard`
Press a key.

| Field | Notes |
|---|---|
| `value` | Key name (e.g. `Enter`, `Escape`, `Control+A`). |

### 4.13 `assert`
Assert a condition. See §5 for the full `expect` grammar.

| Field | Notes |
|---|---|
| `expect` | An assertion expression, e.g. `toast("Saved")`, `count(sel, >=1)`. |
| `within` / `match` | Disambiguate the selector inside selector-based assertions. |

`expect` can also be attached to **any other action** as an extra check after it runs.

### 4.14 `custom`
Escape hatch: emit arbitrary Python. The role page is available as `page`.

| Field | Notes |
|---|---|
| `code` | A YAML block scalar with Python source. |
| `guide-cursor` | Optional cursor target for the guide (see §3). |

```markdown
- action: custom
- code: |
    page.get_by_role("button", name="Confirm").click()
    page.wait_for_timeout(200)
```

### 4.15 `capture`
Store a value from an external source into `_vars`, usable later as `{{name}}`.

| Field | Notes |
|---|---|
| `store` | Variable name. **Required.** |
| `from` | Fetcher spec. `type` = `redis` \| `http` \| `shell` \| `dom`. **Required.** |
| `retry` | Attempts (default 1). |
| `interval` | Retry interval, e.g. `500ms`. |

`from` shape by type: `redis` needs `key`; `http` needs `url`; `shell` needs
`command`; `dom` needs `selector` + `role` and an optional `extract`
(`text` \| `attribute` \| `regex`, with `attribute`/`regex` requiring their companion
field).

> **Security:** `from.type: shell` runs `subprocess.run(..., shell=True)` and `dom`
> values can be interpolated into it — treat captured page content as untrusted.

### 4.16 `upload`
Set files on a `<input type="file">`.

| Field | Notes |
|---|---|
| `selector` | The file input. **Required.** |
| `value` | File path (relative to project root). **Required.** |
| `guide-cursor` | For hidden inputs behind a styled trigger, point the cursor/bbox at the visible trigger. |

### 4.17 `confirm`
Click a confirmation-dialog button (PrimeNG `p-confirmDialog`). Selectors come from the
`dialog:` config preset. Default = accept the active dialog.

| Field | Notes |
|---|---|
| `variant` | `accept` (default) \| `reject`. |
| `danger` | `true` → prefer the destructive/danger button. |
| `button` | Match by visible label (mutually exclusive with `variant`/`danger`). |

### 4.18 `fill-date`
Compute a relative/dynamic date **at run time** and type it into an input (so tests
stay valid forever).

| Field | Notes |
|---|---|
| `selector` | The date input. **Required.** |
| `date` | Date expression (grammar below). **Required.** |
| `format` | `strftime` format; default `%d/%m/%Y`. |
| `method` | `type` (default). `calendar` is reserved for `pick-date` and currently **rejected**. |

**Date grammar:** `today` \| `this-<weekday>` \| `next-<weekday>` \| `±N[d|w|m|y]`
(e.g. `+7d`, `-1m`) \| `YYYY-MM-DD`.

### 4.19 `pick-date`
Drive a calendar widget (clicks through the datepicker instead of typing). Selectors
come from the `datepicker:` config preset.

| Field | Notes |
|---|---|
| `selector` | The datepicker host. **Required.** |
| `date` | Same date grammar as `fill-date`. **Required.** |

### 4.20 `pick-time`
Drive an hour/minute clock widget.

| Field | Notes |
|---|---|
| `selector` | The timepicker host. **Required.** |
| `time` | 24-hour `HH:MM`. **Required.** |

### 4.21 `toggle`
Flip a switch or select a segmented-button option. Selectors come from the `toggle:`
config preset.

| Field | Notes |
|---|---|
| `selector` | The switch/host. **Required.** |
| `state` | `on` \| `off` (switch). Omit to flip. |
| `option` | Label of a segmented-button choice. |

`state` and `option` are mutually exclusive.

### 4.22 `expand`
Open a collapsible section (PrimeNG `p-fieldset`). Legend/content selectors come from
the `fieldset:` config preset.

| Field | Notes |
|---|---|
| `selector` | The fieldset host. **Required.** |

### 4.23 `login`
Fill the hosted login form for the step's role (uses the role's configured credentials and
the `auth.form` selectors). Works against any IdP with a fillable login form (Auth0,
Keycloak, …). `auth0-login` is a kept-forever deprecated alias.

| Field | Notes |
|---|---|
| `role` | Role whose credentials to use. |

> **Auth modes:** `apps.<role>.auth.mode` is `form` \| `storage-state` \| `bypass` \| `token`
> (`auth0` is a deprecated alias for `form`). The `form` selectors live under global
> `auth.form:` and accept an IdP `preset:` (`auth0` \| `keycloak`). Optional
> `login_path` + `start_selector` get automatic login past the app's own "Log in" page,
> `post_login_url_pattern` names the URL that means "logged in" (default: any page
> under the app's `url`), and the post-submit wait follows `timing.navigate_wait`.
> The `login` step clicks `start_selector` too when that page is showing. `token` mode fetches a
> JWT from an endpoint and injects it via `inject_as: localStorage | cookie | header`.
> For SAML / corporate SSO with conditional access, magic links, or CAPTCHA, use the
> one-time headed `steppy auth login` + storage-state TTL (see SECURITY.md).

---

## 5. Assertion grammar (`expect`)

Used by the `assert` action, and attachable to any action as a post-check. Selector
arguments accept the same `within`/`match` disambiguation as element actions.

| Form | Playwright behavior |
|---|---|
| `toast("message")` | A `text="message"` element is visible (5s). |
| `visible(selector)` | `to_be_visible`. |
| `hidden(selector)` | `to_be_hidden`. |
| `disabled(selector)` | `to_be_disabled`. |
| `enabled(selector)` | `to_be_enabled`. |
| `not-empty(selector)` | `not_to_be_empty`. |
| `text(selector, "expected")` | `to_have_text` (exact). |
| `contains(selector, "expected")` | `to_contain_text` (substring, auto-waiting). |
| `url-contains("/path")` | URL matches `.*<path>.*`. |
| `count(selector, N)` | `to_have_count(N)` (exact integer). |
| `count(selector, >=N)` | Re-counts until the comparison holds (`>=` `<=` `==` `!=` `>` `<`), within the step timeout. `<`/`<=` hold at once on an empty page. |
| `row-contains("text")` | A `text="text"` element is visible. |
| `status("value")` | Status-badge text check. |

```markdown
- action: assert
- expect: count([data-testid^="order-row-"], >=1)
```

---

## 6. Modes, locales, and layouts (quick pointers)

- **Guide mode** (`steppy run --guide`) captures screenshots for `guide.enabled`
  scenarios and auto-disables assertions (`--no-asserts`), since toast text is
  language-specific.
- **Locales / layouts** are parametrized at run time (`--locales`, `--layouts`) and
  configured in `config.yaml`; screenshots land under
  `output/screenshots/<scenario>/[<locale>/][<layout>/]step-NN.png`. A scenario
  can also **declare** its viewports with a frontmatter `layouts:` (§2) so
  responsive coverage is versioned with the flow rather than a run-time flag.
- **Exclusion groups** (`exclusive:` in §2) are the same idea for concurrency:
  scenarios that contend on a shared resource declare a group name, and
  `--workers N` runs the whole group on one worker, sequentially. The run's
  makespan is then floored by the group's total duration — a group is free while
  that total stays under *(suite total ÷ workers)*.
- **PDF export** is opt-in on both assembly commands: `--pdf` prints every
  generated guide page, `--handbook` assembles all of them into one document
  (cover + contents). Paper settings live in `config.yaml`'s `pdf:` block; see
  the README's PDF export section.
- **Video export** is opt-in the same way: `--video` renders a narrated MP4 per
  (guide × locale × layout) from the screenshots and cursor data a run already
  captured. Narration is the per-locale `guide-text` you already wrote — there
  is nothing new to author in a scenario. On `steppy build` a guide must also
  declare `modes: ['video']`; settings and the TTS provider live in
  `config.yaml`'s `video:` block. See the README's video guide mode section.
- **In-app tours** compile from `tour:`-enabled scenarios when `config.yaml` has
  `tours: {enabled: true}`: `steppy generate` writes the tour files and appends a
  CI replay (`test_<slug>__tour`) to the scenario's test; `steppy tours` writes
  the files alone. Steps that should not be in the tour, and text that should differ
  from the guide's, are the step keys in §3. See [docs/tours.md](docs/tours.md).
- **Roles, base URLs, auth, component-driver presets** (`dropdown`, `dialog`,
  `toggle`, `datepicker`, `fieldset`) all live in `config.yaml`.

## 7. Component-driver presets (UI kits)

The component actions (`select-option`, `confirm`, `toggle`, `pick-date`,
`pick-time`, `expand`) drive framework widgets whose internals have no stable
`data-testid`, so their selectors come from a `config.yaml` block. Each block is
optional and defaults to **PrimeNG**, so those actions work out of the box for a
PrimeNG app.

To target another UI kit, name a `preset:` (built-ins: `primeng`, `material`,
`primevue`, `mui`, `vuetify`, `shadcn`) and/or override individual selectors —
an explicitly set selector always wins over the preset:

```yaml
dropdown:
  preset: mui               # adopt Material UI (React) selectors...
  filter: ".my-search-box"  # ...but override this one
```

`primeng` is authoritative; **every other preset is a best-effort starter set**
(framework markup varies by version and by add-ons) — verify each selector
against your app, and expect to override a few. These starters are foundational
and will be hardened as Steppy is exercised on cross-framework projects. Omitting
`preset` keeps the PrimeNG defaults. The five blocks that accept `preset` are
`dropdown`, `dialog`, `toggle`, `datepicker`, and `fieldset`.

---

## 8. Artifact output paths

Where a run writes its artifacts, and where the guide/build/bundle commands look
for them, comes from one `config.yaml` block:

```yaml
output:
  screenshots_dir: "./output/screenshots"   # default
  guides_dir: "./output/guides"             # default
  tours_dir: "./output/tours"               # default (in-app tours)
```

All three are resolved against the project root: a relative value lands under it, an
absolute value wins outright. Omitting the block keeps the defaults, which are
the paths Steppy has always used.

`screenshots_dir` is honoured by **both** sides, which is the point — the capture
side (the generated conftest, read at run time, so a change takes effect without
re-running `steppy generate`) and every reader:

| Consumer | How it uses it |
|---|---|
| `steppy run` | writes captures + `_failures/` there |
| `steppy guide` | `--screenshots-dir` default (an explicit flag wins) |
| `steppy build` | used when the TypeScript config omits `screenshotsDir` |
| `steppy run --workers N` | the coordinator's failure-screenshot harvest |
| `steppy bundle create` | the screenshots root it archives |

`tours_dir` is where `steppy generate` / `steppy tours` write the canonical
in-app tour tree (`<tours_dir>/<build-id>/`) and where `steppy build` reads it for
the `guide` target. A tour's `app-assets` targets live in **your app**, outside
`output/`, so the results bundle, the CI artifact upload and `.gitignore` do not
cover them — they are meant to be committed or built with the app.

`guides_dir` is `steppy guide`'s `--output-dir` default and the bundle's guides
root. It does **not** feed `steppy build`'s `outputDir`, which names a published
site rather than an artifact directory and stays required in the TypeScript
config.

### Pointing outside `output/`

Allowed and honoured everywhere above, but four things only ever look at
`output/`, so each warns rather than failing quietly:

- `steppy bundle create` archives the `output/` tree only — relocated artifacts
  are **not** in the archive, and it says so.
- `steppy run --workers N` bind-mounts only `output/` back from the worker
  container (and mounts the project read-only), so captures written elsewhere
  cannot leave the container at all.
- The scaffolded `.gitignore` ignores `output/` — a relocated directory starts
  getting committed unless you add it.
- The shipped CI workflow uploads `output/` as its artifact.

### Paths that are *not* configurable

Playwright traces and videos (`output/traces`, `output/videos`), parallel-run
failure summaries (`output/failures`), and the `--bundle` sinks
(`output/events`, `output/junit`) have no knob. `report.junit_xml` and
`report.html` take explicit paths of their own.

`steppy report` writes to **`output/report/`** and takes `-o/--output` instead of a
config key, deliberately: that root has one writer and no automated reader (a CI
job names the path in its own upload step anyway), and a live `output.report_dir`
sitting one letter from the deprecated `output.reports_dir` below would be a
permanent trap. `output/report/` is the one path `steppy bundle create` skips by
name — the rendered site contains *copies* of the screenshots the bundle already
carries, so archiving it would roughly double the payload. Point `-o` elsewhere
under `output/` and the report says so.

`output.reports_dir` is **deprecated and ignored** — nothing has ever written
`output/reports`. It still parses so existing configs keep loading, but
`steppy validate` warns and `steppy update --check` reports it. Remove it. (Note
the plural: it is unrelated to `output/report/` above.)

Coverage records have their own knob, `coverage.records_dir` (default
`./output/coverage`), which follows the same rules.

---

See [README.md](README.md) for the command surface and [CLAUDE.md](CLAUDE.md) for the
architecture and the full configuration model.
