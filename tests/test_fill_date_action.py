"""Tests for the `fill-date` action (Cmd 3): relative / dynamic dates.

Tests constantly need "this Monday", "next Friday", "today + 7 days" computed AT
RUN TIME (so they stay valid forever) and typed into a date input — every project
re-implemented the same datetime/timedelta/strftime boilerplate. `fill-date`
emits a `_resolve_date(expr, fmt)` call plus sequential typing + Tab.

The `_resolve_date` runtime helper lives in `conftest.py.j2`. Its body is plain
Python (no Jinja tags), so we extract and exec it to unit-test the date math
against the real source rather than a copy.
"""

import ast
import datetime
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.guide import _compute_cursor_behavior
from steppy.models import Step
from steppy.parser import parse_scenario_file

_CONFTEST = Path(__file__).resolve().parents[1] / "templates" / "conftest.py.j2"


def _load_resolve_date():
    """Extract the `_resolve_date_obj` + `_resolve_date` helpers from the template
    and exec them (the formatter delegates to the date-object helper)."""
    text = _CONFTEST.read_text(encoding="utf-8")
    start = text.index("def _resolve_date_obj(")
    end = text.index("def _pick_date(")
    snippet = text[start:end]
    ns: dict = {}
    exec(compile(snippet, "<resolve_date>", "exec"), ns)
    return ns["_resolve_date"]


resolve_date = _load_resolve_date()


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
    title: Fill date
    auth: false
    ---

    ### 1. Type a date
    - role: editor
    - action: fill-date
    - selector: '[data-testid="start-date"] input'
{body}
    - screenshot: false
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    return src


# ── Date-expression resolution (real runtime helper) ─────────────────────────


def test_today():
    assert resolve_date("today", "%Y-%m-%d") == datetime.date.today().isoformat()


def test_this_monday_is_a_monday_in_current_week():
    d = datetime.datetime.strptime(resolve_date("this-monday", "%Y-%m-%d"), "%Y-%m-%d").date()
    assert d.weekday() == 0
    today = datetime.date.today()
    assert abs((today - d).days) < 7


def test_next_friday_is_future_friday():
    d = datetime.datetime.strptime(resolve_date("next-friday", "%Y-%m-%d"), "%Y-%m-%d").date()
    assert d.weekday() == 4
    assert d > datetime.date.today()


def test_plus_seven_days():
    expected = datetime.date.today() + datetime.timedelta(days=7)
    assert resolve_date("+7d", "%Y-%m-%d") == expected.isoformat()


def test_minus_one_month():
    today = datetime.date.today()
    out = datetime.datetime.strptime(resolve_date("-1m", "%Y-%m-%d"), "%Y-%m-%d").date()
    # One calendar month earlier (clamped to month length).
    assert (today.year - out.year) * 12 + (today.month - out.month) == 1


def test_iso_literal_reformatted():
    assert resolve_date("2026-06-15", "%d/%m/%Y") == "15/06/2026"


def test_default_format_is_ddmmyyyy():
    assert resolve_date("2026-06-15") == "15/06/2026"


# ── Model validation ────────────────────────────────────────────────────────


def test_fill_date_requires_selector():
    with pytest.raises(ValueError, match="requires 'selector'"):
        Step(action="fill-date", role="editor", date="today")


def test_fill_date_requires_date():
    with pytest.raises(ValueError, match="requires 'date'"):
        Step(action="fill-date", role="editor", selector="input")


def test_fill_date_rejects_calendar_method():
    with pytest.raises(ValueError, match="calendar"):
        Step(action="fill-date", role="editor", selector="input", date="today", method="calendar")


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_codegen_emits_resolve_date_and_typing(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - date: this-monday")
    assert "_resolve_date('this-monday', '%d/%m/%Y')" in src
    assert ".press_sequentially(_date, delay=50)" in src
    assert "keyboard.press('Tab')" in src


def test_codegen_custom_format(temp_scenario_file):
    src = _gen(temp_scenario_file, "    - date: +7d\n    - format: '%Y-%m-%d'")
    assert "_resolve_date('+7d', '%Y-%m-%d')" in src


# ── Guide cursor ────────────────────────────────────────────────────────────


def test_fill_date_cursor_is_type_on_selector():
    step = Step(action="fill-date", role="editor", selector="input.date", date="this-monday")
    sidecar = {
        "target": {"kind": "single", "selector": "x", "bbox": {"x": 1, "y": 2, "width": 3, "height": 4}},
    }
    behavior = _compute_cursor_behavior(step, sidecar)
    assert behavior["kind"] == "type"
    assert behavior["target"]["selector"] == "input.date"
    assert behavior["value"] == "this-monday"


# ── capture from.type: date ─────────────────────────────────────────────────
#
# Steppy already owned this grammar; it was reachable only from inside
# `fill-date`/`pick-date`, i.e. only to TYPE a date into a field. The corpus
# needs the resolved value as a selector fragment
# (`[data-testid="agenda-calendar-day-{{target_day}}"]`) and as an ISO week —
# 296 blocks in one project compute a date by hand, and 18 notes in the other
# say so outright. Capturing it adds no date code at all.


def test_capture_accepts_the_date_source():
    step = Step(
        action="capture",
        role="editor",
        store="target_day",
        **{"from": {"type": "date", "date": "next-monday", "format": "%Y-%m-%d"}},
    )
    assert step.from_["type"] == "date"


def test_capture_date_requires_the_expression():
    with pytest.raises(ValueError, match="from.type=date requires 'date'"):
        Step(action="capture", role="editor", store="d", **{"from": {"type": "date"}})


def test_capture_date_needs_no_role_because_it_reads_no_page():
    assert Step(action="capture", store="d", **{"from": {"type": "date", "date": "today"}}).store == "d"


def test_a_captured_date_interpolates_into_a_later_selector(temp_scenario_file):
    content = """
    ---
    title: Date capture
    auth: false
    ---

    ### 1. Capture
    - role: editor
    - action: capture
    - store: target_day
    - from: {type: date, date: next-monday, format: '%Y-%m-%d'}

    ### 2. Click
    - role: editor
    - action: click
    - selector: '[data-testid="day-{{target_day}}"]'
    """
    src = SteppyGenerator(load_config()).generate_test_file(parse_scenario_file(temp_scenario_file(content)))
    ast.parse(src)
    assert '"type": "date"' in src
    assert "_vars['target_day'] = capture(" in src
    assert "_interp('[data-testid=\"day-{{target_day}}\"]', _vars)" in src


@pytest.mark.parametrize(
    "fmt,expected",
    [("%Y-%m-%d", "2026-08-31"), ("%V", "36"), ("%G", "2026")],
)
def test_the_date_source_reuses_the_existing_resolver(fmt, expected):
    """`%V`/`%G` are what covers the ISO-week blocks; strftime already had them,
    so nothing new had to be written to reach them."""
    assert resolve_date("2026-08-31", fmt) == expected
