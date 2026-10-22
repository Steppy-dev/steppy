"""Required-field validation for the core actions (FLAWS §2.4).

Before this, a ``click`` with no ``selector`` (or a ``navigate`` with no
``target``) parsed cleanly and only failed at runtime with a baffling Playwright
timeout on the literal selector ``"None"``. These tests pin that such steps now
fail at model-construction / ``steppy validate`` time.
"""

import pytest
from pydantic import ValidationError

from steppy.models import Step


# ── missing required fields raise ────────────────────────────────────────────


def test_click_requires_selector():
    with pytest.raises(ValidationError, match="click action requires 'selector'"):
        Step(action="click", role="editor")


def test_hover_requires_selector():
    with pytest.raises(ValidationError, match="hover action requires 'selector'"):
        Step(action="hover", role="editor")


def test_scroll_requires_selector():
    with pytest.raises(ValidationError, match="scroll action requires 'selector'"):
        Step(action="scroll", role="editor")


def test_wait_for_requires_selector():
    with pytest.raises(ValidationError, match="wait-for action requires 'selector'"):
        Step(action="wait-for", role="editor")


def test_fill_requires_value():
    with pytest.raises(ValidationError, match="fill action requires 'value'"):
        Step(action="fill", role="editor", selector="#x")


def test_fill_requires_selector():
    with pytest.raises(ValidationError, match="fill action requires 'selector'"):
        Step(action="fill", role="editor", value="hi")


def test_select_requires_value():
    with pytest.raises(ValidationError, match="select action requires 'value'"):
        Step(action="select", role="editor", selector="#x")


def test_keyboard_requires_value():
    with pytest.raises(ValidationError, match="keyboard action requires 'value'"):
        Step(action="keyboard", role="editor")


def test_wait_for_text_requires_value():
    with pytest.raises(ValidationError, match="wait-for-text action requires 'value'"):
        Step(action="wait-for-text", role="editor")


def test_wait_requires_duration():
    with pytest.raises(ValidationError, match="wait action requires 'duration'"):
        Step(action="wait", role="editor")


def test_blank_selector_is_rejected():
    with pytest.raises(ValidationError, match="click action requires 'selector'"):
        Step(action="click", role="editor", selector="   ")


# ── navigate target / reload exemption ───────────────────────────────────────


def test_navigate_requires_target():
    with pytest.raises(ValidationError, match="navigate action requires 'target'"):
        Step(action="navigate", role="editor")


def test_navigate_reload_without_target_ok():
    # reload: true is a legitimate navigate with no target
    Step(action="navigate", role="editor", reload=True)


# ── role requirement for DOM actions ─────────────────────────────────────────


def test_dom_action_requires_role():
    with pytest.raises(ValidationError, match="click action requires 'role'"):
        Step(action="click", selector="#x")


def test_wait_does_not_require_role():
    # wait is intentionally exempt — it needs no page/role
    Step(action="wait", duration="3s")


# ── valid steps still construct ──────────────────────────────────────────────


def test_valid_click_ok():
    Step(action="click", role="editor", selector="#x")


def test_valid_fill_ok():
    Step(action="fill", role="editor", selector="#x", value="hello")


def test_valid_navigate_ok():
    Step(action="navigate", role="editor", target="/orders")
