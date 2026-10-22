"""Tests for the extended `assert` DSL (Cmd 5): substring `contains(...)` and
comparator-aware `count(...)`.

`assert text(...)` is exact-full-text only and `count(...)` had no `>=`/`== 0`
comparator, so substring and "row removed" checks went to custom code. These add
`contains(sel, "text")` and `count(sel, >=1)` while keeping the bare-integer
`count(sel, 3)` form byte-identical (still `to_have_count`).
"""

import ast
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.parser import parse_scenario_file


@pytest.fixture
def temp_scenario_file(tmp_path):
    def _create(content: str) -> Path:
        path = tmp_path / "scenario.md"
        path.write_text(dedent(content), encoding="utf-8")
        return path

    return _create


def _gen(temp_scenario_file, expect: str) -> str:
    content = f"""
    ---
    title: Assert DSL
    auth: false
    ---

    ### 1. Assert
    - role: editor
    - action: assert
    - expect: {expect}
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    return src


# ── _format_assertion parsing ───────────────────────────────────────────────


def test_parse_contains():
    gen = SteppyGenerator(load_config())
    parsed = gen._format_assertion('contains([data-testid="tag"], "Pleine boîte")')
    assert parsed == {
        "type": "contains",
        "selector": '[data-testid="tag"]',
        "text": "Pleine boîte",
    }


def test_parse_count_comparator():
    gen = SteppyGenerator(load_config())
    parsed = gen._format_assertion('count([data-testid^="row-"], >=1)')
    assert parsed == {
        "type": "count-cmp",
        "selector": '[data-testid^="row-"]',
        "comparator": ">=",
        "count": "1",
    }


def test_parse_count_plain_integer_unchanged():
    gen = SteppyGenerator(load_config())
    parsed = gen._format_assertion('count([data-testid="x"], 3)')
    assert parsed == {"type": "count", "selector": '[data-testid="x"]', "count": "3"}


def test_parse_count_zero_unchanged():
    gen = SteppyGenerator(load_config())
    parsed = gen._format_assertion('count([data-testid="x"], 0)')
    assert parsed == {"type": "count", "selector": '[data-testid="x"]', "count": "0"}


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_codegen_contains_emits_to_contain_text(temp_scenario_file):
    src = _gen(temp_scenario_file, 'contains([data-testid="kind-tag"], "Pleine boîte")')
    assert ".to_contain_text(" in src
    assert "Pleine boîte" in src


def test_codegen_count_comparator_emits_manual_assert(temp_scenario_file):
    src = _gen(temp_scenario_file, 'count([data-testid^="site-list-row-"], >=1)')
    # Comparator form: capture .count() into a var, then a manual assert.
    assert ".count()" in src
    assert "assert _n >= 1" in src
    # The comparator form does NOT use to_have_count.
    assert "to_have_count" not in src


def test_codegen_count_plain_uses_to_have_count(temp_scenario_file):
    src = _gen(temp_scenario_file, 'count([data-testid="temp-picker"], 0)')
    assert "to_have_count(0" in src
    assert ".count() " not in src


def test_codegen_count_equality_three(temp_scenario_file):
    src = _gen(temp_scenario_file, 'count([data-testid="x"], 3)')
    assert "to_have_count(3" in src
