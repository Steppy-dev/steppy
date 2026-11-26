"""What the report's pages show: the kinds, the order, the matrix, the causes, the words.

The rules are Steppy Cloud's run page's, so that a local report and the same run in
the cloud read the same. They are pinned here as data: no page is rendered.
"""

import base64

import pytest

from steppy.bundle.results import steps_from_records
from steppy.contracts.bundle import BundleCi, BundleGit, BundleManifest, BundleRun, BundleSpec, BundleTest
from steppy.contracts.report import ReportBox, ReportCapture, ReportSourceInfo, ReportStep, ReportVariant
from steppy.report import words
from steppy.report.model import build_model
from steppy.report.view import (
    Ref,
    RunModel,
    build_view,
    kind_of,
    marks_for,
    retried,
    step_title,
    worst_of,
)

SPEC = BundleSpec(locales=["en", "fr", "de"], layouts=["desktop", "mobile"])


def _run(**kw) -> BundleRun:
    return BundleRun(
        **{
            "run_id": "20261125T201335Z-0a895b",
            "created_at": "2026-11-25T20:13:35Z",
            "steppy_version": "0.10.0",
            "spec": SPEC,
            **kw,
        }
    )


def _test(scenario, locale="fr", layout="mobile", outcome="passed", **kw) -> BundleTest:
    tail = kw.pop("tail", None)
    if tail is not None:
        kw["fail_tail_b64"] = base64.b64encode(tail.encode()).decode("ascii")
    nodeid = kw.pop("nodeid", f"tests/e2e/test_{scenario}.py::test_flow[{locale}-{layout}]")
    return BundleTest(scenario=scenario, nodeid=nodeid, outcome=outcome, locale=locale, layout=layout, **kw)


def _report(*tests, run=None, **kw):
    manifest = BundleManifest(steppy_bundle=1, run=run or _run(), tests=list(tests), files=kw.pop("files", []))
    return build_model(manifest=manifest, source=ReportSourceInfo(kind="tree", label="output"), **kw)


def _view(*tests, **kw):
    sizes = kw.pop("sizes", None)
    return build_view(_report(*tests, **kw), sizes=sizes)


def _every(scenario, outcomes=None):
    """One test per (locale, layout) of the spec; *outcomes* overrides some."""
    outcomes = outcomes or {}
    return [
        _test(scenario, locale, layout, outcomes.get((locale, layout), "passed"))
        for locale in SPEC.locales
        for layout in SPEC.layouts
    ]


def _retry(nodeid, *outcomes):
    """The event records of a test attempted once per outcome."""
    events, ts = [], 1.0
    for outcome in outcomes:
        events.append({"kind": "test-start", "ts": ts, "nodeid": nodeid})
        events.append({"kind": "step-start", "ts": ts, "nodeid": nodeid, "step": 1, "title": "Open", "action": "click"})
        if outcome == "passed":
            events.append({"kind": "step-end", "ts": ts + 1, "nodeid": nodeid, "step": 1})
        events.append({"kind": "test-end", "ts": ts + 1, "nodeid": nodeid, "outcome": outcome, "duration_ms": 1000})
        ts += 2
    return steps_from_records(events)


# ── kinds ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "outcome,attempts,kind",
    [
        ("failed", [], "f"),
        ("error", [], "f"),
        ("interrupted", [], "f"),
        ("worker_died", [], "f"),
        ("teleported", [], "f"),  # an outcome nobody knows is not a pass
        ("failed", ["passed", "failed"], "f"),  # failing wins over flaky
        ("passed", ["failed", "passed"], "k"),
        ("passed", ["error", "passed"], "k"),
        ("passed", ["passed", "passed"], "p"),
        ("passed", ["passed"], "p"),
        ("passed", [], "p"),
        ("skipped", [], "s"),
    ],
)
def test_the_kind_of_a_variant(outcome, attempts, kind):
    assert kind_of(ReportVariant(nodeid="n", outcome=outcome, attempt_outcomes=attempts)) == kind


def test_a_variant_that_did_not_run_has_a_kind_of_its_own():
    assert kind_of(None) == "n"


def test_flaky_needs_attempts_that_disagree():
    assert retried(ReportVariant(nodeid="n", attempt_outcomes=["failed", "failed", "passed"]))
    assert not retried(ReportVariant(nodeid="n", attempt_outcomes=["failed", "failed"]))
    assert not retried(ReportVariant(nodeid="n", attempt_outcomes=["unknown", "passed"]))


def test_the_worst_kind_wins_and_none_is_not_run():
    assert worst_of(["p", "s", "k", "f"]) == "f"
    assert worst_of(["s", "p"]) == "p"
    assert worst_of([]) == "n"


# ── order ───────────────────────────────────────────────────────────────────


def test_scenarios_are_failing_then_flaky_then_passed_then_skipped():
    flaky = _test("b-flaky")
    view = _view(
        _test("a-passed"),
        _test("a-skipped", outcome="skipped"),
        flaky,
        _test("z-failed", outcome="failed"),
        _test("y-failed", outcome="error"),
        _test("c-passed"),
        attempts=_retry(flaky.nodeid, "failed", "passed"),
    )
    assert [(r.slug, r.kind) for r in view.matrix.rows] == [
        ("y-failed", "f"),
        ("z-failed", "f"),
        ("b-flaky", "k"),
        ("a-passed", "p"),
        ("c-passed", "p"),
        ("a-skipped", "s"),
    ]


def test_a_scenario_with_no_variant_comes_last():
    from steppy.contracts.bundle import BundleFile

    log = BundleFile(path="output/failures/a/pytest.log", kind="failure_log", sha256="0" * 64, size=1, scenario="a")
    view = _view(_test("b", outcome="skipped"), files=[log])
    assert [(r.slug, r.kind) for r in view.matrix.rows] == [("b", "s"), ("a", "n")]


def test_failing_variants_go_by_scenario_then_layout_then_locale_in_the_runs_own_order():
    tests = _every("b", {("de", "desktop"): "failed", ("fr", "mobile"): "failed", ("en", "mobile"): "failed"})
    tests += _every("a", {("fr", "mobile"): "failed", ("fr", "desktop"): "failed"})
    model = RunModel(_report(*tests))
    assert model.locales == ["en", "fr", "de"]  # the spec's order, not the alphabet's
    assert model.failing == [
        Ref("a", "fr", "desktop"),
        Ref("a", "fr", "mobile"),
        Ref("b", "de", "desktop"),
        Ref("b", "en", "mobile"),
        Ref("b", "fr", "mobile"),
    ]


def test_a_dimension_the_spec_does_not_name_goes_last():
    model = RunModel(_report(_test("a", "it", "watch"), _test("a", "ar", "mobile"), _test("a", "fr", "mobile")))
    assert model.locales == ["fr", "ar", "it"]
    assert model.layouts == ["mobile", "watch"]


def test_the_run_opens_on_its_first_failing_variant_else_on_its_first():
    view = _view(*_every("a"), *_every("b", {("fr", "mobile"): "failed"}))
    assert view.variant is not None and view.variant.ref == Ref("b", "fr", "mobile")
    assert view.variant.counter == "Failing variant 1 of 1"

    green = _view(*_every("a"), *_every("b"))
    assert green.variant is not None and green.variant.ref == Ref("a", "en", "desktop")
    assert green.variant.counter == "No failing variant in this run"


def test_a_run_with_no_outcome_opens_on_nothing():
    view = _view()
    assert view.variant is None and view.matrix.rows == []
    assert view.header.verdict == "No test outcomes recorded"


def test_previous_and_next_walk_the_failing_variants():
    tests = [*_every("a", {("en", "mobile"): "failed"}), *_every("b", {("de", "desktop"): "failed"})]
    view = _view(*tests)
    by_anchor = {v.anchor: v for s in view.scenarios for v in s.variants}
    first, last, passing = by_anchor["a/en/mobile"], by_anchor["b/de/desktop"], by_anchor["a/fr/mobile"]
    assert (first.previous, first.next) == (None, "scenarios/b.html#b/de/desktop")
    assert (last.previous, last.next) == ("scenarios/a.html#a/en/mobile", None)
    assert passing.counter == "Not a failing variant · 2 failing in this run"
    assert (passing.previous, passing.next) == (None, "scenarios/a.html#a/en/mobile")


def test_a_scenarios_page_lists_failing_variants_first():
    page = _view(*_every("a", {("de", "mobile"): "failed", ("fr", "desktop"): "skipped"})).scenarios[0]
    assert [v.label for v in page.variants] == [
        "de · mobile",
        "en · desktop",
        "de · desktop",
        "en · mobile",
        "fr · mobile",
        "fr · desktop",
    ]
    assert page.first == "a/de/mobile"


# ── the matrix ──────────────────────────────────────────────────────────────


def test_a_cell_sums_a_layout_over_every_locale():
    row = _view(*_every("a", {("fr", "mobile"): "failed", ("de", "mobile"): "failed"})).matrix.rows[0]
    assert [(c.kind, c.label, c.more) for c in row.cells] == [("p", "3 passed", ""), ("f", "2 failed", ", 1 passed")]
    assert row.where == "Fails on mobile in fr and de"
    assert row.expandable and row.cells[0].href is None
    assert [line.locale for line in row.lines] == ["fr", "de"]
    assert row.fold_label == "1 locale passed everywhere"
    assert row.lines[0].cells[1].href == "scenarios/a.html#a/fr/mobile"


def test_failing_locales_come_before_flaky_ones():
    flaky = _test("a", "en", "mobile")
    tests = [t for t in _every("a", {("de", "mobile"): "failed"}) if (t.locale, t.layout) != ("en", "mobile")]
    view = _view(*tests, flaky, attempts=_retry(flaky.nodeid, "failed", "passed"))
    row = view.matrix.rows[0]
    assert [line.locale for line in row.lines] == ["de", "en"]
    assert row.where == "Fails on mobile in de · Flaky on mobile in en"


def test_one_locale_makes_each_cell_a_variant():
    run = _run(spec=BundleSpec(layouts=["desktop", "mobile"]))
    view = _view(_test("a", None, "desktop"), _test("a", None, "mobile", "failed"), run=run)
    row = view.matrix.rows[0]
    assert not row.expandable
    assert [(c.label, c.href) for c in row.cells] == [
        ("Passed", "scenarios/a.html#a/-/desktop"),
        ("Failed", "scenarios/a.html#a/-/mobile"),
    ]
    assert view.matrix.note == "One outcome per layout. Open one to see its steps."


def test_a_variant_that_never_ran_is_not_run_and_links_nowhere():
    run = _run(spec=BundleSpec(layouts=["desktop", "mobile"]))
    tests = [_test("a", None, "desktop"), _test("a", None, "mobile"), _test("b", None, "desktop")]
    row = _view(*tests, run=run).matrix.rows[1]
    assert (row.cells[1].kind, row.cells[1].label, row.cells[1].href) == ("n", "Not run", None)


def test_a_flat_run_has_one_result_column():
    run = _run(spec=BundleSpec())
    view = _view(_test("a", None, None, "failed", failed_step=3), _test("b", None, None), run=run)
    assert [c.label for c in view.matrix.columns] == ["Result"]
    assert view.matrix.rows[0].where == "Failed at step 3"
    assert view.header.shape == "No locales or layouts"
    assert view.header.of == "of 2 scenarios"
    assert view.variant is not None and view.variant.anchor == "a/-/-"


def test_a_variant_without_the_runs_layout_keeps_a_column_and_is_never_lost():
    """One scenario declares ``layouts:`` and another does not: the second one's failure
    must show, not read "Not run" in every column."""
    run = _run(spec=BundleSpec())
    tests = [
        _test("declares", None, "desktop"),
        _test("declares", None, "mobile"),
        _test("plain", None, None, "failed"),
    ]
    view = _view(*tests, run=run)
    assert [c.label for c in view.matrix.columns] == ["desktop", "mobile", "default"]
    assert view.variant is not None and view.variant.ref == Ref("plain", None, None)
    assert view.variant.label == "default"
    assert view.matrix.rows[0].where == "Fails on default"


def test_two_tests_in_one_variant_show_the_worst_and_the_page_shows_both():
    """A tour's replay sits beside its scenario's test: a passing replay must not hide a failure."""
    module = "tests/e2e/test_a.py"
    main = _test("a", "fr", "mobile", "failed", nodeid=f"{module}::test_a[fr-mobile]")
    replay = _test("a", "fr", "mobile", "passed", nodeid=f"{module}::test_a__tour[fr-mobile]")
    view = _view(main, replay)
    assert view.matrix.rows[0].kind == "f"
    assert [(v.kind, v.extra) for v in view.scenarios[0].variants] == [("f", False), ("p", True)]
    assert view.header.of == "of 2 variants"


# ── the header ──────────────────────────────────────────────────────────────


def test_the_verdict_and_the_totals():
    tests = [*_every("a", {("fr", "mobile"): "failed"}), *_every("b"), _test("c", outcome="skipped")]
    header = _view(*tests).header
    assert header.verdict == "1 of 3 scenarios failing, 1 cause"
    assert [c.words for c in header.counts] == ["1 failed", "11 passed", "1 skipped"]
    assert header.of == "of 13 variants"
    assert header.shape == "3 locales × 2 layouts"
    assert header.when == "25 Nov 2026, 20:13 UTC"


def test_a_green_run_keeps_its_passed_count():
    header = _view(_test("a")).header
    assert header.verdict == "All 1 scenarios passing"
    assert [c.words for c in header.counts] == ["1 passed"]


def test_where_the_run_came_from():
    git = BundleGit(
        sha="a07d66851d4f0c3b", branch="feat/cart", pr=57, base_branch="main", tested_sha="bbbbbbbbbbbb", dirty=True
    )
    ci = BundleCi(
        provider="github-actions", url="https://github.com/acme/shop/actions/runs/4821/attempts/1", repo="acme/shop"
    )
    header = _view(_test("a"), run=_run(git=git, ci=ci, environment="staging", executor="ci")).header
    assert (header.branch, header.change, header.base_branch) == ("feat/cart", "PR #57", "main")
    assert (header.commit, header.tested_as, header.dirty) == ("a07d668", "bbbbbbb", True)
    assert (header.environment, header.source) == ("staging", "from CI")
    assert header.ci == "GitHub Actions run 4821"
    assert header.ci_url == "https://github.com/acme/shop/actions/runs/4821/attempts/1"


def test_a_gitlab_change_is_a_merge_request():
    ci = BundleCi(provider="gitlab-ci", url="https://gitlab.example.com/acme/shop/-/jobs/4242")
    header = _view(_test("a"), run=_run(git=BundleGit(pr=4), ci=ci, executor="ci")).header
    assert (header.change, header.ci) == ("MR !4", "GitLab job 4242")


def test_an_executor_nobody_knows_is_shown_as_it_is():
    assert _view(_test("a"), run=_run(executor="github-actions")).header.source == "github-actions"
    assert _view(_test("a"), run=_run(executor="local")).header.source == "from a local machine"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        " javascript:alert(1)",
        "java\nscript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:x",
        "file:///etc/passwd",
        "//evil.example/x",
        "/relative",
        "http:evil",
        "http://",
        "https:///path",
        "https://ok.example/\x00",
        "https://ok.example/a b",
        "https://ok.example\\@evil.example/",
        "ftp://ok.example/",
        "\x01https://ok.example/",
        "",
    ],
)
def test_only_http_and_https_addresses_become_links(url):
    assert words.web_url(url) is None
    header = _view(_test("a"), run=_run(ci=BundleCi(provider="gitlab-ci", url=url))).header
    assert header.ci_url is None
    assert header.ci_text == url
    assert header.ci == "GitLab CI"


@pytest.mark.parametrize("url", ["https://ci.example/job/1?x=1#log", "HTTP://ci.example", "http://127.0.0.1:8080/x"])
def test_web_addresses_are_links(url):
    assert words.web_url(url) == url


def test_the_time_is_the_runs_own_and_never_a_clock():
    assert words.when_text("2026-08-04T09:05:00+02:00") == "4 Aug 2026, 07:05 UTC"
    assert words.when_text("2026-08-04T09:05:00") == "4 Aug 2026, 09:05"
    assert words.when_text("yesterday, around noon") == "yesterday, around noon"


# ── failure causes ──────────────────────────────────────────────────────────


def test_a_cause_names_where_it_hits_once_per_scenario_and_set_of_locales():
    outcomes = {("fr", "mobile"): "failed", ("de", "mobile"): "failed", ("fr", "desktop"): "failed"}
    tests = [t.model_copy(update={"failed_step": 6}) if t.outcome == "failed" else t for t in _every("a", outcomes)]
    cause = _view(*tests).causes[0]
    assert cause.line == "Hits 3 variants in 1 scenario · failing at step 6 · fingerprint"
    assert [(g.scenario, g.where, g.count) for g in cause.groups] == [
        ("a", " · desktop · fr", "1 variant"),
        ("a", " · mobile · 2 of 3 locales (fr and de)", "2 variants"),
    ]
    assert [c.href for c in cause.groups[1].chips] == ["scenarios/a.html#a/fr/mobile", "scenarios/a.html#a/de/mobile"]


def test_layouts_that_share_their_locales_are_one_group():
    outcomes = {(locale, layout): "failed" for locale in SPEC.locales for layout in SPEC.layouts}
    cause = _view(*_every("a", outcomes)).causes[0]
    assert [g.where for g in cause.groups] == [" · desktop and mobile · all 3 locales"]


def test_a_cause_has_an_anchor_no_link_can_be_and_its_variants_point_at_it():
    view = _view(_test("a", outcome="failed", tail="E boom"))
    cause = view.causes[0]
    assert cause.anchor == f"cause/{cause.fingerprint}"
    assert view.variant is not None and view.variant.cause == cause.anchor


def test_groups_past_the_fifth_are_folded():
    view = _view(*[_test(f"s{i}", outcome="failed", tail="E boom") for i in range(8)])
    assert (len(view.causes[0].shown), len(view.causes[0].hidden)) == (5, 3)


# ── steps ───────────────────────────────────────────────────────────────────


def _variant(**kw) -> ReportVariant:
    return ReportVariant(
        **{"nodeid": "n", "locale": "fr", "layout": "mobile", "outcome": "failed", "failed_step": 2, **kw}
    )


def _steps(variant: ReportVariant, sizes=None):
    report = _report(_test("a", "fr", "mobile", variant.outcome))
    report.scenarios[0].variants = [variant]
    view = build_view(report, sizes=sizes)
    assert view.variant is not None
    return view.variant


def test_the_failing_step_is_marked_and_shows_the_page_at_the_failure():
    view = _steps(
        _variant(
            failure_screenshot="f.png",
            steps=[
                ReportStep(step=1, title="Open", action="navigate", screenshots=["one.png"], duration_ms=5),
                ReportStep(step=2, title="Pay", action="click", status="running"),
            ],
        )
    )
    assert [(s.n, s.kind, s.thumb.url if s.thumb else None) for s in view.steps] == [
        (1, "p", "one.png"),
        (2, "f", "f.png"),
    ]
    assert view.stopped_at == 2
    assert [s.anchor for s in view.steps] == ["a/fr/mobile/step-1", "a/fr/mobile/step-2"]


def test_without_a_failure_screenshot_the_failing_steps_last_frame_stands_in():
    step = ReportStep(step=2, status="running", screenshots=["a.png", "b.png"])
    view = _steps(_variant(steps=[step]))
    assert view.failure_shot is not None and view.failure_shot.url == "b.png"


def test_a_failing_step_the_run_recorded_no_boundary_of_is_still_listed():
    view = _steps(_variant(failed_step=7, failed_step_title="Pay", steps=[ReportStep(step=1)]))
    assert [(s.n, s.title, s.kind, s.recorded) for s in view.steps] == [(1, "", "p", True), (7, "Pay", "f", False)]


def test_a_passing_variant_marks_no_step():
    view = _steps(_variant(outcome="passed", failed_step=None, steps=[ReportStep(step=1, status="running")]))
    assert [s.kind for s in view.steps] == ["p"]
    assert view.failure_shot is None and view.cause is None


def test_a_title_nobody_wrote_is_not_repeated():
    assert step_title("Step 7") == "" and step_title(" step7 ") == ""
    assert step_title("Step 7 of the form") == "Step 7 of the form"


def test_a_flaky_variant_tells_its_attempts():
    test = _test("a")
    view = _view(test, attempts=_retry(test.nodeid, "failed", "passed"))
    assert view.variant is not None
    assert view.variant.status_word == "Passed on retry"
    assert view.variant.flaky_note == "Attempt 1 failed, attempt 2 passed. Passed on retry in this run."


# ── click targets ───────────────────────────────────────────────────────────


def _capture(*boxes, scale=1.0):
    return ReportCapture(
        url="x.png",
        device_scale_factor=scale,
        boxes=[ReportBox(x=x, y=y, width=w, height=h) for x, y, w, h in boxes],
    )


def test_an_outline_is_in_percent_of_the_images_css_size():
    (mark,) = marks_for(_capture((39, 84.4, 97.5, 42.2), scale=3), (1170, 2532))
    assert mark == {"left": "10", "top": "10", "width": "25", "height": "5"}


def test_a_full_page_capture_is_placed_by_the_image_not_the_viewport():
    capture = _capture((0, 1500, 100, 100))
    capture.viewport = {"width": 1000, "height": 800}
    (mark,) = marks_for(capture, (1000, 3000))
    assert mark["top"] == "50"


def test_one_outline_per_field_and_none_without_a_size_or_a_box():
    assert len(marks_for(_capture((0, 0, 5, 5), (10, 10, 5, 5)), (100, 100))) == 2
    assert marks_for(_capture((0, 0, 5, 5)), None) == ()
    assert marks_for(_capture(), (100, 100)) == ()
    assert marks_for(None, (100, 100)) == ()


def test_a_box_outside_the_image_draws_nothing_and_a_huge_one_stays_drawable():
    assert marks_for(_capture((500, 0, 5, 5)), (100, 100)) == ()
    assert marks_for(_capture((-50, 0, 5, 5)), (100, 100)) == ()
    (mark,) = marks_for(_capture((-1e300, 0, 2e300, 1e308)), (100, 100))
    assert mark == {"left": "-200", "top": "0", "width": "300", "height": "300"}


def test_two_steps_with_the_same_bytes_keep_their_own_target():
    steps = [ReportStep(step=n, screenshots=["same.png"], captures=[_same(x)]) for n, x in ((1, 10.0), (2, 60.0))]
    view = _steps(_variant(outcome="passed", failed_step=None, steps=steps), sizes={"same.png": (100, 100)})
    assert [s.thumb.marks[0]["left"] for s in view.steps if s.thumb] == ["10", "60"]


def _same(x):
    capture = _capture((x, 0, 5, 5))
    capture.url = "same.png"
    return capture


# ── words ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pairs,expected",
    [
        ([("fr", "mobile"), ("de", "mobile")], "on mobile in fr and de"),
        ([("fr", "mobile"), ("fr", "desktop")], "in fr on desktop and mobile"),
        ([(x, y) for x in SPEC.locales for y in SPEC.layouts], "in every variant"),
        ([(x, "mobile") for x in SPEC.locales], "on mobile in all 3 locales"),
        ([("fr", "mobile"), ("de", "desktop")], "on desktop in de; on mobile in fr"),
        ([], ""),
    ],
)
def test_where_a_scenario_fails(pairs, expected):
    assert words.where_text(pairs, SPEC.locales, SPEC.layouts) == expected


def test_where_without_a_dimension():
    assert words.where_text([("fr", ""), ("de", "")], SPEC.locales, []) == "in fr and de"
    assert words.where_text([("", "mobile")], [], SPEC.layouts) == "on mobile"
    assert words.where_text([("", "")], [], []) == ""


def test_lists_and_counts():
    assert words.few_list(["a", "b", "c", "d", "e"]) == "a, b, c and 2 more"
    assert words.join_list(["a", "b", "c"]) == "a, b and c"
    assert words.counted("variant", 1) == "1 variant"
    assert words.counted("variant", 1875) == "1,875 variants"


def test_the_pages_content_is_a_pure_function_of_the_report():
    tests = [*_every("a", {("fr", "mobile"): "failed"}), *_every("b")]
    assert _view(*tests) == _view(*tests)
