"""Tests for the `upload` step action: model validation, codegen, and the
HTML/interactive guide cursor mapping."""

import ast
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.guide import _compute_cursor_behavior
from steppy.models import GuideCursorOverride, Step
from steppy.parser import parse_scenario_file


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def temp_scenario_file(tmp_path):
    def _create(content: str) -> Path:
        path = tmp_path / "scenario.md"
        path.write_text(dedent(content), encoding="utf-8")
        return path

    return _create


def _sidecar(bbox=None):
    return {
        "step": 1,
        "screenshot": "step-01.png",
        "action": "upload",
        "viewport": {"width": 1280, "height": 720},
        "target": {"kind": "single", "selector": "x", "bbox": bbox},
    }


# ── Model validation ────────────────────────────────────────────────────────


def test_upload_step_accepts_selector_and_value():
    step = Step(
        action="upload",
        role="editor",
        selector="input[type=file]",
        value="fixtures/uploads/file.pdf",
    )
    assert step.action == "upload"
    assert step.selector == "input[type=file]"
    assert step.value == "fixtures/uploads/file.pdf"


def test_upload_step_requires_selector():
    with pytest.raises(ValueError, match="upload action requires 'selector'"):
        Step(action="upload", role="editor", value="x.pdf")


def test_upload_step_requires_value():
    with pytest.raises(ValueError, match="upload action requires 'value'"):
        Step(action="upload", role="editor", selector="input[type=file]")


def test_upload_step_rejects_blank_selector_and_value():
    with pytest.raises(ValueError, match="upload action requires 'selector'"):
        Step(action="upload", role="editor", selector="   ", value="x.pdf")
    with pytest.raises(ValueError, match="upload action requires 'value'"):
        Step(action="upload", role="editor", selector="input", value="   ")


def test_unknown_upload_typo_still_rejected():
    with pytest.raises(ValueError, match="Unknown action type"):
        Step(action="uploadx", role="editor")


# ── Parser ──────────────────────────────────────────────────────────────────


def test_parser_accepts_upload_step(temp_scenario_file):
    content = """
    ---
    title: Upload test
    auth: false
    ---

    ### 1. Upload a file
    - role: editor
    - action: upload
    - selector: input[data-testid="file"]
    - value: fixtures/uploads/a.pdf
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    assert len(scenario.steps) == 1
    step = scenario.steps[0]
    assert step.action == "upload"
    assert step.selector == 'input[data-testid="file"]'
    assert step.value == "fixtures/uploads/a.pdf"


def test_parser_accepts_upload_with_guide_cursor_override(temp_scenario_file):
    content = """
    ---
    title: Upload with hidden input
    auth: false
    ---

    ### 1. Upload via drop zone
    - role: editor
    - action: upload
    - selector: input[data-testid="scan"]
    - value: fixtures/uploads/scan.png
    - guide-cursor:
      behavior: click
      target: '[data-testid="dropzone"]'
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    step = scenario.steps[0]
    assert step.guide_cursor is not None
    assert step.guide_cursor.behavior == "click"
    assert step.guide_cursor.target == '[data-testid="dropzone"]'


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_codegen_emits_set_input_files(temp_scenario_file):
    content = """
    ---
    title: Upload codegen
    auth: false
    ---

    ### 1. Upload
    - role: editor
    - action: upload
    - selector: input[data-testid="file"]
    - value: fixtures/uploads/a.pdf
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    # Generated source must be valid Python.
    ast.parse(src)
    # The action codegens to Playwright's set_input_files via a locator.
    assert ".set_input_files(" in src
    assert 'input[data-testid=\\"file\\"]' in src or 'input[data-testid="file"]' in src
    assert "fixtures/uploads/a.pdf" in src


def test_codegen_with_screenshot_captures_bbox_on_selector(temp_scenario_file):
    content = """
    ---
    title: Upload with screenshot
    auth: false
    ---

    ### 1. Upload visible input
    - role: editor
    - action: upload
    - selector: input[data-testid="file"]
    - value: fixtures/uploads/a.pdf
    - screenshot: editor
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    # Bbox is taken from the input selector itself (no guide-cursor override).
    assert "_safe_bbox_scrolled" in src
    assert 'input[data-testid="file"]' in src
    # Default screenshot-when is 'after', so one capture call after the action.
    assert src.count("take_screenshot_with_bbox") >= 1
    assert '"upload"' in src


def test_codegen_with_guide_cursor_uses_override_target_for_bbox(temp_scenario_file):
    content = """
    ---
    title: Upload hidden input
    auth: false
    ---

    ### 1. Upload via drop zone
    - role: editor
    - action: upload
    - selector: input[data-testid="scan"]
    - value: fixtures/uploads/scan.png
    - guide-cursor:
      behavior: click
      target: '[data-testid="dropzone"]'
    - screenshot: editor
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    # bbox + screenshot selector must reference the visible trigger, not the
    # hidden input.
    assert '[data-testid="dropzone"]' in src
    # set_input_files still targets the hidden input.
    assert 'input[data-testid="scan"]' in src or 'input[data-testid="scan"]' in src


def test_codegen_before_screenshot_timing(temp_scenario_file):
    content = """
    ---
    title: Upload before screenshot
    auth: false
    ---

    ### 1. Upload
    - role: editor
    - action: upload
    - selector: input[data-testid="file"]
    - value: fixtures/uploads/a.pdf
    - screenshot: editor
    - screenshot-when: before
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    # In 'before' mode the capture line precedes the set_input_files call.
    capture_pos = src.find("take_screenshot_with_bbox")
    upload_pos = src.find(".set_input_files(")
    assert capture_pos != -1 and upload_pos != -1
    assert capture_pos < upload_pos


# ── HTML / interactive guide cursor behavior ────────────────────────────────


def test_upload_default_cursor_is_click_on_selector():
    step = Step(
        action="upload",
        role="editor",
        selector='input[data-testid="file"]',
        value="x.pdf",
    )
    bbox = {"x": 10, "y": 20, "width": 100, "height": 40}
    behavior = _compute_cursor_behavior(step, _sidecar(bbox=bbox))
    assert behavior == {
        "kind": "click",
        "target": {"selector": 'input[data-testid="file"]', "bbox": bbox},
    }


def test_upload_with_guide_cursor_override_uses_override_target():
    step = Step(
        action="upload",
        role="editor",
        selector='input[data-testid="scan"]',
        value="scan.png",
        **{
            "guide-cursor": GuideCursorOverride(
                behavior="click",
                target='[data-testid="dropzone"]',
            )
        },
    )
    bbox = {"x": 5, "y": 5, "width": 200, "height": 100}
    behavior = _compute_cursor_behavior(step, _sidecar(bbox=bbox))
    assert behavior == {
        "kind": "click",
        "target": {"selector": '[data-testid="dropzone"]', "bbox": bbox},
        "value": None,
    }


def test_upload_with_guide_cursor_hover_override():
    """An override may pick any cursor behavior — verify hover round-trips."""
    step = Step(
        action="upload",
        role="editor",
        selector="input[type=file]",
        value="x.pdf",
        **{
            "guide-cursor": GuideCursorOverride(
                behavior="hover",
                target=".upload-zone",
            )
        },
    )
    behavior = _compute_cursor_behavior(step, _sidecar(bbox={"x": 0, "y": 0, "width": 10, "height": 10}))
    assert behavior["kind"] == "hover"
    assert behavior["target"]["selector"] == ".upload-zone"
