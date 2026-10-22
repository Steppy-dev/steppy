"""Tests for the `confirm` action (Cmd 2): confirmation-dialog button.

PrimeNG `p-confirmDialog` accept/danger buttons have no `data-testid` (only CSS
classes), so authors hand-wrote brittle class+text fallbacks with `force=True`.
`confirm` waits for the active dialog and clicks accept (default), reject, the
danger button, or a button matched by visible label. Selectors come from the
config `dialog:` preset (PrimeNG defaults).
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
    title: Confirm
    auth: false
    ---

    ### 1. Confirm the dialog
    - role: editor
    - action: confirm
{body}
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    return src


# ── Model validation ────────────────────────────────────────────────────────


def test_confirm_minimal():
    step = Step(action="confirm", role="editor")
    assert step.variant is None and step.button is None


def test_confirm_accepts_variant_reject():
    step = Step(action="confirm", role="editor", variant="reject")
    assert step.variant == "reject"


def test_confirm_rejects_button_with_variant():
    with pytest.raises(ValueError, match="cannot combine"):
        Step(action="confirm", role="editor", button="Oui", danger=True)


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_confirm_default_accept(temp_scenario_file):
    src = _gen(temp_scenario_file, "")
    assert ".last" in src  # active (last) overlay
    assert "wait_for(state='visible'" in src
    assert "p-confirmdialog-accept-button" in src
    assert "_confirm_btn.click()" in src


def test_confirm_reject(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - variant: reject")
    assert "p-confirmdialog-reject-button" in src


def test_confirm_danger(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - danger: true")
    assert "p-button-danger" in src


def test_confirm_by_label(temp_scenario_file):
    src = _gen(temp_scenario_file, '    - button: "Oui"')
    assert "has_text=_interp('Oui'" in src


# ── Guide cursor ────────────────────────────────────────────────────────────


def test_confirm_cursor_uses_sidecar_bbox():
    step = Step(action="confirm", role="editor")
    sidecar = {
        "target": {"kind": "single", "selector": "x", "bbox": {"x": 1, "y": 2, "width": 3, "height": 4}},
    }
    behavior = _compute_cursor_behavior(step, sidecar)
    assert behavior == {
        "kind": "click",
        "target": {"selector": None, "bbox": {"x": 1, "y": 2, "width": 3, "height": 4}},
    }
