---
name: steppy-ci-report
description: |
  How to run Steppy in CI and publish what came out of it. Use whenever a user asks to
  "set up Steppy in CI / GitHub Actions / GitLab CI", "add a CI workflow", "publish the
  test results", "publish to GitHub Pages / GitLab Pages", "build a run report", "show
  which step failed", "why did my report render empty", "add a coverage badge", "what
  isn't my suite testing", "why is my coverage percentage so low", "set up
  coverage.non_ui", "package a run", "attach the results to a build", "tour replay
  failed", "set up in-app tour CI", or edits
  `.github/workflows/steppy.yml`, `.gitlab-ci.yml`, or the `coverage:` / `report:`
  blocks in `config.yaml`.

  Covers: `steppy report` (the static run-report site), `steppy run --bundle` +
  `steppy bundle`, the coverage map end to end (record → merge → refresh → gaps), the
  e2e-surface badge and the `coverage.non_ui` declaration it depends on, the in-app tour
  replays (`test_<slug>__tour`) and their pipeline order, and the two shipped CI templates.

  For writing scenarios use `steppy-scenario-author`. For styling guides use
  `steppy-template`. For wiring tours into the app's code use `steppy-app-tours`. This
  skill is about shipping results, not authoring or theming.
---

# Running Steppy in CI, and publishing the results

Three artifacts come out of a run, and they answer different questions:

| Artifact | Question it answers | Command |
|---|---|---|
| **Run report** | Did it pass, and *which step* failed? | `steppy report` |
| **Results bundle** | Can I hand this whole run to someone else? | `steppy bundle create` |
| **Guides** | What does the app look like to a user? | `steppy guide` / `steppy build` |

Guides are a different job (see `steppy-scenario-author`). This skill is the first two,
plus the coverage map that feeds the report's badge.

> **One thing to be clear about up front.** `report:` in `config.yaml` is an *unrelated*
> block — it configures pytest artifacts (JUnit XML, an HTML report, Playwright traces
> and videos, reruns). The `steppy-dev[report]` extra is `pytest-html`. Neither has anything
> to do with the `steppy report` command. If a user says "the report", ask which they
> mean when it is ambiguous.

---

## The CI loop

Two shipped templates, both installed by `steppy init`:

```bash
steppy init --ci           # → .github/workflows/steppy.yml
steppy init --ci-gitlab    # → .gitlab-ci.yml
```

Separate booleans rather than `--ci <vendor>`, because `--ci` is a flag people already
type. Run `steppy update` after upgrading Steppy and both files are reconciled with the
installed version.

### Two ways to run it on GitHub

The scaffolded workflow is the **owned** copy: every trap documented as a comment beside
the step it applies to, yours to edit. The alternative is the **reusable** workflow Steppy
ships, called by ref:

```yaml
jobs:
  e2e:
    uses: Steppy-dev/steppy/.github/workflows/steppy-e2e.yml@v0.3.0
    with:
      compose-file: docker-compose.yml
    secrets:
      steppy-env: ${{ secrets.STEPPY_ENV }}
```

Pin a tag, never a branch — that ref selects both the recipe and, by default, the Steppy
version it installs, so a suite's tooling changes only when someone edits that line.

It exposes `verdict`, `exit-code`, `scenarios-not-passed` and `badge-percent` as job
outputs, read from `report.json`, so a caller can gate or comment on the result without
re-parsing anything. Credentials arrive as **one optional `steppy-env` secret** holding
dotenv `KEY=VALUE` lines — the `${VAR}` names in a project's config.yaml belong to that
project and cannot be declared as individual secrets upstream.

The trade against a composite action: a reusable workflow **is** the job, so a caller
cannot interleave its own steps into it. For an e2e repo whose compose pulls published
images that costs nothing. Someone who needs to build an image first, in the same job, has
found the reason to promote it to a composite action.

The order that matters:

```bash
docker compose up -d --wait      # 0. the app the scenarios talk to
steppy generate                  # 1. rebuild the tests from scenarios
steppy run --trace --bundle      # 2. run them, recording what a report needs
steppy report                    # 3. render output/report/
```

With in-app tours enabled, two steps come *before* step 0, because the app must be built
with the current tour files: `steppy tours --check` (or `steppy tours` when CI
builds the app itself), then `docker compose up --build`. See "In-app tour replays" below;
the shipped CI templates carry both as commented steps to uncomment.

### Step 0 — the stack under test

Steppy runs scenarios; it does not run your application. On a single-worker run nothing
starts your app for you, so CI has to — either `docker compose up` in the job, or an
`apps.<role>.url` pointing at an already-deployed environment.

A hosted runner is just another Linux box with Docker on it: if `docker compose up` then
`steppy run` works on a developer's laptop against that repo, CI is the same two commands
plus artifact plumbing. Two properties of **your** compose file decide whether it actually
works, and both fail the same way — every scenario red, no error that names the cause:

- **`--wait` only waits on services that declare a `healthcheck:`.** With none, it returns
  as soon as the containers are created and the run starts against a port that is not
  serving yet. On a suite of any size the circuit breaker then aborts after ~10 identical
  failures, which is the good outcome — it fails in a minute instead of thirty.
- **The scenarios and the seed scripts run on the runner, not in a container.** So the app
  — and the database, if `seed/*.py` touches it — must publish its ports, and
  `apps.<role>.url` points at `localhost`. (Under `--workers N` this inverts: seeds run
  *inside* the worker container, where host URLs do not resolve.)

Tear down with `docker compose down -v` in an always-run step. Without `-v` the next run
inherits the last one's seeded database, which produces failures that reproduce in CI and
never locally. Dump `docker compose logs` on failure too — when a suite goes red the first
question is always "the tests or the stack?", and that is the cheapest way to know.

`--workers N ≥ 2` changes this picture entirely: the coordinator brings up **one full
stack per worker** from `parallel.compose_file` and tears them down itself, so the
explicit compose steps come out. N copies of an app on one hosted runner is a lot — that
mode earns its keep on a large suite or a self-hosted runner, not on a thirty-scenario one.

**`--bundle` is the load-bearing flag.** It is opt-in because it changes the pytest
invocation, and without it the report can still tell you a test failed but *not which
step* — there are no per-step event records to read. It sets three things:
`STEPPY_EVENTS_DIR` (per-test and per-step JSONL), a fixed-path `--junitxml`, and
`STEPPY_RUN_ID`. Export `STEPPY_STEP_EVENTS=0` to keep the rest and drop step
granularity.

**`steppy report` exits 0 even when the run failed.** It describes rendering, not the
run. Gate the build on `steppy run` itself, or read `totals.exit_code` out of
`report.json`. A report step that goes red for a red run is a step people delete — so
run it with `if: always()` (GitHub) or in `after_script` (GitLab), because a failing run
is exactly when it matters.

### In-app tour replays

When `config.yaml` has `tours.enabled: true`, every scenario that opts into an in-app tour
(`tour:` in its frontmatter) gets a second test, **`test_<slug>__tour`**, appended to its own
test file by `steppy generate`. It is the feature's CI promise: a developer who renames a
test-id a tour points at gets a red build naming the tour, the step and the test-id, instead
of a tour that quietly breaks in production. The replay:

1. runs the steps before the tour's start point, as the scenario test does;
2. starts the tour through the host element's API;
3. for each tour step, checks the popover shows that step anchored on the element it acts
   on (the spotlight around its centre, the popover not covering it), performs **the
   scenario's own action code** for it, and checks the tour moved on;
4. checks `tour-complete` fired and no `tour-error` did.

Because it lives in the scenario's file, `--scenario` / `--tag` selection, `--workers N`,
`lastfailed` and JUnit treat it like its scenario — running the replay needs no extra
step. What does need one is getting the *current* tour files into the app before it
starts:

**The order is the whole trick.** The replay tests the tour files *the running app serves*,
so they have to be in the app image before the app starts:

```bash
steppy tours                       # 1. write the tour files into the app's app-assets folder
docker compose up -d --build --wait   # 2. build the app image WITH them, then start it
steppy generate                    # 3. tests + replays
steppy run --trace --bundle        # 4. the replays run with their scenarios
```

(In a one-repository setup the app's own build usually runs `steppy tours`, and the
committed tour files are what step 2 bakes in.) Start the app first and the replays exercise
whatever tour files the image already had.

**Reading a failure.** One message carries the whole diagnosis:

```text
TourReplayError: [tour create-invoice] step 'invoice-title' (scenario step 4 "Name the invoice", fill,
test-id "invoice-title"): target-timeout: the overlay never found [data-testid="invoice-title"]
  hint: the test-id was renamed or removed in the app, or the element appears too late
  page: /invoices  tour: active, current 'invoice-title'  bubble: fallback  navigations since start: 0
  last events: tour-start(bundled) | tour-step(new-invoice-btn) | tour-error(target-timeout, invoice-title)
```

The first line is *which* — tour, tour step, scenario step, action, test-id — and the
overlay's error reason; `hint:` is the likely cause; `page:` / `tour:` / `bubble:` are the
state at the moment it failed; `last events:` is the overlay's own event tail
(`tour-start(bundled)` says the app's bundled tour files were used).

Things worth knowing before wiring it in:

- **The kill switch.** With tours enabled, the generated tests set
  `globalThis.__STEPPY_TOURS_DISABLED__ = true` in every browser context, so a tour the
  app starts on its own never covers what an ordinary test clicks; only the replays lift it.
  Another E2E suite testing the same app should set it too.
- `steppy run -- -k "not __tour"` skips the replays for one run; `tours.replay: false`
  stops generating them.
- `steppy run --guide` deselects them — guide captures are not tours.
- Replays record **no coverage**; `--coverage` numbers come from the scenario tests alone.
- `STEPPY_TOUR_EXPECT_BUILD_ID=<id>` makes a replay also check that the app serves tours
  for that build — set it to the id the app build stamped to catch an image baked from the
  wrong tour files. It reaches every worker under `--workers N` too.
- A replay re-runs its scenario's seed and steps, so the flow must be repeatable.
- **`steppy tours --check` is a freshness gate**: it writes nothing and exits 1 when a
  committed `app-assets` copy is missing or its *content* no longer matches what the
  scenarios compile to. No browser, no app — a cheap step that catches a scenario edited
  without regenerating the committed tour files. When it reports files that "differ only
  in line endings", git converted them on checkout (`core.autocrlf` on a Windows runner):
  regenerating cannot fix that — mark the folder `-text` in the app repository's
  `.gitattributes`. It compares each copy for the build id
  that copy records and only prints a note when that differs from the one resolved now
  (under `git-sha` a committed copy always carries an earlier commit), so it passes on a
  fresh checkout; the gitignored `output/tours/` tree is not checked. It needs the app's
  checkout (a missing `app-assets` folder is an error; `generate` only warns
  `app-assets-skipped`), and it re-renders under CI's environment, so any `${VAR}` in
  `tours.guide_base_url` or an app URL must resolve as it did where the files were
  written. To gate the *build id* the running app serves, use
  `STEPPY_TOUR_EXPECT_BUILD_ID` above.
- **Wiring it into a scaffolded pipeline.** `steppy init --ci` / `--ci-gitlab` write the
  tour steps commented out (tours are opt-in). When the project enables tours, uncomment
  them; leave them out of the reusable `steppy-e2e.yml` workflow, whose caller cannot
  interleave steps — a project that builds its app in CI promotes it to a composite
  action (see above).
- **In the run report, a replay is a row of its own under its scenario.** When both fail in
  the same variant they share the failure screenshot, so read them as one cause first. A
  red replay under a *green* scenario is the tour, not the app.

---

## `steppy report` — the run report site

```bash
steppy report                                   # this project's live output/
steppy report steppy-bundle-<run-id>.tar.zst    # a bundle downloaded from CI
steppy report ./unpacked-bundle -o public       # after tar -xf
```

All three inputs produce the same report. That is the point: an assistant debugging a
failure from a downloaded CI artifact sees exactly what CI saw.

### What the site contains

- **`index.html`** — run identity (run id, commit, branch, PR, executor), the spec
  dimensions, outcome counts, **failure causes grouped by cause**, a scenario ×
  (locale, layout) outcome matrix, warnings, and a coverage card.
- **`scenarios/<slug>.html`** — per (locale, layout) variant: outcome, duration, the
  failing step **named**, the decoded failure tail, the step timeline (number, title,
  action, elapsed) and the captures for each step.
- **`coverage.html`** + **`badge.json`** — when the run recorded coverage.
- **`report.json`** — the same model as a versioned wire contract
  (`steppy_report: 1`, shape at `contracts/report.schema.json`). Deterministic: no
  render-time clock, no absolute paths, so two renders of one run are byte-identical and
  CI can diff them.

### Failure causes are the feature

The report groups failures by *normalised* failure text, biggest first — the same
grouping the parallel console summary uses. One broken selector across forty scenarios
is **one row with a count**, not forty tracebacks, and the full tail is shown once per
cause. When a user says "the CI output is unreadable", this is the answer.

### Flags worth knowing

| Flag | Why |
|---|---|
| `--screenshots failed \| all \| none` | Default `failed`: the captures of variants that did not pass, plus every failure screenshot. `all` on a 4-locale × 2-layout corpus is hundreds of MB pushed on every run. Captures identical across layouts are copied once. |
| `-o/--output` | Default `output/report`. See the warning below before pointing it elsewhere. |
| `--badge-surface all \| endpoints \| routes` | What the badge measures. `all` = endpoints + routes. |
| `--dry-run` | Report what would be written, write nothing. |
| `--json` | Print `report.json` to stdout instead of a summary — for a CI annotation. |
| `--force` | Replace a non-empty output directory. |
| `--open` | Open it in a browser locally. |

**`output/report/` is the one path `steppy bundle create` skips by name.** The rendered
site contains *copies* of the screenshots a bundle already carries, so archiving it
would roughly double the payload. Point `-o` somewhere else under `output/` and the
command warns you about exactly that. There is deliberately no `output.report_dir`
config key — one writer, no automated reader, and a live `report_dir` sitting one letter
from the deprecated `output.reports_dir` would be a permanent trap.

### It is honest about what it could not know

This matters when reading a report that looks thin — the report says which case it is
rather than rendering an empty box, and `report.json`'s `capabilities` block carries the
same information for a machine:

| What you see | What it means |
|---|---|
| "No per-step timeline was recorded" | The run did not pass `--bundle`, or set `STEPPY_STEP_EVENTS=0`, or the tests were generated before per-step markers existed (`steppy generate` again). |
| "This run recorded no test outcomes" + verdict `unknown` | No `--bundle`, so there are no event records. The scenarios listed were reconstructed from captures on disk. **Not** `passed` — that would be a claim with no evidence. |
| "Assert strength is not shown" | Coverage was rebuilt from a detached bundle's raw records, which carry no scenario markdown to count assert steps in. Every endpoint would otherwise read as weakly covered. |
| "Truncated" | A `--max-size` budget dropped artifacts that belonged in the bundle. Screenshots and sidecars are never dropped. |

Traces and videos are **named, not copied** — a Playwright trace needs
`playwright show-trace` to open, so a link would be a dead end.

---

## Publishing to Pages

The site is relative and self-contained, so publishing it is a copy. Both shipped
templates carry the job **commented out**, with the warning below. Do not arm it for a
user without saying this out loud:

> **Pages is public.** Screenshots show the app with real-looking data in it.
> `screenshot.mask` redacts fields at capture time and the report marks every page
> `noindex` with a `Disallow: /` robots.txt — but a leak is permanent and cannot be
> taken back. This is for showcase and open-source repos. For client work, read the
> report from the CI artifact instead.

Two vendor facts worth carrying:

- **GitHub Pages**: publish as an **artifact** (`actions/configure-pages` +
  `upload-pages-artifact` + `deploy-pages`), never by committing to a `gh-pages`
  branch — screenshots pushed on every run grow the repository's history forever and
  cannot be pruned without a rewrite. Needs `permissions: {pages: write, id-token:
  write}` and Settings → Pages → Source: "GitHub Actions".
- **GitLab Pages** can be restricted to **project members** (Settings → Pages → Access
  Control). That is the one place it beats GitHub's for this, and it is what makes
  publishing a run report viable for client work. It is a project setting, not something
  the pipeline can guarantee — check it before the first deploy.

Gate the deploy on the default branch either way: a report from every PR publishes work
in progress, and fork PRs cannot get an id-token anyway.

**A repository has exactly one Pages site.** A project that publishes both the run report
and generated guides (`steppy build`) has to place them — `site/report/` and
`site/guides/` — and that decision is worth making before anything links to either,
because moving the report also moves `badge.json`, and that URL is a contract with every
README and dashboard pointing at it. Deciding it late means either a dead badge or a
permanent redirect nobody owns.

The badge is then served at `<pages-url>/badge.json`, **when the run recorded coverage** —
`render.py` writes the file only when there is a coverage section, so a badge URL added
before `--coverage` is a 404 that reads to a visitor as an abandoned project:

```markdown
![e2e surface](https://img.shields.io/endpoint?url=https://<owner>.github.io/<repo>/badge.json)
```

A shields **endpoint JSON**, not a generated SVG — it inherits whatever style the rest of
the README's badge row uses, and no image generation lands in the repo.

---

## The coverage map

**This is not line coverage.** Codecov answers "what percentage of source lines ran".
Steppy's map answers *which of the routes and API endpoints I declared has any e2e
scenario actually exercised* — which nothing else measures. Everything below is
deterministic: no LLM in recording, merging, or gap computation.

### How it is built — four stages

**1. Record** (`steppy run --coverage`, sets `STEPPY_COVERAGE=1`). The *generated
conftest* does it, so there is zero overhead when the flag is off. Three signals per
scenario run:

- **Route trail** — Playwright's `framenavigated`, which is authoritative because it
  fires for History-API changes too, plus an injected hook tagging push/replace/pop/hash.
- **API endpoints** — every request's `{method, url}`, filtered to
  `coverage.api_resource_types` (default `xhr`, `fetch`).
- **Source modules** — V8 coverage over raw CDP `Profiler.startPreciseCoverage`
  (playwright-python has no `page.coverage` API), with inline `data:` source maps
  captured and gzipped.

Raw records land at
`output/coverage/raw/<run_id>/<scenario>/<fixture>__<locale>-<layout>.json`. The recorder
is deliberately **dumb** — it writes observations, nothing interpreted.

**2. Merge** (automatic after the run, or `steppy coverage merge`). *All* interpretation
happens here: dynamic route segments cluster (`/invoices/123` → `/invoices/:id`, extra
regexes via `coverage.route_param_patterns`), API attribution by longest `base_urls`
prefix, source-map decode with a bundle-granularity fallback. Upserts replace a
scenario's whole contribution, so re-running one scenario cannot leave stale rows.

**3. Refresh** (`steppy coverage refresh`) writes the **declared surface** — the
denominator. This is the step people forget, and without it every percentage is
meaningless.

**4. Gaps** (`steppy coverage report`) is pure bucket math:

| Bucket | Meaning |
|---|---|
| **tested** | declared ∩ exercised. *Strong* when an asserting scenario covered it, *weak* when only assert-free or `--guide`/`--no-asserts` runs did. |
| **declared-unreached** | In the manifest, never hit. **The bucket that matters** — this is the answer to "what should I write a scenario for next?" |
| **discovered-uncovered** | Seen in a trail, only weakly covered. |
| **discovered-tested** | Exercised but *absent* from the manifest — a stale spec, or a `base_urls` prefix mismatch. |
| **unclassified** | Requests matching no configured `base_urls` at all. Check `base_urls`. |

### It needs a declared surface — usually an OpenAPI document

```yaml
coverage:
  apis:
    - name: core
      openapi: "./api/openapi.yaml"        # URL or local path; JSON or YAML
      base_urls: ["http://localhost:8080", "http://app-backend:8080"]
  sitemap: "./public/sitemap.xml"          # optional — declares the ROUTE surface
```

Two things to get right:

- **Prefer a committed file over a URL in CI.** A URL makes the refresh depend on the
  backend being up at that moment, which is an ordering constraint nobody needs.
- **`base_urls` must mirror the OpenAPI `servers[].url`, including any path prefix**, and
  should include the Docker-internal origins for parallel runs. Get this wrong and
  observed requests land in `unclassified` while the declared surface reads untested.

With **no** declared surface you still get recording and the *discovered* buckets — "here
is what the suite touched" — and the badge honestly reads **`n/a` in grey**, never `0%`.
What you lose is `declared-unreached`, which is the actionable half. A frontend-only
project can get a real route denominator from `coverage.sitemap` alone, with no backend
spec at all.

### `coverage.non_ui` — required for a correct badge in CI

Some endpoints can *never* have a UI test: health checks, webhook receivers, OAuth
callbacks, internal cron. Marking them with `steppy coverage triage set` writes to
`.steppy/coverage.db`, which is **gitignored** — so a fresh CI checkout has an empty
store, every endpoint reads `unknown`, and all of them count against the denominator. The
badge then reads artificially low for reasons the maintainer cannot fix from CI, and a
badge like that gets deleted.

Declare them in committed config instead:

```yaml
coverage:
  non_ui:
    - "GET /health"
    - "/webhooks/*"
    - "POST /internal/cron/*"
```

Grammar is `[<METHOD> ]<glob>`:

- A bare glob matches **any method**; a method-qualified pattern matches only that one.
- `*` **crosses `/`** — `/internal/*` covers the whole subtree. This is intentional.
- A pattern applies across **every** configured API, because it matches on the path
  alone. Use `steppy coverage triage set` for the surgical single-node case;
  `steppy coverage triage list` shows every flag set that way.
- A method-qualified pattern never matches a *route* (routes have no method).

Precedence is a total order: **db `ui_testable` > config pattern > db `non_ui` >
`unknown`/absent**. An explicit human "yes this is reachable" outranks a glob; nothing is
ever written back to the store, so merge and refresh still never touch triage.
`steppy coverage report` **warns about a pattern that matches nothing** — after an API
rename a stale rule silently inflates the number.

### The badge number

**`tested / (declared − non_ui)`, with non_ui subtracted from *both* sides.** Subtracting
it from the denominator only would let a tested-but-non_ui endpoint push the result past
100%.

```bash
steppy coverage report --badge output/badge.json --badge-surface all
```

Worked example — 6 declared endpoints, 3 matched by the patterns above, 2 tested:
denominator 3, numerator 2, **67%**. Without `non_ui` the same suite reads 33%.

**Cost note:** `--coverage` drives CDP precise coverage, so it costs runtime. On free
runners, recording it on default-branch pushes (to refresh the badge) rather than on
every PR is usually the right trade.

**Why the coverage steps ship unarmed in both CI templates:** `steppy coverage refresh`
**exits 1** when neither `coverage.apis` nor `coverage.sitemap` is configured. Arming it
in the scaffold would break the pipeline of every project that has not declared a surface
yet — which is every project on the day it runs `steppy init`. Add the two steps once the
`coverage:` block exists. Until then the honest outcome is what the report already
produces: coverage recorded, nothing to score it against, badge reads **`n/a` in grey**
rather than a `0%` that looks like a finding.

### How the report gets coverage — four tiers

The store lives **outside `output/`** on purpose, so wiping run artifacts never destroys
hand-made triage — which also means it is never inside a bundle. So:

| Tier | Source | When |
|---|---|---|
| `bundled` | `output/coverage/gaps.json` carried in the run | any input that has one |
| `store` | the project's live `.steppy/coverage.db` | **live `output/` tree only** |
| `records` | a throwaway map rebuilt from the raw records | records, no gaps.json, no store |
| `none` | no coverage section at all | nothing recorded |

`gaps.json` is written automatically after the post-run merge, so
`steppy run --coverage --bundle` is self-sufficient — tier 1 with no extra CI step. Two
guardrails are deliberate and worth knowing, because the wrong answer would look
entirely plausible:

- **Tier 2 is live-tree only.** Reading this machine's map to describe someone else's
  downloaded bundle would attribute your coverage to their run.
- **Tier 3 cannot report assert strength**, because the scenario markdown it would need
  to count assert steps in never travels in a bundle. It says so instead of guessing.

---

## Results bundles

```bash
steppy run --bundle           # capture per-test AND per-step events + JUnit XML
steppy bundle create          # → output/bundles/steppy-bundle-<run-id>.tar.zst
steppy bundle inspect <file>  # the manifest, without extracting
steppy bundle verify <file>   # re-hash every member against it
```

`--bundle` does not create the bundle; it captures what a bundle needs. `create` then
packages `output/` **exactly as Steppy wrote it** — no path rewriting — plus a generated
`manifest.json`, a merged `junit.xml`, the event JSONL, and `coverage/gaps.json` when the
run has one. Unpacking yields a tree that `steppy report`, `steppy guide` and
`steppy build` all consume unchanged.

Useful with no server involved: a self-describing archive to attach to a CI job, so a
failed run is one download rather than a scavenger hunt. `--no-videos` / `--no-traces` /
`--no-guides` trim it; `--max-size 2GiB` drops the cheapest-to-lose artifacts first and
marks the manifest `truncated`.

`steppy bundle upload` pushes to a Steppy control plane, which is optional and out of
scope here — everything above works with no plane at all.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Report says "no test outcomes", verdict `unknown` | The run had no `--bundle`, so no event records exist | `steppy run --bundle` |
| Report shows outcomes but no step detail | `STEPPY_STEP_EVENTS=0`, or tests generated before per-step markers | Unset the env var; re-run `steppy generate` |
| Badge says `n/a` | Nothing declared — no `coverage.apis`, or `refresh` never ran | Configure `coverage.apis[].openapi`, then `steppy coverage refresh` |
| Coverage percentage far lower in CI than locally | Triage flags live in the gitignored `.steppy/coverage.db`; CI starts empty | Declare `coverage.non_ui` in `config.yaml` |
| Everything in `unclassified`, declared surface reads untested | `base_urls` does not match the OpenAPI `servers[].url` prefix | Fix `base_urls`; include internal origins for parallel runs |
| `discovered-tested` endpoints appear | Stale spec, or a base-URL mismatch | `steppy coverage refresh`, then check `base_urls` |
| Every tested endpoint reads "weak" | Assert-free run (`--guide` implies `--no-asserts`), or coverage rebuilt from a bundle (tier 3) | Record coverage on a normal test run, not a guide capture |
| No coverage section at all | Nothing recorded | `steppy run --coverage` |
| Report refuses to write | Target looks like a project (`config.yaml`, `.git`, `scenarios/`, `pyproject.toml`) — refused even with `--force` | Point `-o` at a dedicated directory |
| Report refuses a non-empty directory | Not a previous report (no `report.json` with a `steppy_report` key) | `--force`, or a different `-o` |
| Bundle roughly doubled in size | The report was written somewhere under `output/` other than `output/report/`, so the bundler archived the site | Use the default `-o`, or exclude it |
| CI step red but the run passed | Something else — `steppy report` always exits 0 | Read `report.json`'s `totals.exit_code` |
| A tour replay fails with `target-timeout`, or on a step id it does not expect, while its scenario passes | Stale tour files in the app image: the app serves tours compiled from older scenarios | Run `steppy tours`, rebuild the app image, then run; add `steppy tours --check` as a gate (it compares the committed `app-assets` copy's content, never the build id) |

---

## What this deliberately does not do

The report **transforms one run**. With a single run's artifacts there is nothing to
compare against, so it structurally cannot:

- diff guides or screenshots against an accepted baseline,
- track flake rate or duration trends across runs,
- show a coverage delta for a PR,
- gate a pull request, or let a human accept a baseline.

Those need memory across runs, which is a control plane's job. Do not try to build them
on top of `steppy report`. Two things that *do* exist for the adjacent needs:
`steppy flaky` ranks scenarios by flake rate from `.steppy/history.jsonl` (parallel runs
only — cache that file between CI runs or the history evaporates), and
`steppy bundle upload` hands a run to a plane if the project has one.

Nor is there an **analytics backend for in-app tours**. The overlay sends nothing anywhere:
its events (`tour-start`, `tour-step`, `tour-complete`, `tour-dismiss`, `tour-offpath`,
`tour-error`) stay in the page, for the app's own analytics if it has any. CI replays prove
a tour *works*; whether real users finish it is the app's question, not this pipeline's.
