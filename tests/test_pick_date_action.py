"""Tests for the `pick-date` action (Cmd 7): drive a calendar widget.

When a date field is read-only and only its popup works, authors wrote
month-stepping loops + day-cell clicks. `pick-date` owns that loop via the
`_pick_date` runtime helper (config-driven datepicker selectors), computing the
target date at run time with the same grammar as `fill-date`.
"""

import ast
import builtins
import datetime
import symtable
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.guide import _compute_cursor_behavior
from steppy.models import Step
from steppy.parser import parse_scenario_file

_CONFTEST = Path(__file__).resolve().parents[1] / "templates" / "conftest.py.j2"


def _load_resolve_date_obj():
    """Extract `_resolve_date_obj` (plain Python, no Jinja) and exec it."""
    text = _CONFTEST.read_text(encoding="utf-8")
    start = text.index("def _resolve_date_obj(")
    end = text.index("def _resolve_date(")
    ns: dict = {}
    exec(compile(text[start:end], "<resolve_date_obj>", "exec"), ns)
    return ns["_resolve_date_obj"]


resolve_date_obj = _load_resolve_date_obj()


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
    title: Pick date
    auth: false
    ---

    ### 1. Pick a date
    - role: editor
    - action: pick-date
    - selector: '[data-testid="rating-date-datepicker"]'
{body}
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    return src


# ── Date-object helper ───────────────────────────────────────────────────────


def test_resolve_date_obj_returns_date():
    assert resolve_date_obj("today") == datetime.date.today()


def test_resolve_date_obj_plus_seven_days():
    assert resolve_date_obj("+7d") == datetime.date.today() + datetime.timedelta(days=7)


# ── Model ───────────────────────────────────────────────────────────────────


def test_pick_date_requires_selector():
    with pytest.raises(ValueError, match="requires 'selector'"):
        Step(action="pick-date", role="editor", date="+7d")


def test_pick_date_requires_date():
    with pytest.raises(ValueError, match="requires 'date'"):
        Step(action="pick-date", role="editor", selector="#x")


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_codegen_calls_pick_date_with_config_selectors(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - date: +7d")
    assert "_pick_date(" in src
    assert "'+7d'" in src
    assert "panel='.p-datepicker-panel'" in src
    assert "next_btn='.p-datepicker-next-button'" in src
    assert "day_cell=" in src
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


def test_generated_pick_date_module_has_no_undefined_helpers(temp_scenario_file):
    """Regression: every conftest helper the body calls must be imported.

    The `pick-date` codegen emits `_pick_date(...)`; the import line in
    `test_scenario.py.j2` must carry `_pick_date` or the generated test raises
    `NameError` at runtime (the codegen-imported-but-forgotten bug). `ast.parse`
    misses it because the call sits in the function body, not module top level.
    """
    src = _gen(temp_scenario_file, "    - date: +7d")
    assert "_pick_date" not in _undefined_conftest_globals(src)


# ── Guide cursor ────────────────────────────────────────────────────────────


def test_pick_date_cursor_clicks_selector():
    step = Step(action="pick-date", role="editor", selector="#dp", date="+7d")
    sidecar = {"target": {"bbox": {"x": 1, "y": 2, "width": 3, "height": 4}}}
    behavior = _compute_cursor_behavior(step, sidecar)
    assert behavior["kind"] == "click"
    assert behavior["target"]["selector"] == "#dp"
