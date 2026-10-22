"""Tests for the `pick-time` action (Cmd 7): drive an hour/minute clock widget.

Read-only time fields expose only their popup spinner. `pick-time` steps the
increment/decrement arrows to the target `HH:MM` via the `_pick_time` runtime
helper (config-driven datepicker selectors). Best-effort + framework-tunable.
"""

import ast
import builtins
import symtable
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
    title: Pick time
    auth: false
    ---

    ### 1. Pick a time
    - role: editor
    - action: pick-time
    - selector: '[data-testid="start-time-datepicker"]'
{body}
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    return src


# ── Model ───────────────────────────────────────────────────────────────────


def test_pick_time_accepts_hhmm():
    step = Step(action="pick-time", role="editor", selector="#x", time="09:00")
    assert step.time == "09:00"


def test_pick_time_requires_selector():
    with pytest.raises(ValueError, match="requires 'selector'"):
        Step(action="pick-time", role="editor", time="09:00")


def test_pick_time_requires_time():
    with pytest.raises(ValueError, match="requires 'time'"):
        Step(action="pick-time", role="editor", selector="#x")


def test_pick_time_rejects_bad_format():
    with pytest.raises(ValueError, match="HH:MM"):
        Step(action="pick-time", role="editor", selector="#x", time="9am")


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_codegen_calls_pick_time_with_config_selectors(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - time: '09:00'")
    assert "_pick_time(" in src
    assert "'09:00'" in src
    assert "hour_picker='.p-datepicker-hour-picker'" in src
    assert "minute_picker='.p-datepicker-minute-picker'" in src
    assert "trigger='button.p-datepicker-dropdown, button.p-datepicker-trigger'" in src


def _undefined_conftest_globals(src: str) -> set:
    """Names the generated module *calls* but never binds at module scope.

    `_pick_date`/`_pick_time` are emitted as calls inside the test function body,
    so `ast.parse()` (and even `exec()`-ing the module top level) can't catch a
    helper missing from the `from .conftest import …` line — the call never runs
    at import time, it just raises `NameError` later in production. Static scope
    analysis does catch it: a name referenced as a *global* inside a function but
    bound nowhere at module level (and not a builtin) would NameError at runtime.
    """
    table = symtable.symtable(src, "gen.py", "exec")
    module_bound = {s.get_name() for s in table.get_symbols() if s.is_assigned() or s.is_imported()}
    builtin_names = set(dir(builtins))
    missing: set = set()

    def _walk(t):
        if t.get_type() == "function":
            for s in t.get_symbols():
                name = s.get_name()
                if s.is_global() and s.is_referenced() and name not in module_bound and name not in builtin_names:
                    missing.add(name)
        for child in t.get_children():
            _walk(child)

    _walk(table)
    return missing


def test_generated_pick_time_module_has_no_undefined_helpers(temp_scenario_file):
    """Regression: every conftest helper the body calls must be imported.

    The `pick-time` codegen emits `_pick_time(...)`; the import line in
    `test_scenario.py.j2` must carry `_pick_time` or the generated test raises
    `NameError` at runtime (the codegen-imported-but-forgotten bug). `ast.parse`
    misses it because the call sits in the function body, not module top level.
    """
    src = _gen(temp_scenario_file, "    - time: '09:00'")
    assert "_pick_time" not in _undefined_conftest_globals(src)


# ── Guide cursor ────────────────────────────────────────────────────────────


def test_pick_time_cursor_clicks_selector():
    step = Step(action="pick-time", role="editor", selector="#tp", time="09:00")
    sidecar = {"target": {"bbox": {"x": 1, "y": 2, "width": 3, "height": 4}}}
    behavior = _compute_cursor_behavior(step, sidecar)
    assert behavior["kind"] == "click"
    assert behavior["target"]["selector"] == "#tp"
