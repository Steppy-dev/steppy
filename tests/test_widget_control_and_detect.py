"""``control:`` on ``toggle``/``expand`` and ``detect:``/``state:`` on ``expand``.

These exist because of a measurement, not a feature request. Across ~8 500
authored steps in one project, ``toggle`` and ``expand`` were used **zero**
times while 141 custom blocks called ``.check()`` and 128 called an
``ensure_open`` helper. The cause is in the codegen, not the authors:

  * ``toggle`` always builds ``locator(selector).locator(config.toggle.switch)``,
    so a selector aimed at the checkbox itself resolves to nothing;
  * ``expand`` decides openness from the *content* element's visibility, while a
    disclosure button exposes ``aria-expanded`` — which is exactly what the
    project's own helper reads.

Byte-identity when the new fields are absent is the load-bearing property: both
actions already ship, and their existing output must not move.
"""

import ast
from textwrap import dedent

import pytest

from steppy.config import load_config
from steppy.generator import SteppyGenerator
from steppy.models import Step
from steppy.parser import parse_scenario_file


@pytest.fixture
def gen_step(tmp_path):
    def _gen(body: str) -> str:
        path = tmp_path / "scenario.md"
        path.write_text("---\ntitle: T\nauth: false\n---\n\n" + dedent(body), encoding="utf-8")
        src = SteppyGenerator(load_config()).generate_test_file(parse_scenario_file(path))
        ast.parse(src)
        return src

    return _gen


# ── toggle: reaching a native checkbox ──────────────────────────────────────

_TOGGLE_DEFAULT = """
### 1. Toggle
- role: editor
- action: toggle
- selector: '#s'
- state: on
"""


def test_toggle_without_control_keeps_the_descendant_hop(gen_step):
    src = gen_step(_TOGGLE_DEFAULT)
    assert "_switch = editor_page.locator(_interp('#s', _vars)).locator('input[role=\"switch\"]')" in src


def test_control_self_targets_the_selector_itself(gen_step):
    """The 93 blocks whose data-testid is on the input, not on a wrapper."""
    src = gen_step("""
    ### 1. Toggle
    - role: editor
    - action: toggle
    - selector: '[data-testid="cb"]'
    - control: self
    - state: on
    """)
    assert "_switch = editor_page.locator(_interp('[data-testid=\"cb\"]', _vars))\n" in src
    assert "input[role=" not in src


def test_control_overrides_the_config_selector_for_one_step(gen_step):
    """A page with two widget flavours no longer needs a global config change."""
    src = gen_step("""
    ### 1. Toggle
    - role: editor
    - action: toggle
    - selector: '#wrap'
    - control: 'input[type=checkbox]'
    - state: on
    """)
    assert ".locator(_interp('input[type=checkbox]', _vars))" in src


def test_toggle_keeps_its_idempotent_guard(gen_step):
    """Strictly better than the unconditional `.check(force=True)` it replaces."""
    src = gen_step("""
    ### 1. Toggle
    - role: editor
    - action: toggle
    - selector: '[data-testid="cb"]'
    - control: self
    - state: on
    """)
    assert "if not _switch.is_checked():" in src
    assert "_switch.check(force=True)" in src


# ── expand: reading the signal the app exposes ──────────────────────────────

_EXPAND_DEFAULT = """
### 1. Expand
- role: editor
- action: expand
- selector: '#f'
"""


def test_expand_default_output_is_unchanged(gen_step):
    """The exact three lines `expand` has always emitted."""
    src = gen_step(_EXPAND_DEFAULT)
    assert "    _content = _fieldset.locator('.p-toggleable-content, .p-fieldset-content-container').first\n" in src
    assert "    if not _content.is_visible():\n        _toggle.click()\n" in src
    assert "aria-expanded" not in src


def test_detect_aria_reads_the_toggle_instead_of_the_content(gen_step):
    src = gen_step("""
    ### 1. Expand
    - role: editor
    - action: expand
    - selector: '[data-testid="toggle"]'
    - control: self
    - detect: aria
    """)
    assert 'if (_toggle.get_attribute("aria-expanded") or "false") != "true":' in src
    # No content element is resolved at all in aria mode — there may not be one.
    assert "_content" not in src


def test_detect_aria_waits_for_the_toggle_before_reading_it(gen_step):
    """`get_attribute` does not auto-wait, so an unwaited read races the render."""
    src = gen_step("""
    ### 1. Expand
    - role: editor
    - action: expand
    - selector: '#t'
    - control: self
    - detect: aria
    """)
    assert '_toggle.wait_for(state="visible"' in src


def test_state_off_collapses_instead_of_expanding(gen_step):
    src = gen_step("""
    ### 1. Expand
    - role: editor
    - action: expand
    - selector: '#f'
    - state: off
    """)
    assert "    if _content.is_visible():\n        _toggle.click()\n" in src
    assert '_content.wait_for(state="hidden"' in src


def test_state_off_with_aria(gen_step):
    src = gen_step("""
    ### 1. Expand
    - role: editor
    - action: expand
    - selector: '#t'
    - control: self
    - detect: aria
    - state: off
    """)
    assert 'if (_toggle.get_attribute("aria-expanded") or "false") == "true":' in src


def test_expand_screenshot_still_targets_the_toggle(gen_step):
    src = gen_step("""
    ### 1. Expand
    - role: editor
    - action: expand
    - selector: '#t'
    - control: self
    - detect: aria
    - screenshot: editor
    """)
    assert "_bbox = _safe_bbox_locator(editor_page, _toggle)" in src


# ── validation ──────────────────────────────────────────────────────────────


def test_expand_state_reuses_toggles_on_off_spelling():
    """`open`/`closed` would give one concept two spellings."""
    with pytest.raises(ValueError, match="expand 'state' must be on|off"):
        Step(action="expand", role="editor", selector="#f", state="visible")


@pytest.mark.parametrize("action", ["click", "fill", "select-option"])
def test_control_is_rejected_where_there_is_no_control_to_point_at(action):
    body = {
        "click": dict(selector="#a"),
        "fill": dict(selector="#a", value="v"),
        "select-option": dict(selector="#a", option="first"),
    }[action]
    with pytest.raises(ValueError, match="does not accept"):
        Step(action=action, role="editor", control="self", **body)


def test_detect_is_rejected_on_toggle():
    """`toggle` reads `is_checked()`, which has no aria equivalent to choose."""
    with pytest.raises(ValueError, match="does not accept"):
        Step(action="toggle", role="editor", selector="#s", detect="aria")
