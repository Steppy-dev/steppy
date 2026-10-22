"""Tests for the `wait-for` action's optional `state:` field (Cmd 4).

`wait-for` historically only waited for `visible`. The dominant real need is the
opposite — waiting for a dialog to *close* (`hidden`/`detached`) as the success
signal after a save. The `state` field passes through to Playwright's
`wait_for_selector(state=...)`. Omitting it must keep output byte-identical.
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


# ── Model ───────────────────────────────────────────────────────────────────


def test_wait_for_accepts_state():
    step = Step(action="wait-for", role="editor", selector="#dialog", state="hidden")
    assert step.state == "hidden"


def test_wait_for_rejects_unknown_state():
    with pytest.raises(ValueError):
        Step(action="wait-for", role="editor", selector="#dialog", state="gone")


def test_wait_for_state_defaults_none():
    step = Step(action="wait-for", role="editor", selector="#dialog")
    assert step.state is None


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_codegen_emits_state_kwarg(temp_scenario_file):
    content = """
    ---
    title: Wait for dialog close
    auth: false
    ---

    ### 1. Wait for the dialog to close
    - role: editor
    - action: wait-for
    - selector: '[data-testid="floor-form-dialog"]'
    - state: hidden
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    assert "wait_for_selector(" in src
    assert "state='hidden'" in src


def test_codegen_without_state_omits_kwarg(temp_scenario_file):
    content = """
    ---
    title: Wait for visible
    auth: false
    ---

    ### 1. Wait for element
    - role: editor
    - action: wait-for
    - selector: '[data-testid="row"]'
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    assert "wait_for_selector(" in src
    # No state kwarg when the field is omitted (backward-compatible output).
    assert "state=" not in src
