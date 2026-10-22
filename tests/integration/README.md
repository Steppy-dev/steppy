# Integration tests

These tests exercise `steppy run --workers N >= 2` end-to-end against a real
Docker stack. They require Docker Desktop (or `docker-compose-plugin` on
Linux) and are NOT run in CI by default — the standard `python -m pytest
tests/ --ignore=tests/e2e` invocation skips this directory.

The in-app tour suites (`test_tour_replay_it.py`, `test_tour_player_it.py`,
`test_tour_angular_it.py`) are the exception: they need Chromium but no Docker,
and the `tour-e2e` and `tour-angular-e2e` CI jobs run them on every push. See
recipes 11 and 12.

## Why they're not in the unit-test suite

- They need Docker on the host, which CI may not have.
- They build the `steppy-worker` image (~3 min on first run) and spin up
  multiple Compose stacks. Per-test cost is high.
- They make assertions about real container lifecycle (was the stack
  cleanly torn down? did volumes get removed? did Ctrl-C work?), which
  can't be mocked.

The unit suite (`tests/test_parallel_*.py`) covers the deterministic bits:
config validation, compose override rendering, the work-stealing queue
under concurrent claims, the lazy-import invariant, and the byte-identical
snapshot for the single-worker path.

## Running the integration tests manually

Per the spec's "Definition of done" section, exercise these against
`fixtures/demo-guide` (or any project that has been adapted with a
`docker-compose.yml` + per-app `internal_url`):

> **Prerequisite for `steppy build` (step 6):** the fixture reads
> `steppy.guide.config.ts` via `npx tsx`, and its `node_modules/` is now gitignored —
> run `npm --prefix fixtures/demo-guide install` once first.

```bash
# 1. Byte-identical existing path
git checkout main
steppy run --scenario editor-onboarding 2>&1 | tee /tmp/before.log
git checkout <feature-branch>
steppy run --scenario editor-onboarding 2>&1 | tee /tmp/after.log
diff /tmp/before.log /tmp/after.log    # expect: no diff (modulo timestamps)

steppy run --workers 1 2>&1 | tee /tmp/w1.log
diff /tmp/before.log /tmp/w1.log        # expect: no diff

# 2. Parallel run against a real project
cd fixtures/demo-guide
steppy generate
steppy run --workers 3
docker ps          # during run: 3 stacks visible (steppy-run-<id>-w1/w2/w3)
docker ps -a       # after:    none of the steppy-run-* containers
docker volume ls | grep steppy-run-    # after: empty
docker network ls | grep steppy-run-   # after: empty

# 3. Speedup
time steppy run --workers 1   # baseline
time steppy run --workers 3   # expect: at least 2x faster

# 4. Ctrl-C cleanliness
steppy run --workers 3 &
sleep 30
kill -INT $!       # graceful interrupt
docker ps -a | grep steppy-run-                   # expect: empty
docker volume ls | grep steppy-run-               # expect: empty
docker network ls | grep steppy-run-              # expect: empty

# 4b. Clean auto-exit after queue drains (regression guard for the
#     "workers stay idle" hang). Pre-fix: docker compose ps -q (without
#     -a) hid exited containers; the coordinator's wait loop polled
#     forever and the user had to Ctrl-C. Post-fix: the worker emits
#     STEPPY-EVT::worker-done before exiting, the renderer row flips
#     from "running …" → "done" instantly, and the coordinator detects
#     the container exit within ~1.5s.
timeout 600 steppy run --workers 2 ; echo "exit=$?"
# Expect: exit=0 (or 1 on test failures), NOT 124 (timeout), NOT 130 (Ctrl-C)
# Expect: "Run summary" appears in stdout before the prompt returns
docker ps -a | grep steppy-run-                   # expect: empty (auto-teardown)

# 4c. worker-done event is emitted by each worker
steppy run --workers 2 --verbose 2>&1 | tee /tmp/run.log
grep -c "STEPPY-EVT::worker-done::" /tmp/run.log  # expect: 2 (one per worker)

# 5. Rescue command
steppy run --workers 3 &
sleep 30
kill -9 $!         # force-kill (skips teardown)
docker ps -a | grep steppy-run-                   # expect: leftovers visible
steppy stacks cleanup
docker ps -a | grep steppy-run-                   # expect: empty

# 6. Parallel + guide mode — screenshot tree shape
# Requires: at least 2 scenarios with `guide: { enabled: true }`,
# `locales:` + `screenshot.layouts:` declared in config.yaml.
cd fixtures/<project-with-guide-enabled-scenarios>
steppy generate
rm -rf output/screenshots
steppy run --workers 3 --guide
# Expect: output/screenshots/<scenario>/<locale>/<layout>/step-NN.png for
# every (scenario, locale, layout) cell, plus matching step-NN.json sidecars.
# Multi-frame fill-form steps additionally emit step-NN-a.png / -b.png / …
ls output/screenshots/<a-scenario>/fr/desktop/*.png   # non-empty
ls output/screenshots/<a-scenario>/nl/mobile/*.png    # non-empty, same count
ls output/screenshots/<a-scenario>/fr/desktop/*.json  # one per .png
steppy guide --guide                                   # assembles HTML guides
steppy build                                           # builds Angular/static artifacts

# 7. Single-worker / parallel parity (guide mode)
# Captures with --workers 1 first (single-process path), then with --workers N,
# and asserts the screenshot trees match in shape.
rm -rf output/screenshots
steppy run --guide                                     # baseline (no Docker)
cp -r output/screenshots /tmp/screenshots-w1

rm -rf output/screenshots
steppy run --workers 3 --guide                         # parallel
diff -rq /tmp/screenshots-w1 output/screenshots
# Expect: identical filenames at identical paths. Pixel-level differences are
# possible (browser non-determinism), but the set of PNGs + JSON sidecars
# should match exactly. JSON contents (viewport, target bbox) should be
# byte-identical modulo timestamps in sidecars (none today).
```

```bash
# 8. Coverage recorder against a real browser (no Docker — needs Playwright
#    Chromium). Self-contained: serves a tiny pushState+fetch SPA via
#    http.server, runs the full generate → run --coverage → merge → refresh
#    → report loop, and asserts the record shape (SPA route trail, API
#    request capture, CDP JS coverage, inline source-map persistence) plus
#    the declared-unreached bucket.
STEPPY_IT=1 python -m pytest tests/integration/test_coverage_recorder_it.py -q
# PowerShell:  $env:STEPPY_IT='1'; python -m pytest tests/integration/test_coverage_recorder_it.py -q
# Expect: 1 passed in ~10s. Without STEPPY_IT=1 the test self-skips, so the
# standard unit-suite invocation stays browser-free.

# 8b. Coverage + parallel: workers record into the shared output/ bind mount
#     under the coordinator's STEPPY_RUN_ID; the coordinator merges post-run.
steppy run --workers 3 --coverage
steppy coverage report
# Expect: "Coverage: merged N scenario(s) …" after the run summary, and
# output/coverage/raw/<run_id>/ containing one subdir per scenario regardless
# of which worker ran it.
```

```bash
# 9. Parallel pre-authentication (parallel.pre_auth)
#    Requires: a project using `mode: storage-state` with a
#    `parallel.pre_auth.command` in config.yaml, and the app served in-network
#    at each role's internal_url. Verifies the preauth container runs (and
#    completes) BEFORE any worker claims a scenario.
cd fixtures/<project-with-storage-state-auth>
rm -f .auth/*.json                                     # start clean
steppy run --workers 3 --verbose 2>&1 | tee /tmp/preauth.log
# Expect: exactly one stack logs in; the others wait on the O_EXCL lock.
grep -c "\[preauth\] done" /tmp/preauth.log            # expect: 1 (the winner)
grep -c "another stack is authenticating" /tmp/preauth.log   # expect: N-1 (losers)
ls .auth/*.json                                        # populated by the in-network login
# Gating: the worker rows must not start until "[preauth] done" is printed.
# Idempotent re-run: within auth.storage_state_ttl the capture command should
# skip roles whose in-network origin is already fresh.
steppy run --workers 3 --verbose 2>&1 | grep -E "preauth|skipped"

# 9b. Missing pre_auth is fatal for storage-state workers (regression note).
#     Without the hook, a host-captured .auth/<role>.json is origin-bound to
#     localhost and every worker scenario redirects to /login. This is the bug
#     parallel.pre_auth exists to fix — capture at the internal origin.
```

### 10. Exclusion groups — members never overlap

The unit tests prove the queue's scheduling property; this proves it end to end,
through real containers and real pytest processes.

```bash
# Give two scenarios the same group, leave a third ungrouped.
#   scenarios/a.md:  exclusive: demo
#   scenarios/c.md:  exclusive: demo
#   scenarios/b.md:  (no exclusive)
steppy generate
steppy run --workers 3 --verbose | tee /tmp/run.log

# The census is printed next to the queue line:
grep "exclusion groups:" /tmp/run.log
#   exclusion groups: demo[2] — 1 group(s) serialising 2 scenario(s) into 1 task(s)

# a and c must never be in flight at the same time; b may overlap either.
grep -E "STEPPY-EVT::scenario-(start|end)::(a|c)::" /tmp/run.log
#   Read the timestamps: a's scenario-end must precede c's scenario-start
#   (or vice versa). Any interleaving is a regression.
grep -E "STEPPY-EVT::scenario-(start|end)::b::" /tmp/run.log
#   b's interval SHOULD overlap one of them — otherwise the group is
#   serialising the whole run, not just its members.

# Both members ran on ONE worker, in one claim:
grep "claimed exclusion group" /tmp/run.log
#   [w1] claimed exclusion group 'demo' (2 scenarios: a, c)
```

### 10b. Full collapse still runs

Put `exclusive: all` on **every** selected scenario and run `--workers 4`. The
coordinator must bring up ONE stack, run every scenario serially, and exit on the
results. An immediate `exit 0` with no stack and no pytest output is the failure
this checks for — it is the shape a green-but-empty CI run would take.

```bash
steppy run --workers 4 ; echo "exit=$?"
#   INFO exclusion groups collapsed N scenario(s) into 1 task(s); running on a single stack.
#   …every scenario runs…
```

### 10c. A worker killed mid-group

During recipe 10, `docker kill` the worker container holding the group. The
finished member keeps its result; the rest must be reported as orphans **by
scenario slug** — never as the group name — retried, and the run must exit 1 if
they do not recover. A summary line naming a scenario that does not exist (the
group) means the batch map did not reach orphan detection.

### 11. In-app tours against the plain-HTML demo

Both suites drive the demo app in `fixtures/tour-demo/` (served in-process by
`fixtures/tour-demo/serve.py`). Prerequisites: the player build (it produces the
overlay, `packages/player/dist/steppy-tour.js`) and Chromium.

```bash
npm --prefix packages/player install && npm --prefix packages/player run build
playwright install chromium --with-deps
STEPPY_TOUR_E2E=1 python -m pytest tests/integration/test_tour_replay_it.py tests/integration/test_tour_player_it.py -q
```

PowerShell:

```powershell
$env:STEPPY_TOUR_E2E = '1'
python -m pytest tests/integration/test_tour_replay_it.py tests/integration/test_tour_player_it.py -q
```

Expected: every test passes in under a minute.

- `test_tour_replay_it.py` copies `fixtures/tour-demo/project`, runs `steppy
  generate`, then the generated suite against the demo: the scenarios and their
  `__tour` replays pass; with a test-id renamed in the app
  (`testIdOverrides`) the replay fails, naming the tour, the step and the
  test-id; the strict build-id check and the kill switch hold.
- `test_tour_player_it.py` drives the overlay directly: the tour by mouse and
  by keyboard, hit-testing through the layer, the "can't find this element"
  fallback, rejected files, remote mode (`ok`, `fail`, `tamper`, `nocors`) and a
  guide site built by `steppy build` as the remote origin, entry routes,
  resume after a reload, jump-ahead, Escape rules, and a strict CSP with Trusted
  Types.

`STEPPY_TOUR_BUNDLE=<path>` points the demo server at another build of the
overlay. To look at the demo yourself: `python fixtures/tour-demo/serve.py`
(see `fixtures/tour-demo/README.md`).

### 12. In-app tours in an Angular app (`@steppy/angular`)

`test_tour_angular_it.py` runs against `fixtures/tour-demo-angular/app` — the
same demo in Angular, PrimeNG and ngx-translate, mounting the overlay through
the adapter. Build the player, the adapter and the demo first:

```bash
npm --prefix packages/player ci && npm --prefix packages/player run build
npm --prefix packages/angular ci && npm --prefix packages/angular run build
npm --prefix fixtures/tour-demo-angular/app ci && npm --prefix fixtures/tour-demo-angular/app run build
STEPPY_TOUR_ANGULAR_E2E=1 python -m pytest tests/integration/test_tour_angular_it.py -q
```

- The plain demo's project (`fixtures/tour-demo/project`) is generated and its
  scenarios and `__tour` replays run unchanged against the Angular build — the
  `select-option` steps on a real `p-select`, the form in a real `p-dialog`;
  a renamed test-id fails the replay; the kill switch holds.
- Then the adapter itself: the help button and the service state, a tour by
  mouse through the dialog and the dropdown, an entry route reached through the
  router without a reload, a guard that refuses the route failing the tour at
  once, a mid-tour language switch through ngx-translate, no change-detection
  turns while a step is shown, and resume after a reload with no console
  errors.

## Future: automated integration suite

A `pytest -m integration` marker could opt-in via a hand-curated set of
tests that wrap these manual steps in subprocess assertions. Out of scope
for v0.1 of parallel mode; document here so we don't lose the idea.
