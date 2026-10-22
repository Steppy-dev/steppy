"""The events plugin's JSONL sink, exercised inside a real pytest run.

The plugin is a static file in the canonical ``templates/`` tree that
``steppy generate`` copies next to the generated conftest; loading it from
there means these tests pass on a fresh clone, before any build.

What matters here is the thing a unit test of ``_emit`` cannot show: that the
sink attributes each line to the right ``(scenario, locale, layout)`` under a
real parametrization — including the cross-role case, where two page fixtures
carry the same ``(locale, layout)`` and the attribution must still come out once.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

_TEMPLATES = Path(__file__).parent.parent / "templates"
_PLUGIN_PATH = _TEMPLATES / "_steppy_events.py"


def _step_event_helpers() -> str:
    """The real ``_steppy_mark_step`` / ``_steppy_end_step`` block, lifted out
    of ``templates/conftest.py.j2``.

    Copying the source rather than reimplementing it is the point: these tests
    then exercise the code that actually ships, and the slice fails loudly if
    the block ever gains a Jinja tag (it must stay plain Python — several other
    tests exec slices of this template the same way).
    """
    text = (_TEMPLATES / "conftest.py.j2").read_text(encoding="utf-8")
    start = text.index("# ── Per-step events")
    end = text.index("# ── Auth helpers", start)
    block = text[start:end]
    assert "{%" not in block and "{{" not in block, (
        "the per-step events block in conftest.py.j2 gained a Jinja tag; it must stay "
        "plain Python so tests can exercise it without rendering the whole template"
    )
    return block


_CONFTEST = """
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _steppy_events import (
    pytest_configure, pytest_collection_finish, pytest_runtest_protocol,
    pytest_runtest_logstart, pytest_runtest_logreport, pytest_sessionfinish,
)
import pytest

__STEP_EVENT_HELPERS__

def _lid(value):
    locale, layout = value
    return f"{locale or 'default'}-{layout or 'default'}"

@pytest.fixture
def editor_page(request):
    return request.param

@pytest.fixture
def admin_page(request):
    return request.param

def pytest_generate_tests(metafunc):
    # Same shape as conftest.py.j2: every role page a test requests is bound
    # under one parametrize call, so the roles move in lockstep.
    params = [("fr", "mobile"), ("nl", "desktop")]
    names = [n for n in ("editor_page", "admin_page") if n in metafunc.fixturenames]
    if len(names) == 1:
        metafunc.parametrize(names[0], params, ids=_lid, indirect=True)
    elif names:
        metafunc.parametrize(
            names, [tuple(p for _ in names) for p in params], ids=[_lid(p) for p in params], indirect=True
        )
"""

_TEST_MODULE = """
SCENARIO = "order-lifecycle"

def test_ok(editor_page):
    assert editor_page

def test_fails(editor_page):
    assert False, "boom"

def test_cross_role(editor_page, admin_page):
    assert editor_page and admin_page
"""

_UNPARAMETRIZED = """
SCENARIO = "smoke"

def test_plain():
    assert True
"""

#: Shaped like a generated test file: every step body bracketed by the two
#: markers, and the failing step's ``_steppy_end_step`` unreachable because the
#: statement above it raises — which is exactly how ``failed_step`` is derived.
_STEPPED = """
from conftest import _steppy_mark_step, _steppy_end_step

SCENARIO = "order-lifecycle"

def test_passes(editor_page):
    _steppy_mark_step(1, 'Open the list', 'navigate')
    _steppy_end_step(1)
    _steppy_mark_step(2, 'Click new', 'click')
    _steppy_end_step(2)

def test_fails_mid_flow(editor_page):
    _steppy_mark_step(1, 'Open the list', 'navigate')
    _steppy_end_step(1)
    _steppy_mark_step(2, 'Click a missing button', 'click')
    raise AssertionError("boom")
    _steppy_end_step(2)

def test_fails_between_steps(editor_page):
    _steppy_mark_step(1, 'Open the list', 'navigate')
    _steppy_end_step(1)
    raise AssertionError("blew up after every step closed")
"""


@pytest.fixture
def run_suite(pytester, monkeypatch):
    """Run a generated-looking suite under the real plugin and return the
    directory the sink wrote to."""

    def _run(
        module_source: str = _TEST_MODULE,
        *,
        events_dir: str | None = "events",
        emit_stdout: bool = False,
        step_events: bool = False,
    ):
        pytester.makepyfile(_steppy_events=_PLUGIN_PATH.read_text(encoding="utf-8"))
        pytester.makeconftest(_CONFTEST.replace("__STEP_EVENT_HELPERS__", _step_event_helpers()))
        pytester.makepyfile(test_order_lifecycle=module_source)

        target = None
        if events_dir is not None:
            target = pytester.path / events_dir
            monkeypatch.setenv("STEPPY_EVENTS_DIR", str(target))
        else:
            monkeypatch.delenv("STEPPY_EVENTS_DIR", raising=False)
        if emit_stdout:
            monkeypatch.setenv("STEPPY_EMIT_EVENTS", "1")
        else:
            monkeypatch.delenv("STEPPY_EMIT_EVENTS", raising=False)
        if step_events:
            monkeypatch.setenv("STEPPY_STEP_EVENTS", "1")
        else:
            monkeypatch.delenv("STEPPY_STEP_EVENTS", raising=False)

        result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
        return target, result

    return _run


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_sink_writes_one_file_per_scenario_locale_layout(run_suite):
    target, _result = run_suite()
    names = sorted(p.name for p in target.glob("*.jsonl"))
    assert names == ["order-lifecycle__fr-mobile.jsonl", "order-lifecycle__nl-desktop.jsonl"]


def test_lines_carry_the_full_contract_fields(run_suite):
    target, _result = run_suite()
    events = _lines(target / "order-lifecycle__fr-mobile.jsonl")

    ends = [e for e in events if e["kind"] == "test-end"]
    assert {e["outcome"] for e in ends} == {"passed", "failed"}
    for event in events:
        assert event["v"] == 1
        assert event["scenario"] == "order-lifecycle"
        assert (event["locale"], event["layout"]) == ("fr", "mobile")
        assert isinstance(event["ts"], float)

    tails = [e for e in events if e["kind"] == "test-fail-tail"]
    assert len(tails) == 1
    import base64

    assert b"boom" in base64.b64decode(tails[0]["tail_b64"])


def test_test_start_is_attributed_not_filed_under_unknown(run_suite):
    """``pytest_runtest_logstart`` fires BEFORE setup, which is why the context
    is captured in ``pytest_runtest_protocol``. Getting this wrong files every
    test-start line under ``unknown__default-default.jsonl``."""
    target, _result = run_suite()
    assert not (target / "unknown__default-default.jsonl").exists()
    events = _lines(target / "order-lifecycle__fr-mobile.jsonl")
    assert any(e["kind"] == "test-start" for e in events)


def test_cross_role_parametrization_is_attributed_without_parsing_the_nodeid(run_suite):
    """A test using two page fixtures has both in ``callspec.params``; every one
    of its lines must still land in a well-formed file. The node id is no help
    either way: ``fr-BE`` contains the separator the id joins on."""
    target, _result = run_suite()
    for path in target.glob("*.jsonl"):
        for event in _lines(path):
            assert event["locale"] in ("fr", "nl")
            assert event["layout"] in ("mobile", "desktop")
    cross = [e for path in target.glob("*.jsonl") for e in _lines(path) if "test_cross_role" in e.get("nodeid", "")]
    assert cross, "the cross-role test produced no events"


def test_unparametrized_tests_get_the_default_filename(run_suite):
    target, _result = run_suite(_UNPARAMETRIZED)
    assert [p.name for p in target.glob("*.jsonl")] == ["smoke__default-default.jsonl"]
    events = _lines(target / "smoke__default-default.jsonl")
    assert all(e["locale"] is None and e["layout"] is None for e in events)


def test_sink_off_writes_nothing_and_prints_nothing(run_suite, pytester):
    _target, result = run_suite(events_dir=None)
    assert not list(pytester.path.glob("events/*.jsonl"))
    assert "STEPPY-EVT::" not in result.stdout.str()


def test_events_dir_alone_does_not_print_stdout_events(run_suite):
    """The two sinks are independent: a --bundle run must not spray machine
    lines into a human's pytest output."""
    _target, result = run_suite()
    assert "STEPPY-EVT::" not in result.stdout.str()


def test_emit_events_still_prints_the_stdout_grammar(run_suite):
    _target, result = run_suite(emit_stdout=True)
    assert "STEPPY-EVT::test-end::" in result.stdout.str()


def test_a_broken_sink_never_fails_the_run(run_suite, pytester):
    """The events directory is created under a path that is a FILE, so every
    write raises. The suite's own results must be unaffected."""
    blocker = pytester.path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    _target, result = run_suite(events_dir="blocked/events")
    # 2 (test_ok) + 2 (test_fails) + 2 (cross-role, in lockstep) = 6 items.
    result.assert_outcomes(passed=4, failed=2)


def test_rerunning_a_scenario_replaces_its_file(run_suite, pytester):
    """A retry pass re-runs one scenario; its events must be the final
    attempt's, not both attempts stacked."""
    target, _first = run_suite()
    before = _lines(target / "order-lifecycle__fr-mobile.jsonl")
    pytester.runpytest_subprocess("-p", "no:cacheprovider")
    after = _lines(target / "order-lifecycle__fr-mobile.jsonl")
    assert len(after) == len(before)


def _steps(events: list[dict], nodeid_part: str) -> list[tuple]:
    return [
        (e["kind"], e["step"]) for e in events if e["kind"].startswith("step-") and nodeid_part in e.get("nodeid", "")
    ]


def test_step_markers_bracket_each_step(run_suite):
    target, _result = run_suite(_STEPPED, step_events=True)
    events = _lines(target / "order-lifecycle__fr-mobile.jsonl")

    assert _steps(events, "test_passes") == [
        ("step-start", 1),
        ("step-end", 1),
        ("step-start", 2),
        ("step-end", 2),
    ]


def test_a_failing_step_leaves_its_start_unclosed(run_suite):
    """The unclosed marker *is* the answer to "which step failed" — the
    generated body's ``_steppy_end_step`` is simply never reached."""
    target, _result = run_suite(_STEPPED, step_events=True)
    events = _lines(target / "order-lifecycle__fr-mobile.jsonl")

    assert _steps(events, "test_fails_mid_flow") == [
        ("step-start", 1),
        ("step-end", 1),
        ("step-start", 2),
    ]


def test_a_failure_after_every_step_closed_leaves_nothing_open(run_suite):
    """The teardown-ish case: blaming the last step here would point a reader
    at code that worked."""
    target, _result = run_suite(_STEPPED, step_events=True)
    events = _lines(target / "order-lifecycle__fr-mobile.jsonl")

    assert _steps(events, "test_fails_between_steps") == [("step-start", 1), ("step-end", 1)]


def test_step_records_carry_title_action_and_attribution(run_suite):
    target, _result = run_suite(_STEPPED, step_events=True)
    events = _lines(target / "order-lifecycle__nl-desktop.jsonl")

    starts = [e for e in events if e["kind"] == "step-start"]
    assert starts, "no step-start records"
    first = starts[0]
    assert (first["title"], first["action"]) == ("Open the list", "navigate")
    for event in events:
        assert event["scenario"] == "order-lifecycle"
        assert (event["locale"], event["layout"]) == ("nl", "desktop")
    # step-end is deliberately lean — the number is enough to close the bracket.
    end = next(e for e in events if e["kind"] == "step-end")
    assert "title" not in end and "action" not in end


def test_step_events_are_inert_without_the_gate(run_suite):
    """The marker calls are emitted unconditionally into every generated file,
    so they must cost nothing when the gate is off."""
    target, result = run_suite(_STEPPED, step_events=False)
    events = _lines(target / "order-lifecycle__fr-mobile.jsonl")
    assert events, "the test-level records should still be written"
    assert not [e for e in events if e["kind"].startswith("step-")]
    result.assert_outcomes(passed=2, failed=4)


def test_step_markers_never_fail_a_test(run_suite):
    """With no events dir at all the helpers cannot even import their sink."""
    _target, result = run_suite(_STEPPED, events_dir=None, step_events=True)
    result.assert_outcomes(passed=2, failed=4)


def test_plugin_defines_no_hook_the_conftest_also_defines():
    """The generated conftest imports these hooks BY NAME into its own
    namespace, so a collision would silently shadow the conftest's own version
    of the hook. Pin the intersection at empty."""
    import ast

    plugin = ast.parse(_PLUGIN_PATH.read_text(encoding="utf-8"))
    plugin_hooks = {
        node.name for node in plugin.body if isinstance(node, ast.FunctionDef) and node.name.startswith("pytest_")
    }
    conftest_src = (Path(__file__).parent.parent / "templates" / "conftest.py.j2").read_text(encoding="utf-8")
    conftest_hooks = {
        line.split("(")[0].removeprefix("def ").strip()
        for line in conftest_src.splitlines()
        if line.startswith("def pytest_")
    }
    collisions = plugin_hooks & conftest_hooks
    assert not collisions, (
        f"_steppy_events defines {sorted(collisions)}, which conftest.py.j2 also defines. "
        "The conftest's `from _steppy_events import ...` would shadow its own hook."
    )
