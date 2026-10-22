"""The ``call`` action — run a project helper as a first-class step.

The largest single bucket of residual custom code: 2 398 blocks in one project
import a ``*_helpers`` module, and 926 do nothing else. The helpers stay in the
project — one of them knows the app's French status labels *and* branches on
viewport, which is not Steppy's business — but naming one should not require
dropping into Python.

Two things come free with it. A ``call`` step is a *named* step, so it can carry
a ``guide-cursor:`` and appear in the interactive player, where a ``custom``
block publishes as ``cursor: null, interactive: false``. And the three-line
``sys.path.insert`` preamble that opens 2 334 of those blocks disappears — it
was never necessary (see ``call_helper``'s docstring).
"""

import ast
import re
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.guide import _compute_cursor_behavior
from steppy.models import VALID_ACTIONS, Step
from steppy.parser import parse_scenario_file

_CONFTEST = Path(__file__).resolve().parents[1] / "templates" / "conftest.py.j2"


def _load_call_helper():
    """Exec the real ``call_helper`` out of the canonical template."""
    text = _CONFTEST.read_text(encoding="utf-8")
    start = text.index("def call_helper(")
    end = text.index("def synthetic_click(")
    ns: dict = {"Path": Path, "_interp": lambda s, v: s}
    exec(compile(text[start:end], "<call_helper>", "exec"), ns)
    return ns["call_helper"]


call_helper = _load_call_helper()


@pytest.fixture
def gen_step(tmp_path):
    def _gen(body: str) -> str:
        path = tmp_path / "scenario.md"
        path.write_text("---\ntitle: T\nauth: false\n---\n\n" + dedent(body), encoding="utf-8")
        src = SteppyGenerator(load_config()).generate_test_file(parse_scenario_file(path))
        ast.parse(src)
        return src

    return _gen


def _step_body(src: str) -> str:
    start = src.index("_steppy_mark_step(1")
    return src[start : src.index("_steppy_end_step(1")].split("\n", 1)[1]


# ── The runtime helper, against a real module on disk ───────────────────────


@pytest.fixture
def helper_module(tmp_path, monkeypatch):
    """Write a helper module and put it on sys.path, as the project root is."""
    (tmp_path / "demo_helpers.py").write_text(
        dedent("""
        CALLS = []

        def ensure_open(page, testid, timeout=15000):
            CALLS.append((page, testid, timeout))
            return f"opened {testid}"

        def sentinel_email(role):
            return f"{role}@example.test"
        """),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("demo_helpers", None)
    yield tmp_path
    sys.modules.pop("demo_helpers", None)


def test_the_page_is_passed_first(helper_module):
    page = object()
    result = call_helper(page, "demo_helpers.ensure_open", args=["order-toggle"], _vars={})
    assert result == "opened order-toggle"
    import demo_helpers

    assert demo_helpers.CALLS[0][:2] == (page, "order-toggle")


def test_kwargs_reach_the_helper(helper_module):
    call_helper(object(), "demo_helpers.ensure_open", args=["x"], kwargs={"timeout": 500}, _vars={})
    import demo_helpers

    assert demo_helpers.CALLS[-1][2] == 500


def test_pass_page_false_is_for_a_pure_value_producer(helper_module):
    """`sentinel_email("editor")` takes no page; forcing one would be a TypeError."""
    assert call_helper(object(), "demo_helpers.sentinel_email", args=["editor"], _vars={}, pass_page=False) == (
        "editor@example.test"
    )


def test_a_missing_module_names_the_project_root(helper_module):
    with pytest.raises(AssertionError, match="cannot import 'nope_helpers'"):
        call_helper(object(), "nope_helpers.thing", _vars={})


def test_a_missing_function_says_so(helper_module):
    with pytest.raises(AssertionError, match="has no attribute 'nope'"):
        call_helper(object(), "demo_helpers.nope", _vars={})


def test_non_string_arguments_keep_their_type(helper_module):
    """`args: [active-orders, 6]` must reach the helper with 6 an int."""
    call_helper(object(), "demo_helpers.ensure_open", args=["x"], kwargs={"timeout": 6}, _vars={})
    import demo_helpers

    assert demo_helpers.CALLS[-1][2] == 6


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_the_minimal_form(gen_step):
    body = _step_body(
        gen_step("""
    ### 1. C
    - role: editor
    - action: call
    - fn: schedule_helpers.save
    """)
    )
    assert "call_helper(" in body and "'schedule_helpers.save'," in body
    assert "editor_page," in body


def test_args_and_kwargs_are_emitted_as_json_literals(gen_step):
    body = _step_body(
        gen_step("""
    ### 1. C
    - role: billing
    - action: call
    - fn: list_helpers.select_status
    - args: [active-orders, 6, En traitement]
    - kwargs:
        label: En traitement
    """)
    )
    assert 'args=["active-orders", 6, "En traitement"],' in body
    assert 'kwargs={"label": "En traitement"},' in body


def test_store_binds_the_return_value(gen_step):
    body = _step_body(
        gen_step("""
    ### 1. C
    - role: editor
    - action: call
    - fn: api_helpers.order_id
    - store: oid
    """)
    )
    assert "_vars['oid'] = call_helper(" in body


def test_page_false_is_threaded_through(gen_step):
    body = _step_body(
        gen_step("""
    ### 1. C
    - role: editor
    - action: call
    - fn: admin_helpers.sentinel_email
    - args: [editor]
    - page: false
    - store: email
    """)
    )
    assert "pass_page=False," in body


def test_the_emitted_call_never_reproduces_the_syspath_preamble(gen_step):
    """The preamble was cargo-cult: `python -m pytest` already puts the project
    root on sys.path[0], and the worker image sets WORKDIR to it."""
    src = gen_step("""
    ### 1. C
    - role: editor
    - action: call
    - fn: ui_helpers.ensure_open
    """)
    assert "sys.path.insert" not in src


# ── Guide mode ──────────────────────────────────────────────────────────────


def test_a_call_step_can_carry_a_guide_cursor():
    """The reason to prefer `call` over `custom` for a helper-driven step."""
    step = Step(
        action="call",
        role="editor",
        fn="ui_helpers.ensure_open",
        **{"guide-cursor": {"behavior": "click", "target": "#toggle"}},
    )
    behavior = _compute_cursor_behavior(
        step, {"target": {"kind": "single", "bbox": {"x": 1, "y": 2, "width": 3, "height": 4}}}
    )
    assert behavior["kind"] == "click"
    assert behavior["target"]["selector"] == "#toggle"


def test_without_an_override_a_call_shows_no_cursor():
    """It has no selector; guessing one would put the cursor on the wrong pixel."""
    step = Step(action="call", role="editor", fn="ui_helpers.ensure_open")
    assert _compute_cursor_behavior(step, None) is None


# ── Validation ──────────────────────────────────────────────────────────────


def test_call_is_a_known_action():
    assert "call" in VALID_ACTIONS


def test_fn_is_required():
    with pytest.raises(ValueError, match="call action requires 'fn'"):
        Step(action="call", role="editor")


@pytest.mark.parametrize("fn", ["ensure_open", "ui_helpers.", ".ensure_open", "ui helpers.open", ""])
def test_fn_must_be_a_dotted_path(fn):
    """A bare name would resolve against the test module's globals — the
    `custom`-block behaviour this action replaces."""
    with pytest.raises(ValueError, match="requires 'fn'|must be a dotted path"):
        Step(action="call", role="editor", fn=fn)


def test_call_fields_are_rejected_on_other_actions():
    with pytest.raises(ValueError, match="does not accept"):
        Step(action="click", role="editor", selector="#a", fn="helpers.thing")


def test_the_player_schema_accepts_the_new_action():
    """A `ManifestAction` missing an action makes the player reject the WHOLE
    manifest, so the interactive guide renders nothing with every suite green."""
    schema_ts = Path(__file__).resolve().parents[1] / "packages" / "player" / "src" / "manifest" / "schema.ts"
    enum = re.search(
        r"const\s+ManifestAction\s*=\s*z\.enum\(\s*\[(.*?)\]\s*\)", schema_ts.read_text(encoding="utf-8"), re.DOTALL
    )
    assert "call" in re.findall(r"'([^']+)'", enum.group(1))
