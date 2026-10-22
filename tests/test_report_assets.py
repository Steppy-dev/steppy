"""Which captures travel into the site, where they land, and what cannot escape.

The sanitisation tests are the security-shaped ones: destinations are computed
from validated components precisely because a downloaded bundle's manifest path is
attacker-controlled and streaming tar members skips Python's extraction filters.
"""

from pathlib import Path

import pytest

from steppy.contracts.bundle import BundleFile, BundleManifest, BundleRun, BundleTest
from steppy.report.assets import WARN_BYTES, plan_assets, sanitise, variant_dirname

RUN = BundleRun(run_id="r1", created_at="2026-08-14T10:00:00Z", steppy_version="0.3.0")
SITE = Path("/site")


def _shot(scenario, locale, layout, step, *, frame=None, digest=None, size=1000, kind="screenshot"):
    parts = [p for p in (scenario, locale, layout) if p]
    stem = f"step-{step:02d}" + (f"-{frame}" if frame else "")
    return BundleFile(
        path="output/screenshots/" + "/".join(parts) + f"/{stem}.png",
        kind=kind,
        sha256=digest or f"{scenario}{locale}{layout}{step}{frame}".ljust(64, "0")[:64],
        size=size,
        scenario=scenario,
        locale=locale,
        layout=layout,
        step=step,
        frame=frame,
    )


def _failshot(scenario, locale, layout, *, digest=None):
    return BundleFile(
        path=f"output/screenshots/_failures/{scenario}__editor_page.png",
        kind="failure_screenshot",
        sha256=digest or ("f" * 64),
        size=500,
        scenario=scenario,
        locale=locale,
        layout=layout,
    )


def _test(scenario, locale, layout, outcome):
    return BundleTest(
        scenario=scenario,
        nodeid=f"tests/e2e/test_{scenario}.py::test_flow[{locale}-{layout}]",
        outcome=outcome,
        locale=locale,
        layout=layout,
    )


def _manifest(files, tests=()):
    return BundleManifest(steppy_bundle=1, run=RUN, tests=list(tests), files=list(files))


# ── sanitisation ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("order-lifecycle", "order-lifecycle"),
        ("..", "_"),
        ("../../etc/passwd", "etc-passwd"),
        ("a/b", "a-b"),
        ("C:\\windows", "C-windows"),
        ("", "_"),
        ("  ", "_"),
        (".hidden", "hidden"),
        ("fr-BE", "fr-BE"),
    ],
)
def test_a_component_cannot_carry_path_syntax(raw, expected):
    assert sanitise(raw) == expected


def test_a_null_byte_does_not_survive():
    assert "\x00" not in sanitise("a\x00b")


@pytest.mark.parametrize(
    "locale,layout,expected",
    [("fr", "mobile", "fr-mobile"), ("fr", None, "fr-_"), (None, None, "_-_"), (None, "desktop", "_-desktop")],
)
def test_an_unset_dimension_gets_a_placeholder_rather_than_merging(locale, layout, expected):
    assert variant_dirname(locale, layout) == expected


def test_a_hostile_scenario_slug_stays_inside_the_site():
    plan = plan_assets(
        _manifest([_shot("../../evil", "fr", "mobile", 1)]),
        site_dir=SITE,
        policy="all",
    )
    dest = next(iter(plan.into.values()))
    assert SITE in dest.parents
    assert ".." not in dest.parts


# ── the policy ──────────────────────────────────────────────────────────────


def test_failed_copies_only_the_variants_that_did_not_pass():
    files = [
        _shot("flow", "fr", "mobile", 1),
        _shot("flow", "nl", "mobile", 1),
    ]
    tests = [_test("flow", "fr", "mobile", "passed"), _test("flow", "nl", "mobile", "failed")]
    plan = plan_assets(_manifest(files, tests), site_dir=SITE, policy="failed")
    assert set(plan.urls) == {files[1].path}


def test_failed_still_copies_every_failure_screenshot():
    """The single most useful artifact in a red run, and at most one per variant."""
    files = [_shot("flow", "fr", "mobile", 1), _failshot("flow", "fr", "mobile")]
    plan = plan_assets(_manifest(files, [_test("flow", "fr", "mobile", "passed")]), site_dir=SITE, policy="failed")
    assert list(plan.urls) == [files[1].path]


def test_all_copies_every_step_capture():
    files = [_shot("flow", "fr", "mobile", 1), _shot("flow", "nl", "mobile", 1)]
    plan = plan_assets(_manifest(files, [_test("flow", "fr", "mobile", "passed")]), site_dir=SITE, policy="all")
    assert len(plan.urls) == 2


def test_none_copies_nothing_at_all():
    files = [_shot("flow", "fr", "mobile", 1), _failshot("flow", "fr", "mobile")]
    plan = plan_assets(_manifest(files), site_dir=SITE, policy="none")
    assert plan.urls == {} and plan.into == {} and plan.bytes == 0


def test_an_unknown_policy_is_rejected():
    with pytest.raises(ValueError, match="screenshot policy"):
        plan_assets(_manifest([]), site_dir=SITE, policy="some")


def test_a_run_with_no_test_rows_copies_no_step_captures_under_failed():
    """No outcomes recorded means no variant is known to have failed. Shipping the
    whole corpus "just in case" is the wrong default for a Pages deploy."""
    plan = plan_assets(_manifest([_shot("flow", "fr", "mobile", 1)]), site_dir=SITE, policy="failed")
    assert plan.urls == {}


def test_an_error_outcome_counts_as_not_passed():
    files = [_shot("flow", "fr", "mobile", 1)]
    plan = plan_assets(_manifest(files, [_test("flow", "fr", "mobile", "error")]), site_dir=SITE, policy="failed")
    assert list(plan.urls) == [files[0].path]


def test_skipped_counts_as_passed_for_copying_purposes():
    files = [_shot("flow", "fr", "mobile", 1)]
    plan = plan_assets(_manifest(files, [_test("flow", "fr", "mobile", "skipped")]), site_dir=SITE, policy="failed")
    assert plan.urls == {}


# ── naming and dedupe ───────────────────────────────────────────────────────


def test_the_destination_shape_is_fixed_and_readable():
    plan = plan_assets(_manifest([_shot("order-lifecycle", "fr", "mobile", 3)]), site_dir=SITE, policy="all")
    assert list(plan.urls.values()) == ["assets/order-lifecycle/fr-mobile/step-03.png"]


def test_a_multi_frame_capture_keeps_its_frame_letter():
    files = [_shot("flow", "fr", "mobile", 3, frame="a"), _shot("flow", "fr", "mobile", 3, frame="b")]
    plan = plan_assets(_manifest(files), site_dir=SITE, policy="all")
    assert sorted(plan.urls.values()) == [
        "assets/flow/fr-mobile/step-03-a.png",
        "assets/flow/fr-mobile/step-03-b.png",
    ]


def test_byte_identical_captures_across_layouts_are_copied_once():
    """On a guide corpus only a handful of steps differ per layout, so this is most
    of the payload."""
    shared = "d" * 64
    files = [
        _shot("flow", "fr", "desktop", 1, digest=shared),
        _shot("flow", "fr", "mobile", 1, digest=shared),
    ]
    plan = plan_assets(_manifest(files), site_dir=SITE, policy="all")
    assert plan.copies == 1
    assert len(plan.urls) == 2
    assert len(set(plan.urls.values())) == 1
    assert plan.bytes == 1000  # counted once


def test_dedupe_picks_the_same_winner_every_time():
    """Two renders of one bundle must produce the same URLs or the report stops
    being diffable."""
    shared = "d" * 64
    files = [
        _shot("flow", "fr", "mobile", 1, digest=shared),
        _shot("flow", "fr", "desktop", 1, digest=shared),
    ]
    a = plan_assets(_manifest(files), site_dir=SITE, policy="all")
    b = plan_assets(_manifest(list(reversed(files))), site_dir=SITE, policy="all")
    assert a.urls == b.urls


def test_two_different_failure_captures_for_one_variant_both_survive():
    """The fixture name is already gone from the manifest, so without the digest
    suffix the second would silently overwrite the first."""
    files = [
        _failshot("flow", "fr", "mobile", digest="a" * 64),
        _failshot("flow", "fr", "mobile", digest="b" * 64),
    ]
    files[1].path = "output/screenshots/_failures/flow__reviewer_page.png"
    plan = plan_assets(_manifest(files), site_dir=SITE, policy="all")
    assert len(set(plan.urls.values())) == 2


def test_a_capture_with_no_step_number_is_skipped_rather_than_guessed():
    entry = _shot("flow", "fr", "mobile", 1)
    entry.step = None
    plan = plan_assets(_manifest([entry]), site_dir=SITE, policy="all")
    assert plan.urls == {}


def test_a_weird_extension_falls_back_to_png():
    entry = _shot("flow", "fr", "mobile", 1)
    entry.path = "output/screenshots/flow/fr/mobile/step-01.png.exe.verylongextension"
    plan = plan_assets(_manifest([entry]), site_dir=SITE, policy="all")
    assert list(plan.urls.values())[0].endswith(".png")


# ── size warning ────────────────────────────────────────────────────────────


def test_a_large_copy_set_warns_about_the_pages_limit():
    big = _shot("flow", "fr", "mobile", 1, size=WARN_BYTES + 1)
    plan = plan_assets(_manifest([big]), site_dir=SITE, policy="all")
    assert any("Pages caps" in w for w in plan.warnings)


def test_a_small_copy_set_says_nothing():
    plan = plan_assets(_manifest([_shot("flow", "fr", "mobile", 1)]), site_dir=SITE, policy="all")
    assert plan.warnings == []
