"""``coverage.non_ui``: committed triage, and the badge denominator it fixes.

Why this exists at all is the thing worth pinning. The ``ui_testable | non_ui |
unknown`` flags live in the ``triage`` table inside ``.steppy/coverage.db``, and
``.steppy/`` is gitignored — so a fresh CI checkout sees every declared endpoint
as ``unknown``, and health checks / webhook receivers / OAuth callbacks land in
the coverage denominator. ``coverage.non_ui`` moves the declaration into a
committed file; these tests pin the grammar, the precedence order, and the
arithmetic that keeps the percentage in range.
"""

import json

import pytest
from pydantic import ValidationError

from steppy.coverage.gaps import compute_gaps, surface_ratio
from steppy.coverage.report import badge_payload, badge_to_json
from steppy.coverage.triage import (
    SOURCE_CONFIG,
    SOURCE_STORE,
    apply_config_triage,
    matches,
    split_pattern,
    unmatched_patterns,
)
from steppy.models import CoverageConfig


def _node(kind="endpoint", *, api="core", method="GET", key="/orders", declared=True, covered=(), triage=None):
    """One ``read_map`` node. ``covered`` is a list of (scenario_id, asserts_enabled)."""
    return {
        "kind": kind,
        "api": api,
        "method": method,
        "key": key,
        "declared": [{"source": "openapi", "details": {}}] if declared else [],
        "scenarios": [{"scenario_id": sid, "evidence": {}, "asserts_enabled": asserts} for sid, asserts in covered],
        "triage": triage,
    }


def _map(nodes, scenarios=None):
    return {"nodes": nodes, "scenarios": scenarios or {}}


# ── pattern grammar ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pattern,expected",
    [
        ("/health", (None, "/health")),
        ("GET /health", ("GET", "/health")),
        ("get /health", ("GET", "/health")),  # method is case-insensitive
        ("  POST  /webhooks/x  ", ("POST", "/webhooks/x")),
        ("/internal/*", (None, "/internal/*")),
    ],
)
def test_split_pattern(pattern, expected):
    assert split_pattern(pattern) == expected


def test_bare_glob_matches_any_method():
    assert matches(_node(method="GET", key="/health"), ["/health"]) == "/health"
    assert matches(_node(method="POST", key="/health"), ["/health"]) == "/health"


def test_method_qualified_pattern_matches_only_that_method():
    assert matches(_node(method="GET", key="/health"), ["GET /health"]) == "GET /health"
    assert matches(_node(method="POST", key="/health"), ["GET /health"]) is None


def test_method_pattern_never_matches_a_route():
    """Routes carry ``method == ""`` — an HTTP verb is meaningless for a page."""
    route = _node(kind="route", method="", key="/health")
    assert matches(route, ["GET /health"]) is None
    assert matches(route, ["/health"]) == "/health"


def test_star_crosses_slash():
    """Intentional: ``/internal/*`` is meant to cover the whole subtree."""
    assert matches(_node(key="/internal/jobs/run"), ["/internal/*"]) == "/internal/*"


def test_pattern_applies_across_every_api():
    """Matching is on ``key`` alone. Documented breadth, not an oversight."""
    assert matches(_node(api="core", key="/health"), ["/health"])
    assert matches(_node(api="billing", key="/health"), ["/health"])


def test_matches_returns_the_rule_not_a_bool():
    """So a report can say *which* rule discounted an endpoint."""
    assert matches(_node(key="/webhooks/stripe"), ["/health", "/webhooks/*"]) == "/webhooks/*"


# ── precedence ──────────────────────────────────────────────────────────────


def test_config_pattern_flags_non_ui_with_its_source():
    view = apply_config_triage(_map([_node(key="/health")]), ["/health"])
    triage = view["nodes"][0]["triage"]
    assert triage["flag"] == "non_ui"
    assert triage["source"] == SOURCE_CONFIG
    assert "/health" in triage["note"]


def test_explicit_ui_testable_in_the_store_beats_a_glob():
    """Someone typed that about this one node; a pattern is a blanket guess."""
    node = _node(key="/health", triage={"flag": "ui_testable", "note": "reachable from the footer"})
    view = apply_config_triage(_map([node]), ["/health"])
    triage = view["nodes"][0]["triage"]
    assert triage["flag"] == "ui_testable"
    assert triage["source"] == SOURCE_STORE


def test_stored_unknown_does_not_beat_a_glob():
    """``unknown`` and "no row" already render identically, so it carries no
    information a committed pattern should have to lose to."""
    node = _node(key="/health", triage={"flag": "unknown", "note": ""})
    view = apply_config_triage(_map([node]), ["/health"])
    assert view["nodes"][0]["triage"]["flag"] == "non_ui"
    assert view["nodes"][0]["triage"]["source"] == SOURCE_CONFIG


def test_store_non_ui_survives_with_no_patterns():
    node = _node(key="/webhooks/x", triage={"flag": "non_ui", "note": "payment callback"})
    view = apply_config_triage(_map([node]), [])
    assert view["nodes"][0]["triage"] == {"flag": "non_ui", "note": "payment callback", "source": SOURCE_STORE}


def test_untriaged_node_stays_none():
    view = apply_config_triage(_map([_node(key="/orders")]), ["/health"])
    assert view["nodes"][0]["triage"] is None


def test_overlay_does_not_mutate_the_input():
    """Callers hand the same map to more than one consumer."""
    original = _map([_node(key="/health")])
    apply_config_triage(original, ["/health"])
    assert original["nodes"][0]["triage"] is None


def test_overlay_never_writes_to_the_store(tmp_path):
    """The invariant merge/refresh rely on: triage rows are only ever written by
    an explicit ``steppy coverage triage set``."""
    from steppy.coverage.store import dump_canonical, ensure_node, open_store

    conn = open_store(tmp_path / "coverage.db")
    try:
        ensure_node(conn, kind="endpoint", api="core", method="GET", key="/health")
        before = json.dumps(dump_canonical(conn), sort_keys=True)
        from steppy.coverage.store import read_map

        apply_config_triage(read_map(conn), ["/health"])
        assert json.dumps(dump_canonical(conn), sort_keys=True) == before
    finally:
        conn.close()


# ── stale patterns ──────────────────────────────────────────────────────────


def test_unmatched_patterns_are_reported():
    """A renamed endpoint leaves a rule that silently inflates the percentage."""
    view = _map([_node(key="/health")])
    assert unmatched_patterns(view, ["/health", "/gone/*"]) == ["/gone/*"]


def test_no_patterns_means_nothing_stale():
    assert unmatched_patterns(_map([_node()]), []) == []


# ── validation ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "   ", "GET ", "GET"])
def test_validator_rejects_patterns_that_cannot_mean_anything(bad):
    with pytest.raises(ValidationError):
        CoverageConfig(non_ui=[bad])


@pytest.mark.parametrize("good", ["/health", "GET /health", "/webhooks/*", "DELETE /admin/*"])
def test_validator_accepts_the_documented_grammar(good):
    assert CoverageConfig(non_ui=[good]).non_ui == [good]


def test_default_is_empty_so_the_field_is_inert_when_absent():
    assert CoverageConfig().non_ui == []


# ── the denominator ─────────────────────────────────────────────────────────


def _gaps_with(patterns, nodes, scenarios=None):
    return compute_gaps(apply_config_triage(_map(nodes, scenarios), patterns))


def test_non_ui_leaves_both_numerator_and_denominator():
    """A *tested* non_ui endpoint must leave both sides, or the ratio can exceed
    100% — which is what the formula as first written in the docs would do."""
    nodes = [
        _node(key="/orders", covered=[("s1", True)]),  # tested, actionable
        _node(key="/health", covered=[("s1", True)]),  # tested, but non_ui
        _node(key="/admin/jobs", covered=()),  # declared, unreached
    ]
    gaps = _gaps_with(["/health"], nodes, {"s1": {"asserts_count": 1}})
    ep = gaps["counts"]["endpoints"]
    assert ep["declared"] == 3
    assert ep["tested"] == 2
    assert ep["tested_non_ui"] == 1
    assert ep["declared_non_ui"] == 1
    ratio = gaps["e2e_surface"]["all"]
    assert (ratio["tested"], ratio["declared"]) == (1, 2)
    assert ratio["percent"] == 50


def test_ratio_never_exceeds_one_when_every_tested_node_is_non_ui():
    nodes = [_node(key="/health", covered=[("s1", True)])]
    ratio = _gaps_with(["/health"], nodes, {"s1": {"asserts_count": 1}})["e2e_surface"]["all"]
    assert ratio["percent"] is None  # nothing measurable is left
    assert ratio["declared"] == 0


def test_declared_unreached_actionable_still_discounts_non_ui():
    nodes = [_node(key="/webhooks/stripe", method="POST", covered=())]
    ep = _gaps_with(["/webhooks/*"], nodes)["counts"]["endpoints"]
    assert ep["declared_unreached"] == 1
    assert ep["declared_unreached_actionable"] == 0


def test_routes_and_endpoints_are_summed_for_the_all_surface():
    nodes = [
        _node(kind="endpoint", key="/orders", covered=[("s1", True)]),
        _node(kind="route", api="", method="", key="/orders", covered=()),
    ]
    gaps = _gaps_with([], nodes, {"s1": {"asserts_count": 1}})
    assert gaps["e2e_surface"]["all"]["declared"] == 2
    assert gaps["e2e_surface"]["endpoints"]["declared"] == 1
    assert gaps["e2e_surface"]["routes"]["declared"] == 1


def test_zero_denominator_reads_as_not_measured_not_as_zero_percent():
    ratio = surface_ratio({"endpoints": {}, "routes": {}}, "all")
    assert ratio["ratio"] is None and ratio["percent"] is None


def test_covered_entries_carry_their_triage_source():
    """The report and `coverage report` both need to say *why* something was
    discounted; a number nobody can trace is a number nobody trusts."""
    nodes = [_node(key="/health", covered=[("s1", True)])]
    tested = _gaps_with(["/health"], nodes, {"s1": {"asserts_count": 1}})["endpoints"]["tested"][0]
    assert tested["triage"] == "non_ui"
    assert tested["triage_source"] == SOURCE_CONFIG


# ── the badge ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "percent,color",
    [
        (0, "red"),
        (49, "red"),
        (50, "orange"),
        (69, "orange"),
        (70, "yellow"),
        (84, "yellow"),
        (85, "green"),
        (100, "brightgreen"),
    ],
)
def test_badge_colors(percent, color):
    gaps = {"e2e_surface": {"all": {"percent": percent}}}
    assert badge_payload(gaps)["color"] == color


def test_badge_is_a_shields_endpoint_object():
    gaps = {"e2e_surface": {"all": {"percent": 62}}}
    payload = badge_payload(gaps)
    assert payload == {"schemaVersion": 1, "label": "e2e surface", "message": "62%", "color": "orange"}


def test_badge_says_n_a_rather_than_zero_when_nothing_is_declared():
    """A badge reading 0% invites someone to fix a number that is not broken."""
    gaps = {"e2e_surface": {"all": {"percent": None}}}
    payload = badge_payload(gaps)
    assert payload["message"] == "n/a"
    assert payload["color"] == "lightgrey"


def test_badge_surface_and_label_are_selectable():
    gaps = {"e2e_surface": {"routes": {"percent": 90}}}
    payload = badge_payload(gaps, surface="routes", label="routes covered")
    assert payload["message"] == "90%" and payload["label"] == "routes covered"


def test_badge_json_is_deterministic_and_newline_terminated():
    gaps = {"e2e_surface": {"all": {"percent": 62}}}
    text = badge_to_json(gaps)
    assert text.endswith("\n")
    assert text == badge_to_json(gaps)
    assert json.loads(text)["schemaVersion"] == 1
