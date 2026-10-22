"""Tests for the selector brittleness lint (FLAWS §6.3)."""

import pytest
from textwrap import dedent

from steppy.parser import (
    lint_scenario_selectors,
    _lint_one_selector,
    _count_top_level_parts,
    parse_scenario_file,
)


# ── Unit: single-selector heuristics ─────────────────────────────────────────


def test_positional_pseudo_class_flagged():
    assert any("positional" in r for r in _lint_one_selector("ul li:nth-child(3)"))
    assert any("positional" in r for r in _lint_one_selector(".card:first-child"))


def test_text_coupled_flagged():
    assert any("text-coupled" in r for r in _lint_one_selector("button:has-text('Save')"))
    assert any("text-coupled" in r for r in _lint_one_selector("div >> text=Submit"))


def test_deep_chain_flagged():
    assert any("deep descendant" in r for r in _lint_one_selector("div section ul li a span"))


def test_clean_selectors_silent():
    assert _lint_one_selector('[data-testid="save-btn"]') == []
    assert _lint_one_selector("button.primary") == []
    # A bracketed attribute with spaces must not inflate the hop count.
    assert _lint_one_selector('[aria-label="a b c d e"]') == []


# ── Combinator-aware hop counting ────────────────────────────────────────────


def test_spaceless_combinators_counted_as_hops():
    # `>` combinators written without surrounding spaces used to hide a deep chain.
    assert any("deep descendant" in r for r in _lint_one_selector("div>span>a>b>c"))
    # Mixed spaced/unspaced combinators collapse per-boundary, not per-character.
    assert any("deep descendant" in r for r in _lint_one_selector("div > span>a > b>c"))


def test_combinator_operators_inside_brackets_not_counted():
    # `~` here is an attribute operator inside [...], not a sibling combinator.
    assert _lint_one_selector('[data-testid~="x"]') == []
    # `2n+1`'s `+` lives inside :nth-child(...) — positional is flagged, deep is not.
    reasons = _lint_one_selector(".card:nth-child(2n+1)")
    assert any("positional" in r for r in reasons)
    assert all("deep descendant" not in r for r in reasons)


def test_spaced_child_combinator_two_parts():
    # `div > span` is 2 parts (one boundary), not 3.
    assert _count_top_level_parts("div > span") == 2
    assert _count_top_level_parts("div>span") == 2


# ── Structure-only heuristic ─────────────────────────────────────────────────


def test_structure_only_chain_flagged():
    assert any("structure-only" in r for r in _lint_one_selector("div > span"))
    assert any("structure-only" in r for r in _lint_one_selector("ul li"))


def test_anchored_shallow_chain_silent():
    # A class/id/attribute anchor keeps a shallow chain out of the structure-only bucket.
    assert _lint_one_selector(".list > .row") == []
    assert _lint_one_selector('form [data-testid="name"]') == []


def test_deep_tag_chain_prefers_deep_reason():
    # A tag-only deep chain reports the (more informative) deep-chain reason only.
    reasons = _lint_one_selector("div section ul li a span")
    assert any("deep descendant" in r for r in reasons)
    assert all("structure-only" not in r for r in reasons)


# ── Integration: scenario-level lint ─────────────────────────────────────────


@pytest.fixture
def temp_scenario_file(tmp_path):
    def _create_file(content: str):
        p = tmp_path / "test_scenario.md"
        p.write_text(dedent(content), encoding="utf-8")
        return p

    return _create_file


def test_scenario_lint_reports_brittle_selectors(temp_scenario_file):
    path = temp_scenario_file("""
        ---
        title: T
        ---

        ### 1. Click a fragile target
        - role: editor
        - action: click
        - selector: div section ul li:nth-child(2) a

        ### 2. Click a clean target
        - role: editor
        - action: click
        - selector: [data-testid="ok"]
    """)
    scenario = parse_scenario_file(path)
    warnings = lint_scenario_selectors(scenario)
    assert any("step 1" in w for w in warnings)
    assert all("step 2" not in w for w in warnings)
    assert all("prefer a data-testid" in w for w in warnings)


def test_scenario_lint_clean_is_empty(temp_scenario_file):
    path = temp_scenario_file("""
        ---
        title: T
        ---

        ### 1. Click
        - role: editor
        - action: click
        - selector: [data-testid="submit"]
    """)
    scenario = parse_scenario_file(path)
    assert lint_scenario_selectors(scenario) == []


def test_guide_cursor_target_linted(temp_scenario_file):
    path = temp_scenario_file("""
        ---
        title: T
        ---

        ### 1. Custom with a fragile cursor target
        - role: editor
        - action: custom
        - code: page.wait_for_timeout(1)
        - guide-cursor:
            behavior: click
            target: 'div>section>ul>li>a>span'
    """)
    scenario = parse_scenario_file(path)
    warnings = lint_scenario_selectors(scenario)
    assert any("guide-cursor.target" in w and "deep descendant" in w for w in warnings)
    assert all("prefer a data-testid" in w for w in warnings)


def test_guide_cursor_clean_target_silent(temp_scenario_file):
    path = temp_scenario_file("""
        ---
        title: T
        ---

        ### 1. Custom with a clean cursor target
        - role: editor
        - action: custom
        - code: page.wait_for_timeout(1)
        - guide-cursor:
            behavior: type
            target: '[data-testid="dob"] input'
            value: "21-06-1985"
    """)
    scenario = parse_scenario_file(path)
    assert lint_scenario_selectors(scenario) == []


def test_capture_from_selector_linted(temp_scenario_file):
    path = temp_scenario_file("""
        ---
        title: T
        ---

        ### 1. Capture from a fragile DOM selector
        - role: editor
        - action: capture
        - store: order_id
        - from:
            type: dom
            selector: 'div>section>ul>li>a>span'
    """)
    scenario = parse_scenario_file(path)
    warnings = lint_scenario_selectors(scenario)
    assert any("from.selector" in w and "deep descendant" in w for w in warnings)
