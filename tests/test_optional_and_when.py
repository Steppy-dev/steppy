"""``optional:`` and ``when:`` — conditional execution against live page state.

``only-in:`` and ``layouts:`` look like guards but gate on the run's *mode*, not
on what the page is showing. Nothing expressed "click it if it is there", so 142
custom blocks in one project and 17 in the other wrap the step in ``try:`` or an
``if ... .count():`` by hand; five of the second project's ``kept custom`` notes
name it outright ("an optional confirm plus a wait on a banner appearing then
clearing", "a click that falls back between two conditionally-rendered buttons").

Where each wrapper lands is load-bearing:

  * ``optional:`` goes INSIDE the step markers — the step really did run, and
    ``_steppy_end_step`` must be reached or ``failed_step`` would blame a step
    that merely opted out.
  * ``when:`` goes OUTSIDE them, joining ``_step_runs`` — a step whose condition
    is false did not run, and must emit neither marker.
"""

import ast
import re
import time
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.models import Step
from steppy.parser import parse_scenario_file

_CONFTEST = Path(__file__).resolve().parents[1] / "templates" / "conftest.py.j2"


def _load_when():
    text = _CONFTEST.read_text(encoding="utf-8")
    start = text.index("_WAIT_COMPARATOR = re.compile")
    end = text.index("def call_helper(")
    ns: dict = {"re": re, "time": time, "_interp": lambda s, v: s}
    exec(compile(text[start:end], "<when>", "exec"), ns)
    return ns["_when"]


when_predicate = _load_when()


class _FakePage:
    def __init__(self, count=0, visible=True, text=""):
        self._count, self._visible, self._text = count, visible, text

    def locator(self, _selector):
        return self

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def is_visible(self):
        return self._visible

    def inner_text(self):
        return self._text


# ── The runtime predicate ───────────────────────────────────────────────────


def test_visible_is_false_when_nothing_matches():
    assert when_predicate(_FakePage(count=0), "visible(#a)", {}) is False


def test_visible_is_true_when_one_matches_and_shows():
    assert when_predicate(_FakePage(count=1, visible=True), "visible(#a)", {}) is True


def test_visible_is_false_when_present_but_not_shown():
    assert when_predicate(_FakePage(count=1, visible=False), "visible(#a)", {}) is False


def test_hidden_is_the_negation():
    assert when_predicate(_FakePage(count=0), "hidden(#a)", {}) is True
    assert when_predicate(_FakePage(count=1, visible=True), "hidden(#a)", {}) is False


def test_count_honours_the_comparator():
    assert when_predicate(_FakePage(count=3), "count(#a, >=2)", {}) is True
    assert when_predicate(_FakePage(count=1), "count(#a, >=2)", {}) is False


def test_count_accepts_a_bare_integer():
    assert when_predicate(_FakePage(count=0), "count(#a, 0)", {}) is True


def test_contains_is_false_when_the_element_is_absent():
    """It must answer *now*, so an absent element is False, not a 30s wait."""
    assert when_predicate(_FakePage(count=0), 'contains(#a, "Hi")', {}) is False


def test_contains_matches_a_substring():
    assert when_predicate(_FakePage(count=1, text="Well Hi there"), 'contains(#a, "Hi")', {}) is True


def test_an_unsupported_condition_raises():
    with pytest.raises(AssertionError, match="unsupported condition"):
        when_predicate(_FakePage(), "toast(#a)", {})


# ── Codegen ─────────────────────────────────────────────────────────────────


@pytest.fixture
def gen_step(tmp_path):
    def _gen(body: str) -> str:
        path = tmp_path / "scenario.md"
        tail = "\n### 9. Tail\n- role: editor\n- action: click\n- selector: '#z'\n"
        path.write_text("---\ntitle: T\nauth: false\n---\n\n" + dedent(body) + tail, encoding="utf-8")
        src = SteppyGenerator(load_config()).generate_test_file(parse_scenario_file(path))
        ast.parse(src)
        return src[: src.index("_steppy_mark_step(2")]

    return _gen


def test_neither_flag_leaves_the_step_untouched(gen_step):
    src = gen_step("""
    ### 1. C
    - role: editor
    - action: click
    - selector: '#a'
    """)
    body = src[src.index("_steppy_mark_step(1") :]
    assert "try:" not in body
    assert "_step_runs" not in body


def test_optional_wraps_the_body_inside_the_markers(gen_step):
    src = gen_step("""
    ### 1. C
    - role: editor
    - action: click
    - selector: '#a'
    - optional: true
    """)
    assert src.index("_steppy_mark_step(1") < src.index("try:")
    assert src.index("except Exception as _optional_err:") < src.index("_steppy_end_step(1")


def test_optional_reports_what_it_swallowed(gen_step):
    """A silent skip is indistinguishable from a step that never existed."""
    src = gen_step("""
    ### 1. C
    - role: editor
    - action: confirm
    - optional: true
    """)
    assert 'print(f"STEPPY: optional step 1 skipped: {_optional_err}", flush=True)' in src


def test_when_guards_outside_the_markers(gen_step):
    src = gen_step("""
    ### 1. C
    - role: editor
    - action: click
    - selector: '#a'
    - when: visible(#banner)
    """)
    guard = "if _step_runs(None, None) and _when(editor_page, 'visible(#banner)', _vars):"
    assert guard in src
    assert src.index(guard) < src.index("_steppy_mark_step(1")


def test_when_composes_with_the_mode_filters(gen_step):
    src = gen_step("""
    ### 1. C
    - role: editor
    - action: click
    - selector: '#a'
    - when: 'count(#row, >=1)'
    - layouts: [desktop]
    """)
    assert "if _step_runs(None, ['desktop']) and _when(editor_page, 'count(#row, >=1)', _vars):" in src


def test_both_flags_nest_in_the_right_order(gen_step):
    src = gen_step("""
    ### 1. C
    - role: editor
    - action: click
    - selector: '#a'
    - when: visible(#b)
    - optional: true
    """)
    assert src.index("_when(") < src.index("_steppy_mark_step(1") < src.index("try:")


# ── Validation ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cond", ["visible(#a)", "hidden(#a)", "count(#a, >=2)", 'contains(#a, "Hi")'])
def test_the_four_immediate_conditions_are_accepted(cond):
    assert Step(action="click", role="editor", selector="#a", when=cond).when == cond


@pytest.mark.parametrize("cond", ['toast("x")', "url-contains(/a)", "visible(#a", "nope(#a)"])
def test_conditions_that_would_auto_wait_or_do_not_exist_are_rejected(cond):
    """`when:` is deliberately a smaller vocabulary than `expect:`: an assertion
    auto-waits, and a guarded step must not pay a timeout for something that was
    never going to appear."""
    with pytest.raises(ValueError, match="unknown 'when' condition"):
        Step(action="click", role="editor", selector="#a", when=cond)


def test_when_needs_a_role_because_it_reads_the_page():
    with pytest.raises(ValueError, match="'when' reads the page"):
        Step(action="wait", duration="1s", when="visible(#a)")


def test_optional_is_universal():
    assert Step(action="wait", duration="1s", optional=True).optional is True
