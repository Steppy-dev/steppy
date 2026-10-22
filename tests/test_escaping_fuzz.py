"""Property/fuzz tests for string escaping in generated pytest code (FLAWS §1.1).

Scenario selectors and values used to be interpolated straight into single-quoted
Python string literals (``_interp('{{ step.selector }}', _vars)``), so any value
containing an apostrophe/quote/backslash produced a ``SyntaxError`` at pytest
collection — surfacing as an opaque collection error, never at ``steppy validate``.
For a tool whose flagship deployments are French-language apps, ``L'adresse``
and ``toast("L'élément a été créé")`` are not edge cases.

The fix routes every interpolated value through the ``pystr`` Jinja filter
(``repr(str(value))``), which emits a well-formed Python string literal. These
tests assert the generated file *always parses* (``ast.parse`` — the guarantee the
byte-identical golden tests over curated inputs could never give) and that the
value survives escaping intact (found among the file's string constants).
"""

import ast
import json
from pathlib import Path
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.models import SteppyConfig
from steppy.parser import parse_scenario_file


# Nasty strings that terminate/inject a naive single-quoted Python literal, plus
# unicode and the ``{{var}}`` placeholder syntax that must pass through untouched
# for runtime ``_interp`` substitution.
NASTY = [
    "L'adresse",  # the headline French apostrophe case
    "input[name='email']",  # single quotes inside a CSS selector
    'button[aria-label="Fermer"]',  # double quotes inside a CSS selector
    'O\'Brien said "hi" there',  # both quote kinds
    r"back\slash and \t tab-ish",  # backslashes
    "café ☕ 日本語 — naïve",  # unicode / accents
    "keep {{order_id}} placeholder",  # _interp placeholder must survive
    "l'apostrophe mid string ok",  # interior apostrophe (not at an end)
]


@pytest.fixture
def temp_scenario_file(tmp_path):
    def _create(content: str) -> Path:
        path = tmp_path / "scenario.md"
        path.write_text(dedent(content), encoding="utf-8")
        return path

    return _create


def _generate(temp_scenario_file, body: str) -> str:
    content = "---\ntitle: Escaping fuzz\nauth: false\n---\n\n" + dedent(body)
    scenario = parse_scenario_file(temp_scenario_file(content))
    return SteppyGenerator(load_config()).generate_test_file(scenario)


def _string_constants(src: str) -> list[str]:
    """Every string-literal constant in the generated module (post-parse)."""
    tree = ast.parse(src)  # raises SyntaxError if escaping is broken
    return [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def _assert_parses_and_contains(src: str, value: str) -> None:
    consts = _string_constants(src)  # parse-safety is the primary guarantee
    assert any(value in c for c in consts), f"value {value!r} not preserved among generated string constants"


# ── selector / value / target across the core actions ───────────────────────


@pytest.mark.parametrize("nasty", NASTY)
def test_fill_value_and_selector(temp_scenario_file, nasty):
    src = _generate(
        temp_scenario_file,
        f"""
        ### 1. Fill
        - role: editor
        - action: fill
        - selector: {nasty}
        - value: {nasty}
        """,
    )
    _assert_parses_and_contains(src, nasty)


@pytest.mark.parametrize("nasty", NASTY)
def test_click_selector(temp_scenario_file, nasty):
    src = _generate(
        temp_scenario_file,
        f"""
        ### 1. Click
        - role: editor
        - action: click
        - selector: {nasty}
        """,
    )
    _assert_parses_and_contains(src, nasty)


@pytest.mark.parametrize("nasty", NASTY)
def test_navigate_target(temp_scenario_file, nasty):
    src = _generate(
        temp_scenario_file,
        f"""
        ### 1. Navigate
        - role: editor
        - action: navigate
        - target: /orders?q={nasty}
        """,
    )
    _assert_parses_and_contains(src, f"/orders?q={nasty}")


@pytest.mark.parametrize("nasty", NASTY)
def test_keyboard_and_upload(temp_scenario_file, nasty):
    src = _generate(
        temp_scenario_file,
        f"""
        ### 1. Upload
        - role: editor
        - action: upload
        - selector: {nasty}
        - value: /tmp/{nasty}.pdf
        """,
    )
    _assert_parses_and_contains(src, nasty)


# ── assertion grammar (both the dedicated assert block and inline expect) ────


@pytest.mark.parametrize(
    "expect",
    [
        'toast("L\'élément a été créé")',
        'contains([data-testid="tag"], "l\'adresse validée")',
        "text([name='x'], \"O'Brien\")",
        'row-contains("l\'utilisateur")',
        "visible([name='email'])",
        'url-contains("/orders/l\'x")',
    ],
)
def test_assert_action_expect(temp_scenario_file, expect):
    src = _generate(
        temp_scenario_file,
        f"""
        ### 1. Assert
        - role: editor
        - action: assert
        - expect: {expect}
        """,
    )
    # Primary guarantee: the file parses (naive single-quoting raised SyntaxError).
    ast.parse(src)


def test_inline_expect_on_click(temp_scenario_file):
    """`expect` on a non-assert action goes through the *second*, duplicated
    assertion block — exercise it too."""
    src = _generate(
        temp_scenario_file,
        """
        ### 1. Click then assert
        - role: editor
        - action: click
        - selector: button[data-testid="save"]
        - expect: toast("L'élément a été créé")
        """,
    )
    ast.parse(src)
    assert "L'élément a été créé" in "".join(_string_constants(src))


# ── the two headline escaping regressions, end to end ───────────────────────


def test_headline_apostrophe_fill_value(temp_scenario_file):
    src = _generate(
        temp_scenario_file,
        """
        ### 1. Fill the order field
        - role: editor
        - action: fill
        - selector: input[name='adresse']
        - value: L'adresse
        """,
    )
    ast.parse(src)  # was a SyntaxError before the pystr filter
    consts = _string_constants(src)
    assert "L'adresse" in consts
    assert "input[name='adresse']" in consts


def test_placeholder_survives_for_interp(temp_scenario_file):
    """A ``{{var}}`` in a value must reach the generated literal verbatim so the
    runtime ``_interp`` can substitute it — repr keeps the braces."""
    src = _generate(
        temp_scenario_file,
        """
        ### 1. Fill with a captured var
        - role: editor
        - action: fill
        - selector: input[name='q']
        - value: order-{{order_id}}-done
        """,
    )
    ast.parse(src)
    assert "order-{{order_id}}-done" in _string_constants(src)


# ── frontmatter + config string values (FLAWS §8.1) ─────────────────────────
# The §1.1 layer fuzzed step selector/value/expect; these extend it to the other
# source-baked strings: the frontmatter title/description (emitted into the test
# function's triple-quoted docstring), the seed script path, and config layout
# names (dict keys in the generated conftest). ``json.dumps(..., ensure_ascii=False)``
# renders each nasty value as a valid YAML double-quoted scalar so the *parser*
# accepts it and we exercise the *codegen* path.


@pytest.mark.parametrize("nasty", NASTY)
def test_frontmatter_title_and_description(temp_scenario_file, nasty):
    """title/description flow into ``\"\"\"...\"\"\"`` — a bare ``\"`` or trailing
    ``\\`` would terminate/corrupt the docstring (a §1.1-style SyntaxError)."""
    content = (
        "---\n"
        f"title: {json.dumps(nasty, ensure_ascii=False)}\n"
        f"description: {json.dumps(nasty, ensure_ascii=False)}\n"
        "auth: false\n"
        "---\n\n"
        "### 1. Nav\n- role: editor\n- action: navigate\n- target: /\n"
    )
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)


@pytest.mark.parametrize("nasty", NASTY)
def test_seed_script_path(temp_scenario_file, nasty):
    """seed.script is spliced into ``Path('seed/<script>.py')`` — an apostrophe in
    the name broke the literal before it was routed through ``pystr``."""
    content = (
        "---\n"
        "title: Seed fuzz\n"
        "auth: false\n"
        f"seed: {json.dumps(nasty, ensure_ascii=False)}\n"
        "---\n\n"
        "### 1. Nav\n- role: editor\n- action: navigate\n- target: /\n"
    )
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)


@pytest.mark.parametrize("nasty", NASTY)
def test_config_layout_name_in_conftest(temp_scenario_file, nasty):
    """A config ``screenshot.layouts`` key is emitted as a dict key in the generated
    conftest (``LAYOUT_VIEWPORTS``/``LAYOUT_SCALE_FACTORS``) — an inner ``\"`` broke
    it before the ``tojson`` filter."""
    scenario = parse_scenario_file(
        temp_scenario_file(
            "---\ntitle: Layout fuzz\nauth: false\n---\n\n### 1. Nav\n- role: editor\n- action: navigate\n- target: /\n"
        )
    )
    cfg = SteppyConfig(
        apps={
            "editor": {
                "name": "Editor",
                "url": "http://localhost",
                "auth": {
                    "mode": "auth0",
                    "credentials": {"username": "u", "password": "p"},
                },
            }
        },
        screenshot={"layouts": {nasty: {"width": 800, "height": 600}}},
    )
    conftest_src = SteppyGenerator(cfg).generate_conftest([scenario])
    ast.parse(conftest_src)
