"""Per-step ``timeout:`` and ``force:``.

Both are field extensions, not new actions, so the load-bearing property is that
omitting them regenerates byte-identically: the corpus that motivated them is
made of scenarios that already exist.

``timeout:`` because the only knob was the global ``config.timing.step_timeout``,
which cannot serve both a 5s dropdown and a 6-minute report generation — 42% of
one project's custom blocks carry a hand-written ``timeout=``. ``force:`` because
``click`` could not pass it at all, and 896 custom blocks exist for that reason
alone. ``synthetic:`` is not a substitute: it dispatches untrusted MouseEvents
and bypasses real input events.
"""

import ast
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.durations import DurationSyntaxError, parse_ms
from steppy.generator import SteppyGenerator
from steppy.models import Step
from steppy.parser import parse_scenario_file


@pytest.fixture
def gen_steps(tmp_path):
    def _gen(body: str) -> str:
        path = tmp_path / "scenario.md"
        path.write_text("---\ntitle: T\nauth: false\n---\n\n" + dedent(body), encoding="utf-8")
        src = SteppyGenerator(load_config()).generate_test_file(parse_scenario_file(path))
        ast.parse(src)  # the generated file must always be valid Python
        return src

    return _gen


def _lines(src: str, needle: str) -> list[str]:
    return [ln.strip() for ln in src.splitlines() if needle in ln and not ln.strip().startswith("#")]


# ── The duration grammar ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,ms",
    [("25s", 25000), ("500ms", 500), (25000, 25000), ("25000", 25000), ("1.5s", 1500)],
)
def test_duration_grammar_matches_the_wait_action(value, ms):
    """Bare numbers are milliseconds, the same rule `wait`'s `duration:` uses."""
    assert parse_ms(value) == ms


@pytest.mark.parametrize("bad", ["fast", "5m", "-5s", 0, True, ""])
def test_bad_durations_are_rejected(bad):
    with pytest.raises(DurationSyntaxError):
        parse_ms(bad)


def test_a_bad_timeout_fails_at_validate_not_at_run_time():
    with pytest.raises(ValueError, match="timeout"):
        Step(action="wait-for", role="editor", selector="#a", timeout="fast")


# ── Byte-identity when unset ────────────────────────────────────────────────

_UNFLAGGED = """
### 1. Click
- role: editor
- action: click
- selector: '#a'

### 2. Wait
- role: editor
- action: wait-for
- selector: '#b'
- state: visible

### 3. Assert
- role: editor
- action: assert
- expect: toast("Saved")

### 4. Go
- role: editor
- action: navigate
- target: /orders
"""


def test_omitting_both_flags_emits_no_extra_kwargs(gen_steps):
    src = gen_steps(_UNFLAGGED)
    assert _lines(src, "_page.click") == ["editor_page.click(_interp('#a', _vars))"]
    assert _lines(src, "_page.goto") == ["editor_page.goto(_interp('/orders', _vars))"]


def test_the_global_default_still_reaches_the_waits_that_always_had_one(gen_steps):
    src = gen_steps(_UNFLAGGED)
    default = load_config().timing.step_timeout
    assert f"timeout={default}" in _lines(src, "wait_for_selector")[0]


def test_toast_keeps_its_own_tighter_default(gen_steps):
    """``toast()`` has always used 5s, not step_timeout. Rewiring it would have
    changed the behaviour of every existing toast assertion."""
    assert "timeout=5000" in _lines(gen_steps(_UNFLAGGED), "to_be_visible")[0]


# ── The flags, when set ─────────────────────────────────────────────────────


def test_timeout_reaches_an_action_that_emitted_none_before(gen_steps):
    src = gen_steps("""
    ### 1. Click
    - role: editor
    - action: click
    - selector: '#a'
    - timeout: 5s
    """)
    assert _lines(src, "_page.click") == ["editor_page.click(_interp('#a', _vars), timeout=5000)"]


def test_timeout_overrides_the_global_on_a_wait(gen_steps):
    src = gen_steps("""
    ### 1. Wait
    - role: editor
    - action: wait-for
    - selector: '#b'
    - state: visible
    - timeout: 300s
    """)
    assert "timeout=300000" in _lines(src, "wait_for_selector")[0]


def test_timeout_overrides_the_toast_default(gen_steps):
    src = gen_steps("""
    ### 1. Assert
    - role: editor
    - action: assert
    - expect: toast("Saved")
    - timeout: 2s
    """)
    assert "timeout=2000" in _lines(src, "to_be_visible")[0]


def test_force_on_a_plain_click(gen_steps):
    src = gen_steps("""
    ### 1. Click
    - role: editor
    - action: click
    - selector: '#a'
    - force: true
    """)
    assert _lines(src, "_page.click") == ["editor_page.click(_interp('#a', _vars), force=True)"]


def test_force_on_a_disambiguated_click_has_no_stray_comma(gen_steps):
    """``locator(...).first.click()`` has no positional argument to comma-follow."""
    src = gen_steps("""
    ### 1. Click
    - role: editor
    - action: click
    - selector: '#row'
    - match: first
    - force: true
    """)
    assert _lines(src, ".click(") == ["editor_page.locator(_interp('#row', _vars)).first.click(force=True)"]


def test_both_flags_together(gen_steps):
    src = gen_steps("""
    ### 1. Click
    - role: editor
    - action: click
    - selector: '#a'
    - force: true
    - timeout: 5s
    """)
    assert _lines(src, "_page.click") == ["editor_page.click(_interp('#a', _vars), timeout=5000, force=True)"]


# ── Scenario-level default ──────────────────────────────────────────────────


def _render_scenario(tmp_path, frontmatter: str, body: str) -> str:
    path = tmp_path / "s.md"
    path.write_text(f"---\ntitle: T\nauth: false\n{frontmatter}---\n\n" + dedent(body), encoding="utf-8")
    return SteppyGenerator(load_config()).generate_test_file(parse_scenario_file(path))


_ONE_WAIT = """
### 1. Wait
- role: editor
- action: wait-for
- selector: '#b'
"""


def test_scenario_timeout_is_finally_wired(tmp_path):
    """``Scenario.timeout`` shipped in the first release and was read by nothing."""
    src = _render_scenario(tmp_path, "timeout: 90000\n", _ONE_WAIT)
    assert "timeout=90000" in _lines(src, "wait_for_selector")[0]


def test_a_step_timeout_beats_the_scenario_default(tmp_path):
    src = _render_scenario(tmp_path, "timeout: 90000\n", _ONE_WAIT + "- timeout: 3s\n")
    assert "timeout=3000" in _lines(src, "wait_for_selector")[0]


# ── Action scoping ──────────────────────────────────────────────────────────


def test_timeout_is_rejected_on_wait_whose_duration_is_the_wait():
    with pytest.raises(ValueError, match="wait action does not accept"):
        Step(action="wait", duration="1s", timeout="5s")


@pytest.mark.parametrize(
    "action,body",
    [("custom", dict(role="n", code="pass")), ("keyboard", dict(role="n", value="Tab"))],
)
def test_timeout_is_rejected_where_nothing_would_consume_it(action, body):
    with pytest.raises(ValueError, match="does not accept"):
        Step(action=action, timeout="5s", **body)


@pytest.mark.parametrize(
    "action,body",
    [
        ("upload", dict(role="n", selector="#a", value="f.png")),  # set_input_files takes no force
        ("toggle", dict(role="n", selector="#a")),  # already forces unconditionally
    ],
)
def test_force_is_rejected_where_playwright_has_no_such_argument(action, body):
    with pytest.raises(ValueError, match="does not accept"):
        Step(action=action, force=True, **body)
