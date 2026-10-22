"""Tests for `match` / `within` locator disambiguation (Cmd 6).

Native `click`/`hover`/`wait-for`/`assert` take one flat selector, so the moment a
selector matches >1 element Playwright throws a strict-mode violation and authors
escape to custom for `.first`/`.nth(i)`/parent-scoping. `match` (first|last|N) and
`within` (a scope selector) express that declaratively. Both default to "off", so
steps that use neither regenerate byte-identically to the pre-feature output.
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


def _gen(temp_scenario_file, step_body: str) -> str:
    content = f"""
    ---
    title: Match within
    auth: false
    ---

    ### 1. Disambiguate
{step_body}
    """
    scenario = parse_scenario_file(temp_scenario_file(content))
    src = SteppyGenerator(load_config()).generate_test_file(scenario)
    ast.parse(src)
    return src


# ── Model validation ────────────────────────────────────────────────────────


def test_match_accepts_first_last_index():
    for m in ("first", "last", "0", "3"):
        step = Step(action="click", role="editor", selector="li", match=m)
        assert step.match == m


def test_within_accepts_selector():
    step = Step(action="click", role="editor", selector="li", within=".row")
    assert step.within == ".row"


@pytest.mark.parametrize(
    "action,body",
    [
        # `confirm` resolves its button imperatively from the dialog overlay and
        # has no `selector` to disambiguate.
        ("confirm", dict(role="editor")),
        ("keyboard", dict(role="editor", value="Tab")),
        ("navigate", dict(role="editor", target="/x")),
        ("custom", dict(role="editor", code="pass")),
    ],
)
def test_match_rejected_on_actions_that_resolve_no_selector(action, body):
    with pytest.raises(ValueError, match="only valid on|does not accept"):
        Step(action=action, match="first", **body)


def test_match_is_accepted_on_the_widget_actions_it_was_widened_to():
    """`fill` and friends used to reject this, leaving `>>` chaining as the
    only way to name the n-th widget — invisible to the guide cursor."""
    assert Step(action="fill", role="editor", selector="input", value="x", match="first").match == "first"


def test_match_rejects_garbage_value():
    with pytest.raises(ValueError, match="must be first"):
        Step(action="click", role="editor", selector="li", match="middle")


# ── Codegen ─────────────────────────────────────────────────────────────────


def test_click_without_disambiguation_uses_string_form(temp_scenario_file):
    # Byte-identity guard: no within/match ⇒ the classic page.click(sel) form.
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: click
    - selector: '[data-testid="row"]'
    - screenshot: false""",
    )
    assert "editor_page.click(_interp('[data-testid=\"row\"]', _vars))" in src
    assert ".first.click()" not in src
    assert ".nth(" not in src


def test_click_match_first(temp_scenario_file):
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: click
    - selector: '[data-testid="row"]'
    - match: first
    - screenshot: false""",
    )
    assert ".locator(_interp('[data-testid=\"row\"]', _vars)).first.click()" in src


def test_click_match_index(temp_scenario_file):
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: click
    - selector: 'li'
    - match: 2
    - screenshot: false""",
    )
    assert ".locator(_interp('li', _vars)).nth(2).click()" in src


def test_click_within_scopes_parent(temp_scenario_file):
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: click
    - selector: 'app-product-select'
    - within: '[data-testid="full-box-item-row"]'
    - match: 0
    - screenshot: false""",
    )
    assert (
        "editor_page.locator(_interp('[data-testid=\"full-box-item-row\"]', _vars))"
        ".locator(_interp('app-product-select', _vars)).nth(0).click()"
    ) in src


def test_hover_match_last(temp_scenario_file):
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: hover
    - selector: 'tr'
    - match: last
    - screenshot: false""",
    )
    assert ".locator(_interp('tr', _vars)).last.hover()" in src


def test_wait_for_within_uses_locator_wait(temp_scenario_file):
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: wait-for
    - selector: '.cell'
    - within: '.table'
    - state: hidden
    - screenshot: false""",
    )
    assert ("editor_page.locator(_interp('.table', _vars)).locator(_interp('.cell', _vars)).wait_for(timeout=") in src
    assert "state='hidden'" in src


def test_assert_visible_with_match(temp_scenario_file):
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: assert
    - expect: visible([data-testid="site-list-row"])
    - match: first
    - screenshot: false""",
    )
    assert (".locator(_interp('[data-testid=\"site-list-row\"]', _vars)).first") in src
    assert ".to_be_visible(" in src


def test_assert_without_disambiguation_is_unchanged(temp_scenario_file):
    # Byte-identity: a plain assert keeps the exact pre-feature locator form.
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: assert
    - expect: visible([data-testid="x"])
    - screenshot: false""",
    )
    assert ("expect(editor_page.locator(_interp('[data-testid=\"x\"]', _vars))).to_be_visible(") in src


# ── Widened to every action that resolves a selector ────────────────────────
#
# The original four (click/hover/wait-for/assert) left every widget-driving
# action with no way to name the n-th widget. Playwright's own `>>` chaining was
# the workaround — one project uses it 159 times — but it buries the
# disambiguation inside an opaque selector string, which guide mode cannot
# resolve a bbox for. Each case below also pins that omitting both fields keeps
# the pre-feature output.


def test_fill_resolves_through_the_locator_chain(temp_scenario_file):
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: fill
    - selector: '#q'
    - value: hi
    - match: '2'
    - screenshot: false""",
    )
    assert "editor_page.locator(_interp('#q', _vars)).nth(2).fill(_interp_val('hi', _vars))" in src


def test_fill_without_disambiguation_keeps_the_two_argument_call(temp_scenario_file):
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: fill
    - selector: '#q'
    - value: hi
    - screenshot: false""",
    )
    assert "editor_page.fill(_interp('#q', _vars), _interp_val('hi', _vars))" in src


def test_scroll_accepts_within_and_match(temp_scenario_file):
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: scroll
    - selector: '.row'
    - within: '#panel'
    - match: last
    - screenshot: false""",
    )
    assert "editor_page.locator(_interp('#panel', _vars)).locator(_interp('.row', _vars)).last" in src


def test_upload_accepts_within(temp_scenario_file):
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: upload
    - selector: 'input[type=file]'
    - value: a.png
    - within: '#form'
    - screenshot: false""",
    )
    assert (
        "editor_page.locator(_interp('#form', _vars)).locator(_interp('input[type=file]', _vars)).set_input_files("
        in src
    )


def test_select_option_disambiguates_its_trigger(temp_scenario_file):
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: select-option
    - selector: '.dd'
    - option: first
    - match: '1'
    - screenshot: false""",
    )
    assert "editor_page.locator(_interp('.dd', _vars)).nth(1).click()" in src


def test_pick_date_folds_the_chain_into_its_selector_string(temp_scenario_file):
    """`_pick_date` takes a selector, not a locator; Playwright's `>>` engine
    composes the same thing without changing the conftest helper's signature."""
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: pick-date
    - selector: '.dp'
    - date: next-monday
    - match: first
    - screenshot: false""",
    )
    assert "_interp('.dp >> nth=0', _vars)" in src


def test_pick_date_without_disambiguation_passes_the_bare_selector(temp_scenario_file):
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: pick-date
    - selector: '.dp'
    - date: next-monday
    - screenshot: false""",
    )
    assert "_interp('.dp', _vars)" in src


def test_a_disambiguated_widget_gets_a_real_locator_bbox(temp_scenario_file):
    """The point of widening rather than telling authors to use `>>`: the guide
    cursor needs a locator, and `_safe_bbox_scrolled` only takes a selector."""
    src = _gen(
        temp_scenario_file,
        """\
    - role: editor
    - action: toggle
    - selector: '.sw'
    - state: 'on'
    - match: '3'
    - screenshot: editor""",
    )
    assert "_bbox = _safe_bbox_locator(editor_page, editor_page.locator(_interp('.sw', _vars)).nth(3))" in src
