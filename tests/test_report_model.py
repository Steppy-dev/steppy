"""The view model: what a run *means*, built from a manifest and a timeline.

Synthetic manifests throughout — no browser, no bundle on disk. That is the point
of ``build_model`` being pure: the interesting cases here (a scenario that failed
in one locale only, forty tests sharing one cause, a run made without ``--bundle``)
are a nuisance to produce for real and trivial to state as data.
"""

import pytest

from steppy.bundle.results import steps_from_records
from steppy.contracts.bundle import BundleFile, BundleManifest, BundleRun, BundleTest
from steppy.contracts.report import ReportCoverage, ReportSourceInfo, report_bytes
from steppy.report.model import build_model, group_manifest_failures, worse

RUN = BundleRun(run_id="20260814T100000Z-abc123", created_at="2026-08-14T10:00:00Z", steppy_version="0.3.0")
SOURCE = ReportSourceInfo(kind="tree", label="output")


def _tail_b64(text: str) -> str:
    import base64

    return base64.b64encode(text.encode()).decode("ascii")


def _test(scenario="order-lifecycle", locale="fr", layout="mobile", outcome="passed", **kw):
    nodeid = kw.pop("nodeid", f"tests/e2e/test_{scenario}.py::test_flow[{locale}-{layout}]")
    return BundleTest(scenario=scenario, nodeid=nodeid, outcome=outcome, locale=locale, layout=layout, **kw)


def _file(path, kind, **kw):
    return BundleFile(path=path, kind=kind, sha256="0" * 64, size=10, **kw)


def _manifest(tests=(), files=(), **kw):
    return BundleManifest(steppy_bundle=1, run=RUN, tests=list(tests), files=list(files), **kw)


def _build(manifest, **kw):
    return build_model(manifest=manifest, source=SOURCE, **kw)


# ── outcome arithmetic ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("passed", "failed", "failed"),
        ("failed", "error", "error"),
        ("passed", "skipped", "skipped"),
        ("failed", "worker_died", "worker_died"),
        ("passed", "passed", "passed"),
    ],
)
def test_worse_picks_the_worse_outcome(a, b, expected):
    assert worse(a, b) == expected
    assert worse(b, a) == expected


def test_an_unknown_outcome_is_not_a_pass_and_not_a_failure():
    """Ranked between them: claiming a pass would be wrong, claiming a failure
    would be a guess."""
    assert worse("passed", "teleported") == "teleported"
    assert worse("failed", "teleported") == "failed"


def test_a_scenario_failing_in_one_locale_only_is_a_failing_scenario():
    manifest = _manifest(
        [
            _test(locale="fr", outcome="passed"),
            _test(locale="nl", outcome="failed"),
        ]
    )
    model = _build(manifest)
    assert model.scenarios[0].outcome == "failed"
    assert model.scenarios[0].outcomes == {"passed": 1, "failed": 1}
    assert model.totals.verdict == "failed"
    assert model.totals.exit_code == 1


def test_a_green_run_has_exit_code_zero_and_a_passed_verdict():
    model = _build(_manifest([_test(), _test(locale="nl")]))
    assert (model.totals.verdict, model.totals.exit_code) == ("passed", 0)
    assert model.totals.scenarios_not_passed == 0


def test_skipped_does_not_fail_the_run():
    model = _build(_manifest([_test(outcome="skipped")]))
    assert model.totals.exit_code == 0


def test_durations_are_summed_and_named_for_what_they_are():
    """Under --workers N this is not wall clock, and a bundle carries no
    finished_at to compute the real elapsed time from."""
    model = _build(_manifest([_test(duration_ms=1000), _test(locale="nl", duration_ms=2500)]))
    assert model.totals.duration_ms_sum == 3500


# ── dimensions ──────────────────────────────────────────────────────────────


def test_dimensions_come_from_observed_rows_not_from_the_run_spec():
    """A scenario deselected by a disjoint frontmatter ``layouts:`` never ran, so a
    matrix built from the requested set grows an empty column that reads like a
    failure."""
    manifest = _manifest([_test(locale="fr", layout="mobile")])
    manifest.run.spec.locales = ["fr", "nl", "de"]
    manifest.run.spec.layouts = ["desktop", "mobile"]
    model = _build(manifest)
    assert model.dimensions == {"locales": ["fr"], "layouts": ["mobile"]}


def test_dimensions_are_empty_when_nothing_was_parametrised():
    model = _build(_manifest([_test(locale=None, layout=None)]))
    assert model.dimensions == {"locales": [], "layouts": []}


# ── failure grouping ────────────────────────────────────────────────────────


def test_one_cause_across_many_scenarios_collapses_to_one_row():
    """The primitive that makes this readable where a JUnit report is not."""
    tail = "E   TimeoutError: locator.click: Timeout 30000ms exceeded\nE   waiting for [data-testid='save']"
    tests = [
        _test(scenario=f"flow-{i}", outcome="failed", fail_tail_b64=_tail_b64(tail + f"\n  at line {i}"))
        for i in range(5)
    ]
    model = _build(_manifest(tests))
    assert len(model.failure_causes) == 1
    cause = model.failure_causes[0]
    assert cause.count == 5
    assert len(cause.scenarios) == 5
    assert "Timeout" in cause.headline
    assert cause.exemplar_tail  # the full tail of the first only


def test_two_different_selectors_stay_two_causes():
    """Normalisation is aggressive on purpose, but a false merge would hide a
    second bug behind the first."""
    a = "E   TimeoutError: waiting for [data-testid='save']"
    b = "E   TimeoutError: waiting for [data-testid='cancel']"
    model = _build(
        _manifest(
            [
                _test(scenario="one", outcome="failed", fail_tail_b64=_tail_b64(a)),
                _test(scenario="two", outcome="failed", fail_tail_b64=_tail_b64(b)),
            ]
        )
    )
    assert len(model.failure_causes) == 2


def test_each_variant_links_to_its_cause():
    tail = "E   AssertionError: nope"
    model = _build(_manifest([_test(outcome="failed", fail_tail_b64=_tail_b64(tail))]))
    variant = model.scenarios[0].variants[0]
    assert variant.fingerprint
    assert variant.fingerprint == model.failure_causes[0].fingerprint


def test_a_failure_with_no_tail_still_groups():
    model = _build(_manifest([_test(outcome="failed")]))
    assert model.failure_causes[0].fingerprint == "unknown"
    assert "no failure detail" in model.failure_causes[0].headline


def test_passing_tests_produce_no_causes():
    assert _build(_manifest([_test()])).failure_causes == []


def test_a_corrupt_fail_tail_does_not_lose_the_row():
    model = _build(_manifest([_test(outcome="failed", fail_tail_b64="!!!not base64!!!")]))
    assert model.scenarios[0].variants[0].outcome == "failed"
    assert model.scenarios[0].variants[0].fail_tail == ""


def test_group_manifest_failures_returns_the_nodeid_map():
    tests = [_test(outcome="failed", fail_tail_b64=_tail_b64("E   boom"))]
    causes, prints = group_manifest_failures(tests)
    assert list(prints) == [tests[0].nodeid]
    assert prints[tests[0].nodeid] == causes[0].fingerprint


# ── the timeline ────────────────────────────────────────────────────────────


def _events(nodeid, steps, outcome="passed"):
    base = [{"kind": "test-start", "ts": 100.0, "nodeid": nodeid, "scenario": "order-lifecycle"}]
    ts = 100.0
    for number, title, action, closed in steps:
        base.append(
            {"kind": "step-start", "ts": ts, "nodeid": nodeid, "step": number, "title": title, "action": action}
        )
        ts += 1.0
        if closed:
            base.append({"kind": "step-end", "ts": ts, "nodeid": nodeid, "step": number})
    base.append({"kind": "test-end", "ts": ts, "nodeid": nodeid, "outcome": outcome, "duration_ms": 2000})
    return base


def test_the_timeline_is_attached_and_the_failing_step_is_titled():
    test = _test(outcome="failed", failed_step=7)
    events = _events(
        test.nodeid,
        [(1, "Open the dashboard", "navigate", True), (7, "Submit the order", "click", False)],
        outcome="failed",
    )
    model = _build(_manifest([test]), attempts=steps_from_records(events))
    variant = model.scenarios[0].variants[0]
    assert [s.step for s in variant.steps] == [1, 7]
    assert variant.steps[1].status == "running"
    assert variant.failed_step_title == "Submit the order"
    assert model.capabilities.step_timeline is True


def test_no_step_events_reads_as_a_missing_capability_not_an_empty_timeline():
    """``STEPPY_STEP_EVENTS=0``, or a tree generated before the markers existed.
    Without ``capabilities`` a consumer cannot tell that from "nothing happened"."""
    model = _build(_manifest([_test()]))
    assert model.capabilities.step_timeline is False
    assert model.scenarios[0].variants[0].steps == []


def test_two_in_process_attempts_are_reported():
    test = _test(outcome="passed")
    events = _events(test.nodeid, [(1, "Click", "click", False)], outcome="failed")
    events += _events(test.nodeid, [(1, "Click", "click", True)], outcome="passed")
    model = _build(_manifest([test]), attempts=steps_from_records(events))
    variant = model.scenarios[0].variants[0]
    assert variant.attempts == 2
    assert variant.attempt_outcomes == ["failed", "passed"]
    assert model.capabilities.attempts is True


def test_the_last_attempt_is_the_one_rendered():
    test = _test(outcome="passed")
    events = _events(test.nodeid, [(1, "Click", "click", False)], outcome="failed")
    events += _events(test.nodeid, [(1, "Click", "click", True), (2, "Verify", "assert", True)], outcome="passed")
    model = _build(_manifest([test]), attempts=steps_from_records(events))
    assert [s.step for s in model.scenarios[0].variants[0].steps] == [1, 2]


# ── assets and artifacts ────────────────────────────────────────────────────


def test_step_screenshots_are_joined_through_manifest_attribution():
    """Never by parsing a path: ``classify`` recorded scenario/locale/layout/step
    at create time precisely so readers do not have to."""
    test = _test()
    files = [
        _file(
            "output/screenshots/order-lifecycle/fr/mobile/step-01.png",
            "screenshot",
            scenario="order-lifecycle",
            locale="fr",
            layout="mobile",
            step=1,
        ),
        _file(
            "output/screenshots/order-lifecycle/fr/mobile/step-01.json",
            "sidecar",
            scenario="order-lifecycle",
            locale="fr",
            layout="mobile",
            step=1,
        ),
    ]
    assets = {
        "output/screenshots/order-lifecycle/fr/mobile/step-01.png": "assets/order-lifecycle/fr-mobile/step-01.png"
    }
    events = _events(test.nodeid, [(1, "Open", "navigate", True)])
    model = _build(_manifest([test], files), attempts=steps_from_records(events), assets=assets)
    assert model.scenarios[0].variants[0].steps[0].screenshots == ["assets/order-lifecycle/fr-mobile/step-01.png"]


def test_multi_frame_captures_stay_in_frame_order():
    test = _test()
    files = [
        _file(
            f"output/screenshots/order-lifecycle/fr/mobile/step-03-{f}.png",
            "screenshot",
            scenario="order-lifecycle",
            locale="fr",
            layout="mobile",
            step=3,
            frame=f,
        )
        for f in ("b", "a")
    ]
    assets = {f.path: f"assets/{f.frame}.png" for f in files}
    events = _events(test.nodeid, [(3, "Fill", "fill-form", True)])
    model = _build(_manifest([test], files), attempts=steps_from_records(events), assets=assets)
    assert model.scenarios[0].variants[0].steps[0].screenshots == ["assets/a.png", "assets/b.png"]


def test_a_step_whose_capture_was_not_copied_simply_has_none():
    """`--screenshots failed` is a policy, not an error."""
    test = _test()
    files = [
        _file(
            "output/screenshots/order-lifecycle/fr/mobile/step-01.png",
            "screenshot",
            scenario="order-lifecycle",
            locale="fr",
            layout="mobile",
            step=1,
        )
    ]
    events = _events(test.nodeid, [(1, "Open", "navigate", True)])
    model = _build(_manifest([test], files), attempts=steps_from_records(events), assets={})
    assert model.scenarios[0].variants[0].steps[0].screenshots == []


def test_the_failure_screenshot_is_matched_without_reversing_a_sanitised_nodeid():
    test = _test(outcome="failed")
    files = [
        _file(
            "output/screenshots/_failures/x__editor_page.png",
            "failure_screenshot",
            scenario="order-lifecycle",
            locale="fr",
            layout="mobile",
        )
    ]
    model = _build(_manifest([test], files), assets={files[0].path: "assets/_failures/x.png"})
    assert model.scenarios[0].variants[0].failure_screenshot == "assets/_failures/x.png"


def test_traces_and_videos_are_named_but_not_copied():
    """A trace needs ``playwright show-trace`` to open, so a link would be a dead
    end; the honest thing is to say where it is."""
    test = _test(outcome="failed")
    files = [
        _file("output/traces/x.zip", "trace", scenario="order-lifecycle", locale="fr", layout="mobile"),
        _file("output/videos/x.webm", "video", scenario="order-lifecycle", locale="fr", layout="mobile"),
    ]
    variant = _build(_manifest([test], files)).scenarios[0].variants[0]
    assert variant.trace_path == "output/traces/x.zip"
    assert variant.video_path == "output/videos/x.webm"


def test_the_failure_log_is_scenario_level_because_that_is_all_the_bundle_knows():
    test = _test(outcome="failed")
    files = [_file("output/failures/order-lifecycle/pytest.log", "failure_log", scenario="order-lifecycle")]
    assert _build(_manifest([test], files)).scenarios[0].failure_log == "output/failures/order-lifecycle/pytest.log"


def test_artifacts_are_inventoried_by_kind_not_duplicated():
    """The manifest is the file list; copying it in would double every report."""
    files = [
        _file("output/screenshots/a/step-01.png", "screenshot", scenario="a", step=1),
        _file("output/screenshots/a/step-02.png", "screenshot", scenario="a", step=2),
        _file("output/traces/x.zip", "trace"),
    ]
    model = _build(_manifest([], files))
    assert model.artifacts.files == 3
    assert model.artifacts.by_kind["screenshot"].count == 2
    assert model.artifacts.by_kind["trace"].count == 1
    assert "files" not in model.model_dump()  # no copy of manifest.files[]


# ── honest degradation ──────────────────────────────────────────────────────


def test_a_run_made_without_bundle_still_lists_its_scenarios():
    """No test rows at all — the report must show what was captured rather than
    render an empty page."""
    files = [_file("output/screenshots/onboarding/step-01.png", "screenshot", scenario="onboarding", step=1)]
    model = _build(_manifest([], files))
    assert [s.slug for s in model.scenarios] == ["onboarding"]
    assert model.scenarios[0].outcome == "unknown"
    assert model.totals.tests == 0


def test_a_run_with_no_outcomes_is_unknown_not_passed():
    """Reporting PASSED for a run that recorded nothing is a claim with no
    evidence behind it — and it reads as a green build to anyone skimming."""
    files = [_file("output/screenshots/onboarding/step-01.png", "screenshot", scenario="onboarding", step=1)]
    totals = _build(_manifest([], files)).totals
    assert totals.verdict == "unknown"
    # Still 0: nothing failed, and the run itself already reported its own verdict.
    assert totals.exit_code == 0


def test_manifest_warnings_are_carried_through_and_extended():
    manifest = _manifest([_test()], warnings=["size budget: dropped 3 video file(s)"])
    model = _build(manifest, extra_warnings=["coverage: no declared surface"])
    assert model.warnings == ["size budget: dropped 3 video file(s)", "coverage: no declared surface"]


def test_truncated_is_carried_through():
    assert _build(_manifest([_test()], truncated=True)).truncated is True


def test_strength_is_unreliable_exactly_when_coverage_was_rebuilt_from_records():
    """A bundle carries no ``scenarios/*.md``, so ``count_scenario_asserts``
    returns 0 and every covered node would read "weak" — a plausible-looking lie."""
    assert _build(_manifest(), coverage=ReportCoverage(tier="records")).capabilities.strength_reliable is False
    assert _build(_manifest(), coverage=ReportCoverage(tier="bundled")).capabilities.strength_reliable is True


# ── determinism ─────────────────────────────────────────────────────────────


def test_the_same_manifest_renders_byte_identical_json():
    """What lets CI diff two reports, and what a render-time clock would break."""
    manifest = _manifest([_test(), _test(locale="nl", outcome="failed", fail_tail_b64=_tail_b64("E boom"))])
    assert report_bytes(_build(manifest)) == report_bytes(_build(manifest))


def test_scenarios_and_variants_are_ordered_deterministically():
    tests = [
        _test(scenario="zeta", locale="nl", layout="desktop"),
        _test(scenario="alpha", locale="fr", layout="mobile"),
        _test(scenario="alpha", locale="fr", layout="desktop"),
    ]
    model = _build(_manifest(tests))
    assert [s.slug for s in model.scenarios] == ["alpha", "zeta"]
    assert [(v.locale, v.layout) for v in model.scenarios[0].variants] == [("fr", "desktop"), ("fr", "mobile")]


def test_the_model_never_imports_jinja2():
    """``report/model.py`` is what a control plane imports. Pulling a renderer in
    would defeat the whole model/render split."""
    import sys

    for name in [m for m in sys.modules if m.startswith(("steppy.report", "jinja2"))]:
        del sys.modules[name]
    import steppy.report.model  # noqa: F401

    assert not [m for m in sys.modules if m.startswith("jinja2")]
