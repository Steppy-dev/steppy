"""Tests for the `expand` action (Cmd 13): open a collapsible section.

PrimeNG `p-fieldset` (and similar accordions) hide their body behind a clickable
legend/toggle. `expand` opens it idempotently — it only clicks when the content
is not already visible — and waits for the body via an event, not a hard sleep.
Selectors come from a `fieldset:` config preset (PrimeNG defaults, so it works
with no config).
"""

import ast
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.guide import _compute_cursor_behavior
from steppy.models import FieldsetConfig, Step
from steppy.parser import parse_scenario_file


@pytest.fixture
def temp_scenario_file(tmp_path):
    def _create(content: str) -> Path:
        path = tmp_path / "scenario.md"
        path.write_text(dedent(content), encoding="utf-8")
        return path

    return _create


def _gen(temp_scenario_file, body: str = "", screenshot: str = "false") -> str:
    content = f"""
    ---
    title: Expand
    auth: false
    ---

    ### 1. Expand
    - role: editor
    - action: expand
    - selector: '[data-testid="rating-tab"]'
{body}
    - screenshot: {screenshot}
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    return src


# ── Model ───────────────────────────────────────────────────────────────────


def test_expand_accepts_selector():
    step = Step(action="expand", role="editor", selector="#fs")
    assert step.action == "expand"
    assert step.selector == "#fs"


def test_expand_requires_selector():
    with pytest.raises(ValueError, match="requires 'selector'"):
        Step(action="expand", role="editor")


def test_fieldset_config_defaults_target_primeng():
    cfg = FieldsetConfig()
    assert ".p-fieldset" in cfg.toggle
    assert ".p-toggleable-content" in cfg.content


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_codegen_resolves_fieldset_toggle_content(temp_scenario_file):
    src = _gen(temp_scenario_file)
    assert "_fieldset = editor_page.locator(_interp(" in src
    assert ".p-fieldset-toggle-button" in src
    assert ".p-toggleable-content" in src


def test_codegen_idempotent_visibility_guard(temp_scenario_file):
    src = _gen(temp_scenario_file)
    assert "if not _content.is_visible():" in src
    assert "_toggle.click()" in src
    assert '_content.wait_for(state="visible"' in src


def test_codegen_uses_event_wait_not_hard_sleep(temp_scenario_file):
    src = _gen(temp_scenario_file)
    assert "wait_for_timeout" not in src


def test_codegen_screenshot_captures_toggle_bbox(temp_scenario_file):
    src = _gen(temp_scenario_file, screenshot="editor")
    assert "_bbox = _safe_bbox_locator(editor_page, _toggle)" in src
    assert 'take_screenshot_with_bbox(editor_page, SCENARIO, 1, "expand"' in src


# ── Guide cursor ────────────────────────────────────────────────────────────


def test_expand_cursor_is_click():
    step = Step(action="expand", role="editor", selector="#fs")
    sidecar = {"target": {"kind": "single", "bbox": {"x": 5, "y": 6, "width": 7, "height": 8}}}
    behavior = _compute_cursor_behavior(step, sidecar)
    assert behavior["kind"] == "click"
    assert behavior["target"]["selector"] == "#fs"
    assert behavior["target"]["bbox"] == {"x": 5, "y": 6, "width": 7, "height": 8}
