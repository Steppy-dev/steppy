"""Tests for the `select-option` action (Cmd 1): component-dropdown select.

`select` only drives native `<select>`; all real Steppy projects use component
dropdowns (PrimeNG `p-select`), so every dropdown was hand-rolled custom code.
`select-option` opens the trigger, optionally types into a filter box, and picks
an option by `first`/`last`/index/text — or several via `multiple` (multiselect).
Selectors come from the config `dropdown:` preset (PrimeNG defaults).
"""

import ast
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.guide import _compute_cursor_behavior
from steppy.models import Step
from steppy.parser import parse_scenario_file


@pytest.fixture
def temp_scenario_file(tmp_path):
    def _create(content: str) -> Path:
        path = tmp_path / "scenario.md"
        path.write_text(dedent(content), encoding="utf-8")
        return path

    return _create


def _gen(temp_scenario_file, body: str) -> str:
    content = f"""
    ---
    title: Select option
    auth: false
    ---

    ### 1. Pick from dropdown
    - role: editor
    - action: select-option
    - selector: '[data-testid="schedule-select-product"]'
{body}
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    return src


# ── Model validation ────────────────────────────────────────────────────────


def test_select_option_accepts_option():
    step = Step(action="select-option", role="editor", selector="p-select", option="first")
    assert step.option == "first"


def test_select_option_accepts_multiple():
    step = Step(
        action="select-option",
        role="editor",
        selector="p-select",
        multiple=["Role A", "Role B"],
    )
    assert step.multiple == ["Role A", "Role B"]


def test_select_option_requires_selector():
    with pytest.raises(ValueError, match="requires 'selector'"):
        Step(action="select-option", role="editor", option="first")


def test_select_option_requires_option_or_multiple():
    with pytest.raises(ValueError, match="requires 'option'"):
        Step(action="select-option", role="editor", selector="p-select")


def test_select_option_rejects_both_option_and_multiple():
    with pytest.raises(ValueError, match="not both"):
        Step(
            action="select-option",
            role="editor",
            selector="p-select",
            option="first",
            multiple=["A"],
        )


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_codegen_plain_first(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - option: first")
    # Opens the trigger, waits for the overlay, picks the first option.
    assert "schedule-select-product" in src
    assert ".p-select-overlay" in src
    assert "wait_for_selector(" in src
    assert ".first.click(force=True)" in src


def test_codegen_typeahead_search(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - search: Losart\n    - option: first")
    # Types into the filter box and settles (networkidle by default).
    assert ".fill(" in src
    assert "Losart" in src
    assert 'wait_for_load_state("networkidle")' in src
    assert ".first.click(force=True)" in src


def test_codegen_option_index(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - option: 3")
    assert ".nth(3).click(force=True)" in src


def test_codegen_option_by_text(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - option: 'text:\"Après le petit-déjeuner\"'")
    assert "has_text=" in src
    assert "Après le petit-déjeuner" in src


def test_codegen_multiselect_emits_escape(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - multiple:\n      - Role A\n      - Role B")
    assert src.count("has_text=") >= 2
    assert "Role A" in src and "Role B" in src
    assert 'keyboard.press("Escape")' in src


def test_codegen_option_clicks_are_forced(temp_scenario_file):
    # PrimeNG overlay animates/re-renders; option clicks must bypass the
    # stability wait or they time out (regression: dropped force=True).
    src = _gen(temp_scenario_file, "    - option: first")
    assert ".first.click(force=True)" in src


def test_codegen_multiselect_forces_clicks(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - multiple:\n      - Role A\n      - Role B")
    assert src.count("click(force=True)") >= 2


# ── Guide cursor ────────────────────────────────────────────────────────────


def test_select_option_cursor_targets_trigger():
    step = Step(
        action="select-option",
        role="editor",
        selector='[data-testid="schedule-select-product"]',
        option="first",
    )
    sidecar = {
        "target": {"kind": "single", "selector": "x", "bbox": {"x": 1, "y": 2, "width": 3, "height": 4}},
    }
    behavior = _compute_cursor_behavior(step, sidecar)
    assert behavior == {
        "kind": "click",
        "target": {
            "selector": '[data-testid="schedule-select-product"]',
            "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
        },
    }
