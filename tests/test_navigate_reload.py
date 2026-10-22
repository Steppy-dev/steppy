"""Tests for the `navigate` action's optional `reload:` flag (Cmd 14).

`reload: true` hard-refetches the *current* page (`page.reload()`) — the idiom
after async/queue propagation, where a `navigate` to the same URL is a no-op and
a bare `page.reload()` is needed. It reuses the existing `wait:` load-state handling (Cmd 9) and
needs no `target` (reloading the current URL). Omitting it must keep output
byte-identical (`page.goto`).
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


def _gen(temp_scenario_file, step_lines: str) -> str:
    content = f"""
    ---
    title: Navigate
    auth: false
    ---

    ### 1. Step
    - role: editor
    - action: navigate
{step_lines}
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    return src


# ── Model ───────────────────────────────────────────────────────────────────


def test_reload_accepted_on_navigate():
    step = Step(action="navigate", role="editor", reload=True)
    assert step.reload is True


def test_reload_defaults_none():
    step = Step(action="navigate", role="editor", target="/x")
    assert step.reload is None


def test_reload_rejected_on_non_navigate():
    with pytest.raises(ValueError, match="only valid on navigate"):
        Step(action="click", role="editor", selector="#x", reload=True)


def test_reload_and_target_mutually_exclusive():
    with pytest.raises(ValueError, match="reloads the current page"):
        Step(action="navigate", role="editor", target="/x", reload=True)


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_codegen_reload_emits_reload_not_goto(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - reload: true")
    assert "editor_page.reload()" in src
    assert "goto(" not in src


def test_codegen_reload_keeps_default_wait(temp_scenario_file):
    # reload still honors the default load-state wait (networkidle).
    src = _gen(temp_scenario_file, "    - reload: true")
    assert 'wait_for_load_state("networkidle")' in src


def test_codegen_reload_with_explicit_wait(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - reload: true\n    - wait: domcontentloaded")
    assert "editor_page.reload()" in src
    assert 'wait_for_load_state("domcontentloaded")' in src
    assert 'wait_for_load_state("networkidle")' not in src


def test_codegen_default_navigate_unchanged(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - target: /customers")
    assert "editor_page.goto(_interp('/customers'" in src
    assert "reload()" not in src
