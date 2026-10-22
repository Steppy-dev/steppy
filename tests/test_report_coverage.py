"""The four coverage tiers, and the two lies they are built to avoid.

The store lives outside ``output/`` and the declared surface comes off the network,
so a downloaded bundle has raw records and nothing to score them against. Tier
order is about trustworthiness, and two of the tests here exist because the wrong
answer would look entirely plausible: reading *this* machine's map to describe
*someone else's* run, and reporting assert strength that was computed with no
scenario files to count asserts in.
"""

import json
from pathlib import Path

import pytest

from steppy.bundle import create_bundle
from steppy.report.coverage_source import (
    STRENGTH_UNKNOWN_WARNING,
    TIER_BUNDLED,
    TIER_NONE,
    TIER_RECORDS,
    TIER_STORE,
    resolve_coverage,
)
from steppy.report.source import open_source

from .test_bundle import build_project

_GAPS = {
    "counts": {
        "endpoints": {
            "declared": 10,
            "tested": 6,
            "tested_strong": 5,
            "declared_unreached": 4,
            "declared_unreached_actionable": 3,
            "discovered_uncovered": 0,
            "discovered_tested": 0,
            "tested_non_ui": 0,
            "declared_non_ui": 1,
            "unclassified": 0,
        },
        "routes": {
            "declared": 0,
            "tested": 0,
            "tested_strong": 0,
            "declared_unreached": 0,
            "declared_unreached_actionable": 0,
            "discovered_uncovered": 0,
            "discovered_tested": 0,
            "tested_non_ui": 0,
            "declared_non_ui": 0,
        },
        "modules": {"covered": 0},
        "scenarios": 2,
    },
    "e2e_surface": {
        "all": {"surface": "all", "tested": 6, "declared": 9, "ratio": 0.6667, "percent": 67},
        "endpoints": {"surface": "endpoints", "tested": 6, "declared": 9, "ratio": 0.6667, "percent": 67},
        "routes": {"surface": "routes", "tested": 0, "declared": 0, "ratio": None, "percent": None},
    },
    "endpoints": {
        "tested": [{"api": "core", "method": "GET", "key": "/orders", "triage": "unknown", "triage_source": ""}],
        "declared_unreached": [
            {"api": "core", "method": "GET", "key": "/health", "triage": "non_ui", "triage_source": "config"},
        ],
        "discovered_uncovered": [],
        "discovered_tested": [],
        "unclassified": [],
    },
    "routes": {"tested": [], "declared_unreached": [], "discovered_uncovered": [], "discovered_tested": []},
    "modules": {"covered": []},
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """The shared bundle fixture, minus its gap report.

    ``build_project`` ships an ``output/coverage/gaps.json`` (that is what proves
    the classify fix), which would put every test here on tier 1. Each test below
    puts back exactly the inputs its tier needs, so the ordering is what is being
    asserted rather than what the fixture happens to contain.
    """
    root = build_project(tmp_path)
    (root / "output" / "coverage" / "gaps.json").unlink()
    return root


def _with_gaps(project: Path, gaps=None) -> Path:
    (project / "output" / "coverage" / "gaps.json").write_text(json.dumps(gaps or _GAPS), encoding="utf-8")
    return project


def _without_records(project: Path) -> Path:
    import shutil

    shutil.rmtree(project / "output" / "coverage" / "raw")
    return project


def _source(project: Path, *, archive=False):
    if not archive:
        return open_source(None, project_root=project)
    return open_source(create_bundle(project_root=project, fmt="gz").path, project_root=project)


# ── tier 1: the gap report travelled with the run ───────────────────────────


def test_a_bundled_gaps_json_is_the_best_source(project: Path):
    view = resolve_coverage(_source(_with_gaps(project), archive=True), project_root=project)
    assert view.tier == TIER_BUNDLED
    assert view.badge.percent == 67
    assert view.badge.tested == 6 and view.badge.declared == 9
    assert view.counts["endpoints"]["declared"] == 10


def test_the_badge_colour_comes_from_the_shared_palette(project: Path):
    view = resolve_coverage(_source(_with_gaps(project), archive=True), project_root=project)
    assert view.badge.color == "orange"  # 67% → the shared threshold table


def test_the_badge_surface_is_selectable(project: Path):
    view = resolve_coverage(_source(_with_gaps(project), archive=True), project_root=project, surface="routes")
    assert view.badge.surface == "routes"
    assert view.badge.percent is None  # nothing declared → not measured
    assert view.badge.color == "lightgrey"


def test_non_ui_is_summarised_with_its_source(project: Path):
    """So a reader can see a percentage was raised by committed config rather than
    by a flag someone set locally and never shared."""
    view = resolve_coverage(_source(_with_gaps(project), archive=True), project_root=project)
    assert view.non_ui.declared == 1
    assert view.non_ui.by_source == {"config": 1}


def test_an_unreadable_bundled_gaps_json_degrades_rather_than_raising(project: Path):
    (project / "output" / "coverage" / "gaps.json").write_text("{not json", encoding="utf-8")
    view = resolve_coverage(_source(project, archive=True), project_root=project)
    assert view.tier != TIER_BUNDLED
    assert any("not readable JSON" in w for w in view.warnings)


# ── tier 2: the project's own store, live tree only ─────────────────────────


def test_the_live_store_is_used_for_a_live_tree(project: Path):
    _seed_store(project)
    view = resolve_coverage(_source(project), project_root=project)
    assert view.tier == TIER_STORE
    assert view.counts["endpoints"]["tested"] == 1


def test_an_archive_input_never_opens_the_local_store(project: Path):
    """Reading this machine's map to describe someone else's run would print a
    confident percentage about the wrong thing. The raw records are removed too,
    so the only thing that could produce a tier here is the forbidden one."""
    _seed_store(project)
    source = _source(_without_records(project), archive=True)
    view = resolve_coverage(source, project_root=project)
    assert view.tier == TIER_NONE
    assert view.counts == {}


def test_no_store_and_no_records_is_simply_no_coverage_section(project: Path):
    import shutil

    shutil.rmtree(project / "output" / "coverage")
    view = resolve_coverage(_source(project), project_root=project)
    assert view.tier == TIER_NONE
    assert view.badge.percent is None


# ── tier 3: rebuilt from raw records ────────────────────────────────────────


def test_raw_records_rebuild_a_map_when_nothing_better_exists(project: Path):
    view = resolve_coverage(_source(project, archive=True), project_root=project)
    assert view.tier == TIER_RECORDS


def test_the_records_tier_refuses_to_report_assert_strength(project: Path):
    """``count_scenario_asserts`` reads scenarios/*.md, which a bundle does not
    carry — so it returns 0 and every tested endpoint would read "weak"."""
    view = resolve_coverage(_source(project, archive=True), project_root=project)
    assert view.tier == TIER_RECORDS
    assert STRENGTH_UNKNOWN_WARNING in view.warnings


def test_a_bundled_gaps_json_wins_over_the_records(project: Path):
    view = resolve_coverage(_source(_with_gaps(project), archive=True), project_root=project)
    assert view.tier == TIER_BUNDLED
    assert STRENGTH_UNKNOWN_WARNING not in view.warnings


def test_records_excluded_from_the_bundle_means_no_coverage(project: Path):
    archive = create_bundle(project_root=project, fmt="gz", coverage=False).path
    view = resolve_coverage(open_source(archive, project_root=project), project_root=project)
    assert view.tier == TIER_NONE


# ── never fatal ─────────────────────────────────────────────────────────────


def test_a_broken_config_does_not_stop_the_report(project: Path):
    (project / "config.yaml").write_text("apps: [not a mapping\n", encoding="utf-8")
    view = resolve_coverage(_source(project, archive=True), project_root=project)
    assert view.tier in (TIER_RECORDS, TIER_NONE)  # degraded, not raised


def test_a_corrupt_store_warns_and_falls_through(project: Path):
    store = project / ".steppy" / "coverage.db"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(b"definitely not sqlite")
    view = resolve_coverage(_source(_without_records(project)), project_root=project)
    assert view.tier == TIER_NONE
    assert any("could not be read" in w for w in view.warnings)


def _seed_store(project: Path) -> None:
    """A minimal real map: one declared endpoint, covered by one scenario."""
    from steppy.coverage.store import (
        Contribution,
        DeclaredEntry,
        open_store,
        replace_declared_source,
        replace_scenario_contribution,
    )

    path = project / ".steppy" / "coverage.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_store(path)
    try:
        replace_declared_source(
            conn,
            "openapi:core",
            [DeclaredEntry(kind="endpoint", key="/orders", api="core", method="GET")],
        )
        replace_scenario_contribution(
            conn,
            "order-lifecycle",
            [Contribution(kind="endpoint", key="/orders", api="core", method="GET", asserts_enabled=True)],
            asserts_count=1,
        )
    finally:
        conn.close()
