"""Tests for the guide cursor behavior mapping and HTML template rendering."""

from pathlib import Path

import jinja2
import pytest

from steppy.guide import _compute_cursor_behavior
from steppy.models import GuideCursorOverride, Step


def _sidecar_single(bbox=None, selector="x"):
    return {
        "step": 1,
        "screenshot": "step-01.png",
        "action": "click",
        "viewport": {"width": 1280, "height": 720},
        "target": {"kind": "single", "selector": selector, "bbox": bbox},
    }


def _sidecar_multi(bboxes):
    return {
        "step": 1,
        "screenshot": "step-01.png",
        "action": "fill-form",
        "viewport": {"width": 1280, "height": 720},
        "target": {
            "kind": "multi",
            "fields": [{"selector": f"#f{i}", "bbox": b} for i, b in enumerate(bboxes)],
        },
    }


# ── Behavior mapping ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "action",
    [
        "navigate",
        "assert",
        "wait",
        "wait-for",
        "wait-for-text",
        "auth0-login",
        "keyboard",
        "capture",
    ],
)
def test_hidden_actions_return_none(action):
    step = Step(
        action=action,
        role="editor",
        selector="x" if action in ("wait-for",) else None,
        target="/x" if action == "navigate" else None,
        value="ok" if action in ("wait-for-text", "keyboard") else None,
        duration="1s" if action == "wait" else None,
        store="v" if action == "capture" else None,
        **({"from": {"type": "shell", "command": "echo 1"}} if action == "capture" else {}),
        expect='toast("ok")' if action == "assert" else None,
    )
    assert _compute_cursor_behavior(step, _sidecar_single()) is None


def test_click_returns_click_kind_with_bbox():
    step = Step(action="click", role="editor", selector='[data-testid="btn"]')
    bbox = {"x": 10, "y": 20, "width": 100, "height": 40}
    behavior = _compute_cursor_behavior(step, _sidecar_single(bbox=bbox))
    assert behavior == {
        "kind": "click",
        "target": {"selector": '[data-testid="btn"]', "bbox": bbox},
    }


def test_select_maps_to_click_kind():
    step = Step(action="select", role="editor", selector="#dropdown", value="A")
    behavior = _compute_cursor_behavior(step, _sidecar_single(bbox={"x": 1, "y": 2, "width": 3, "height": 4}))
    assert behavior["kind"] == "click"


def test_fill_returns_type_kind_with_value():
    step = Step(action="fill", role="editor", selector="#name", value="Alice")
    bbox = {"x": 5, "y": 5, "width": 200, "height": 30}
    behavior = _compute_cursor_behavior(step, _sidecar_single(bbox=bbox))
    assert behavior == {
        "kind": "type",
        "target": {"selector": "#name", "bbox": bbox},
        "value": "Alice",
    }


def test_fill_form_returns_type_multi_in_declaration_order():
    step = Step(
        action="fill-form",
        role="editor",
        fields={"#first": "Alice", "#last": "Smith", "#email": "a@b.c"},
    )
    bboxes = [
        {"x": 1, "y": 1, "width": 10, "height": 10},
        {"x": 2, "y": 2, "width": 10, "height": 10},
        {"x": 3, "y": 3, "width": 10, "height": 10},
    ]
    behavior = _compute_cursor_behavior(step, _sidecar_multi(bboxes))
    assert behavior["kind"] == "type-multi"
    selectors = [t["selector"] for t in behavior["targets"]]
    values = [t["value"] for t in behavior["targets"]]
    target_bboxes = [t["bbox"] for t in behavior["targets"]]
    assert selectors == ["#first", "#last", "#email"]
    assert values == ["Alice", "Smith", "a@b.c"]
    assert target_bboxes == bboxes


def test_hover_and_scroll_map_to_hover_kind():
    for action in ("hover", "scroll"):
        step = Step(action=action, role="editor", selector="#thing")
        behavior = _compute_cursor_behavior(step, _sidecar_single(bbox={"x": 0, "y": 0, "width": 1, "height": 1}))
        assert behavior["kind"] == "hover"
        assert behavior["target"]["selector"] == "#thing"


def test_missing_sidecar_yields_null_bbox():
    step = Step(action="click", role="editor", selector="#btn")
    behavior = _compute_cursor_behavior(step, None)
    assert behavior["kind"] == "click"
    assert behavior["target"]["bbox"] is None


def test_custom_without_override_is_hidden():
    step = Step(action="custom", role="editor", code="page.fill('x', 'y')")
    assert _compute_cursor_behavior(step, _sidecar_single()) is None


def test_custom_with_override_uses_override():
    override = GuideCursorOverride(behavior="type", target="#dob", value="21-06-1985")
    step = Step(
        action="custom",
        role="editor",
        code="page.fill('x', 'y')",
        guide_cursor=override,
    )
    bbox = {"x": 50, "y": 60, "width": 120, "height": 32}
    behavior = _compute_cursor_behavior(step, _sidecar_single(bbox=bbox, selector="#dob"))
    assert behavior == {
        "kind": "type",
        "target": {"selector": "#dob", "bbox": bbox},
        "value": "21-06-1985",
    }


# ── Template smoke test ────────────────────────────────────────────────────


def test_guide_html_template_renders_cursor_layer():
    """Render the bundled guide.html.j2 against synthetic steps and confirm the cursor layer is present."""
    from steppy.guide import register_jinja_filters

    bundled = Path(__file__).resolve().parent.parent / "templates"
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(bundled)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    register_jinja_filters(env)
    template = env.get_template("guide.html.j2")
    steps = [
        {
            "index": 1,
            "text": "Click the button. It creates a thing.",
            "screenshot": "s1.png",
            "viewport": {"width": 1280, "height": 720},
            "cursor": {
                "kind": "click",
                "target": {"selector": "#btn", "bbox": {"x": 100, "y": 200, "width": 50, "height": 20}},
            },
        },
        {
            "index": 2,
            "text": "Hidden step.",
            "screenshot": "s2.png",
            "viewport": {"width": 1280, "height": 720},
            "cursor": None,
        },
    ]
    cursor_timings = {
        "pre_action_ms": 200,
        "action_ms": 300,
        "post_action_ms": 150,
        "travel_speed_px_per_ms": 1.5,
    }
    html = template.render(title="Test", role="Editor", steps=steps, cursor_timings=cursor_timings)
    assert "window.__STEPPY_STEPS__" in html
    assert "window.__STEPPY_TIMINGS__" in html
    assert "steppy-cursor" in html
    assert "steppy-ripple" in html
    assert "steppy-kbd" in html
    assert '"width": 1280' in html
    assert 'data-step-index="1"' in html
    # The hidden step must still render its screenshot.
    assert "s2.png" in html
