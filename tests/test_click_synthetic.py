"""Tests for the `click` action's optional `synthetic:` flag (Cmd 12).

Some elements swallow Playwright's native `.click()` (custom widgets that only
react to raw DOM MouseEvents). `synthetic: true` dispatches a manual
mousedown/mouseup/click sequence via `locator.evaluate(...)` instead. Omitting
it must keep output byte-identical (native `.click()`).
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
    title: Click
    auth: false
    ---

    ### 1. Click
    - role: editor
    - action: click
    - selector: '[data-testid="next-btn"]'
{body}
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    return src


# ── Model ───────────────────────────────────────────────────────────────────


def test_synthetic_accepted_on_click():
    step = Step(action="click", role="editor", selector="#x", synthetic=True)
    assert step.synthetic is True


def test_synthetic_defaults_none():
    step = Step(action="click", role="editor", selector="#x")
    assert step.synthetic is None


def test_synthetic_rejected_on_non_click():
    with pytest.raises(ValueError, match="only valid on click"):
        Step(action="hover", role="editor", selector="#x", synthetic=True)


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_codegen_default_uses_native_click(temp_scenario_file):
    src = _gen(temp_scenario_file, "")
    assert "editor_page.click(_interp(" in src
    assert "synthetic_click(editor_page" not in src


def test_codegen_synthetic_dispatches_events(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - synthetic: true")
    assert "synthetic_click(editor_page.locator(_interp(" in src
    assert "editor_page.click(_interp(" not in src


def test_codegen_synthetic_with_match_scopes_locator(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - synthetic: true\n    - match: first")
    assert "synthetic_click(editor_page.locator(_interp(" in src
    assert ".first)" in src


# ── Guide cursor ────────────────────────────────────────────────────────────


def test_synthetic_click_cursor_still_click():
    # synthetic doesn't change the cursor — it's still a click on the selector.
    step = Step(action="click", role="editor", selector="#x", synthetic=True)
    sidecar = {"target": {"bbox": {"x": 1, "y": 2, "width": 3, "height": 4}}}
    behavior = _compute_cursor_behavior(step, sidecar)
    assert behavior["kind"] == "click"
    assert behavior["target"]["selector"] == "#x"
