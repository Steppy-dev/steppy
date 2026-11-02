"""Three `expect:` forms that generated code which failed at run time.

- `url-contains(...)` neither interpolated `{{var}}` nor escaped the path, so
  `url-contains("/invoices/{{invoice_id}}")` looked for the literal braces and a
  `?` or `.` in a query string was read as a regex.
- `toast("...")` and `row-contains("...")` asserted on a `text="..."` locator,
  which is a strict-mode violation as soon as two identical toasts (or two
  matching rows) are on screen.
"""

import ast
import re
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.parser import parse_scenario_file


@pytest.fixture
def temp_scenario_file(tmp_path):
    def _create(content: str) -> Path:
        path = tmp_path / "scenario.md"
        path.write_text(dedent(content), encoding="utf-8")
        return path

    return _create


def _gen(temp_scenario_file, expect: str) -> str:
    content = f"""
    ---
    title: Assert locators
    auth: false
    ---

    ### 1. Assert
    - role: editor
    - action: assert
    - expect: {expect}
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    return src


def _interp(s, _vars):
    """Same substitution as the generated conftest's ``_interp``."""
    for k, v in _vars.items():
        s = s.replace("{{" + k + "}}", str(v))
    return s


def _url_pattern(src: str, _vars: dict) -> re.Pattern:
    line = next(ln for ln in src.splitlines() if "to_have_url(" in ln)
    expr = line[line.index("re.compile(") : line.rindex(", timeout=")]
    return eval(expr, {"re": re, "_interp": _interp, "_vars": _vars})


def test_url_contains_interpolates_vars(temp_scenario_file):
    src = _gen(temp_scenario_file, 'url-contains("/invoices/{{invoice_id}}")')
    pattern = _url_pattern(src, {"invoice_id": "42"})
    assert pattern.match("http://app.test/invoices/42?tab=lines")
    assert not pattern.match("http://app.test/invoices/{{invoice_id}}x")


def test_url_contains_is_a_literal_match(temp_scenario_file):
    src = _gen(temp_scenario_file, "url-contains(status=0&q=a.b)")
    pattern = _url_pattern(src, {})
    assert pattern.match("http://app.test/list?status=0&q=a.b")
    # `.` is a dot, not "any character".
    assert not pattern.match("http://app.test/list?status=0&q=axb")


def test_url_contains_plain_path_still_matches(temp_scenario_file):
    src = _gen(temp_scenario_file, 'url-contains("/orders")')
    assert _url_pattern(src, {}).match("http://app.test/orders/7")


@pytest.mark.parametrize(
    "expect",
    ['toast("Facture enregistrée")', 'row-contains("Website redesign")'],
)
def test_text_locator_assertions_tolerate_duplicates(temp_scenario_file, expect):
    src = _gen(temp_scenario_file, expect)
    line = next(ln for ln in src.splitlines() if "to_be_visible(" in ln)
    assert "_vars)).first).to_be_visible(" in line
