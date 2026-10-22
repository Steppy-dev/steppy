"""Tests for the `fill` action's `clear` / `sequential` / `blur-key` flags (Cmd 11).

Framework inputs (p-inputNumber, masked fields) ignore plain `page.fill`; authors
click → select-all → type sequentially → Tab to fire change events. These flags
fold that idiom into the native action. All default off ⇒ byte-identical output.
"""

import ast
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
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
    title: Fill
    auth: false
    ---

    ### 1. Fill a field
    - role: editor
    - action: fill
    - selector: '[data-testid="capacity"] input'
    - value: "2"
{body}
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    return src


# ── Model ───────────────────────────────────────────────────────────────────


def test_fill_accepts_flags():
    step = Step(
        action="fill", role="editor", selector="#x", value="2", clear=True, sequential=True, **{"blur-key": "Tab"}
    )
    assert step.clear is True
    assert step.sequential is True
    assert step.blur_key == "Tab"


def test_flags_rejected_on_non_fill():
    with pytest.raises(ValueError, match="only valid on fill"):
        Step(action="click", role="editor", selector="#x", clear=True)


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_codegen_plain_fill_unchanged(temp_scenario_file):
    src = _gen(temp_scenario_file, "")
    # No flags → the classic single-line fill, no locator/click dance.
    assert "_page.fill(_interp(" in src
    assert "press_sequentially" not in src
    assert "ControlOrMeta+A" not in src


def test_codegen_clear_emits_select_all_delete(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - clear: true")
    assert '_el.press("ControlOrMeta+A")' in src
    assert '_el.press("Delete")' in src
    assert "_el.fill(_interp(" in src


def test_codegen_sequential_uses_press_sequentially(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - sequential: true")
    assert "_el.press_sequentially(_interp(" in src
    assert "delay=50" in src


def test_codegen_blur_key_presses_after(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - blur-key: Tab")
    assert "keyboard.press('Tab')" in src


def test_codegen_all_flags_combine(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - clear: true\n    - sequential: true\n    - blur-key: Enter")
    assert '_el.press("ControlOrMeta+A")' in src
    assert "_el.press_sequentially(_interp(" in src
    assert "keyboard.press('Enter')" in src
