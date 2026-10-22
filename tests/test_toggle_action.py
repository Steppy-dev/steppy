"""Tests for the `toggle` action (Cmd 10): switch / segmented button.

PrimeNG `ToggleSwitch` hides a real `input[role="switch"]` behind styling, so
authors `.check(force=True)` it; segmented `p-togglebutton` groups are clicked by
label. `toggle` owns that idiom. Switch internals come from a `toggle:` config
preset (PrimeNG defaults, so it works with no config).
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
    title: Toggle
    auth: false
    ---

    ### 1. Toggle
    - role: editor
    - action: toggle
    - selector: '[data-testid="recurrence"]'
{body}
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    return src


# ── Model ───────────────────────────────────────────────────────────────────


def test_toggle_state_on():
    step = Step(action="toggle", role="editor", selector="#x", state="on")
    assert step.state == "on"


def test_toggle_yaml_bool_normalized_to_on():
    # PyYAML coerces `on` → True; the field validator maps it back to "on".
    step = Step(action="toggle", role="editor", selector="#x", state=True)
    assert step.state == "on"


def test_toggle_yaml_bool_false_to_off():
    step = Step(action="toggle", role="editor", selector="#x", state=False)
    assert step.state == "off"


def test_toggle_segmented_option():
    step = Step(action="toggle", role="editor", selector="#x", option="Range")
    assert step.option == "Range"


def test_toggle_requires_selector():
    with pytest.raises(ValueError, match="requires 'selector'"):
        Step(action="toggle", role="editor", state="on")


def test_toggle_state_and_option_mutually_exclusive():
    with pytest.raises(ValueError, match="not both"):
        Step(action="toggle", role="editor", selector="#x", state="on", option="Range")


def test_wait_for_rejects_on_off_state():
    # The broadened state Literal must not loosen wait-for.
    with pytest.raises(ValueError, match="visible|hidden"):
        Step(action="wait-for", role="editor", selector="#x", state="on")


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_codegen_state_on_checks_when_unchecked(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - state: on")
    assert 'input[role="switch"]' in src
    assert "if not _switch.is_checked():" in src
    assert "_switch.check(force=True)" in src


def test_codegen_state_off_unchecks(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - state: off")
    assert "if _switch.is_checked():" in src
    assert "_switch.uncheck(force=True)" in src


def test_codegen_no_state_flips(temp_scenario_file):
    src = _gen(temp_scenario_file, "")
    assert "_switch.click(force=True)" in src


def test_codegen_segmented_clicks_by_label(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - option: Range")
    assert ".p-togglebutton" in src
    assert "filter(has_text=_interp('Range'" in src


# ── Guide cursor ────────────────────────────────────────────────────────────


def test_toggle_cursor_clicks_selector():
    step = Step(action="toggle", role="editor", selector="#sw", state="on")
    sidecar = {"target": {"bbox": {"x": 1, "y": 2, "width": 3, "height": 4}}}
    behavior = _compute_cursor_behavior(step, sidecar)
    assert behavior["kind"] == "click"
    assert behavior["target"]["selector"] == "#sw"
